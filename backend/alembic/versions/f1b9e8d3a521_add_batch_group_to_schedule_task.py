"""add batch_group column to schedule_task

Revision ID: f1b9e8d3a521
Revises: e7a1c4f9b3d2
Create Date: 2026-04-29

Scope:
  schedule_task.batch_group 컬럼만 추가한다 (model 에는 이미 존재).

왜 필요:
  ORM 모델 (`app.infrastructure.models.schedule_task.ScheduleTask`) 가
  `batch_group = Column(String(50), index=True)` 를 노출하는데, 마이그레이션
  체인 어디에도 해당 컬럼 생성 단계가 없다. Supabase (dev DB) 는 out-of-band
  SQL 로 이미 가지고 있어 dev 환경에서 발견되지 않았으나, fresh Postgres
  ( CI throwaway, 로컬 새 DB ) 에서 `alembic upgrade head` 후 INSERT 시
  `column "batch_group" of relation "schedule_task" does not exist` 로
  실패한다. b9e2f4a6d018 (production_batch.batch_group) 와 동일 패턴.

Idempotent 설계:
  Supabase 는 이미 컬럼을 가진다 → information_schema 로 존재 확인 후 no-op.
  Fresh DB 만 실제 op.add_column + index 실행.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1b9e8d3a521"
down_revision: Union[str, Sequence[str], None] = "e7a1c4f9b3d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'schedule_task' AND column_name = 'batch_group'
            """
        )
    ).first()
    if exists is None:
        op.add_column(
            "schedule_task",
            sa.Column("batch_group", sa.String(length=50), nullable=True),
        )
        op.create_index(
            op.f("ix_schedule_task_batch_group"),
            "schedule_task",
            ["batch_group"],
            unique=False,
        )


def downgrade() -> None:
    """No-op.

    b9e2f4a6d018 와 동일 정책: Supabase out-of-band 로 미리 만들어진 컬럼을
    backfill 했으므로 downgrade 시 drop 하면 운영 데이터 손실. fresh DB 는
    full teardown (drop all tables) 시 자연 제거.
    """
    pass
