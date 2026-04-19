"""Task 14: POST /api/schedules/revert/{change_set_id} — Undo 경로.

세 가지 계약을 검증:
1) 404 — 존재하지 않는 change_set_id.
2) 200 — snapshot_before 로 start/end/equipment_code 복구 + change_set 삭제.
3) 409 — 본 change_set 이후 더 최근 change_set 이 존재 (freshness 충돌).

conftest.py 의 `db` 픽스처(실 DB + 세션 롤백) 를 공유하지만, revert 엔드포인트는
자체 요청 세션에서 commit 을 수행하므로 테스트가 만든 change_set 은 수동 cleanup.
"""

from datetime import timedelta
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.schedule_task import ScheduleTask

client = TestClient(app)


def test_revert_unknown_change_set_returns_404():
    """미존재 id → 404. DB 상태 변경 없음."""
    resp = client.post("/api/schedules/revert/nonexistent-xyz-does-not-exist")
    assert resp.status_code == 404


def test_revert_success_restores_snapshot(db):
    """change_set 한 건 INSERT + task 를 snapshot_after 상태로 미리 변경
    → revert 호출 → task 가 snapshot_before 상태로 복구되는지 확인."""
    existing = db.query(ScheduleTask).first()
    if existing is None:
        pytest.skip("no existing ScheduleTask to revert test against")

    original_start = existing.start_datetime
    original_end = existing.end_datetime
    original_eq = existing.equipment_code
    task_id_str = str(existing.task_id)

    cs_id = "cs-revert-success-test"
    cs = ScheduleChangeSet(
        change_set_id=cs_id,
        preview_request_id="req-1",
        snapshot_before={
            task_id_str: {
                "start": original_start.isoformat(),
                "end": original_end.isoformat(),
                "equipment_code": original_eq,
            }
        },
        snapshot_after={
            task_id_str: {
                "start": (original_start + timedelta(hours=1)).isoformat(),
                "end": (original_end + timedelta(hours=1)).isoformat(),
                "equipment_code": original_eq,
            }
        },
    )
    # 기존 change_set 잔여 정리 (이전 실패한 테스트 재실행 대비)
    prev = db.get(ScheduleChangeSet, cs_id)
    if prev is not None:
        db.delete(prev)
        db.commit()

    db.add(cs)
    # DB 값도 snapshot_after 상태로 선변경 — revert 대상.
    existing.start_datetime = original_start + timedelta(hours=1)
    existing.end_datetime = original_end + timedelta(hours=1)
    db.commit()

    try:
        resp = client.post(f"/api/schedules/revert/{cs_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reverted"] is True
        assert body["change_set_id"] == cs_id

        db.expire_all()
        refreshed = (
            db.query(ScheduleTask)
            .filter(ScheduleTask.task_id == existing.task_id)
            .first()
        )
        assert refreshed.start_datetime == original_start
        assert refreshed.end_datetime == original_end
        assert refreshed.equipment_code == original_eq

        # change_set 이 삭제되었는지 확인 (revert 는 1회 소비).
        assert db.get(ScheduleChangeSet, cs_id) is None
    finally:
        # 잔여 cleanup — revert 실패 시에도 테스트 DB 가 오염되지 않게.
        leftover = db.get(ScheduleChangeSet, cs_id)
        if leftover is not None:
            db.delete(leftover)
        # task 원본 복구 (테스트가 중간 실패한 경우 대비)
        t = (
            db.query(ScheduleTask)
            .filter(ScheduleTask.task_id == existing.task_id)
            .first()
        )
        if t is not None:
            t.start_datetime = original_start
            t.end_datetime = original_end
            t.equipment_code = original_eq
        db.commit()


def test_revert_conflict_when_newer_change_set_exists(db):
    """cs_old 이후 cs_new 가 생기면 cs_old 는 409 Conflict."""
    existing = db.query(ScheduleTask).first()
    if existing is None:
        pytest.skip("no existing ScheduleTask")

    task_id_str = str(existing.task_id)
    snap = {
        task_id_str: {
            "start": existing.start_datetime.isoformat(),
            "end": existing.end_datetime.isoformat(),
            "equipment_code": existing.equipment_code,
        }
    }

    cs_old_id = "cs-revert-conflict-old"
    cs_new_id = "cs-revert-conflict-new"
    # 이전 실행 잔여 정리
    for cid in (cs_old_id, cs_new_id):
        prev = db.get(ScheduleChangeSet, cid)
        if prev is not None:
            db.delete(prev)
    db.commit()

    cs1 = ScheduleChangeSet(
        change_set_id=cs_old_id,
        preview_request_id=None,
        snapshot_before=snap,
        snapshot_after=snap,
    )
    db.add(cs1)
    db.commit()
    # created_at 순서 보장 — utcnow() 는 ms 해상도지만 여유 sleep.
    time.sleep(0.05)
    cs2 = ScheduleChangeSet(
        change_set_id=cs_new_id,
        preview_request_id=None,
        snapshot_before=snap,
        snapshot_after=snap,
    )
    db.add(cs2)
    db.commit()

    try:
        resp = client.post(f"/api/schedules/revert/{cs_old_id}")
        assert resp.status_code == 409, resp.text
        assert cs_new_id in resp.text  # 최신 change_set_id 를 detail 에 노출
    finally:
        for cid in (cs_old_id, cs_new_id):
            leftover = db.get(ScheduleChangeSet, cid)
            if leftover is not None:
                db.delete(leftover)
        db.commit()
