"""Stage 2 (자동 스케줄링) API endpoints.

원본: ``plan_pipeline.py`` 의 /stage2/* + ai-status + trigger-reanalysis
endpoint 들을 sub-router 로 분리 (Task 1.3). URL path 변경 없음.

Appendix A.3 (Critical) — monkeypatch-friendly 호출 패턴:
    test_stage2_async_job.py:126 가 ``monkeypatch.setattr(plan_pipeline,
    "_execute_stage2_core", ...)`` 로 patch 한다. 호출 측이 모듈 import 후
    attribute access (``plan_pipeline._execute_stage2_core``) 를 해야
    monkeypatch 가 적용된다. ``from ... import _execute_stage2_core`` 패턴은
    import 시점에 reference 가 고정돼 patch 가 무시된다.
"""

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.exceptions import SchedulerOverlapError
from app.infrastructure.database import SessionLocal, get_db
from app.presentation.routes import plan_pipeline  # 모듈 자체 import — Appendix A.3
from app.presentation.routes._pipeline_shared import (
    _ai_cache,
    _ai_cache_lock,
    _run_ai_background,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/stage2", summary="Stage 2: 자동 스케줄링 (동기)")
def run_stage2(body: dict, db: Session = Depends(get_db)):
    """Stage 2 동기 경로 — 기존 호환 유지.

    왜 동기를 유지하는가:
      기존 테스트 (test_schedule_route_overlap) 및 프론트 일부 흐름이
      이 엔드포인트의 즉시 응답에 의존. async 경로는 `/stage2/async`
      로 분리하여 점진 이관 가능하도록 함.

    body:
        run_label: str (필수)
        base_date: str (선택, YYYYMMDD 형식 — 스케줄 시작 기준일)
        optimizer: "cpsat" | "greedy" (기본 "cpsat")
    """
    run_label, base_date_dt, optimizer = plan_pipeline._parse_stage2_body(body)

    try:
        return plan_pipeline._execute_stage2_core(
            run_label, base_date_dt, optimizer, db
        )
    except SchedulerOverlapError as exc:
        # 왜 200: 이 예외는 '겹침 재시도 실패' 비즈니스 시그널이지 서버 장애가 아니다.
        # 프론트가 overlap_alert=True 플래그로 경고 배너를 표시할 수 있도록 성공 코드로 반환.
        # DB rollback 은 auto_schedule 내부에서 이미 수행됨(기존 스케줄 불변).
        db.rollback()
        logger.warning(
            "stage2 overlap alert — run_label=%s attempts=%s violations=%s",
            run_label,
            exc.attempts,
            len(exc.violations),
        )
        return {
            "run_label": run_label,
            "status": "overlap_alert",
            "overlap_alert": True,
            "message": str(exc),
            "violations": exc.violations,
            "total_violations": len(exc.violations),
            "attempts": exc.attempts,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"스케줄링 실패: {exc}") from exc


@router.post("/stage2/async", summary="Stage 2: 자동 스케줄링 (비동기 job 제출)")
def run_stage2_async(body: dict) -> dict:
    """Stage 2 비동기 경로 — 즉시 job_id 반환, 실제 처리는 백그라운드 스레드.

    왜 async 경로가 필요한가:
      동기 엔드포인트는 10분 급 Stage2 처리 동안 uvicorn 워커 + threadpool
      슬롯을 점유해 동시 요청 응답성을 악화시키고, 프론트 입장에서 진행률
      표시가 불가능해 UX 가 나쁘다. 이 경로는 job 을 큐에 등록하고 즉시
      반환 → 프론트는 `GET /stage2/status/{job_id}` 로 폴링.

    Returns:
        { "job_id": str, "status": "running", "run_label": str }
    """
    from app.application.stage2_job_queue import Stage2JobRequest, submit_job

    run_label, base_date_dt, optimizer = plan_pipeline._parse_stage2_body(body)

    def _runner(req: Stage2JobRequest, db: Session) -> dict:
        # SchedulerOverlapError 는 큐 워커가 overlap_alert 상태로 매핑.
        # 다른 예외는 큐 워커가 error 상태로 기록하고 로그에 남김.
        # 모듈 attribute access 로 monkeypatch 친화적 (Appendix A.3).
        return plan_pipeline._execute_stage2_core(
            req.run_label, req.base_date, req.optimizer, db
        )

    req = Stage2JobRequest(
        run_label=run_label, base_date=base_date_dt, optimizer=optimizer
    )
    job_id = submit_job(req, _runner, SessionLocal)
    return {"job_id": job_id, "status": "running", "run_label": run_label}


@router.get(
    "/stage2/status/{job_id}",
    summary="Stage 2 비동기 job 상태 조회",
)
def get_stage2_status(job_id: str) -> dict:
    """job 상태 반환.

    상태 필드:
      - status: "running" | "done" | "overlap_alert" | "error"
      - result: done/overlap_alert 일 때만 채워짐 (sync 응답과 동일 구조)
      - error: error 일 때만 채워짐
      - started_at / finished_at: ISO-8601 UTC
    """
    from app.application.stage2_job_queue import get_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job_id={job_id} 없음")
    return job


@router.get("/stage2/{run_label}/ai-status", summary="AI 분석 진행 상태 조회")
def get_ai_status(run_label: str):
    """Stage 2 실행 후 비동기 AI 분석의 진행 상태를 반환한다.

    Returns:
        status: "pending" | "done" | "error"
        summary: AI 분석 결과 (status=done일 때만)
        error: 오류 메시지 (status=error일 때만)
    """
    with _ai_cache_lock:
        cached = _ai_cache.get(run_label)
    if not cached:
        return {"status": "pending"}
    return cached


@router.post("/stage2/{run_label}/trigger-reanalysis", summary="AI 재분석 트리거")
def trigger_reanalysis(run_label: str):
    """블록 변경(이동/분할/연장/재배치) 후 AI 분석을 재실행한다.

    캐시를 pending으로 초기화하고 백그라운드 스레드에서 AI 분석을 다시 실행한다.
    즉시 반환하여 프론트엔드를 블로킹하지 않는다.
    """
    with _ai_cache_lock:
        _ai_cache[run_label] = {"status": "pending"}
    thread = threading.Thread(target=_run_ai_background, args=(run_label,), daemon=True)
    thread.start()
    return {"status": "pending", "message": f"AI 재분석 시작: {run_label}"}
