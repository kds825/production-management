"""add spec_raw to production_batch

Revision ID: a1b2c3d4e5f6
Revises: 90b349ba1c61
Create Date: 2026-04-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '90b349ba1c61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('production_batch', sa.Column('spec_raw', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('production_batch', 'spec_raw')
