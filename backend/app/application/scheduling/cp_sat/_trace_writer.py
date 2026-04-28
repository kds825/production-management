"""cp_sat_schedule() 의 §10 trace write 단계 — Phase 2 Task 2.7 (B-3.2) 추출.

원본: ``orchestrator.py:1340-1468`` 본문 그대로. solver_run + solver_decision
한 벌을 DB 에 기록하는 observability 라인을 별도 모듈로 분리.

Why module name `_trace_writer.py` (underscore):
    동일 디렉토리에 이미 존재하는 ``trace_writer.py`` (TraceMetadata /
    write_trace / compute_input_hash / compute_output_hash 정의 모듈) 와
    충돌하지 않도록 underscore prefix. Phase 2 컨벤션 (`_X.py` = use-case
    composition helper, `X.py` = pure logic) 과 일치.

Why try/except:
    Trace 는 observability 이지 correctness 가 아님. 실패 시 result 에 영향
    주지 않고 warning log 만 남긴다 (parity 보존 invariant).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from app.application.scheduling.cp_sat import SolverInput
from app.application.scheduling.cp_sat.helpers import _SOLVER_TIME_LIMIT_SEC
from app.application.scheduling.cp_sat.model_builder import BuiltModel
from app.infrastructure.models.schedule_task import ScheduleTask


def write_solver_trace(
    *,
    db: Session,
    run_id: str,
    run_label: str,
    started_at: datetime,
    base_date: datetime,
    batches: list,
    solver_input_override: SolverInput | None,
    solver: cp_model.CpSolver,
    solver_status: int,
    built: BuiltModel,
    result: dict[str, Any],
    random_seed: int,
    time_limit_sec: int | None,
    sheath_color_hard: bool,
    tardiness_hard: bool,
) -> None:
    """원본: orchestrator.py:1340-1468 본문 그대로.

    실패 시 warning log + result["run_id"] 누락 (원본과 동일). result 는
    in-place mutate ("run_id" 키만 happy path 에서 추가).
    """
    # Why inline import: the module-level auto-formatter strips unused
    # imports during in-flight refactors; function-local keeps the
    # dependency explicit and co-located with the call site (matches the
    # existing `from app.infrastructure.models.wip_inventory import ...`
    # pattern ~L1122).
    # Why try/except: trace is observability, not correctness — if it
    # fails (e.g., schema drift, network blip), log a warning and let the
    # caller receive a valid `result`. The unit test suite asserts the
    # happy path; parity 11/11 catches SAVEPOINT rollback regressions.
    from app.application.scheduling.cp_sat.decision_aggregator import (
        build_decision_inputs,
    )
    from app.application.scheduling.cp_sat.trace_writer import (
        TraceMetadata,
        compute_input_hash,
        compute_output_hash,
        write_trace,
    )

    try:
        # Reconstruct an assignments shape that `compute_output_hash`
        # understands. We read from ScheduleTask (already flushed by the
        # scheduler passes above) rather than maintaining an in-memory
        # mirror — one source of truth, robust against future loops
        # inserting/updating rows we don't track here.
        _trace_tasks = (
            db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
        )
        _trace_assignments: list[dict[str, Any]] = [
            {
                "group_key": t.batch_group or f"_single_{t.batch_id}",
                "equipment_id": t.equipment_code,
                "production_batch_id": t.batch_id,
                "assigned_start": t.start_datetime,
            }
            for t in _trace_tasks
        ]
        # base_date is guaranteed non-None here: either rebound from
        # solver_input_override at §1-3 or derived at §2 of the DB-load
        # branch; all pre-solver early-exits return before reaching us.
        _trace_output_hash = (
            compute_output_hash(run_label, _trace_assignments, base_date)
            if _trace_assignments
            else None
        )
        _trace_input_hash = (
            compute_input_hash(solver_input_override, run_label)
            if solver_input_override is not None
            # Non-override DB-load path: build the same shape synthetically
            # from the ProductionBatch rows we loaded at §1.
            else (
                compute_input_hash(
                    type("_S", (), {"batches": batches})(),  # lightweight shim
                    run_label,
                )
            )
        )
        _trace_meta = TraceMetadata(
            run_label=run_label,
            run_id=run_id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            solver_status=result.get("solver_status", "UNKNOWN"),
            objective_value=(
                float(result["objective_value"])
                if result.get("objective_value") is not None
                else None
            ),
            input_hash=_trace_input_hash,
            output_hash=_trace_output_hash,
            constraint_config_version=None,  # Week 4+
            solver_params={
                "num_search_workers": result.get("solver_num_workers"),
                "random_seed": int(random_seed),
                "time_limit_sec": (
                    int(time_limit_sec)
                    if time_limit_sec and int(time_limit_sec) > 0
                    else _SOLVER_TIME_LIMIT_SEC
                ),
                "sheath_color_hard": bool(sheath_color_hard),
                "tardiness_hard": bool(tardiness_hard),
            },
        )
        # Pilot-prep harness (closes Week 4 Task 2A.4 gap): aggregate the
        # per-constraint penalty/applied values from BuiltModel + solver
        # so the Decision Card has real numbers to show. Status check
        # gates the IntVar extraction (UNKNOWN/INFEASIBLE → undefined).
        _trace_pv, _trace_hv = build_decision_inputs(
            solver=solver,
            built=built,
            solver_status_ok=solver_status in (cp_model.OPTIMAL, cp_model.FEASIBLE),
            db=db,
        )
        write_trace(
            db,
            _trace_meta,
            penalty_values=_trace_pv,
            hard_literal_values=_trace_hv,
            specs=[],
            assignments=_trace_assignments,
        )
        result["run_id"] = run_id
    except Exception as _trace_exc:  # pragma: no cover — observability
        # Do not surface as `result["warnings"]` — the user-facing
        # warnings list is reserved for scheduling-semantic issues.
        # Task 2A.4 (spec §10a): use get_run_logger so this failure
        # message carries the [run_id=...] prefix — the one log line
        # where run_id tagging matters most for support triage.
        from app.infrastructure.logging import get_run_logger as _get_run_logger

        _get_run_logger(__name__).warning(
            "trace_writer failed (non-fatal): %s", _trace_exc, exc_info=True
        )
