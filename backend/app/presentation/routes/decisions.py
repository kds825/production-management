"""Decision Card endpoint — GET /api/decisions/{batch_id}/latest (Week 4 Task 4B.1).

Why this route exists:
  The Decision Card UI (Week 5) needs one HTTP call to render: the latest
  solver run for a batch, the per-constraint contributions, the binding
  hard constraints, the assigned slot, any manual override metadata, and
  a one-sentence Korean summary. Joining solver_run + solver_decision +
  schedule_change_sets + schedule_task on the server keeps the UI
  thin and avoids N+1 round-trips.

Why ``batch_id`` is a string in the URL even though
``production_batch.batch_id`` is an Integer column:
  Future batch identifiers may grow into composite strings (e.g.
  "20260425_EX-B100_001"); routing as ``str`` and parsing inside keeps
  the URL contract stable. We parse-to-int when the input is numeric and
  fall through to the run_label fallback otherwise.

Why we cache the LLM summary on ``solver_run`` (not solver_decision):
  See ``alembic c4a1b2d3e5f7`` — the summary is a per-run sentence, not
  per-constraint. Storing it on solver_run avoids N redundant copies.

404 contract: returns 404 when no solver_decision row references a run
whose run_label matches a ProductionBatch with the given batch_id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.solver_decision import SolverDecision
from app.infrastructure.models.solver_run import SolverRun
from app.infrastructure.llm import (
    ConstraintRef,
    Contribution,
    ExplainPayload,
)
from app.application.decisions.alternatives import compute_alternatives

router = APIRouter(prefix="/decisions", tags=["decisions"])


def _resolve_batch_and_run(
    db: Session, batch_id: str
) -> tuple[ProductionBatch, SolverRun]:
    """Resolve (ProductionBatch, latest SolverRun) for the given batch_id.

    Why two-step lookup:
      ProductionBatch.batch_id is Integer; we accept the URL form as str
      and try int conversion. The latest SolverRun is the one with the
      maximum ``started_at`` for the batch's ``run_label`` — this is the
      same predicate the admin dashboard uses, so semantics stay
      consistent.

    Raises HTTPException(404) when:
      - batch_id is non-numeric or no batch matches, or
      - the batch has no SolverRun.
    """
    try:
        batch_pk = int(batch_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=404, detail=f"배치 '{batch_id}' 를 찾을 수 없습니다."
        ) from exc

    batch = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id == batch_pk)
        .one_or_none()
    )
    if batch is None:
        raise HTTPException(
            status_code=404, detail=f"배치 '{batch_id}' 를 찾을 수 없습니다."
        )

    run = (
        db.query(SolverRun)
        .filter(SolverRun.run_label == batch.run_label)
        .order_by(SolverRun.started_at.desc())
        .first()
    )
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"배치 '{batch_id}' 에 대한 solver_run 이 없습니다.",
        )
    return batch, run


def _korean_name_lookup(db: Session) -> dict[str, str]:
    """Map ``constraint_id -> constraint_name`` for the enabled constraints.

    Why pulled separately: the per-decision rows reference constraint_id
    only; the human-readable Korean name lives on ConstraintConfig. We
    fetch once and re-use across all SolverDecision rows.
    """
    rows = db.query(ConstraintConfig).all()
    return {r.constraint_id: r.constraint_name for r in rows}


def _to_iso(dt: datetime | None) -> str | None:
    """Serialize datetime to ISO-8601 with explicit Z when UTC.

    Why explicit handling: the schedule_task table stores naive datetimes
    while solver_run stores tz-aware. Returning a single canonical shape
    from the endpoint avoids client-side parsing forks.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat()
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_contributions(
    decisions: list[SolverDecision], names: dict[str, str]
) -> list[dict[str, Any]]:
    """Project SolverDecision rows into Contribution dicts for the response.

    Why include rows where penalty_value is None:
      Hard constraints have no penalty but may still be relevant context
      ("this slot was forced by a hard constraint"). The UI decides
      whether to render them based on weight_applied.
    """
    out: list[dict[str, Any]] = []
    for d in decisions:
        out.append(
            {
                "constraint_id": d.constraint_id,
                "korean_name": names.get(d.constraint_id, d.constraint_id),
                "weight_applied": (
                    float(d.penalty_value) if d.penalty_value is not None else 0.0
                ),
                "bound": None,
                "delta_if_removed": None,
            }
        )
    return out


def _build_binding_hard(
    decisions: list[SolverDecision], names: dict[str, str]
) -> list[dict[str, str]]:
    """Decisions whose hard_literal_value is True — i.e. binding hard rules."""
    return [
        {
            "constraint_id": d.constraint_id,
            "korean_name": names.get(d.constraint_id, d.constraint_id),
        }
        for d in decisions
        if d.hard_literal_value is True
    ]


def _manual_override_block(
    db: Session, decisions: list[SolverDecision]
) -> tuple[bool, dict[str, Any] | None]:
    """Look for a SolverDecision that links to a schedule_change_set.

    Why first-non-null wins: in practice at most one decision row per run
    carries a manual override; if multiple appear we surface the first
    deterministic one (sorted by constraint_id). Tests rely on this
    behaviour for stable assertions.
    """
    linked = sorted(
        (d for d in decisions if d.manual_override_change_set_id is not None),
        key=lambda d: d.constraint_id,
    )
    if not linked:
        return False, None

    cs_id = linked[0].manual_override_change_set_id
    cs = (
        db.query(ScheduleChangeSet)
        .filter(ScheduleChangeSet.change_set_id == cs_id)
        .one_or_none()
    )
    if cs is None:
        # The FK is SET NULL on delete, so this can happen when the
        # change_set was purged after the decision was written. We still
        # report the flag but with a null block — the UI can render
        # "manually adjusted (record purged)".
        return True, None

    return True, {
        "change_set_id": cs.change_set_id,
        "kind": cs.kind,
        "applied_by": cs.applied_by,
        "created_at": _to_iso(cs.created_at),
    }


