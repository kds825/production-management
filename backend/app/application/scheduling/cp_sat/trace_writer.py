"""Trace writer — persist `solver_run` + `solver_decision` rows per solve.

Task 2A.3 (Production Handoff Refactor, Rev 3, Week 2).

Why this module:
  - Every `cp_sat_schedule` invocation should leave a queryable trail:
    which run_label, which input (input_hash), which output (output_hash),
    which constraint params, which solver status + objective. Week 4
    narrator + Week 5 admin dashboard both consume these rows.
  - Keeping the writer as a focused module (not inlined into
    `cp_sat_optimizer.py`) means parity tests can mock/assert on it
    without touching the solver's 2000-line core.

Boundary note (spec §7):
  services/solver/* is supposed to stay pure (no direct SQLAlchemy).
  `trace_writer.py` is an *intentional* boundary crossing alongside
  `constraint_loader.py` and `input_builder.py` — it writes to DB by
  design. Task 2A.5's CI allow-list must include these three files.

Durability discipline:
  `write_trace` issues `db.add()` + `db.flush()` only. It NEVER calls
  `db.commit()`. The caller (web request, parity harness, batch job)
  decides when to commit. Parity tests run inside `db.begin_nested()`
  (SAVEPOINT), so trace rows roll back cleanly at teardown.

Hash algorithm parity:
  `compute_output_hash` MUST be byte-identical to
  `backend.tests._parity_helpers._stable_hash`. If they drift, the
  admin dashboard's output_hash would disagree with the parity
  fixture's expected_output_hash — users would see false "schedule
  changed!" alarms. The drift guard is `test_compute_output_hash_
  matches_parity_stable_hash` in test_trace_writer.py.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.models.solver_decision import SolverDecision
from app.infrastructure.models.solver_run import SolverRun
from app.application.scheduling.cp_sat.constraint_loader import ConstraintSpec


# ────────────────────────────────────────────────────────────────────────
# TraceMetadata — value-object carrying everything solver_run needs
# ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TraceMetadata:
    """Immutable per-solve metadata.

    Frozen because a single `cp_sat_schedule` call produces one metadata
    bundle and should not be mutated between construction and write —
    mutation would risk the DB row disagreeing with the in-memory copy
    the narrator eventually reads.

    Fields align 1:1 with `SolverRun` columns (migration f6a0935366eb).
    """

    run_label: str
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    solver_status: str  # "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN" | ...
    objective_value: float | None
    input_hash: str  # "sha256:" + 64 hex
    output_hash: str | None  # "sha256:" + 64 hex, None when solver bailed early
    constraint_config_version: str | None  # UUID of snapshot; None pre-versioning
    solver_params: dict[str, Any] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────
# Hash helpers
# ────────────────────────────────────────────────────────────────────────


def compute_output_hash(
    run_label: str,
    assignments: list[dict[str, Any]],
    horizon_start: datetime,
) -> str:
    """Canonical hash of a scheduling result (design spec §6).

    MUST stay byte-identical to `tests/_parity_helpers._stable_hash`.
    The invariant is enforced by
    `test_compute_output_hash_matches_parity_stable_hash`. If you change
    the payload shape here, update `_stable_hash` in the same commit
    (or the test will fail and CI will block the merge).

    Algorithm:
      1. Sort the triple (group_key, equipment_id, start_minute) across
         all assignments. `start_minute` = integer minutes from
         horizon_start (avoids tz drift, preserves 1-minute resolution;
         D9-A strict: no tolerance).
      2. SHA-256 the `json.dumps([run_label, items], sort_keys=True)`
         encoding.
      3. Prefix with `"sha256:"` for schema-level format discrimination.

    `group_key` falls back to `production_batch_id` because some call
    sites populate one and not the other (see _parity_helpers line 122
    for the parallel fallback).
    """
    items = sorted(
        (
            a.get("group_key", a.get("production_batch_id", "")),
            a["equipment_id"],
            int((a["assigned_start"] - horizon_start).total_seconds() // 60),
        )
        for a in assignments
    )
    payload = json.dumps([run_label, items], sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def compute_input_hash(solver_input: Any, run_label: str) -> str:
    """Best-guess deterministic hash of a `SolverInput`.

    Week 2 scope: we only need *some* deterministic hash so downstream
    consumers can detect "same input was replayed". A full serialization
    of every ProductionBatch row is out-of-scope here — Week 4 upgrades
    this to the Task 1.3 capture-script's PK-normalized JSON encoding.

    Current payload: `[run_label, num_batches, sorted_batch_group_keys]`.
    - run_label: partitions by weekly plan — different weeks never collide.
    - num_batches: rough cardinality guard.
    - sorted_batch_group_keys: catches most "contents changed" mutations
      without requiring per-column normalization.

    Determinism: same `SolverInput` (same batches in any order) → same
    hash. sorted() ensures order-independence. `_single_{batch_id}`
    fallback matches the `cp_sat_schedule` grouping convention.
    """
    batches = list(getattr(solver_input, "batches", []) or [])
    group_keys = sorted((b.batch_group or f"_single_{b.batch_id}") for b in batches)
    payload = json.dumps(
        [run_label, len(batches), group_keys],
        sort_keys=True,
        default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ────────────────────────────────────────────────────────────────────────
# write_trace — the main entry point
# ────────────────────────────────────────────────────────────────────────


def _applied_heuristic(
    penalty_value: float | None,
    hard_literal_value: bool | None,
) -> bool:
    """Best-guess `applied` flag for Week 2.

    A constraint counts as "applied" if it contributed a non-zero soft
    penalty OR its hard-literal was True. Both None / 0.0 / False →
    False (not enforced or already trivially satisfied).

    This is a heuristic for now — Week 4 refines with structured
    `ConstraintSpec.implementation_type` semantics (`table_param`
    constraints are always "applied" in the params_json sense, etc.).
    """
    if hard_literal_value is True:
        return True
    if penalty_value is not None and penalty_value != 0.0:
        return True
    return False


def write_trace(
    db: Session,
    meta: TraceMetadata,
    *,
    penalty_values: dict[str, float | None],
    hard_literal_values: dict[str, bool | None],
    specs: list[ConstraintSpec],  # noqa: ARG001 — reserved for Week 4 narrator wiring
    assignments: list[dict[str, Any]],  # noqa: ARG001 — reserved for per-decision details
) -> str:
    """Insert one `solver_run` row + one `solver_decision` row per constraint.

    Parameters
    ----------
    db
        Active SQLAlchemy session. Caller owns the transaction.
    meta
        Frozen metadata bundle. `meta.run_id` is the PK for the run row
        AND the returned value (so callers can annotate their own
        `result` dict without a second query).
    penalty_values
        constraint_id → penalty_value (or None). Empty dict is valid;
        Week 2 BuiltModel populates this empty — real per-constraint
        penalty capture arrives in Week 4.
    hard_literal_values
        constraint_id → hard_literal_value (or None). Empty dict valid.
    specs
        Currently unused (Week 2). Reserved for Week 4 narrator, which
        will populate `details_json` with structured "why" fields
        derived from `ConstraintSpec`.
    assignments
        Currently unused (Week 2). Reserved for future per-decision
        assignment-breakdown populated in `details_json`.

    Returns
    -------
    run_id : str
        Same value as `meta.run_id`, for caller convenience.

    Behavior
    --------
    1. Build + flush the `SolverRun` row first (so child FK resolves).
    2. Iterate over the union of constraint_ids in the two value dicts
       (keys may differ if a constraint is purely soft or purely hard;
       union preserves observability for both halves).
    3. Flush all SolverDecision rows in one batch.
    4. NO commit — caller decides durability. Parity tests run inside
       SAVEPOINT so this flush auto-rolls-back at fixture teardown.
    """
    run = SolverRun(
        run_id=meta.run_id,
        run_label=meta.run_label,
        started_at=meta.started_at,
        finished_at=meta.finished_at,
        solver_status=meta.solver_status,
        objective_value=meta.objective_value,
        input_hash=meta.input_hash,
        output_hash=meta.output_hash,
        constraint_config_version=meta.constraint_config_version,
        solver_params=dict(meta.solver_params),
    )
    db.add(run)
    db.flush()

    constraint_ids = set(penalty_values.keys()) | set(hard_literal_values.keys())
    for cid in sorted(constraint_ids):
        pv = penalty_values.get(cid)
        hv = hard_literal_values.get(cid)
        decision = SolverDecision(
            run_id=meta.run_id,
            constraint_id=cid,
            penalty_value=pv,
            hard_literal_value=hv,
            applied=_applied_heuristic(pv, hv),
            # Week 4 narrator populates this with structured detail.
            details_json={},
        )
        db.add(decision)
    if constraint_ids:
        db.flush()

    return meta.run_id
