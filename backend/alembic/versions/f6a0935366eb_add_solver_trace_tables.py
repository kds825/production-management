"""add solver_run + solver_decision + override_reason

Revision ID: f6a0935366eb
Revises: a7c9e11d4f22
Create Date: 2026-04-23 10:30:00.000000

Task 2B.1 (Production Handoff Refactor, Rev 3, Week 2).

Additive-only. Enables trace_writer persistence for CP-SAT runs + operator
override reason column. ORM models land separately in Task 2B.2.

Also renames Phase 0 baseline marker per Rev 3 §8b narrow-history convention:
  PHASE0_INITIAL_20260423 → BASELINE_phase0-initial_20260423T101449Z
The rename is idempotent (matches 0 rows if already renamed).

Path D safety: Supabase sees only `alembic upgrade head` (additive +
idempotent UPDATE). downgrade() is reserved for throwaway local Postgres
via scripts/test_migration_reversibility.sh — never run against Supabase.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# Alembic identifiers
revision: str = "f6a0935366eb"
down_revision: Union[str, Sequence[str], None] = "a7c9e11d4f22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Rename marker constants — keep consistent with Rev 3 §8b narrow-history convention.
# Old marker was ad-hoc for Phase 0; new one carries ISO8601 timestamp +
# lowercase phase-name prefix so future baselines stay sortable.
_OLD_PHASE0_MARKER = "PHASE0_INITIAL_20260423"
_NEW_PHASE0_MARKER = "BASELINE_phase0-initial_20260423T101449Z"


def upgrade() -> None:
    """Additive schema changes + idempotent Phase 0 marker rename."""

    # --- solver_run ---------------------------------------------------------
    # One row per cp_sat_schedule invocation. run_id is a UUID-shaped string
    # (String(36)) to stay consistent with the rest of the codebase which
    # uses uuid.uuid4().hex or str(uuid4()) and never relies on Postgres'
    # native UUID type. Keeps schema portable to SQLite test harnesses.
    op.create_table(
        "solver_run",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("run_label", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("solver_status", sa.String(length=20), nullable=False),
        sa.Column("objective_value", sa.Float(), nullable=True),
        # 71 = len("sha256:") + 64 hex chars. Fixed upper bound lets
        # Postgres pick a compact storage strategy.
        sa.Column("input_hash", sa.String(length=71), nullable=False),
        sa.Column("output_hash", sa.String(length=71), nullable=True),
        # constraint_config_history.config_version is a UUID string — leave
        # nullable so older runs (pre-versioning) still fit.
        sa.Column("constraint_config_version", sa.String(length=36), nullable=True),
        sa.Column(
            "solver_params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    # Recent-runs list + dashboards both filter/sort by run_label and
    # started_at. Separate indexes keep the planner honest.
    op.create_index(
        "ix_solver_run_run_label",
        "solver_run",
        ["run_label"],
    )
    op.create_index(
        "ix_solver_run_started_at",
        "solver_run",
        [sa.text("started_at DESC")],
    )

    # --- solver_decision ----------------------------------------------------
    # One row per (run_id, constraint_id). decision_id is BigInt autoincrement
    # because decisions grow ~70/run and we expect 10^4+ runs/year.
    # FK to solver_run cascades (deleting a run removes its trace rows).
    # FK to schedule_change_sets uses SET NULL so purging a change_set keeps
    # the decision row intact — we still want to see the solver's verdict
    # even if the override audit record was expired.
    op.create_table(
        "solver_decision",
        sa.Column(
            "decision_id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("solver_run.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("constraint_id", sa.String(length=10), nullable=False),
        sa.Column("penalty_value", sa.Float(), nullable=True),
        sa.Column("hard_literal_value", sa.Boolean(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column(
            "details_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Operator override linkage. schedule_change_sets PK is String (no
        # max length declared); we pin to String(36) per plan — all current
        # change_set_ids are UUID hex strings (32 chars) so 36 is safe.
        sa.Column(
            "manual_override_change_set_id",
            sa.String(length=36),
            sa.ForeignKey("schedule_change_sets.change_set_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_solver_decision_run_id",
        "solver_decision",
        ["run_id"],
    )
    op.create_index(
        "ix_solver_decision_constraint_id",
        "solver_decision",
        ["constraint_id"],
    )

    # --- schedule_change_sets.override_reason -------------------------------
    # Free-text operator comment. TEXT/nullable because most system-generated
    # change_sets won't have one.
    op.add_column(
        "schedule_change_sets",
        sa.Column("override_reason", sa.Text(), nullable=True),
    )

    # --- Phase 0 baseline marker rename -------------------------------------
    # Idempotent: runs post-table-create so any trace rows that reference
    # the old name would also see the new name later. WHERE clause matches
    # 0 rows if the rename already happened (e.g. on a re-run or after a
    # downgrade → upgrade cycle in the reversibility test).
    op.execute(
        sa.text(
            "UPDATE constraint_config_history "
            "SET changed_by = :new_name "
            "WHERE changed_by = :old_name"
        ).bindparams(
            new_name=_NEW_PHASE0_MARKER,
            old_name=_OLD_PHASE0_MARKER,
        )
    )


def downgrade() -> None:
    """Drop solver trace objects in reverse-dependency order.

    Never runs against Supabase — only the throwaway local Postgres via
    scripts/test_migration_reversibility.sh. Provided for Rev 3 §9
    reversibility gate (one-round upgrade/downgrade/upgrade must succeed).
    """
    # 1. Revert Phase 0 marker rename (also idempotent on re-runs).
    op.execute(
        sa.text(
            "UPDATE constraint_config_history "
            "SET changed_by = :old_name "
            "WHERE changed_by = :new_name"
        ).bindparams(
            old_name=_OLD_PHASE0_MARKER,
            new_name=_NEW_PHASE0_MARKER,
        )
    )

    # 2. Drop solver_decision (its FK to schedule_change_sets dies with it).
    op.drop_index("ix_solver_decision_constraint_id", table_name="solver_decision")
    op.drop_index("ix_solver_decision_run_id", table_name="solver_decision")
    op.drop_table("solver_decision")

    # 3. Drop solver_run.
    op.drop_index("ix_solver_run_started_at", table_name="solver_run")
    op.drop_index("ix_solver_run_run_label", table_name="solver_run")
    op.drop_table("solver_run")

    # 4. Drop override_reason column.
    op.drop_column("schedule_change_sets", "override_reason")
