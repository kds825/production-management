"""Stage 1 파이프라인 API — ERP 업로드 → 작업지시서 생성 → Excel 다운로드"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from app.application.validation.constraint_checker import validate_all  # noqa: F401 — used in stage2
from app.application.ingest.run_labeler import (  # noqa: F401 — re-export for tests
    new_run_label as _alloc_run_label,
    parse_base_date_yyyymmdd,
    parse_date_yyyymmdd,
)
from app.application.ingest.pipeline_orchestrator import (  # noqa: F401
    execute_stage1_ingest,
    execute_stage2,
)
from app.application.ingest.stage1 import run_solver_stage  # noqa: F401
from app.application.ingest.stage2 import run_greedy_stage  # noqa: F401

# Public re-export under the helper's canonical name (kept importable from
# the route module so callers / tests can reach it as plan_pipeline.new_run_label
# without going through services.pipeline). Aliased above to avoid colliding
# with the local variable named ``new_run_label`` inside run_stage1_update().
new_run_label = _alloc_run_label  # noqa: F811 — intentional re-export alias
from app.application.scheduling.greedy.auto_schedule import auto_schedule  # noqa: F401, E402 — alias 후 import

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["파이프라인"])


# ---------------------------------------------------------------------------
# AI 분석 결과 인메모리 캐시 — _pipeline_shared 모듈로 이동 (Task 1.1)
# 외부 import path 보존: from app.presentation.routes.plan_pipeline import _ai_cache
# ---------------------------------------------------------------------------
from app.presentation.routes._pipeline_shared import (  # noqa: E402, F401
    _ai_cache,
    _ai_cache_lock,
    _run_ai_background,
    _start_ai_background,
)


def _parse_stage2_body(body: dict) -> tuple[str, datetime | None, str]:
    """POST /stage2 body 파싱 공통 루틴 (sync/async 경로 공유)."""
    run_label = body.get("run_label")
    if not run_label:
        raise HTTPException(status_code=400, detail="run_label 필수")

    base_date_dt = parse_base_date_yyyymmdd(body.get("base_date"))
    optimizer = body.get("optimizer", "cpsat")  # "cpsat" | "greedy"
    return run_label, base_date_dt, optimizer


# _start_ai_background 는 _pipeline_shared 로 이동 (Task 1.1) — 위 import 가 re-export.


def _execute_stage2_core(
    run_label: str,
    base_date_dt: datetime | None,
    optimizer: str,
    db: Session,
) -> dict:
    """Stage2 핵심 로직 thin shim — orchestrator.execute_stage2 위임.

    sync `/pipeline/stage2` 와 async `/pipeline/stage2/async` 의 공유 구현.
    SchedulerOverlapError 는 여기서 잡지 않고 호출자가 매핑하도록 전파한다
    (sync 는 200 + overlap_alert 응답, async job 은 status=overlap_alert
    저장).

    Why a shim and not a direct alias to execute_stage2: 본 함수 자체를
    monkeypatch 하는 테스트 (test_stage2_async_job) 가 있어 함수 객체가
    plan_pipeline 모듈 namespace 에 살아 있어야 한다. 또한 orchestrator 가
    auto_schedule / validate_all 을 DI 로 받게 했기 때문에, 라우트 모듈에서
    monkeypatch 가능한 두 심볼을 호출 시점에 전달할 수 있다.
    """
    return execute_stage2(
        run_label,
        base_date_dt,
        optimizer,
        db,
        auto_schedule_fn=auto_schedule,
        validate_all_fn=validate_all,
        ai_background_starter=_start_ai_background,
    )


# ---------------------------------------------------------------------------
# Sub-router includes — runs/batch (Task 1.2) + stage1/stage2 (Task 1.3) +
# batch_group (Task 1.4)
# ---------------------------------------------------------------------------
from app.presentation.routes import (  # noqa: E402
    plan_pipeline_batch,
    plan_pipeline_batch_group,
    plan_pipeline_runs,
    plan_pipeline_stage1,
    plan_pipeline_stage2,
)

router.include_router(plan_pipeline_runs.router)
router.include_router(plan_pipeline_batch.router)
router.include_router(plan_pipeline_stage1.router)
router.include_router(plan_pipeline_stage2.router)
router.include_router(plan_pipeline_batch_group.router)

# Re-export — 외부 import path 보존 (test_stage1_update_versioning.py:177 등)
# from app.presentation.routes.plan_pipeline import compare_runs, list_runs, delete_run
from app.presentation.routes.plan_pipeline_runs import (  # noqa: E402, F401
    compare_runs,
    list_runs,
    delete_run,
)
