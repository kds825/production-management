"""add llm_summary_text + llm_was_template to solver_run

Revision ID: c4a1b2d3e5f7
Revises: f6a0935366eb
Create Date: 2026-04-25 09:00:00.000000

Task 4B.1 (Production Handoff Refactor, Rev 3, Week 4).

Why on solver_run (not solver_decision):
  The narrator output describes the whole run's decision context for one
  batch — a single sentence per run, not per constraint. solver_decision
  is the per-constraint trace and would store N redundant copies of the
  same string. Per-run cache fits the access pattern of the new
  ``GET /api/decisions/{batch_id}/latest`` endpoint, which fetches one
  run row and aggregates its child decisions.

Additive, idempotent, and Path-D safe — Supabase only sees
``alembic upgrade head``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# Alembic identifiers
revision: str = "c4a1b2d3e5f7"
down_revision: Union[str, Sequence[str], None] = "f6a0935366eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Cached narrator output. Nullable because old runs (and freshly
    # written runs before the first Decision Card render) won't have
    # one. TEXT not VARCHAR — output length is bounded by provider
    # max_tokens but we don't want to enforce it at the schema layer.
    op.add_column(
        "solver_run",
        sa.Column("llm_summary_text", sa.Text(), nullable=True),
    )
    # Whether the cached summary was the TemplateProvider fallback
    # (kiwipiepy hallucination filter tripped). UI uses this to label
    # uncertain summaries. Nullable so existing rows stay valid.
    op.add_column(
        "solver_run",
        sa.Column("llm_was_template", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("solver_run", "llm_was_template")
    op.drop_column("solver_run", "llm_summary_text")
