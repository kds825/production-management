"""GET /api/schedules/change-sets/{change_set_id}/diff — change_set 비교 API.

snapshot_before / snapshot_after JSONB 를 비교해 moved / added / removed / unchanged
로 분류해 반환하는 읽기 전용 엔드포인트의 계약을 검증한다.

conftest.py 의 `db` fixture 는 rollback 기반 격리를 제공하지만, TestClient 는 별도
세션에서 쿼리하므로 테스트가 삽입한 change_set 은 `db.commit()` 이 필요하다. 그 대가로
cleanup 도 명시적 DELETE + commit 으로 마무리한다 (rollback 으로는 제거되지 않음).
"""

import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.schedule_task import ScheduleTask
from app.main import app


client = TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# P6 메타 테스트용 helper — equipment_code 는 기존 seed data ("ST-54BO1") 재사용
# (FK 제약 때문에 임의 문자열은 INSERT 실패). test_overload_split.py 와 같은 패턴.
# ─────────────────────────────────────────────────────────────────────────────


def _seed_batch_and_task(
    db,
    *,
    run_label: str,
    process_name: str = "연선",
    sheath_color: str | None = None,
    sq_mm2: int = 95,
    customer_priority: int = 5,
    equipment_code: str = "ST-54BO1",
) -> tuple[int, int]:
    """ProductionBatch + ScheduleTask 1쌍을 INSERT + commit.

    반환: (batch_id, task_id). run_label 은 cleanup 용 식별자.
    """
    batch = ProductionBatch(
        run_label=run_label,
        process_name=process_name,
        batch_seq=1,
        drum_count=1,
        drum_length_m=9700,
        total_length_m=9700,
        sq_mm2=sq_mm2,
        core_count=1,
        equipment_code=equipment_code,
        sheath_color=sheath_color,
        due_date=date.today() + timedelta(days=30),
        customer_priority=customer_priority,
        line_speed_mpm=11.7,
        setup_time_min=30,
        estimated_duration_min=60,
        status="planned",
        batch_group=f"ST-{sq_mm2}-0.6/1kV-{run_label}",
    )
    db.add(batch)
    db.flush()

    task = ScheduleTask(
        batch_id=batch.batch_id,
        equipment_code=equipment_code,
        start_datetime=datetime(2026, 4, 20, 9, 0),
        end_datetime=datetime(2026, 4, 20, 12, 0),
        status="scheduled",
        run_label=run_label,
        batch_group=batch.batch_group,
    )
    db.add(task)
    db.commit()
    return batch.batch_id, task.task_id


def _cleanup_seed(db, run_label: str) -> None:
    """run_label 기반 task/batch 정리 — rollback 이 commit 후 데이터를 못 지우므로 수동."""
    db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).delete(
        synchronize_session=False
    )
    db.commit()


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


# ─────────────────────────────────────────────────────────────────────────────
# P6: diff 응답 메타데이터 필드 확장 (batch_group / process_name / sheath_color /
# cross_section / is_urgent / customer_priority)
# ─────────────────────────────────────────────────────────────────────────────


