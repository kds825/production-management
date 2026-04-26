"""decision_card phase6 — feedback + telemetry tables + indexes

Revision ID: e7a1c4f9b3d2
Revises: c4a1b2d3e5f7
Create Date: 2026-04-26 00:00:00.000000

Phase 6 (decision_card) Step 2:
- decision_feedback 테이블 신설 (운영자 의견)
- decision_card_telemetry 테이블 신설 (KPI source)
- audit_log / solver_decision 인덱스 추가 (build_card.py 쿼리 가속)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "e7a1c4f9b3d2"
down_revision: Union[str, Sequence[str], None] = "c4a1b2d3e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create decision_feedback / decision_card_telemetry + 인덱스 5종."""

    # ── decision_feedback ────────────────────────────────────────────────
    op.create_table(
        "decision_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("run_label", sa.String(length=50), nullable=False),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("production_batch.batch_id"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("schedule_task.task_id"),
            nullable=True,
        ),
        sa.Column("section", sa.String(length=20), nullable=False),
        sa.Column("line_anchor", sa.String(length=50), nullable=False),
        sa.Column("constraint_id_hint", sa.String(length=10), nullable=True),
        sa.Column("free_text", sa.Text(), nullable=False),
        sa.Column("attachment_path", sa.String(length=255), nullable=True),
        sa.Column("operator_id", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("dev_notes", sa.Text(), nullable=True),
        sa.Column("linked_pr_url", sa.String(length=255), nullable=True),
        sa.Column("payload_snapshot", JSONB(), nullable=False),
    )
    op.create_index(
        "ix_decision_feedback_status_created",
        "decision_feedback",
        ["status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_decision_feedback_run_batch",
        "decision_feedback",
        ["run_label", "batch_id"],
    )
    op.create_index(
        "ix_decision_feedback_line_anchor",
        "decision_feedback",
        ["line_anchor"],
    )

    # ── decision_card_telemetry ─────────────────────────────────────────
    op.create_table(
        "decision_card_telemetry",
        sa.Column(
            "telemetry_id", sa.BigInteger(), primary_key=True, autoincrement=True
        ),
        sa.Column("run_label", sa.String(length=50), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.String(length=50), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_decision_card_telemetry_operator_opened",
        "decision_card_telemetry",
        ["operator_id", sa.text("opened_at DESC")],
    )

    # ── audit_log / solver_decision 인덱스 (build_card.py 쿼리 가속) ──────
    op.create_index(
        "ix_audit_log_run_batch_action",
        "audit_log",
        ["run_label", "batch_id", "action_type"],
    )
    op.create_index(
        "ix_solver_decision_run_constraint",
        "solver_decision",
        ["run_id", "constraint_id"],
    )


def downgrade() -> None:
    """Drop indexes + tables (역순)."""
    op.drop_index("ix_solver_decision_run_constraint", table_name="solver_decision")
    op.drop_index("ix_audit_log_run_batch_action", table_name="audit_log")

    op.drop_index(
        "ix_decision_card_telemetry_operator_opened",
        table_name="decision_card_telemetry",
    )
    op.drop_table("decision_card_telemetry")

    op.drop_index("ix_decision_feedback_line_anchor", table_name="decision_feedback")
    op.drop_index("ix_decision_feedback_run_batch", table_name="decision_feedback")
    op.drop_index("ix_decision_feedback_status_created", table_name="decision_feedback")
    op.drop_table("decision_feedback")
