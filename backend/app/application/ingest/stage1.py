"""Pipeline Stage 1 — CP-SAT solver branch (Week 4 Task 4A.1).

Naming context: in this codebase the *HTTP* endpoints are also called
"stage1 / stage2" (ERP→batch vs auto-schedule), but inside the scheduling
pipeline the user-facing "Stage 2 / auto-schedule" call is internally
two stages:

* Stage 1 (this module) — CP-SAT optimal solver attempt
* Stage 2 (sibling module ``stage2.py``) — greedy fallback for whatever
  the solver could not place (or when ``optimizer="greedy"`` is requested)

Splitting these per spec §7 makes the engine selection rule explicit and
gives parity / observability tests a single place to instrument.

Why DI of ``auto_schedule_fn``: the route module exposes ``auto_schedule``
as a module-level attribute that test fixtures monkeypatch
(``test_schedule_route_overlap``). To preserve that patching contract
without coupling this service to the route, we accept the entry point
as a callable parameter — the route's thin ``_execute_stage2_core``
wrapper passes the (potentially patched) reference at call time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session


def run_solver_stage(
    run_label: str,
    db: Session,
    base_date: datetime | None,
    *,
    auto_schedule_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Run the CP-SAT solver branch and tag the engine label.

    Returns the schedule_result dict from ``auto_schedule`` augmented with
    an ``engine`` field used by the response contract:

    * ``"cpsat"``           — solver returned OPTIMAL or FEASIBLE
    * ``"greedy_fallback"`` — solver gave up; ``auto_schedule`` already
      switched to greedy internally (Fix P0-4A unified retry+validate
      wrapper). The label here surfaces that fallback to the API caller.

    Why we don't catch SchedulerOverlapError: it's a business signal
    ("retries exhausted, persistent overlap"), not a server fault. The
    orchestrator (or route) catches it and maps to HTTP 200 +
    overlap_alert=True. Do not swallow it here.
    """
    schedule_result = auto_schedule_fn(
        run_label, db, use_cpsat=True, base_date=base_date
    )
    if schedule_result.get("solver_status") in ("OPTIMAL", "FEASIBLE"):
        schedule_result["engine"] = "cpsat"
    else:
        # auto_schedule already fell back to greedy internally; we just label
        # the response so the front-end shows "fallback" badge instead of
        # claiming CP-SAT solved it.
        schedule_result["engine"] = "greedy_fallback"
    return schedule_result
