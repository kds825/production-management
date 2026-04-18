"""wip unique index + status rename

Revision ID: 708591555e9b
Revises: dc5d1c469f8e
Create Date: 2026-04-18 11:27:33.125686

Task 2: G1 UNIQUE partial index on wip_inventory.source_batch_id +
'실적' → '실사_확정' status mass rename.

Prerequisites for downstream tasks:
- Task 6 listener: relies on ON CONFLICT (source_batch_id) DO NOTHING
- Task 13 create_shortage_batches: filters WipInventory.status == "실사_확정"
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "708591555e9b"
down_revision: Union[str, Sequence[str], None] = "dc5d1c469f8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _preflight_check(conn) -> None:
    """중복 source_batch_id 검출 시 abort.

    Why: UNIQUE partial index 생성 전에 기존 데이터에 중복이 있으면
    index 생성 자체가 실패한다. 명시적 RuntimeError 로 조기 감지해
    DBA 가 중복 제거 후 재시도할 수 있도록 안내한다.
    """
    dup = conn.execute(
        text("""
        SELECT source_batch_id, COUNT(*) AS c
        FROM wip_inventory
        WHERE source_batch_id IS NOT NULL
        GROUP BY source_batch_id
        HAVING COUNT(*) > 1
        LIMIT 1
    """)
    ).fetchone()
    if dup:
        raise RuntimeError(
            f"UNIQUE 제약 위반 가능 데이터 발견: source_batch_id={dup.source_batch_id} "
            f"count={dup.c}. 중복 제거 후 재시도."
        )


def _rename_status(conn) -> None:
    """'실적' → '실사_확정' 라벨 통일.

    Why: 초기 설계에서 '실적'으로 기록된 상태값이 Task 13 에서
    '실사_확정'으로 스펙이 확정됨 → 기존 레거시 데이터도 일괄 rename.
    """
    conn.execute(
        text("UPDATE wip_inventory SET status = '실사_확정' WHERE status = '실적'")
    )


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    _preflight_check(conn)
    _rename_status(conn)
    op.create_index(
        "idx_wip_source_batch_unique",
        "wip_inventory",
        ["source_batch_id"],
        unique=True,
        postgresql_where=text("source_batch_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Note: status rename('실적' → '실사_확정')은 downgrade 에서 복원하지 않는다.
    Why: rename 된 레코드가 이미 downstream 로직에서 '실사_확정' 로 처리됐을 수 있어
    역전환하면 데이터 정합성이 깨질 위험이 있다. UNIQUE index 만 제거.
    """
    op.drop_index("idx_wip_source_batch_unique", table_name="wip_inventory")
