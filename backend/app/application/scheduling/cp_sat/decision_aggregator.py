"""Aggregate per-constraint penalty/applied values for solver_decision.

Closes the Week 4 Task 2A.4 gap where ``cp_sat_optimizer.write_trace``
was called with ``penalty_values={}`` because ``BuiltModel`` did not
expose per-constraint IntVar maps.

Strategy (pragmatic harness):

* Constraints with explicit IntVar accumulators on ``BuiltModel`` get a
  real numeric penalty value:
    - ``1-1`` 납기/EDD  ← ``tardiness_vars`` + ``edd_pair_terms`` +
      ``edd_mixed_pastdue_terms`` + ``slack_terms_meta``.
    - ``4-1`` setup     ← ``transition_terms`` (연선 SQ change count).
* Every other ConstraintConfig row that is ``is_enabled=True`` gets a
  row with ``hard_literal_value=True`` (table-param / structural rules
  that are enforced upstream and don't expose a soft IntVar).
* ``W-*`` weight rows are skipped — they're objective coefficients, not
  user-facing constraints.
* When solver did NOT find a solution we still emit applied=is_enabled
  rows so the UI can explain "constraint X was active even though the
  solver fell back".

The aggregator is intentionally tolerant: any missing attribute or
solver-extraction failure is silently downgraded to ``None``. Trace is
observability — never fail the solve over it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ortools.sat.python import cp_model
    from sqlalchemy.orm import Session

    from app.application.scheduling.cp_sat.model_builder import BuiltModel


def _safe_value(solver: "cp_model.CpSolver", var) -> int | None:
    """``solver.value(var)`` with a None fallback when the var is unset."""
    try:
        return int(solver.value(var))
    except Exception:
        return None


def _aggregate_intvar_penalties(
    solver: "cp_model.CpSolver",
    built: "BuiltModel",
) -> dict[str, float]:
    """Sum the IntVar values that map back to user-facing constraint IDs.

    ``1-1`` is reported as **total tardy minutes** — a single number the
    operator can read directly. EDD pair penalties are *not* added here;
    those are reported as a separate count in ``details_json`` upstream
    (out of scope for this aggregator). Same with slack.

    ``4-1`` is reported as **count of 연선 SQ-change adjacencies** —
    again, a single integer the operator can read.
    """
    pv: dict[str, float] = {}

    if built.tardiness_vars:
        tardy_total = sum(
            _safe_value(solver, v) or 0 for v in built.tardiness_vars.values()
        )
        pv["1-1"] = float(tardy_total)

    if built.transition_terms:
        trans_total = sum(_safe_value(solver, v) or 0 for v in built.transition_terms)
        pv["4-1"] = float(trans_total)

    return pv


def build_decision_inputs(
    *,
    solver: "cp_model.CpSolver | None",
    built: "BuiltModel | None",
    solver_status_ok: bool,
    db: "Session",
) -> tuple[dict[str, float | None], dict[str, bool | None]]:
    """Return ``(penalty_values, hard_literal_values)`` ready for write_trace.

    Why ``solver_status_ok``: when CP-SAT returned UNKNOWN/INFEASIBLE the
    Var values are undefined → calling ``solver.Value`` raises. We still
    emit the applied=is_enabled rows so the UI can show "configured but
    not solved", but skip the penalty extraction.

    Why we look up ConstraintConfig here: the trace must reflect the
    *actual* run-time config the solver loaded, not a baked-in list. If
    the operator disabled 4-2 between two runs, the diff should be
    visible in solver_decision rows.
    """
    from app.infrastructure.models.constraint_config import ConstraintConfig

    penalty_values: dict[str, float | None] = {}
    hard_literal_values: dict[str, bool | None] = {}

    if solver_status_ok and solver is not None and built is not None:
        penalty_values.update(_aggregate_intvar_penalties(solver, built))

    rows = db.query(ConstraintConfig).all()
    for row in rows:
        cid = row.constraint_id
        # W-* are objective weights, not user-facing constraints.
        if cid.startswith("W-"):
            continue
        if cid not in penalty_values:
            penalty_values[cid] = None
        # ``applied`` heuristic in trace_writer treats hard_literal=True as
        # applied. is_enabled=False → applied=False, which is exactly what
        # we want disabled rules to report.
        hard_literal_values[cid] = bool(row.is_enabled)

    return penalty_values, hard_literal_values
