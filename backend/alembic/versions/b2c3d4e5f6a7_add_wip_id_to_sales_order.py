"""add wip_id to sales_order

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-03 00:00:00.000000

1개 WIP가 여러 수주에 적용되는 다대일 매칭을 지원하기 위해
sales_order에 wip_inventory FK 컬럼 추가.
기존 wip.matched_order_id(단일값) 대신 order→wip 방향으로 역참조한다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sales_order',
        sa.Column(
            'wip_id',
            sa.Integer(),
            sa.ForeignKey('wip_inventory.wip_id'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('sales_order', 'wip_id')
