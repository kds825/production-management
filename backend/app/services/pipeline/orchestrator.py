"""Pipeline orchestrator — Stage 1 + Stage 2 coordinator (Week 4 Task 4A.1).

Top-level entry that the HTTP route delegates to. Picks the engine branch
(``run_solver_stage`` for cpsat, ``run_greedy_stage`` for greedy), runs
constraint validation, commits the DB, and queues the AI background task.

Why DI of ``auto_schedule_fn`` / ``validate_all_fn`` / ``ai_background_starter``:

* ``auto_schedule`` is exposed as a *module attribute* on
  ``app.presentation.routes.plan_pipeline`` and monkeypatched by
  ``test_schedule_route_overlap`` (which raises ``SchedulerOverlapError``
  to verify the 200 + overlap_alert mapping). Importing it here directly
  would freeze the reference and defeat the patch.
* ``validate_all`` similarly needs to remain swappable from the route's
  module namespace for parity tests that may stub it in future.
* ``ai_background_starter`` is a callable that the route binds to
  ``_run_ai_background`` (which still owns the in-memory ``_ai_cache``
  PoC dict and threading.Lock). We don't move that cache here because the
  route's GET ``/stage2/{run_label}/ai-status`` endpoint reads it and
  duplicating the lock across modules would break atomicity.

Contract:
* SchedulerOverlapError is *propagated*, never caught here. The route's
  sync handler maps it to HTTP 200 + overlap_alert; the async job worker
  maps it to status="overlap_alert".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.services.pipeline.stage1 import run_solver_stage
from app.services.pipeline.stage2 import run_greedy_stage


def execute_stage2(
    run_label: str,
    base_date_dt: datetime | None,
    optimizer: str,
    db: Session,
    *,
    auto_schedule_fn: Callable[..., dict[str, Any]],
    validate_all_fn: Callable[..., list[Any]],
    ai_background_starter: Callable[[str], None],
) -> dict[str, Any]:
    """Run the chosen scheduling engine, validate, commit, kick AI background.

    sync ``/pipeline/stage2`` and async ``/pipeline/stage2/async`` share
    this implementation through the route's thin shim
    ``_execute_stage2_core``. Returns the response payload contract:

        {
          "run_label": str,
          "schedule": {... engine: "cpsat"|"greedy"|"greedy_fallback" ...},
          "violations": list,
          "total_violations": int,
          "overlap_alert": False,   # always False on success path; the
                                    # caller flips it to True when mapping
                                    # SchedulerOverlapError
        }
    """
    if optimizer == "greedy":
        schedule_result = run_greedy_stage(
            run_label, db, base_date_dt, auto_schedule_fn=auto_schedule_fn
        )
    else:
        # CP-SAT 경로도 auto_schedule 의 retry+validate 래퍼를 타도록 통합
        # (Fix P0-4A). CP-SAT 실패/타임아웃 시 내부에서 그리디로 폴백하고,
        # 겹침 감지 시 random_seed 를 바꿔가며 재시도한다.
        schedule_result = run_solver_stage(
            run_label, db, base_date_dt, auto_schedule_fn=auto_schedule_fn
        )

    violations = validate_all_fn(run_label, db)
    db.commit()

    # AI 분석을 백그라운드 스레드로 비동기 실행 — 응답을 블로킹하지 않음.
    # ai_background_starter 는 route 의 _run_ai_background 를 트리거하는
    # 어댑터 (cache mutation + thread.start) 이며 route 모듈에 in-memory
    # cache 가 살아 있으므로 여기서 직접 cache 를 만지지 않는다.
    ai_background_starter(run_label)

    return {
        "run_label": run_label,
        "schedule": schedule_result,
        "violations": violations,
        "total_violations": len(violations),
        "overlap_alert": False,
    }