def test_diff_includes_batch_metadata(db):
    """moved_tasks / added_tasks 에 batch 메타 필드 포함 확인.

    ProductionBatch + ScheduleTask seed → change_set 의 snapshot 에 task_id 넣고
    diff 호출 → 응답에 batch_group / process_name / cross_section / is_urgent
    가 실제 배치 값으로 채워져 있는지 검증.
    """
    run_label = f"TEST_DIFF_META_{uuid.uuid4().hex[:6]}"
    cs_id = None

    try:
        # Seed — moved 로 간주될 task (sheath_color 포함 — 시스 공정)
        _batch_id, task_id = _seed_batch_and_task(
            db,
            run_label=run_label,
            process_name="저압시스",
            sheath_color="흑",
            sq_mm2=95,
            customer_priority=5,  # <= 7 → is_urgent=True
        )
        task_id_str = str(task_id)

        snapshot_before = {
            task_id_str: {
                "start": "2026-04-20T09:00:00",
                "end": "2026-04-20T12:00:00",
                "equipment_code": "ST-54BO1",
            }
        }
        snapshot_after = {
            task_id_str: {
                "start": "2026-04-20T13:00:00",  # +4h shift → moved
                "end": "2026-04-20T16:00:00",
                "equipment_code": "ST-54BO1",
            }
        }
        cs_id = _insert_change_set(
            db,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
        )

        r = client.get(f"/api/schedules/change-sets/{cs_id}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["moved"] == 1
        assert len(body["moved_tasks"]) == 1

        moved = body["moved_tasks"][0]
        assert moved["task_id"] == task_id_str
        # 하위 호환 필드 — 기존 테스트가 요구하는 start/end delta 등은 그대로.
        assert moved["start_delta_hours"] == 4.0
        assert moved["equipment_changed"] is False
        # P6 신규 메타 필드
        assert moved["batch_group"] == f"ST-95-0.6/1kV-{run_label}"
        assert moved["process_name"] == "저압시스"
        assert moved["sheath_color"] == "흑"
        assert moved["cross_section"] == 95
        assert moved["is_urgent"] is True
        assert moved["customer_priority"] == 5
    finally:
        if cs_id is not None:
            _delete_change_set(db, cs_id)
        _cleanup_seed(db, run_label)


def test_diff_meta_null_for_unknown_task_id(db):
    """숫자가 아닌 task_id (T1 등) 는 DB 조회 불가 → 메타 필드 null.

    legacy snapshot 이나 synthetic id 를 가진 change_set 이 오면 시각화는 포기하고
    스키마만 유지한다. 500/404 로 깨지지 않아야 함.
    """
    snapshot_before = {
        "SYNTHETIC_XYZ": {
            "start": "2026-04-20T09:00:00",
            "end": "2026-04-20T12:00:00",
            "equipment_code": "EQ_FAKE",
        }
    }
    snapshot_after = {
        "SYNTHETIC_XYZ": {
            "start": "2026-04-20T10:00:00",
            "end": "2026-04-20T13:00:00",
            "equipment_code": "EQ_FAKE",
        }
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
        moved = body["moved_tasks"][0]
        # 메타 필드는 null / False 로 채워짐 (스키마 일관성).
        assert moved["batch_group"] is None
        assert moved["process_name"] is None
        assert moved["sheath_color"] is None
        assert moved["cross_section"] is None
        assert moved["is_urgent"] is False
        assert moved["customer_priority"] is None
    finally:
        _delete_change_set(db, cs_id)


def test_diff_meta_added_and_removed_blocks(db):
    """added_tasks / removed_tasks 에도 동일한 메타 필드가 붙는지 확인.

    added: after 에만 존재 — DB 에 실존하는 task 라면 메타 채워짐.
    removed: before 에만 존재 — DB 에 없으면 (삭제된 경우) 메타 null.
    """
    run_label = f"TEST_DIFF_META_AR_{uuid.uuid4().hex[:6]}"
    cs_id = None

    try:
        _batch_id, task_id = _seed_batch_and_task(
            db,
            run_label=run_label,
            process_name="연선",
            sheath_color=None,  # 연선 공정은 색 없음
            sq_mm2=120,
            customer_priority=99,  # > 7 → is_urgent=False
        )
        task_id_str = str(task_id)

        # added: 새로 생긴 task (before 없음)
        snapshot_before = {}
        snapshot_after = {
            task_id_str: {
                "start": "2026-04-20T13:00:00",
                "end": "2026-04-20T16:00:00",
                "equipment_code": "ST-54BO1",
            }
        }
        cs_id = _insert_change_set(
            db,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
        )

        r = client.get(f"/api/schedules/change-sets/{cs_id}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["added"] == 1
        added = body["added_tasks"][0]
        assert added["task_id"] == task_id_str
        assert added["process_name"] == "연선"
        assert added["cross_section"] == 120
        assert added["sheath_color"] is None  # 연선은 색 메타 없음
        assert added["is_urgent"] is False  # priority 99 → 일반
        assert added["customer_priority"] == 99

        # removed 경로: synthetic id — DB 없으므로 메타 null
        snapshot_before2 = {
            "999999": {  # 숫자지만 DB 에 없음
                "start": "2026-04-20T09:00:00",
                "end": "2026-04-20T12:00:00",
                "equipment_code": "ST-54BO1",
            }
        }
        cs_id2 = _insert_change_set(
            db,
            snapshot_before=snapshot_before2,
            snapshot_after={},
        )
        try:
            r2 = client.get(f"/api/schedules/change-sets/{cs_id2}/diff")
            assert r2.status_code == 200
            body2 = r2.json()
            assert body2["summary"]["removed"] == 1
            removed = body2["removed_tasks"][0]
            assert removed["task_id"] == "999999"
            # DB 에 없으므로 메타는 전부 null/False
            assert removed["batch_group"] is None
            assert removed["process_name"] is None
            assert removed["is_urgent"] is False
        finally:
            _delete_change_set(db, cs_id2)
    finally:
        if cs_id is not None:
            _delete_change_set(db, cs_id)
        _cleanup_seed(db, run_label)
