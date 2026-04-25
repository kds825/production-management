"""Change-set reason attribution endpoint (Week 5 Task 5B.2).

Why this route exists:
  After a manual drag-drop on the Gantt chart, ``schedules/tasks/bulk-update``
  immediately persists the move and returns a ``change_set_id``. The operator
  is then prompted by a non-blocking sticky toast (60 s) to attribute a
  reason — "납기 변경" / "현장 긴급" / "설비 고장" / "자재 부족". The chip
  click PATCHes that reason here. The same id can also be cleared (reason →
  None) by an admin during batch review.

Why we *also* flip ``solver_decision.manual_override_change_set_id``:
  The Decision Card (``/api/decisions/{batch_id}/latest``) derives the
  ``is_manually_adjusted`` boolean from the existence of a
  ``manual_override_change_set_id`` link (see ``decisions._manual_override_block``).
  When the operator attributes a reason, every solver_decision row whose
  parent SolverRun shares a run_label with any task affected by the change_set
  must surface that override — so the Decision Card narrator can include
  "operator-overridden" wording without a second backend call.

Why two endpoints in one router:
  Both belong to the ``/api/change-sets`` URL family. Splitting into two
  files would force ``main.py`` to register two routers under the same
  prefix — increases the chance of route-collision drift.

Hardcoded reason allow-list:
  Spec §8d explicitly forbids ``기타`` — operators must pick one of the four
  semantic categories. We validate at the route layer (not Pydantic enum)
  so the 422 error message can name the offending value, which the toast
  bubbles back to the user.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.solver_decision import SolverDecision
from app.infrastructure.models.solver_run import SolverRun

router = APIRouter(prefix="/change-sets", tags=["change-sets"])


# Spec §8d: exactly four chips, no ``기타``. Order matches the toast layout
# (납기 → 현장 → 설비 → 자재) so the allow-list and UI stay in lockstep.
ALLOWED_REASONS: tuple[str, ...] = (
    "납기 변경",
    "현장 긴급",
    "설비 고장",
    "자재 부족",
)


class ReasonPatch(BaseModel):
    """Body for PATCH /api/change-sets/{id}/reason.

    ``None`` is a sentinel for "clear the reason" — admins use this during
    batch review when the original attribution was wrong. The route
    enforces the allow-list when ``reason`` is a string.
    """

    reason: str | None


def _coerce_task_pk(task_id_str: str) -> Any:
    """snapshot_before/after key → ScheduleTask PK.

    bulk_update_v2 stores keys as plain integer strings (see
    ``schedules._coerce_task_id``). We mirror that contract here so the
    ``IN (...)`` lookup uses the indexed integer column.
    """
    return int(task_id_str) if task_id_str.isdigit() else task_id_str


def _affected_task_ids(cs: ScheduleChangeSet) -> set[Any]:
    """Union of task_ids referenced by snapshot_before AND snapshot_after.

    Why union (not just before): a change_set may add a brand-new task
    (added in after but not before). Either side counts as "affected".
    Defensive ``or {}`` because JSONB columns can technically hold NULL
    if a row was inserted directly (tests do this for revert fixtures).
    """
    before_keys = (cs.snapshot_before or {}).keys()
    after_keys = (cs.snapshot_after or {}).keys()
    raw = set(before_keys) | set(after_keys)
    # Numeric-only — we never persist non-numeric task IDs in production,
    # but tests sometimes seed synthetic strings ("task_1") which would
    # blow up an Integer .in_() filter. Filter them out gracefully.
    return {_coerce_task_pk(k) for k in raw if isinstance(k, str) and k.isdigit()}


def _run_labels_for_tasks(db: Session, task_pks: set[Any]) -> set[str]:
    """ScheduleTask.run_label set covering the affected task PKs.

    Why we resolve via run_label (not batch_id):
      ``solver_decision.run_id → solver_run.run_label`` is the canonical
      join key for "decisions belonging to this plan run". A single
      change_set can touch tasks across multiple run_labels (rare, but
      possible during cross-run urgent inserts), so we collect the set.
    """
    if not task_pks:
        return set()
    rows = (
        db.query(ScheduleTask.run_label)
        .filter(ScheduleTask.task_id.in_(task_pks))
        .all()
    )
    return {label for (label,) in rows if label}


def _today_kst_iso_naive() -> datetime:
    """Midnight (KST) of today as a naive datetime.

    Why naive: ScheduleChangeSet.created_at is ``DateTime`` (no tz) and
    server-side default uses ``datetime.utcnow``. To keep the predicate
    consistent we compare with naive UTC midnight of "today in KST".
    KST = UTC+9 → today's KST midnight is yesterday 15:00 UTC.
    """
    today_utc = datetime.now(timezone.utc).date()
    return datetime.combine(today_utc, time.min)


@router.patch("/{change_set_id}/reason")
def patch_reason(
    change_set_id: str,
    body: ReasonPatch,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Update ``schedule_change_sets.override_reason`` and propagate.

    Returns ``{"updated_change_set", "decisions_updated"}``. ``decisions_updated``
    is the number of ``solver_decision`` rows whose ``manual_override_change_set_id``
    we set to this change_set.

    422 when ``reason`` is a string outside the allow-list. ``None`` always
    accepted (clear path). 404 when the change_set doesn't exist.
    """
    if body.reason is not None and body.reason not in ALLOWED_REASONS:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "invalid_reason",
                "offending_value": body.reason,
                "allowed": list(ALLOWED_REASONS),
            },
        )

    cs = db.get(ScheduleChangeSet, change_set_id)
    if cs is None:
        raise HTTPException(
            status_code=404,
            detail=f"change_set_id '{change_set_id}' not found",
        )

    cs.override_reason = body.reason

    # Find every solver_decision whose run_label intersects with the affected
    # tasks' run_labels. We only set the FK when the reason is non-null —
    # clearing the reason also clears the override link so the Decision Card
    # stops showing "manually adjusted".
    decisions_updated = 0
    if body.reason is not None:
        task_pks = _affected_task_ids(cs)
        labels = _run_labels_for_tasks(db, task_pks)
        if labels:
            run_ids = {
                rid
                for (rid,) in db.query(SolverRun.run_id)
                .filter(SolverRun.run_label.in_(labels))
                .all()
            }
            if run_ids:
                rows = (
                    db.query(SolverDecision)
                    .filter(SolverDecision.run_id.in_(run_ids))
                    .all()
                )
                for d in rows:
                    d.manual_override_change_set_id = change_set_id
                decisions_updated = len(rows)
    else:
        # Clear path: drop any existing back-links that point at THIS
        # change_set so the Decision Card no longer flags the run.
        rows = (
            db.query(SolverDecision)
            .filter(SolverDecision.manual_override_change_set_id == change_set_id)
            .all()
        )
        for d in rows:
            d.manual_override_change_set_id = None
        decisions_updated = len(rows)

    db.commit()

    return {
        "updated_change_set": change_set_id,
        "decisions_updated": decisions_updated,
    }


@router.get("/missing-reasons")
def missing_reasons(
    since: date | None = Query(
        default=None,
        description="ISO date (YYYY-MM-DD). Defaults to today (KST).",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Count change sets created on/after ``since`` with no override_reason.

    Feeds the Gantt header "사유 미기록 N건" badge — operators use this to
    catch up on attribution at the end of the day.

    ``since`` parsed by FastAPI as ``date`` (YYYY-MM-DD). We convert to a
    naive datetime at midnight to match the DB column type (DateTime,
    no tz). Default is today (KST) midnight.
    """
    if since is None:
        threshold = _today_kst_iso_naive()
    else:
        threshold = datetime.combine(since, time.min)

    count = (
        db.query(ScheduleChangeSet)
        .filter(
            ScheduleChangeSet.override_reason.is_(None),
            ScheduleChangeSet.created_at >= threshold,
        )
        .count()
    )

    return {"count": count, "since": threshold.isoformat()}
