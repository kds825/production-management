"""Task 12: schedule_change_sets 모델 smoke test.

실 DB 를 사용하고 rollback 으로 격리 (conftest.py `db` fixture). 목적은
INSERT/SELECT 가 동작하는지 + JSONB 직렬화가 dict ↔ JSON 으로 왕복하는지 확인.
"""

import uuid

import pytest
from sqlalchemy import inspect

from app.infrastructure.models.schedule_change_set import ScheduleChangeSet


def _has_kind_column(db) -> bool:
    """실 DB 에 kind 컬럼이 존재하는지 검사.

    왜: 신규 alembic migration (e4f7a9c21b30) 을 아직 적용하지 않은 환경에서는
    kind 컬럼이 없어 INSERT 가 실패한다. 이 경우 테스트를 skip 하여 CI/로컬
    환경을 깨지 않도록 가드 (migration 선행 적용 시 자동으로 통과).
    """
    inspector = inspect(db.get_bind())
    cols = {c["name"] for c in inspector.get_columns("schedule_change_sets")}
    return "kind" in cols


def test_insert_and_query(db):
    """Insert 1건 → get() 으로 조회 → 필드 보존 확인."""
    cs_id = f"cs-test-{uuid.uuid4().hex[:8]}"
    cs = ScheduleChangeSet(
        change_set_id=cs_id,
        preview_request_id="req-1",
        snapshot_before={
            "T1": {
                "start": "2026-04-20T09:00:00",
                "end": "2026-04-20T12:00:00",
                "equipment_code": "EQ1",
            }
        },
        snapshot_after={
            "T1": {
                "start": "2026-04-20T10:00:00",
                "end": "2026-04-20T13:00:00",
                "equipment_code": "EQ1",
            }
        },
    )
    db.add(cs)
    db.flush()

    fetched = db.get(ScheduleChangeSet, cs_id)
    assert fetched is not None
    assert fetched.preview_request_id == "req-1"
    # JSONB 라운드트립: dict 로 돌아와야 함.
    assert fetched.snapshot_before["T1"]["equipment_code"] == "EQ1"
    assert fetched.snapshot_after["T1"]["start"] == "2026-04-20T10:00:00"
    # created_at 은 server default + datetime.utcnow() 로 채워져야 함 (NULL 아님).
    assert fetched.created_at is not None


def test_insert_with_kind_urgent(db):
    """kind='urgent' 로 INSERT + 조회 검증.

    긴급수주 스냅샷을 저장하는 경로. migration 미적용 환경에서는 컬럼이 없어
    INSERT 가 실패하므로 conftest 가 실 DB 를 사용하는 특성상 skip 처리.
    """
    if not _has_kind_column(db):
        pytest.skip(
            "schedule_change_sets.kind column not present — "
            "run alembic upgrade head (rev e4f7a9c21b30) first"
        )

    cs = ScheduleChangeSet(
        change_set_id=f"cs-urg-{uuid.uuid4().hex[:8]}",
        kind="urgent",
        snapshot_before={},
        snapshot_after={},
    )
    db.add(cs)
    db.flush()
    fetched = db.get(ScheduleChangeSet, cs.change_set_id)
    assert fetched.kind == "urgent"


def test_default_kind_is_cascade(db):
    """kind 미지정 시 기본값 'cascade'.

    SQLAlchemy column `default="cascade"` + alembic `server_default='cascade'`
    양쪽 모두에서 기본값이 채워지는지 확인.
    """
    if not _has_kind_column(db):
        pytest.skip(
            "schedule_change_sets.kind column not present — "
            "run alembic upgrade head (rev e4f7a9c21b30) first"
        )

    cs = ScheduleChangeSet(
        change_set_id=f"cs-def-{uuid.uuid4().hex[:8]}",
        snapshot_before={},
        snapshot_after={},
    )
    db.add(cs)
    db.flush()
    fetched = db.get(ScheduleChangeSet, cs.change_set_id)
    assert fetched.kind == "cascade"
