"""add schedule_change_sets

Revision ID: d1e2f3a4b5c6
Revises: c3d4e5f6a7b8
Create Date: 2026-04-18 00:00:00.000000

Task 12: bulk-update v2 가 INSERT 할 change_set 테이블. snapshot_before /
snapshot_after 를 JSONB 로 보관해 Task 14 revert 가 역적용에 사용.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create schedule_change_sets table + created_at index."""
    op.create_table(
        "schedule_change_sets",
        sa.Column("change_set_id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("preview_request_id", sa.String(), nullable=True),
        sa.Column("snapshot_before", JSONB(), nullable=False),
        sa.Column("snapshot_after", JSONB(), nullable=False),
        sa.Column("applied_by", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_schedule_change_sets_created_at",
        "schedule_change_sets",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_change_sets_created_at",
        table_name="schedule_change_sets",
    )
    op.drop_table("schedule_change_sets")
