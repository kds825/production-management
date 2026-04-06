"""add core column to wip_inventory

Revision ID: f2900467a547
Revises: b2c3d4e5f6a7
Create Date: 2026-04-06 21:54:54.692313

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2900467a547"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """wip_inventory 테이블에 core 컬럼 추가 (연합 재공용 — 규격 뒤, cross_section 앞)."""
    op.add_column("wip_inventory", sa.Column("core", sa.String(20), nullable=True))


def downgrade() -> None:
    """core 컬럼 제거."""
    op.drop_column("wip_inventory", "core")
