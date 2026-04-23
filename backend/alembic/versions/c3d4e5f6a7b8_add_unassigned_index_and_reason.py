"""add unassigned partial index + unassign_reason column

Revision ID: c3d4e5f6a7b8
Revises: b9e2f4a6d018
Create Date: 2026-04-17 10:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b9e2f4a6d018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add (1) unassign_reason column, (2) partial indexes for unassigned status lookups.

    unassign_reason stores the user-selected tag at unassign time:
      '자재지연' | '설비고장' | '납기재협상' | '기타' | NULL (legacy)
    Value is validated at the application layer.
    """
    op.add_column(
        "production_batch",
        sa.Column("unassign_reason", sa.String(32), nullable=True),
    )
    op.create_index(
        "ix_production_batch_unassigned",
        "production_batch",
        ["batch_group"],
        postgresql_where="status = 'unassigned'",
    )
    op.create_index(
        "ix_schedule_task_unassigned",
        "schedule_task",
        ["batch_id"],
        postgresql_where="status = 'unassigned'",
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_task_unassigned", table_name="schedule_task")
    op.drop_index("ix_production_batch_unassigned", table_name="production_batch")
    op.drop_column("production_batch", "unassign_reason")
