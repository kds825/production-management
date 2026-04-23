"""ConstraintSpec + loader — solver-side projection of ConstraintConfig.

Task 2A.1 (Production Handoff Refactor, Rev 3, Week 2).

Why this module:
  - services/solver/* must stay pure (no direct SQLAlchemy). This file is
    the ONLY sanctioned boundary crossing — it reads ConstraintConfig rows
    and hands back immutable value-objects (ConstraintSpec) that
    model_builder (Task 2A.2), trace_writer (Task 2A.3), and eventually
    the LLM narrator (Week 4) + weight objective (Week 5) consume.
  - Spec §7 boundary invariant: other solver modules import
    `ConstraintSpec` from here, never `from app.infrastructure`.
    CI enforcement lands in Task 2A.5.

Scope of Week 2 (Task 2A.1):
  - Define the frozen dataclass and loader.
  - Do NOT wire into cp_sat_schedule yet (that's Task 2A.2 + 2A.3).
  - Weight handling intentionally absent — Week 5 (D2-B) migrates
    hardcoded _TARDINESS_WEIGHT etc. into params_json and ConstraintSpec
    starts to affect the solver then. For now ConstraintSpec carries
    priority/impact_level/implementation_type but the solver ignores them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig

# Implementation-type taxonomy from spec §1. Typed as `| str` so legacy /
# NULL rows don't break the loader — downstream consumers (trace_writer,
# narrator) can still decide to warn or skip on unknown values.
ImplementationType = Literal["solver_term", "pre_filter", "post_filter"]


@dataclass(frozen=True)
class ConstraintSpec:
    """Immutable value-object projection of ConstraintConfig ORM row.

    Lives in services/solver/; consumed by model_builder (Task 2A.2),
    trace_writer (Task 2A.3), and eventually LLM narrator (Week 4) +
    weight objective (Week 5).

    Frozen per spec §7 — attribute assignment raises FrozenInstanceError.
    Invariants held by CONVENTION (not enforced by Python):
      - `params` is a defensive copy of params_json; consumers must not
        mutate. Using plain `dict` (not frozendict) keeps the boundary
        simple — violations surface as obvious dict mutations in review.
      - `applicable_processes` is a tuple (hashable + immutable).

    NO computed fields, NO property methods, NO __post_init__ validators.
    Pure data. Validation lives in the loader (_row_to_spec) or downstream.
    """

    constraint_id: str
    constraint_name: str
    category: str
    is_enabled: bool
    priority: int
    impact_level: str | None
    implementation_type: ImplementationType | str  # tolerate legacy values
    params: dict[str, Any]  # defensive copy of params_json
    applicable_processes: tuple[str, ...]  # tuple = hashable; ORM gives list
    notes: str | None
    updated_at: datetime


def load_active_constraints(
    db: Session,
    *,
    include_disabled: bool = False,
) -> list[ConstraintSpec]:
    """Load ConstraintSpec list from ConstraintConfig table.

    By default filters out `is_enabled=False` rows (the active-constraints
    contract). `include_disabled=True` returns all rows — used by admin UI
    + baseline diff.

    Sorted by (category, priority desc, constraint_id) for deterministic
    output. trace_writer (Task 2A.3) and baseline diffs depend on stable
    row order — any sort change here is a semver-style break for them.

    Returns a list of frozen ConstraintSpec instances.
    """
    query = db.query(ConstraintConfig)
    if not include_disabled:
        # `== True` is intentional SQLAlchemy idiom; ruff/E712 suppressed.
        query = query.filter(ConstraintConfig.is_enabled == True)  # noqa: E712
    rows = query.order_by(
        ConstraintConfig.category,
        ConstraintConfig.priority.desc(),
        ConstraintConfig.constraint_id,
    ).all()
    return [_row_to_spec(r) for r in rows]


def _row_to_spec(row: ConstraintConfig) -> ConstraintSpec:
    """Project a ConstraintConfig ORM row into a frozen ConstraintSpec.

    Defensive copies:
      - params_json → fresh dict (consumers mutating spec.params won't
        corrupt the SQLAlchemy-tracked attribute on the row).
      - applicable_processes → tuple (also enforces hashability on the
        spec as a whole if anyone ever wants to put specs in a set).

    Defaults:
      - priority: 0 if NULL (shouldn't happen — column has default=50 —
        but bool(None or 0) is safer than int(None)).
      - implementation_type: "solver_term" if NULL/empty. Covers legacy
        rows before the column was populated; the most common case.
    """
    return ConstraintSpec(
        constraint_id=row.constraint_id,
        constraint_name=row.constraint_name,
        category=row.category,
        is_enabled=bool(row.is_enabled),
        priority=int(row.priority or 0),
        impact_level=row.impact_level,
        implementation_type=row.implementation_type or "solver_term",
        params=dict(row.params_json or {}),
        applicable_processes=tuple(row.applicable_processes or []),
        notes=row.notes,
        updated_at=row.updated_at,
    )
