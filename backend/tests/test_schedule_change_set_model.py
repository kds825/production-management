"""Task 12: schedule_change_sets 모델 smoke test.

실 DB 를 사용하고 rollback 으로 격리 (conftest.py `db` fixture). 목적은
INSERT/SELECT 가 동작하는지 + JSONB 직렬화가 dict ↔ JSON 으로 왕복하는지 확인.
"""

import uuid

from app.infrastructure.models.schedule_change_set import ScheduleChangeSet


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
