"""add batch_group column to production_batch

Revision ID: b9e2f4a6d018
Revises: f2900467a547
Create Date: 2026-04-23

왜 이 마이그레이션이 필요한가:
  c3d4e5f6a7b8_add_unassigned_index_and_reason 가
  production_batch.batch_group 컬럼에 partial index 를 생성하지만,
  체인 어디에도 해당 컬럼을 생성하는 마이그레이션이 없다.
  Supabase(dev DB)에는 out-of-band SQL 로 이미 존재하지만,
  fresh Postgres 에 `alembic upgrade head` 하면 UndefinedColumn 으로 실패한다.

Idempotent 설계:
  Supabase 는 이미 해당 컬럼을 가진다 → information_schema 로 존재 확인 후 no-op.
  Fresh DB 만 실제 op.add_column 실행.
  c3d4e5f6a7b8 의 index 생성보다 먼저 실행되도록 체인에 삽입한다.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9e2f4a6d018"
down_revision: Union[str, Sequence[str], None] = "f2900467a547"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'production_batch' AND column_name = 'batch_group'
            """
        )
    ).first()
    if exists is None:
        op.add_column(
            "production_batch",
            sa.Column("batch_group", sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'production_batch' AND column_name = 'batch_group'
            """
        )
    ).first()
    if exists is not None:
        op.drop_column("production_batch", "batch_group")
