"""GET /api/schedules/change-sets/{change_set_id}/diff — change_set 비교 API.

snapshot_before / snapshot_after JSONB 를 비교해 moved / added / removed / unchanged
로 분류해 반환하는 읽기 전용 엔드포인트의 계약을 검증한다.

conftest.py 의 `db` fixture 는 rollback 기반 격리를 제공하지만, TestClient 는 별도
세션에서 쿼리하므로 테스트가 삽입한 change_set 은 `db.commit()` 이 필요하다. 그 대가로
cleanup 도 명시적 DELETE + commit 으로 마무리한다 (rollback 으로는 제거되지 않음).
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.main import app
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet


client = TestClient(app)


def _has_kind_column(db) -> bool:
    """schedule_change_sets.kind 컬럼 존재 여부 (migration 선행 가드)."""
    inspector = inspect(db.get_bind())
    cols = {c["name"] for c in inspector.get_columns("schedule_change_sets")}
    return "kind" in cols


def _insert_change_set(db, **kwargs) -> str:
    """테스트용 change_set 을 INSERT + commit 하고 id 반환.

    kwargs 로 snapshot_before / snapshot_after / kind 주입 가능.
    TestClient 가 별도 세션을 쓰므로 flush 가 아닌 commit 이 필요하다.
    """
    cs_id = kwargs.pop("change_set_id", None) or f"cs-diff-{uuid.uuid4().hex[:8]}"
    cs = ScheduleChangeSet(change_set_id=cs_id, **kwargs)
    db.add(cs)
    db.commit()
    return cs_id


def _delete_change_set(db, cs_id: str) -> None:
    """cleanup — commit 으로 남은 레코드를 명시적으로 제거."""
    leftover = db.get(ScheduleChangeSet, cs_id)
    if leftover is not None:
        db.delete(leftover)
        db.commit()


def test_diff_404_on_missing_change_set():
    """존재하지 않는 change_set_id → 404. DB 무관, TestClient 만으로 충분."""
    r = client.get("/api/schedules/change-sets/NONEXISTENT-DIFF-XYZ/diff")
    assert r.status_code == 404


def test_diff_all_moved(db):
    """3 개 task 모두 start/end 변경 → moved 3, 나머지 0."""
    snapshot_before = {
        "T1": {
            "start": "2026-04-20T09:00:00",
            "end": "2026-04-20T12:00:00",
            "equipment_code": "EQ1",
        },
        "T2": {
            "start": "2026-04-20T10:00:00",
            "end": "2026-04-20T14:00:00",
            "equipment_code": "EQ2",
        },
        "T3": {
            "start": "2026-04-20T15:00:00",
            "end": "2026-04-20T18:00:00",
            "equipment_code": "EQ3",
        },
    }
    snapshot_after = {
        "T1": {
            "start": "2026-04-20T10:00:00",
            "end": "2026-04-20T13:00:00",
            "equipment_code": "EQ1",
        },
        "T2": {
            "start": "2026-04-20T11:30:00",
            "end": "2026-04-20T15:30:00",
            "equipment_code": "EQ2",
        },
        "T3": {
            "start": "2026-04-20T16:00:00",
            "end": "2026-04-20T19:00:00",
            "equipment_code": "EQ3",
        },
    }
    cs_id = _insert_change_set(
        db,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
    )
    try:
        r = client.get(f"/api/schedules/change-sets/{cs_id}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["moved"] == 3
        assert body["summary"]["added"] == 0
        assert body["summary"]["removed"] == 0
        assert body["summary"]["unchanged"] == 0
        assert body["summary"]["total_before"] == 3
        assert body["summary"]["total_after"] == 3
        # delta_hours 검증: T1 은 +1h start / +1h end
        t1 = next(m for m in body["moved_tasks"] if m["task_id"] == "T1")
        assert t1["start_delta_hours"] == 1.0
        assert t1["end_delta_hours"] == 1.0
        assert t1["equipment_changed"] is False
    finally:
        _delete_change_set(db, cs_id)


def test_diff_mixed_moved_added_unchanged(db):
    """moved / added / unchanged 혼재 → 각 분류 카운트 정확성."""
    snapshot_before = {
        "T_MOVED": {
            "start": "2026-04-20T09:00:00",
            "end": "2026-04-20T12:00:00",
            "equipment_code": "EQ1",
        },
        "T_SAME": {
            "start": "2026-04-20T13:00:00",
            "end": "2026-04-20T17:00:00",
            "equipment_code": "EQ2",
        },
    }
    snapshot_after = {
        "T_MOVED": {
            "start": "2026-04-20T10:00:00",  # +1h
            "end": "2026-04-20T13:00:00",
            "equipment_code": "EQ1",
        },
        "T_SAME": {
            "start": "2026-04-20T13:00:00",
            "end": "2026-04-20T17:00:00",
            "equipment_code": "EQ2",
        },
        "T_NEW": {
            "start": "2026-04-20T18:00:00",
            "end": "2026-04-20T20:00:00",
            "equipment_code": "EQ3",
        },
    }
    cs_id = _insert_change_set(
        db,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
    )
    try:
        r = client.get(f"/api/schedules/change-sets/{cs_id}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["moved"] == 1
        assert body["summary"]["added"] == 1
        assert body["summary"]["removed"] == 0
        assert body["summary"]["unchanged"] == 1
        assert body["summary"]["total_before"] == 2
        assert body["summary"]["total_after"] == 3
        assert [m["task_id"] for m in body["moved_tasks"]] == ["T_MOVED"]
        assert [a["task_id"] for a in body["added_tasks"]] == ["T_NEW"]
        assert body["unchanged_task_ids"] == ["T_SAME"]
    finally:
        _delete_change_set(db, cs_id)


def test_diff_kind_field_included(db):
    """응답에 kind 필드 포함 — urgent 로 INSERT 하면 그대로 직렬화."""
    if not _has_kind_column(db):
        pytest.skip(
            "schedule_change_sets.kind column not present — "
            "run alembic upgrade head first"
        )

    snapshot = {
        "T1": {
            "start": "2026-04-20T09:00:00",
            "end": "2026-04-20T10:00:00",
            "equipment_code": "EQ1",
        },
    }
    cs_id = _insert_change_set(
        db,
        kind="urgent",
        snapshot_before=snapshot,
        snapshot_after=snapshot,
    )
    try:
        r = client.get(f"/api/schedules/change-sets/{cs_id}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "urgent"
        assert body["change_set_id"] == cs_id
        # created_at 직렬화 — None 이 아니고 ISO8601 형태여야.
        assert body["created_at"] is not None
        assert "T" in body["created_at"]
    finally:
        _delete_change_set(db, cs_id)


def test_diff_equipment_change_detected(db):
    """시간은 동일하고 설비만 바뀌어도 moved 로 분류 + equipment_changed=True."""
    snapshot_before = {
        "T1": {
            "start": "2026-04-20T09:00:00",
            "end": "2026-04-20T12:00:00",
            "equipment_code": "EQ_OLD",
        },
    }
    snapshot_after = {
        "T1": {
            "start": "2026-04-20T09:00:00",
            "end": "2026-04-20T12:00:00",
            "equipment_code": "EQ_NEW",
        },
    }
    cs_id = _insert_change_set(
        db,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
    )
    try:
        r = client.get(f"/api/schedules/change-sets/{cs_id}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["moved"] == 1
        assert body["summary"]["unchanged"] == 0
        m = body["moved_tasks"][0]
        assert m["task_id"] == "T1"
        assert m["equipment_changed"] is True
        assert m["old_equipment"] == "EQ_OLD"
        assert m["new_equipment"] == "EQ_NEW"
        # 시간 변화 없으므로 delta 는 0.0.
        assert m["start_delta_hours"] == 0.0
        assert m["end_delta_hours"] == 0.0
    finally:
        _delete_change_set(db, cs_id)


def test_diff_summary_counts_match(db):
    """summary 의 각 카운트가 실제 리스트 길이와 일치해야 한다."""
    snapshot_before = {
        "T_MOVED_1": {
            "start": "2026-04-20T08:00:00",
            "end": "2026-04-20T10:00:00",
            "equipment_code": "EQ1",
        },
        "T_MOVED_2": {
            "start": "2026-04-20T10:00:00",
            "end": "2026-04-20T12:00:00",
            "equipment_code": "EQ2",
        },
        "T_REMOVED": {
            "start": "2026-04-20T14:00:00",
            "end": "2026-04-20T16:00:00",
            "equipment_code": "EQ3",
        },
        "T_SAME": {
            "start": "2026-04-20T18:00:00",
            "end": "2026-04-20T20:00:00",
            "equipment_code": "EQ4",
        },
    }
    snapshot_after = {
        "T_MOVED_1": {
            "start": "2026-04-20T09:00:00",
            "end": "2026-04-20T11:00:00",
            "equipment_code": "EQ1",
        },
        "T_MOVED_2": {
            "start": "2026-04-20T11:00:00",
            "end": "2026-04-20T13:00:00",
            "equipment_code": "EQ2",
        },
        "T_SAME": {
            "start": "2026-04-20T18:00:00",
            "end": "2026-04-20T20:00:00",
            "equipment_code": "EQ4",
        },
        "T_ADDED": {
            "start": "2026-04-20T21:00:00",
            "end": "2026-04-20T23:00:00",
            "equipment_code": "EQ5",
        },
    }
    cs_id = _insert_change_set(
        db,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
    )
    try:
        r = client.get(f"/api/schedules/change-sets/{cs_id}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        # summary counts == 실제 리스트 길이
        assert body["summary"]["moved"] == len(body["moved_tasks"]) == 2
        assert body["summary"]["added"] == len(body["added_tasks"]) == 1
        assert body["summary"]["removed"] == len(body["removed_tasks"]) == 1
        assert body["summary"]["unchanged"] == len(body["unchanged_task_ids"]) == 1
        # total_before/after 도 입력 크기와 일치
        assert body["summary"]["total_before"] == 4
        assert body["summary"]["total_after"] == 4
    finally:
        _delete_change_set(db, cs_id)
