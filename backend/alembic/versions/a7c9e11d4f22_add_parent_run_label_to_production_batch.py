"""add parent_run_label to production_batch

Revision ID: a7c9e11d4f22
Revises: f5d8a1c09e21
Create Date: 2026-04-20 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c9e11d4f22"
down_revision: Union[str, Sequence[str], None] = "f5d8a1c09e21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # stage1/update 가 새 run_label 을 발급할 때 어느 run 에서 파생됐는지 기록.
    # 버전 계보 추적 + two-run diff 기본값(비교 대상) 도출에 사용.
    op.add_column(
        "production_batch",
        sa.Column("parent_run_label", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "ix_production_batch_parent_run_label",
        "production_batch",
        ["parent_run_label"],
    )


def downgrade() -> None:
    op.drop_index("ix_production_batch_parent_run_label", table_name="production_batch")
    op.drop_column("production_batch", "parent_run_label")
