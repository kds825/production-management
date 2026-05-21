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

from datetime import date, datetime, time, timedelta, timezone
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


class BulkReasonUpdate(BaseModel):
    """Single row in a bulk-reason payload."""

    change_set_id: str
    reason: str | None


class BulkReasonPatch(BaseModel):
    """Body for PATCH /api/change-sets/bulk-reason.

    Operators batch-attribute reasons via the missing-reasons modal. Each
    row succeeds or fails independently; the route returns a partial
    success envelope so the UI can surface per-row errors instead of
    aborting the whole request.
    """

    updates: list[BulkReasonUpdate]


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
    """Midnight (KST) of today, expressed as a naive UTC datetime.

    Why naive: ScheduleChangeSet.created_at is ``DateTime`` (no tz) and
    server-side default uses ``datetime.utcnow``. To keep the predicate
    consistent we return the UTC instant of "today 00:00 KST".
    KST = UTC+9 → today's KST midnight = yesterday 15:00 UTC.

    Note: the previous implementation returned ``datetime.now(UTC).date()``
    midnight, which is UTC midnight (= 09:00 KST). That made the predicate
    silently skip the 00:00–09:00 KST window each morning and include the
    prior day's 15:00–24:00 KST window. Fixed by computing the KST date
    explicitly and converting back to a naive UTC datetime.
    """
    now_utc = datetime.now(timezone.utc)
    kst_today = (now_utc + timedelta(hours=9)).date()
    kst_midnight_utc = datetime.combine(kst_today, time.min) - timedelta(hours=9)
    return kst_midnight_utc


def _apply_reason_to_change_set(
    db: Session, cs: ScheduleChangeSet, reason: str | None
) -> int:
    """Set ``cs.override_reason`` and propagate to ``solver_decision``.

    Returns the number of solver_decision rows touched. The caller is
    responsible for (1) allow-list validation on ``reason``, (2) 404
    handling if ``cs`` is missing, and (3) ``db.commit()``. Splitting
    these concerns lets the bulk endpoint apply many rows under a single
    transaction without duplicating logic.

    Propagation rules:
      - reason is non-null → every solver_decision whose run shares a
        run_label with this change_set's affected tasks gets its
        ``manual_override_change_set_id`` pointed at this change_set so
        the Decision Card surfaces "manually adjusted".
      - reason is None (clear path) → drop any existing back-links that
        point AT THIS change_set; the Decision Card stops flagging the run.
    """
    cs.override_reason = reason

    if reason is not None:
        task_pks = _affected_task_ids(cs)
        labels = _run_labels_for_tasks(db, task_pks)
        if not labels:
            return 0
        run_ids = {
            rid
            for (rid,) in db.query(SolverRun.run_id)
            .filter(SolverRun.run_label.in_(labels))
            .all()
        }
        if not run_ids:
            return 0
        rows = db.query(SolverDecision).filter(SolverDecision.run_id.in_(run_ids)).all()
        for d in rows:
            d.manual_override_change_set_id = cs.change_set_id
        return len(rows)

    rows = (
        db.query(SolverDecision)
        .filter(SolverDecision.manual_override_change_set_id == cs.change_set_id)
        .all()
    )
    for d in rows:
        d.manual_override_change_set_id = None
    return len(rows)


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

    decisions_updated = _apply_reason_to_change_set(db, cs, body.reason)
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

    Fixture guard: badge must only count *real* operator drag-drops. A
    legitimate bulk-update always carries ``preview_request_id`` (the
    cascade-preview correlation id), so rows without one are either test
    fixtures or hand-inserted data and are excluded. ``applied_by`` is
    also checked to keep the obvious ``"test-user"`` seed out, since some
    tests bypass the endpoint entirely.
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
            ScheduleChangeSet.preview_request_id.isnot(None),
            ScheduleChangeSet.applied_by.is_distinct_from("test-user"),
        )
        .count()
    )

    return {"count": count, "since": threshold.isoformat()}


def _missing_reasons_filter(threshold: datetime) -> Any:
    """Shared WHERE for the count and list endpoints.

    Centralised so the badge count and the modal's list view stay in
    lockstep — a drift between the two would let operators see rows they
    can't count, or worse, fix rows that vanish from the count after
    refresh.
    """
    return (
        ScheduleChangeSet.override_reason.is_(None),
        ScheduleChangeSet.created_at >= threshold,
        ScheduleChangeSet.preview_request_id.isnot(None),
        ScheduleChangeSet.applied_by.is_distinct_from("test-user"),
    )


