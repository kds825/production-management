"""ConstraintSpec + load_active_constraints — Task 2A.1 contract tests.

Scope per spec §7:
  - Frozen dataclass is the primary contract (verified in code, not
    convention).
  - Default filter = is_enabled=True only.
  - include_disabled=True = full table.
  - Deterministic ordering (trace_writer + baseline diff depend on it).
  - Defensive params dict (mutating spec.params must not touch the ORM
    row's params_json).
  - applicable_processes is a tuple (hashable, matches design).

All tests use the function-scoped `db` fixture from conftest.py; that
fixture rollbacks the entire session on teardown, so SAVEPOINTs aren't
strictly required. We still scope mutations carefully and never commit.
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig
from app.services.solver import ConstraintSpec, load_active_constraints


# ---------- helpers ---------------------------------------------------------


def _insert(
    db: Session,
    *,
    constraint_id: str,
    is_enabled: bool,
    category: str = "TEST_CAT",
    priority: int = 50,
    impl: str = "solver_term",
) -> None:
    """Insert a row and flush (no commit) so the current session sees it.

    The db fixture's rollback-on-teardown will clean up.
    """
    row = ConstraintConfig(
        constraint_id=constraint_id,
        constraint_name=f"test-{constraint_id}",
        category=category,
        is_enabled=is_enabled,
        priority=priority,
        impact_level="중",
        params_json={"foo": "bar"},
        applicable_processes=["EX-B100"],
        implementation_type=impl,
        notes=None,
    )
    db.add(row)
    db.flush()


# ---------- 1. frozen invariant --------------------------------------------


def test_load_returns_frozen_instances(db: Session) -> None:
    """Primary contract: spec is a frozen dataclass. Mutation raises."""
    specs = load_active_constraints(db)
    assert specs, "live DB should have at least one enabled constraint"

    spec = specs[0]
    assert dataclasses.is_dataclass(spec)
    assert spec.__dataclass_params__.frozen is True
    assert isinstance(spec, ConstraintSpec)

    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.priority = 99  # type: ignore[misc]


# ---------- 2 + 3. enabled filter -------------------------------------------


def test_default_filters_disabled(db: Session) -> None:
    """Default call path must NOT return is_enabled=False rows."""
    _insert(db, constraint_id="TEST_FE", is_enabled=True)
    _insert(db, constraint_id="TEST_FD", is_enabled=False)

    ids = {s.constraint_id for s in load_active_constraints(db)}
    assert "TEST_FE" in ids
    assert "TEST_FD" not in ids


def test_include_disabled_returns_all(db: Session) -> None:
    """include_disabled=True must surface both rows."""
    _insert(db, constraint_id="TEST_IE", is_enabled=True)
    _insert(db, constraint_id="TEST_ID", is_enabled=False)

    ids = {s.constraint_id for s in load_active_constraints(db, include_disabled=True)}
    assert "TEST_IE" in ids
    assert "TEST_ID" in ids


# ---------- 4. deterministic order ------------------------------------------


def test_order_is_deterministic(db: Session) -> None:
    """Two loads must return identical ordering — trace_writer relies on it."""
    first = [s.constraint_id for s in load_active_constraints(db)]
    second = [s.constraint_id for s in load_active_constraints(db)]
    assert first == second
    assert len(first) >= 1

    # Stronger: order matches the documented (category, -priority, id) sort.
    specs = load_active_constraints(db)
    sort_key = [(s.category, -s.priority, s.constraint_id) for s in specs]
    assert sort_key == sorted(sort_key), "loader output must be pre-sorted"


# ---------- 5. defensive copy of params -------------------------------------


def test_params_is_defensive_copy(db: Session) -> None:
    """Mutating spec.params[...] must not bleed back to the ORM row.

    Note: `dict` is a mutable container; the frozen dataclass only guards
    attribute assignment. That's an explicit design choice (§7 invariant
    is "consumers promise not to mutate"). This test pins the defensive-
    copy behavior so a future refactor that aliases params_json directly
    would be caught.
    """
    _insert(db, constraint_id="TEST_COPY", is_enabled=True)
    specs = load_active_constraints(db)
    spec = next(s for s in specs if s.constraint_id == "TEST_COPY")

    # Attribute reassignment blocked (frozen).
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.params = {}  # type: ignore[misc]

    # Content mutation permitted by Python but must not affect the ORM row.
    spec.params["injected"] = 999

    row = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "TEST_COPY")
        .one()
    )
    assert "injected" not in (row.params_json or {}), (
        "spec.params must be a defensive copy of params_json"
    )


# ---------- 6. applicable_processes type ------------------------------------


def test_applicable_processes_is_tuple(db: Session) -> None:
    """applicable_processes must be a tuple (hashable, immutable)."""
    _insert(db, constraint_id="TEST_TUPLE", is_enabled=True)
    specs = load_active_constraints(db)
    spec = next(s for s in specs if s.constraint_id == "TEST_TUPLE")

    assert isinstance(spec.applicable_processes, tuple)
    assert spec.applicable_processes == ("EX-B100",)
