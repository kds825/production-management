"""add kind column to schedule_change_sets

Revision ID: e4f7a9c21b30
Revises: da5c8ab3d523
Create Date: 2026-04-20 14:00:00.000000

긴급수주 반영 시에도 ScheduleChangeSet 모델로 before/after 스냅샷을 저장할
예정이라 cascade/urgent/manual 을 구분할 분류 컬럼이 필요. diff API 에서
종류별 필터링 빈도가 높아 index 를 함께 생성.

기존 데이터 호환을 위해 NOT NULL + server_default='cascade' 로 추가하고,
downgrade 시에는 index 먼저 drop 후 column drop (의존성 역순).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e4f7a9c21b30"
down_revision: Union[str, Sequence[str], None] = "da5c8ab3d523"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add kind column + index. server_default 로 기존 행을 'cascade' 로 채움."""
    op.add_column(
        "schedule_change_sets",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="cascade",
        ),
    )
    op.create_index(
        "ix_schedule_change_sets_kind",
        "schedule_change_sets",
        ["kind"],
    )


def downgrade() -> None:
    """Drop index first (의존성 역순) 후 column drop."""
    op.drop_index(
        "ix_schedule_change_sets_kind",
        table_name="schedule_change_sets",
    )
    op.drop_column("schedule_change_sets", "kind")
