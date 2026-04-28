"""plan_pipeline 의 공유 helper 모듈 — _ai_cache singleton + AI background.

추출 대상 (review patch Appendix A.1 적용 — 축소된 범위):
- ``_ai_cache`` 모듈-레벨 dict
- ``_ai_cache_lock`` threading.Lock
- ``_run_ai_background``
- ``_start_ai_background``

**옮기지 않음** (monkeypatch 호환 — 두 review 의 critical 발견):
- ``_parse_stage2_body`` — ``plan_pipeline.py`` 에 그대로 유지
- ``_execute_stage2_core`` — ``plan_pipeline.py`` 에 그대로 유지
  (test_stage2_async_job.py:126 가 ``monkeypatch.setattr(plan_pipeline,
  "_execute_stage2_core", ...)`` 로 patch 하므로, 함수 객체가
  ``plan_pipeline`` 모듈 namespace 에 살아 있어야 한다.)

``_ai_cache`` 는 모듈-레벨 dict 라 multiple sub-router import 가 동일 인스턴스를
참조 — singleton 의미 보존. **단, 본 PoC 는 단일 worker (uvicorn --workers=1)
가정.** future production 전환 시 Redis / shared memory 로 cache 외부화 필요.
"""

from __future__ import annotations

import logging
import threading

from app.infrastructure.database import SessionLocal

logger = logging.getLogger(__name__)

# AI 분석 결과 인메모리 캐시 — PoC 단계용 단순 dict
# key: run_label, value: {"status": "pending"|"done"|"error", "summary": {...}}
_ai_cache: dict[str, dict] = {}
_ai_cache_lock = threading.Lock()


def _run_ai_background(run_label: str) -> None:
    """백그라운드 스레드에서 AI 배치 요약을 생성하여 캐시에 저장한다.

    FastAPI BackgroundTasks는 응답 전송 후 실행되므로, request-scoped DB 세션이
    이미 닫혀 있다. 따라서 SessionLocal()로 독립 세션을 생성한다.
    """
    db = SessionLocal()
    try:
        from app.application.decisions.summarize_run import generate_batch_summary_sync

        result = generate_batch_summary_sync(run_label, db)
        with _ai_cache_lock:
            _ai_cache[run_label] = {"status": "done", "summary": result}
        logger.info("[AI Background] run_label=%s 분석 완료", run_label)
    except Exception as exc:
        with _ai_cache_lock:
            _ai_cache[run_label] = {"status": "error", "error": str(exc)}
        logger.warning("[AI Background] run_label=%s 분석 실패: %s", run_label, exc)
    finally:
        db.close()


def _start_ai_background(run_label: str) -> None:
    """AI 백그라운드 분석을 큐잉하는 어댑터.

    Why an adapter: orchestrator.execute_stage2 는 in-memory _ai_cache /
    _ai_cache_lock 를 직접 만지지 않도록 의도적으로 cache 소유권을 본 라우트
    모듈에 남겼다 (GET /stage2/{run_label}/ai-status 가 같은 dict 를 읽기
    때문). 이 어댑터가 cache mutation + thread.start 를 하나로 묶어
    orchestrator 가 단일 콜만 하면 되도록 한다.
    """
    with _ai_cache_lock:
        _ai_cache[run_label] = {"status": "pending"}
    thread = threading.Thread(target=_run_ai_background, args=(run_label,), daemon=True)
    thread.start()
