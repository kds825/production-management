"""BatchStateBuffer + AuditLogBuffer unit tests (Phase 6 step 14).

Why:
    ProductionBatch state mutation 과 AuditLog INSERT 의 deferred bulk
    operation pattern 의 회귀 가드. 21s + 3s = 24s 단축의 안전성 확보.

본 테스트는 buffer 자체의 invariant 만 검증 — caller (apply_calendar_greedy)
적용은 Task 2-5 에서 진행. parity harness 가 caller 흐름 전체를 검증한다.
"""

from __future__ import annotations

from datetime import datetime  # noqa: F401  formatter unused-import sweep 회피

from app.application._shared.audit_logger import AuditLogBuffer
from app.application.scheduling.cp_sat._calendar_apply import BatchStateBuffer
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


def _seed_batches(db, run_label: str, n: int = 3) -> list[int]:
    """planned 상태 batch n 건 seed. 반환: batch_id 리스트."""
    ids = []
    for i in range(n):
        b = ProductionBatch(
            run_label=run_label,
            batch_seq=i,
            process_name="저압절연",
            sq_mm2=50,
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            sales_order_id=f"SO-BUF-{i}",
            sales_order_line=1,
            batch_group="",
            status="planned",
        )
        db.add(b)
        db.flush()
        ids.append(b.batch_id)
    return ids


def _seed_task(db, batch_id: int, run_label: str) -> int:
    """schedule_task seed (audit_log FK 충족용). 반환: task_id."""
    t = ScheduleTask(
        batch_id=batch_id,
        run_label=run_label,
        equipment_code="EX-B100",
        start_datetime=datetime(2026, 5, 18, 8, 0, 0),
        end_datetime=datetime(2026, 5, 18, 12, 0, 0),
        status="scheduled",
    )
    db.add(t)
    db.flush()
    return t.task_id


def test_buffer_set_then_flush_emits_one_update(db):
    """N batch mutation 누적 후 flush 시 commit 결과 모두 반영."""
    run_label = "test-bs-one-update"
    ids = _seed_batches(db, run_label, n=3)

    buf = BatchStateBuffer()
    for bid in ids:
        buf.set(bid, status="scheduled", equipment_code="EX-B100")

    n = buf.flush(db)
    assert n == 3

    db.expire_all()
    rows = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).all()
    )
    for r in rows:
        assert r.status == "scheduled"
        assert r.equipment_code == "EX-B100"


def test_buffer_set_overwrites_idempotent(db):
    """동일 batch_id 두 번 set → 마지막 값 채택."""
    run_label = "test-bs-overwrite"
    ids = _seed_batches(db, run_label, n=1)

    buf = BatchStateBuffer()
    # 두 번 set — 두 번째 값이 최종. equipment_code 는 equipment_master FK
    # 라 실제 존재하는 코드 사용 (EX-B100 = 저압절연 설비).
    buf.set(ids[0], status="planned", equipment_code=None)
    buf.set(ids[0], status="scheduled", equipment_code="EX-B100")
    n = buf.flush(db)
    assert n == 1

    db.expire_all()
    r = db.query(ProductionBatch).filter(ProductionBatch.batch_id == ids[0]).one()
    assert r.status == "scheduled"
    assert r.equipment_code == "EX-B100"


def test_buffer_flush_empty_is_noop(db):
    """빈 buffer flush → DB 호출 0회, return 0."""
    buf = BatchStateBuffer()
    n = buf.flush(db)
    assert n == 0


def test_buffer_partial_field_set(db):
    """일부 필드만 set → 그 필드만 UPDATE, 다른 필드 보존."""
    run_label = "test-bs-partial"
    ids = _seed_batches(db, run_label, n=1)
    orig = db.query(ProductionBatch).filter(ProductionBatch.batch_id == ids[0]).one()
    orig_seq = orig.batch_seq

    buf = BatchStateBuffer()
    buf.set(ids[0], status="scheduled")  # equipment_code 안 건드림
    buf.flush(db)

    db.expire_all()
    r = db.query(ProductionBatch).filter(ProductionBatch.batch_id == ids[0]).one()
    assert r.status == "scheduled"
    assert r.batch_seq == orig_seq


def test_buffer_rollback_via_savepoint(db):
    """SAVEPOINT _안_ 에서 buffer 생성·flush → rollback → mutation 무효."""
    run_label = "test-bs-rollback"
    ids = _seed_batches(db, run_label, n=2)

    sp = db.begin_nested()
    buf = BatchStateBuffer()
    for bid in ids:
        buf.set(bid, status="scheduled", equipment_code="EX-B100")
    buf.flush(db)

    sp.rollback()

    db.expire_all()
    rows = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).all()
    )
    for r in rows:
        assert r.status == "planned", "rollback 후 status='planned' 보존"
        assert r.equipment_code is None, "rollback 후 equipment_code 보존"


def test_audit_buffer_append_then_flush_emits_one_insert(db):
    """AuditLogBuffer 도 동일 패턴 — append N 후 flush 시 1 bulk_insert."""
    run_label = "test-au-one-insert"
    ids = _seed_batches(db, run_label, n=2)
    tids = [_seed_task(db, bid, run_label) for bid in ids]

    buf = AuditLogBuffer()
    for i, (bid, tid) in enumerate(zip(ids, tids)):
        buf.append(
            run_label=run_label,
            stage="stage2",
            action_type="test_action",
            batch_id=bid,
            task_id=tid,
            reason=f"test {i}",
        )
    n = buf.flush(db)
    assert n == 2

    from app.infrastructure.models.audit_log import AuditLog

    rows = db.query(AuditLog).filter(AuditLog.run_label == run_label).all()
    assert len(rows) == 2
    for r in rows:
        assert r.stage == "stage2"
        assert r.action_type == "test_action"
        assert r.decision_reason.startswith("test ")


def test_audit_buffer_flush_empty_is_noop(db):
    """빈 audit buffer flush → DB 호출 0회, return 0."""
    buf = AuditLogBuffer()
    n = buf.flush(db)
    assert n == 0
