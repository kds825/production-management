"""Tests for `app.application.scheduling.cp_sat.trace_writer` (Task 2A.3).

Scope (per plan spec §Task 2A.3):
  1. Drift guard: `compute_output_hash` == `_parity_helpers._stable_hash`.
     If these drift, the admin dashboard's output_hash disagrees with
     the parity fixtures' expected_output_hash and users see false
     "schedule changed" alarms. This test is the single authoritative
     invariant check.
  2. `compute_input_hash` determinism + run_label sensitivity.
  3. `write_trace` inserts a SolverRun row under SAVEPOINT.
  4. `write_trace` writes 0 decisions on empty dicts; N decisions
     otherwise.
  5. `_applied_heuristic` via the public `write_trace` surface.
  6. `TraceMetadata` immutability (dataclass frozen).

All DB-touching tests wrap work in `db.begin_nested()` so no writes
escape the test transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.models.solver_decision import SolverDecision
from app.infrastructure.models.solver_run import SolverRun
from app.application.scheduling.cp_sat.trace_writer import (
    TraceMetadata,
    compute_input_hash,
    compute_output_hash,
    write_trace,
)
from tests._parity_helpers import _stable_hash


# ────────────────────────────────────────────────────────────────────────
# 1. Hash-algorithm drift guard (the critical invariant)
# ────────────────────────────────────────────────────────────────────────


def test_compute_output_hash_matches_parity_stable_hash() -> None:
    """`compute_output_hash` MUST be byte-identical to `_stable_hash`.

    Both implementations must produce the exact same string for the
    same `(assignments, run_label, horizon_start)` triple. If this
    test fails, one of them changed without the other — see module
    docstring in trace_writer.py for why that's a production-grade
    incident (users would see false "schedule drifted" alarms).
    """
    horizon = datetime(2026, 4, 1, 8, 0, 0, tzinfo=timezone.utc)
    assignments = [
        {
            "group_key": "G1",
            "equipment_id": "EQ-A",
            "assigned_start": datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc),
        },
        {
            "group_key": "G2",
            "equipment_id": "EQ-B",
            "assigned_start": datetime(2026, 4, 1, 10, 30, 0, tzinfo=timezone.utc),
        },
        # Third entry in reverse time order — proves the sort() in both
        # implementations gives the same canonical ordering.
        {
            "group_key": "G0",
            "equipment_id": "EQ-A",
            "assigned_start": datetime(2026, 4, 1, 8, 15, 0, tzinfo=timezone.utc),
        },
    ]
    run_label = "20260401_test"

    via_trace_writer = compute_output_hash(run_label, assignments, horizon)
    via_parity = _stable_hash(assignments, run_label, horizon)

    assert via_trace_writer == via_parity, (
        f"drift between compute_output_hash and _parity_helpers._stable_hash:\n"
        f"  trace_writer: {via_trace_writer}\n"
        f"  _stable_hash: {via_parity}\n"
        f"Fix: align the payload shape in both implementations in the "
        f"same commit. See trace_writer.compute_output_hash docstring."
    )
    # Sanity: prefix format.
    assert via_trace_writer.startswith("sha256:")
    assert len(via_trace_writer) == 7 + 64


# ────────────────────────────────────────────────────────────────────────
# 2. compute_input_hash — determinism + run_label sensitivity
# ────────────────────────────────────────────────────────────────────────


class _FakeBatch:
    """Minimal duck-typed batch. trace_writer.compute_input_hash only
    reads .batch_group and .batch_id, so we avoid dragging ORM overhead
    into a pure-function test."""

    def __init__(self, batch_id: int, batch_group: str | None) -> None:
        self.batch_id = batch_id
        self.batch_group = batch_group


class _FakeInput:
    def __init__(self, batches: list[_FakeBatch]) -> None:
        self.batches = batches


def test_compute_input_hash_is_deterministic() -> None:
    """Same SolverInput (in any order) → same hash."""
    batches_a = [
        _FakeBatch(1, "G1"),
        _FakeBatch(2, None),
        _FakeBatch(3, "G2"),
    ]
    batches_b = [
        _FakeBatch(3, "G2"),
        _FakeBatch(1, "G1"),
        _FakeBatch(2, None),
    ]
    h_a = compute_input_hash(_FakeInput(batches_a), "run-x")
    h_b = compute_input_hash(_FakeInput(batches_b), "run-x")
    assert h_a == h_b
    assert h_a.startswith("sha256:")


def test_compute_input_hash_run_label_matters() -> None:
    """Different run_label → different hash (namespace partition)."""
    batches = [_FakeBatch(1, "G1"), _FakeBatch(2, "G2")]
    h_week_a = compute_input_hash(_FakeInput(batches), "20260101")
    h_week_b = compute_input_hash(_FakeInput(batches), "20260108")
    assert h_week_a != h_week_b


# ────────────────────────────────────────────────────────────────────────
# 3-5. write_trace DB behavior (SAVEPOINT-wrapped)
# ────────────────────────────────────────────────────────────────────────


def _meta(run_id: str, run_label: str = "trace-writer-test") -> TraceMetadata:
    """Factory — every test uses a fresh UUID so rows never collide
    even if a prior test's SAVEPOINT rollback flaked (belt-and-braces)."""
    now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
    return TraceMetadata(
        run_label=run_label,
        run_id=run_id,
        started_at=now,
        finished_at=now,
        solver_status="OPTIMAL",
        objective_value=123.0,
        input_hash="sha256:" + "0" * 64,
        output_hash="sha256:" + "f" * 64,
        constraint_config_version=None,
        solver_params={"num_search_workers": 1, "random_seed": 0},
    )


