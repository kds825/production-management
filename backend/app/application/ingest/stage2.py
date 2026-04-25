"""Pipeline Stage 2 — greedy fallback / explicit greedy branch (Week 4 Task 4A.1).

In the scheduling pipeline, Stage 2 = the greedy auto_schedule path. It is
invoked in two situations:

1. The user explicitly requested ``optimizer="greedy"`` (skip CP-SAT).
2. As an internal fallback when CP-SAT cannot place every batch — that
   fallback is performed inside ``auto_schedule(use_cpsat=True)`` itself
   (Fix P0-4A unified retry+validate wrapper). For "engine" labelling the
   solver branch (``stage1.py``) handles the relabeling on
   ``solver_status not in {OPTIMAL, FEASIBLE}``.

This module covers case (1) — direct greedy invocation when the caller
opts out of the solver. Keeping it in its own file matches spec §7 and
makes future tuning of the greedy-only path (e.g. seed schedules for
unit tests) localised.

Why DI of ``auto_schedule_fn``: same reason as ``stage1.py`` — the route
exposes ``auto_schedule`` as a module attribute that
``test_schedule_route_overlap`` monkeypatches. The route's thin
``_execute_stage2_core`` shim passes the (potentially patched) callable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session


def run_greedy_stage(
    run_label: str,
    db: Session,
    base_date: datetime | None,
    *,
    auto_schedule_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Run the greedy-only branch and tag the engine label.

    Returns the schedule_result dict from ``auto_schedule`` augmented with
    ``engine="greedy"``. SchedulerOverlapError is *not* caught here — it
    is a business signal mapped by the caller (route → 200 + overlap_alert
    or async job → status=overlap_alert).
    """
    schedule_result = auto_schedule_fn(run_label, db, base_date=base_date)
    schedule_result["engine"] = "greedy"
    return schedule_result