@router.get("/missing-reasons/list")
def missing_reasons_list(
    since: date | None = Query(
        default=None,
        description="ISO date (YYYY-MM-DD). Defaults to today (KST).",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List (not just count) un-attributed change sets, with task meta.

    Feeds the missing-reasons modal — the operator clicks the badge,
    sees one row per change_set with affected task info, and bulk-applies
    or per-row-overrides a reason.

    Returned task ``batch_group`` / ``equipment_code`` come from the
    *current* ScheduleTask row (not the snapshot) — operators identify a
    Gantt block by its batch_group, and the snapshot only holds raw
    start/end. We tolerate missing tasks (task may have been deleted
    after the change_set was recorded) by returning nulls for the meta.
    """
    if since is None:
        threshold = _today_kst_iso_naive()
    else:
        threshold = datetime.combine(since, time.min)

    rows = (
        db.query(ScheduleChangeSet)
        .filter(*_missing_reasons_filter(threshold))
        .order_by(ScheduleChangeSet.created_at.asc())
        .all()
    )

    # Single batched lookup for every task referenced across all rows —
    # the modal can render dozens of change_sets, and a per-row query
    # would hit the DB N+1 times.
    all_task_pks: set[Any] = set()
    for r in rows:
        all_task_pks |= _affected_task_ids(r)
    task_meta: dict[Any, dict[str, Any]] = {}
    if all_task_pks:
        for tid, bg, eq in (
            db.query(
                ScheduleTask.task_id,
                ScheduleTask.batch_group,
                ScheduleTask.equipment_code,
            )
            .filter(ScheduleTask.task_id.in_(all_task_pks))
            .all()
        ):
            task_meta[tid] = {"batch_group": bg, "equipment_code": eq}

    items: list[dict[str, Any]] = []
    for r in rows:
        before = r.snapshot_before or {}
        after = r.snapshot_after or {}
        task_keys = sorted(set(before.keys()) | set(after.keys()))
        tasks: list[dict[str, Any]] = []
        for k in task_keys:
            pk = _coerce_task_pk(k) if isinstance(k, str) else k
            meta = task_meta.get(pk, {})
            tasks.append(
                {
                    "task_id": k,
                    "batch_group": meta.get("batch_group"),
                    "equipment_code": meta.get("equipment_code"),
                    "before": before.get(k),
                    "after": after.get(k),
                }
            )
        items.append(
            {
                "change_set_id": r.change_set_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "tasks": tasks,
            }
        )

    return {
        "items": items,
        "since": threshold.isoformat(),
        "allowed_reasons": list(ALLOWED_REASONS),
    }


@router.patch("/bulk-reason")
def patch_bulk_reason(
    body: BulkReasonPatch,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Apply ``override_reason`` to many change_sets in one transaction.

    Partial-success contract: each row is validated independently and a
    failure (404 / invalid reason) is reported in ``errors`` without
    aborting the others. All successful rows commit together so the
    modal either sees them all reflected or none (operator can retry
    failures from the errors list).

    ``errors[]`` item shape: ``{change_set_id, error_code, detail}``.
    ``error_code`` is one of ``"invalid_reason" | "not_found"``.
    """
    updated: list[str] = []
    errors: list[dict[str, Any]] = []
    total_decisions = 0

    for u in body.updates:
        if u.reason is not None and u.reason not in ALLOWED_REASONS:
            errors.append(
                {
                    "change_set_id": u.change_set_id,
                    "error_code": "invalid_reason",
                    "detail": f"reason '{u.reason}' not in allow-list",
                }
            )
            continue
        cs = db.get(ScheduleChangeSet, u.change_set_id)
        if cs is None:
            errors.append(
                {
                    "change_set_id": u.change_set_id,
                    "error_code": "not_found",
                    "detail": f"change_set_id '{u.change_set_id}' not found",
                }
            )
            continue
        total_decisions += _apply_reason_to_change_set(db, cs, u.reason)
        updated.append(u.change_set_id)

    db.commit()

    return {
        "updated_change_set_ids": updated,
        "decisions_updated": total_decisions,
        "errors": errors,
    }
