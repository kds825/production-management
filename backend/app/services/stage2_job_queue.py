"""Stage 2 비동기 작업 큐 — 자동배열(10분 급) 을 워커 블로킹 없이 실행.

왜 필요한가:
  Stage 2 자동배열은 CP-SAT 솔버 + 제약검증 + DB 쓰기가 누적되어 대형 런
  에서 수 분~수십 분 소요될 수 있다. 동기 엔드포인트 (`POST /pipeline/stage2`)
  는 요청 단일 uvicorn 워커(+ threadpool 슬롯)를 그 시간 내내 점유해
  같은 시간대에 들어오는 다른 HTTP 요청이 대기·타임아웃 되는 원인이었다.
  또한 프론트 입장에서는 10분 동안 단일 HTTP 응답만 바라보며 진행률도
  표시할 수 없어 UX 가 나쁨.

설계:
  - 인프로세스(dict + threading.Lock) 큐. 외부 큐(Celery/RQ/Redis) 는 지금
    필요한 규모가 아니고, 이미 `_run_ai_background` 가 동일 패턴을 쓰고
    있어 일관성 확보.
  - 각 job 은 독립 `SessionLocal()` 을 연다. 요청 세션을 공유하면 응답
    직후 세션이 닫혀 job 이 죽는다 (`_run_ai_background` 와 동일 이유).
  - 결과 payload 구조는 기존 sync `POST /pipeline/stage2` 응답과 동일 —
    프론트가 status polling 후 `result` 필드를 기존 응답처럼 처리 가능.
  - job 상태: running | done | overlap_alert | error.
  - 단일 프로세스 메모리라 재시작 시 휘발. PoC 스코프에서 수용.

제약:
  - uvicorn --workers 2+ 구성에서는 각 워커가 독립 큐 → status polling
    이 다른 워커에 착지하면 job_id 못 찾음. 현재 구성은 단일 워커이므로
    문제없으나, 다중 워커 도입 시에는 Redis/DB 백엔드로 승급 필요.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# job_id → 상태 dict
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


@dataclass
class Stage2JobRequest:
    """job worker 에 전달되는 불변 파라미터."""

    run_label: str
    base_date: datetime | None
    optimizer: str  # "cpsat" | "greedy"


def submit_job(
    req: Stage2JobRequest,
    runner: Callable[[Stage2JobRequest, Session], dict],
    session_factory: Callable[[], Session],
) -> str:
    """새 job 생성 → 백그라운드 스레드 기동 → job_id 즉시 반환.

    Args:
        req: 실행 파라미터.
        runner: 실제 Stage2 로직. `(req, db)` 시그니처로 dict 반환 혹은
            `SchedulerOverlapError` 발생. HTTPException 은 잡지 않음
            (호출자가 500 으로 매핑할 수 없으므로 runner 가 내부에서 처리).
        session_factory: 새 DB 세션 팩토리. 보통 `SessionLocal`.

    Returns:
        uuid4 문자열. `GET /stage2/status/{job_id}` 로 조회.
    """
    job_id = str(uuid.uuid4())
    started_at = datetime.utcnow().isoformat()
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "run_label": req.run_label,
            "started_at": started_at,
            "finished_at": None,
            "result": None,
            "error": None,
        }

    def _worker() -> None:
        from app.exceptions import SchedulerOverlapError  # local import

        db = session_factory()
        try:
            try:
                result = runner(req, db)
                payload = {
                    "status": "done",
                    "result": result,
                }
            except SchedulerOverlapError as exc:
                # runner 내부에서 rollback 이 이미 처리됨 (auto_schedule 규약).
                payload = {
                    "status": "overlap_alert",
                    "result": {
                        "run_label": req.run_label,
                        "status": "overlap_alert",
                        "overlap_alert": True,
                        "message": str(exc),
                        "violations": exc.violations,
                        "total_violations": len(exc.violations),
                        "attempts": exc.attempts,
                    },
                }
            except Exception as exc:  # pragma: no cover — 방어적 에러 캡처
                db.rollback()
                logger.exception(
                    "Stage2 job 실패 — job_id=%s run_label=%s",
                    job_id,
                    req.run_label,
                )
                payload = {"status": "error", "error": str(exc)}
        finally:
            db.close()

        finished_at = datetime.utcnow().isoformat()
        with _jobs_lock:
            # job 이 중간에 삭제됐을 수 있으므로 방어적 업데이트.
            if job_id in _jobs:
                _jobs[job_id].update({**payload, "finished_at": finished_at})

    thread = threading.Thread(target=_worker, daemon=True, name=f"stage2-{job_id[:8]}")
    thread.start()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    """job 상태 dict 반환. 없으면 None."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        # 호출자가 값을 수정해도 큐 내부에 영향 없도록 shallow copy.
        return dict(job)


def _reset_for_tests() -> None:
    """테스트 전용: 큐 초기화. 프로덕션 코드에서 호출 금지."""
    with _jobs_lock:
        _jobs.clear()
