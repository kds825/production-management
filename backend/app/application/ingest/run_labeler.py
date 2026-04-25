"""run_label generation + base_date / date-range parsing helpers.

Why isolated: every Stage 1 / Stage 2 entry point needs a fresh run_label
(timestamp-based identifier that ties together SalesOrder + ProductionBatch +
ScheduleTask + AuditLog rows for one planning execution) and a uniform way
to parse user-supplied YYYYMMDD date strings. Centralising here keeps the
"What is a run?" contract in one file so future ID schemes (e.g. ULID,
hash-suffix) can be swapped without hunting through routes.

Format contract:
* run_label: ``YYYYMMDD_HHMMSS`` local-time timestamp. Stable as a primary
  key fragment because it sorts lexicographically by creation time and is
  human-readable in audit logs. Any change here cascades into ``list_runs``
  ordering and fixture filenames in tests/parity — coordinate carefully.
* base_date input: ``YYYYMMDD`` (no separators) per the existing form fields
  in ``/pipeline/stage1`` and ``/pipeline/stage2``. We anchor base_date at
  08:00 local for Stage 2 to align with shift-start convention used by
  ``schedule_optimizer.auto_schedule``.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import HTTPException


def new_run_label(now: datetime | None = None) -> str:
    """Allocate a fresh run_label.

    ``now`` is injectable so tests/parity harnesses can pin time without
    monkeypatching ``datetime``. Production callers omit it.
    """
    return (now or datetime.now()).strftime("%Y%m%d_%H%M%S")


def parse_date_yyyymmdd(value: str | None, *, field_name: str) -> date | None:
    """Parse a ``YYYYMMDD`` string into a ``date``.

    Returns None for falsy input. Raises ``HTTPException(400)`` on bad
    format with the field name embedded so the API consumer knows which
    parameter is wrong.

    Why HTTPException here (vs ValueError): Stage 1 endpoints already raise
    HTTPException(400) directly inline today; centralising the message
    ("date_from 형식 오류: ... (YYYYMMDD)") preserves the wire contract
    seen by the frontend without forcing every call site to repeat the
    try/except block.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 형식 오류: {value} (YYYYMMDD)",
        ) from exc


def parse_base_date_yyyymmdd(value: str | None) -> datetime | None:
    """Parse ``base_date`` (YYYYMMDD) anchored at 08:00 local for Stage 2.

    Why 08:00: matches shift-start used by ``schedule_optimizer.auto_schedule``
    when slotting equipment availability windows. Changing this offset would
    silently shift every Stage 2 plan that supplies base_date — keep aligned
    with the solver convention.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").replace(hour=8, minute=0)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"base_date 형식 오류: {value} (YYYYMMDD)",
        ) from exc
