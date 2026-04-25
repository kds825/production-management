"""Solver snapshot writer — diagnostic JSON dump of solver I/O + objective.

Extracted from ``cp_sat_optimizer._write_solver_snapshot`` (Week 9 SRP cleanup).

Why a separate module: the snapshot writer reads only ``solver.value(var)``
and writes JSON to disk. It has no business logic and no side effects on
the database. Keeping it inline in ``cp_sat_optimizer.py`` made that file
2000+ lines for an observability concern that is conceptually I/O.

Why a ``SnapshotWeights`` dataclass instead of importing constants:
``cp_sat_optimizer.py`` keeps the canonical scaling weights as
module-level constants for the objective function. Passing them in via
a small bundle keeps this module standalone (no upstream imports) so
the snapshot format remains traceable to the exact weights used at the
time of the run — including future weight migrations where
``cp_sat_optimizer`` and the snapshot reader may temporarily disagree.

Why ``except Exception: pass``: snapshot is observability — a write
failure must never block the solver result. Logging is fine but raising
would cost the user a successful schedule. Same contract as the
inlined original.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SnapshotWeights:
    """Scaling weights captured into the snapshot.

    Constants are duplicated here (not imported) so the snapshot file
    records the exact values the solver used at run time. Future
    refactors that move the constants around won't silently change
    historic snapshot interpretation.
    """

    tardiness_normal: int
    past_severity_k: int
    slack_base: int
    edd_pair: int
    edd_mixed_pastdue: int
    transition: int
    chain: int
    idle: int


def _resolve_output_dir() -> str:
    """Resolve ``04_output/solver_snapshots`` (env override allowed).

    The path is relative to ``backend/`` (solver/ → services/ → app/ →
    backend/ → repo root). ``SOLVER_SNAPSHOT_DIR`` overrides for tests
    and CI.
    """
    override = os.environ.get("SOLVER_SNAPSHOT_DIR")
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(
        os.path.join(here, "..", "..", "..", "..", "04_output", "solver_snapshots")
    )


def write_snapshot(
    *,
    run_label: str,
    base_date: datetime | None,
    group_meta: dict,
    frozen_group_keys: set[str] | None,
    solver: Any,
    solver_status_name: str,
    start_vars: dict,
    end_vars: dict,
    equip_vars: dict,
    tardiness_vars: dict,
    edd_pair_vars: list,
    slack_terms_meta: list,
    weights: SnapshotWeights,
    work_min_per_day: int,
    max_horizon_min: int,
    idle_terms: list | None = None,
    transition_terms: list | None = None,
    sheath_end_terms: list | None = None,
    edd_mixed_pastdue_vars: list | None = None,
) -> None:
    """Persist solver I/O + per-term objective breakdown for diagnosis.

    Output path: ``$SOLVER_SNAPSHOT_DIR | <repo>/04_output/solver_snapshots/{run_label}.json``.

    Why include ``solver.value(var)`` for every group: post-mortem
    analysis ("why did EDD pair X fire?") needs to see solver-resolved
    start/end/equipment per group, not just the model definition.

    Why ``slack_terms_meta`` carries ``(gk, w, end_var)``: the
    contribution sum needs the concrete weight per group; the model's
    aggregate slack term doesn't preserve per-group attribution.
    """
    try:
        out_dir = _resolve_output_dir()
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{run_label}.json")

        frozen_set = set(frozen_group_keys or [])

        groups_section: list[dict] = []
        tardiness_contrib_total = 0
        for gk, meta in group_meta.items():
            rep = meta["rep"]
            s_val = solver.value(start_vars[gk]) if gk in start_vars else None
            e_val = solver.value(end_vars[gk]) if gk in end_vars else None
            eq_chosen: str | None = None
            for ec, bool_var in equip_vars.get(gk, {}).items():
                if solver.value(bool_var) == 1:
                    eq_chosen = ec
                    break
            tard_val = (
                solver.value(tardiness_vars[gk]) if gk in tardiness_vars else None
            )
            tard_contrib = (
                int(meta["weight"]) * int(tard_val) if tard_val is not None else 0
            )
            tardiness_contrib_total += tard_contrib

            _due_wmin_v = int(meta["due_wmin"])
            if _due_wmin_v < 0 and _due_wmin_v != -max_horizon_min * 2:
                _past_days = round(max(0, (-_due_wmin_v) / work_min_per_day), 2)
            else:
                _past_days = 0.0

            groups_section.append(
                {
                    "batch_group": gk,
                    "process_name": rep.process_name,
                    "sq_mm2": float(rep.sq_mm2 or 0),
                    "voltage": rep.voltage,
                    "due_date": (
                        meta["earliest_due"].isoformat()
                        if meta.get("earliest_due")
                        else None
                    ),
                    "due_wmin": _due_wmin_v,
                    "past_days": _past_days,
                    "weight": int(meta["weight"]),
                    "cpsat_dur": int(meta["cpsat_dur"]),
                    "eligible_equipment": [e.equipment_code for e in meta["eligible"]],
                    "is_frozen": gk in frozen_set,
                    "n_batches": len(meta["batches"]),
                    "order_ids": sorted(
                        {b.sales_order_id for b in meta["batches"] if b.sales_order_id}
                    ),
                    "solver_start_wmin": s_val,
                    "solver_end_wmin": e_val,
                    "solver_equipment": eq_chosen,
                    "solver_tardiness_wmin": tard_val,
                    "solver_tardiness_contribution": tard_contrib,
                }
            )

        edd_wrong_count = (
            sum(int(solver.value(v)) for v in edd_pair_vars) if edd_pair_vars else 0
        )
        edd_contribution = int(edd_wrong_count) * weights.edd_pair
        edd_mixed_wrong_count = (
            sum(int(solver.value(v)) for v in edd_mixed_pastdue_vars)
            if edd_mixed_pastdue_vars
            else 0
        )
        edd_mixed_contribution = int(edd_mixed_wrong_count) * weights.edd_mixed_pastdue

        slack_contribution_total = 0
        for _gk_s, _w_s, _end_var_s in slack_terms_meta:
            slack_contribution_total += int(_w_s) * int(solver.value(_end_var_s))

        idle_sum = sum(int(solver.value(v)) for v in idle_terms) if idle_terms else 0
        idle_contribution = idle_sum * weights.idle
        transition_sum = (
            sum(int(solver.value(v)) for v in transition_terms)
            if transition_terms
            else 0
        )
        transition_contribution = transition_sum * weights.transition
        sheath_end_sum = (
            sum(int(solver.value(v)) for v in sheath_end_terms)
            if sheath_end_terms
            else 0
        )

        snapshot = {
            "run_label": run_label,
            "base_date": base_date.isoformat() if base_date else None,
            "solver_status": solver_status_name,
            "objective_value": (
                int(solver.objective_value)
                if solver_status_name in ("OPTIMAL", "FEASIBLE")
                else None
            ),
            "frozen_group_keys": sorted(frozen_set),
            "constants": {
                "TARDINESS_WEIGHT_normal": weights.tardiness_normal,
                "PAST_SEVERITY_K": weights.past_severity_k,
                "SLACK_WEIGHT_BASE": weights.slack_base,
                "EDD_PAIR_WEIGHT": weights.edd_pair,
                "EDD_MIXED_PASTDUE_WEIGHT": weights.edd_mixed_pastdue,
                "TRANSITION_WEIGHT": weights.transition,
                "CHAIN_WEIGHT": weights.chain,
                "IDLE_WEIGHT": weights.idle,
            },
            "breakdown": {
                "tardiness_contribution_total": tardiness_contrib_total,
                "edd_pair_wrong_count": int(edd_wrong_count),
                "edd_pair_contribution": edd_contribution,
                "edd_mixed_pastdue_wrong_count": int(edd_mixed_wrong_count),
                "edd_mixed_pastdue_contribution": edd_mixed_contribution,
                "slack_contribution_total": slack_contribution_total,
                "idle_sum_wmin": idle_sum,
                "idle_contribution": idle_contribution,
                "transition_sum_pairs": transition_sum,
                "transition_contribution": transition_contribution,
                "sheath_end_sum_wmin": sheath_end_sum,
            },
            "groups": groups_section,
        }

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001 — observability must not block solver
        pass