def test_write_trace_creates_solver_run(db: Session) -> None:
    """Happy-path: one solver_run row per `write_trace` call."""
    nested = db.begin_nested()
    try:
        run_id = str(uuid.uuid4())
        returned = write_trace(
            db,
            _meta(run_id),
            penalty_values={},
            hard_literal_values={},
            specs=[],
            assignments=[],
        )
        assert returned == run_id
        fetched = db.query(SolverRun).filter_by(run_id=run_id).one()
        assert fetched.run_label == "trace-writer-test"
        assert fetched.solver_status == "OPTIMAL"
        assert fetched.objective_value == pytest.approx(123.0)
        assert fetched.solver_params == {
            "num_search_workers": 1,
            "random_seed": 0,
        }
    finally:
        nested.rollback()


def test_write_trace_creates_decisions_for_each_constraint(db: Session) -> None:
    """Empty dicts → 0 decisions. Non-empty → one per constraint_id."""
    nested = db.begin_nested()
    try:
        # Empty case
        rid_empty = str(uuid.uuid4())
        write_trace(
            db,
            _meta(rid_empty),
            penalty_values={},
            hard_literal_values={},
            specs=[],
            assignments=[],
        )
        assert db.query(SolverDecision).filter_by(run_id=rid_empty).count() == 0, (
            "empty penalty/hard dicts should yield zero SolverDecision rows"
        )

        # Non-empty: mix of soft-only, hard-only, and both.
        rid_full = str(uuid.uuid4())
        write_trace(
            db,
            _meta(rid_full),
            penalty_values={"4-1": 5.0, "4-2": 0.0, "4-3": None},
            hard_literal_values={"3-2a": True, "4-2": False},
            specs=[],
            assignments=[],
        )
        decisions = (
            db.query(SolverDecision)
            .filter_by(run_id=rid_full)
            .order_by(SolverDecision.constraint_id)
            .all()
        )
        ids = [d.constraint_id for d in decisions]
        # Union of soft + hard keys, sorted (trace_writer sorts for
        # deterministic insert order).
        assert ids == ["3-2a", "4-1", "4-2", "4-3"]
    finally:
        nested.rollback()


def test_write_trace_applied_heuristic(db: Session) -> None:
    """`applied` is True iff non-zero penalty or hard_literal_value=True.

    constraint_ids are VARCHAR(10) on the DB side (match real "4-1",
    "3-2a" shapes), so the test IDs stay inside that bound.
    """
    nested = db.begin_nested()
    try:
        run_id = str(uuid.uuid4())
        write_trace(
            db,
            _meta(run_id),
            penalty_values={
                "p1": 5.0,  # applied=True (non-zero penalty)
                "p2": 0.0,  # applied=False (zero penalty)
                "p3": None,  # applied=False (no penalty captured)
            },
            hard_literal_values={
                "h1": True,  # applied=True (hard literal satisfied)
                "h2": False,  # applied=False
                "h3": None,  # applied=False
            },
            specs=[],
            assignments=[],
        )
        rows = {
            d.constraint_id: d.applied
            for d in db.query(SolverDecision).filter_by(run_id=run_id).all()
        }
        assert rows["p1"] is True
        assert rows["p2"] is False
        assert rows["p3"] is False
        assert rows["h1"] is True
        assert rows["h2"] is False
        assert rows["h3"] is False
    finally:
        nested.rollback()


# ────────────────────────────────────────────────────────────────────────
# 6. Immutability invariant
# ────────────────────────────────────────────────────────────────────────


def test_trace_metadata_is_frozen() -> None:
    """Mutating a constructed TraceMetadata raises FrozenInstanceError.

    Rationale: once meta is passed to write_trace, a caller tweaking
    `meta.objective_value` after the fact would create a silent DB-row
    vs in-memory mismatch. Frozen dataclass catches that at runtime.
    """
    meta = _meta(str(uuid.uuid4()))
    with pytest.raises(FrozenInstanceError):
        meta.objective_value = 999.0  # type: ignore[misc]