def _ensure_summary(
    db: Session,
    run: SolverRun,
    payload: ExplainPayload,
    name_catalog: set[str],
) -> tuple[str, bool]:
    """Return cached LLM summary (legacy) — new traces skip generation.

    Why deprecated: the modal now derives a deterministic 1-line meta
    summary from the contributions distribution on the client. The LLM
    tagline carries no extra signal but costs an API call (or at minimum
    a template render + DB write) on every new trace.

    Behavior:
      - If ``run.llm_summary_text`` was already populated by an older
        commit, return it as-is for backward compat.
      - For new runs, return ``("", True)`` without touching the
        provider or persisting anything. ``was_template=True`` reflects
        the deterministic-source semantics ("not from a live LLM").

    The ``payload`` / ``name_catalog`` arguments are kept for signature
    stability so callers (route + future re-enable) remain unchanged.
    """
    if run.llm_summary_text is not None:
        return run.llm_summary_text, bool(run.llm_was_template)
    return "", True


@router.get("/{batch_id}/latest")
def get_latest_decision(batch_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return the latest decision trace + cached LLM summary for a batch."""
    batch, run = _resolve_batch_and_run(db, batch_id)

    decisions = (
        db.query(SolverDecision)
        .filter(SolverDecision.run_id == run.run_id)
        .order_by(SolverDecision.constraint_id)
        .all()
    )
    if not decisions:
        # A run without decisions is structurally invalid for the
        # Decision Card; return 404 rather than rendering a blank card.
        raise HTTPException(
            status_code=404,
            detail=f"배치 '{batch_id}' 에 대한 solver_decision 이 없습니다.",
        )

    names = _korean_name_lookup(db)
    contributions = _build_contributions(decisions, names)
    binding_hard = _build_binding_hard(decisions, names)

    # Pull the assigned slot from schedule_task. Missing slot is non-fatal
    # — the Decision Card can render "미배정" but the run/decision data is
    # still useful, so we degrade gracefully with empty strings/null dts.
    task = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.batch_id == batch.batch_id)
        .order_by(ScheduleTask.created_at.desc())
        .first()
    )
    assigned_equipment_id = task.equipment_code if task else ""
    assigned_start = task.start_datetime if task else run.started_at
    assigned_end = task.end_datetime if task else (run.finished_at or run.started_at)

    is_manually_adjusted, manual_override = _manual_override_block(db, decisions)

    payload = ExplainPayload(
        batch_id=str(batch.batch_id),
        run_label=run.run_label,
        contributions=[
            Contribution(
                constraint_id=c["constraint_id"],
                korean_name=c["korean_name"],
                weight_applied=c["weight_applied"],
            )
            for c in contributions
        ],
        binding_hard_constraints=[
            ConstraintRef(
                constraint_id=b["constraint_id"], korean_name=b["korean_name"]
            )
            for b in binding_hard
        ],
        assigned_equipment_id=assigned_equipment_id,
        assigned_start=assigned_start,
        assigned_end=assigned_end,
    )
    name_catalog = {v for v in names.values() if v}
    summary_text, was_template = _ensure_summary(db, run, payload, name_catalog)

    return {
        "batch_id": str(batch.batch_id),
        "run_id": run.run_id,
        "run_label": run.run_label,
        "solver_status": run.solver_status,
        "objective_value": run.objective_value,
        "assigned_equipment_id": assigned_equipment_id,
        "assigned_start": _to_iso(assigned_start),
        "assigned_end": _to_iso(assigned_end),
        "contributions": contributions,
        "binding_hard_constraints": binding_hard,
        "is_manually_adjusted": is_manually_adjusted,
        "manual_override": manual_override,
        "llm_summary": summary_text,
        "llm_was_template": was_template,
    }


@router.get("/{batch_id}/alternatives")
def get_alternatives(batch_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """이 배치를 다른 설비로 옮길 경우의 영향 추정.

    응답: 호환 설비 list + 각 설비의 충돌 수 + 가용 시점 + 지연일.
    현재 설비가 항상 첫 행 — UI 가 비교 anchor 로 사용.

    on-the-fly compute (solver 미실행) — 자세한 근거는
    application/decisions/alternatives.py docstring 참조.
    """
    try:
        batch_pk = int(batch_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=404, detail=f"배치 '{batch_id}' 를 찾을 수 없습니다."
        ) from exc

    report = compute_alternatives(db, batch_pk)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"배치 '{batch_id}' 를 찾을 수 없습니다."
        )

    return {
        "batch_id": str(report.batch_id),
        "current_equipment_code": report.current_equipment_code,
        "current_assigned_start": _to_iso(report.current_assigned_start),
        "current_assigned_end": _to_iso(report.current_assigned_end),
        "alternatives": [
            {
                "equipment_code": a.equipment_code,
                "equipment_name": a.equipment_name,
                "is_current": a.is_current,
                "conflict_count": a.conflict_count,
                "earliest_available": _to_iso(a.earliest_available),
                "delay_days": a.delay_days,
            }
            for a in report.alternatives
        ],
    }
