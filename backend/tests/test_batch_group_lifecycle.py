"""batch_group 미배정/복원 서비스 단위 테스트 (Task 2.1).

의도: unassign_batch_group 서비스의 flush-only 시맨틱과 reason 태깅 계약을 검증.
- conftest.py의 db 픽스처는 Supabase Postgres 세션을 쓰며, teardown에서 rollback.
  따라서 seed/검증 경로에서 db.commit()을 호출하면 실 데이터에 유출된다 → 전 구간 flush만 사용.
- 서비스 역시 commit하지 않고 flush만 한다 (Eng Critical #1: 라우트가 commit 책임).
"""

import pytest
from datetime import datetime
from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.wip_inventory import WipInventory
from app.services.batch_group_lifecycle import (
    unassign_batch_group,
    restore_batch_group,
    BatchGroupReasonError,
    BatchGroupStatusError,
    BatchGroupNotFoundError,
    BatchGroupWipMatchedError,
    VALID_REASONS,
)


# schedule_task.equipment_code가 equipment_master FK라서 실재 코드를 써야 한다.
# 테스트 seed 단계에서 실 장비를 "소유"하지 않으므로 공정 무관하게 아무 FK-valid 코드 3개로 충분.
_REAL_EQUIPMENT_CODES = ["DS-C11D", "DS-N11D", "DS-A11D"]


def _seed_planned_group(
    db: Session, batch_group: str = "test-group-1", with_wip: bool = False
) -> list[int]:
    """연선·절연·시스 3공정 planned 배치 시드 (flush만; rollback으로 정리).

    with_wip=True일 때는 wip_inventory row를 먼저 flush 해 FK-valid wip_id를 확보한 뒤
    batch.wip_matched_id에 채워 WIP 차단 로직을 검증할 수 있게 한다.
    """
    wip_id: int | None = None
    if with_wip:
        wip = WipInventory(
            process_stage="연선재고",
            spec="25SQ",
            status="사용가능",
            run_label=f"rl-{batch_group}",
        )
        db.add(wip)
        db.flush()
        wip_id = wip.wip_id

    batches: list[int] = []
    for i, process in enumerate(["연선", "저압절연", "저압시스"]):
        b = ProductionBatch(
            run_label=f"rl-{batch_group}",
            sales_order_id="ORD-1",
            sales_order_line=i + 1,
            process_name=process,
            batch_seq=i + 1,
            status="planned",
            spec_raw="25SQ",
            sq_mm2=25,
            total_length_m=1000.0,
            drum_count=1,
            drum_length_m=1000.0,
            batch_group=batch_group,
            wip_matched_id=wip_id,  # None이거나 실 wip_id (FK 준수)
        )
        db.add(b)
        db.flush()
        t = ScheduleTask(
            batch_id=b.batch_id,
            equipment_code=_REAL_EQUIPMENT_CODES[i],
            start_datetime=datetime(2026, 4, 20, 8 + i * 2, 0),
            end_datetime=datetime(2026, 4, 20, 10 + i * 2, 0),
            status="planned",
            batch_group=batch_group,
            run_label=f"rl-{batch_group}",
        )
        db.add(t)
        batches.append(b.batch_id)
    db.flush()
    return batches


def test_unassign_with_reason_sets_status_and_reason(db: Session):
    """정상 reason으로 호출 시 batch/task status와 unassign_reason이 반영된다."""
    _seed_planned_group(db, "g1-unassign")
    result = unassign_batch_group(db, "g1-unassign", reason="자재지연")
    db.flush()

    assert result["batch_group"] == "g1-unassign"
    assert result["reason"] == "자재지연"
    assert result["idempotent"] is False
    batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == "g1-unassign")
        .all()
    )
    assert len(batches) == 3
    assert all(b.status == "unassigned" for b in batches)
    assert all(b.unassign_reason == "자재지연" for b in batches)
    tasks = (
        db.query(ScheduleTask).filter(ScheduleTask.batch_group == "g1-unassign").all()
    )
    assert len(tasks) == 3
    assert all(t.status == "unassigned" for t in tasks)
    # soft-delete: 시간/설비 메타데이터는 보존되어야 복원 가능
    assert all(t.equipment_code is not None for t in tasks)
    assert all(t.start_datetime is not None for t in tasks)


def test_unassign_accepts_scheduled_status(db: Session):
    """scheduled (레거시 default)도 planned와 동일하게 미배정 가능해야 한다.

    왜: 스케줄러(schedule_optimizer.py / cp_sat_optimizer.py)가 신규 배치를
    status='scheduled'로 저장하는 레거시 vocabulary를 쓰므로, unassign 경로가
    'planned' 만 허용하면 실제 데이터에서 전부 블록된다.
    """
    _seed_planned_group(db, "g-sch")
    # Flip all batches/tasks to scheduled to mimic scheduler output
    batches = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_group == "g-sch").all()
    )
    for b in batches:
        b.status = "scheduled"
    tasks = db.query(ScheduleTask).filter(ScheduleTask.batch_group == "g-sch").all()
    for t in tasks:
        t.status = "scheduled"
    db.flush()

    result = unassign_batch_group(db, "g-sch", reason="자재지연")
    db.flush()

    assert result["reason"] == "자재지연"
    assert result["idempotent"] is False
    batches_after = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_group == "g-sch").all()
    )
    assert all(b.status == "unassigned" for b in batches_after)


def test_unassign_without_reason_uses_default(db: Session):
    """reason=None으로 호출 시 기본값 '기타'가 쓰인다."""
    _seed_planned_group(db, "g-no-reason")
    result = unassign_batch_group(db, "g-no-reason", reason=None)
    db.flush()

    assert result["reason"] == "기타"
    batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == "g-no-reason")
        .all()
    )
    assert all(b.unassign_reason == "기타" for b in batches)


def test_unassign_invalid_reason_raises(db: Session):
    """허용 목록 외 reason은 BatchGroupReasonError로 거부된다."""
    _seed_planned_group(db, "g-bad-reason")
    with pytest.raises(BatchGroupReasonError):
        unassign_batch_group(db, "g-bad-reason", reason="해킹시도")
    # fixture teardown이 rollback을 돌리므로 여기서 명시 rollback은 불필요


def test_valid_reasons_contract():
    """VALID_REASONS는 4개 사유 + frozenset 계약을 유지한다."""
    assert VALID_REASONS == frozenset(["자재지연", "설비고장", "납기재협상", "기타"])
    # 불변성 — 오타성 mutation 방어
    with pytest.raises(AttributeError):
        VALID_REASONS.add("test")  # type: ignore[attr-defined]


def test_unassign_blocked_if_in_progress(db: Session):
    """in_progress 포함 시 BatchGroupStatusError + 상태 롤백 보존."""
    _seed_planned_group(db, "g2")
    b = db.query(ProductionBatch).filter(ProductionBatch.batch_group == "g2").first()
    # planned → in_progress 로 강제 전이해 서비스 측 상태 검증 분기를 태움
    b.status = "in_progress"
    db.flush()

    with pytest.raises(BatchGroupStatusError):
        unassign_batch_group(db, "g2", reason="자재지연")

    # 예외 후에도 상태는 원래대로 유지 — 서비스가 부분 변경을 남기지 않아야 한다.
    b_after = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_id == b.batch_id).first()
    )
    assert b_after.status == "in_progress"


def test_unassign_blocked_if_wip_matched(db: Session):
    """WIP 매칭된 batch_group은 BatchGroupWipMatchedError (재고 무결성)."""
    _seed_planned_group(db, "g-wip", with_wip=True)
    with pytest.raises(BatchGroupWipMatchedError):
        unassign_batch_group(db, "g-wip", reason="자재지연")


def test_unassign_idempotent(db: Session):
    """두번째 호출은 no-op, 원래 저장된 reason 유지 (멱등성)."""
    _seed_planned_group(db, "g3")
    unassign_batch_group(db, "g3", reason="설비고장")
    db.flush()

    # 이미 unassigned 상태 — 두 번째 호출은 다른 reason을 주더라도 원래 값을 지켜야 함
    result = unassign_batch_group(db, "g3", reason="자재지연")
    assert result["idempotent"] is True
    assert result["reason"] == "설비고장"  # 원래 사유 유지 (덮어쓰지 않음)
    assert result["affected_batches"] == []
    assert result["affected_tasks"] == []


def test_unassign_not_found(db: Session):
    """존재하지 않는 batch_group은 BatchGroupNotFoundError."""
    with pytest.raises(BatchGroupNotFoundError):
        unassign_batch_group(db, "nonexistent-xyz", reason="자재지연")


def test_unassign_preserves_audit_log(db: Session):
    """미배정 후에도 기존 audit_log 레코드는 보존된다 (soft-delete 핵심).

    왜: audit_log는 스케줄 결정 이력 — status 전이(unassigned)는 메타 로그이며,
    기존 기록을 지우면 소급 감사 불가능. soft-delete의 근간을 회귀로부터 지킨다.
    """
    from app.infrastructure.models.audit_log import AuditLog

    batch_ids = _seed_planned_group(db, "g4")
    task = db.query(ScheduleTask).filter(ScheduleTask.batch_id == batch_ids[0]).first()
    # AuditLog 실 컬럼에 맞게 필드 조정:
    #   PK = log_id, non-null = run_label/stage/action_type.
    #   plan 스니펫의 event_type/message 는 각각 action_type/decision_reason 로 매핑.
    audit = AuditLog(
        run_label="rl-g4",
        stage="stage1",
        batch_id=task.batch_id,
        task_id=task.task_id,
        action_type="TEST_SEED",
        decision_reason="seed audit for preservation test",
    )
    db.add(audit)
    db.flush()
    audit_id = audit.log_id  # PK 이름: log_id (audit_log 테이블 실 컬럼)

    unassign_batch_group(db, "g4", reason="자재지연")
    db.flush()

    audit_after = db.query(AuditLog).filter(AuditLog.log_id == audit_id).first()
    assert audit_after is not None
    assert audit_after.task_id == task.task_id
    assert audit_after.action_type == "TEST_SEED"


def test_restore_success_flips_status(db: Session):
    """unassign → restore 라운드트립: status 복원 + unassign_reason 초기화."""
    _seed_planned_group(db, "r1")
    unassign_batch_group(db, "r1", reason="자재지연")
    db.flush()

    result = restore_batch_group(db, "r1")
    db.flush()

    assert result["conflicts"] == []
    assert len(result["restored_tasks"]) == 3
    assert result["idempotent"] is False

    batches = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_group == "r1").all()
    )
    assert all(b.status == "planned" for b in batches)
    # unassign_reason은 복원 시 초기화
    assert all(b.unassign_reason is None for b in batches)

    tasks = db.query(ScheduleTask).filter(ScheduleTask.batch_group == "r1").all()
    assert all(t.status == "planned" for t in tasks)


def test_restore_blocked_if_original_slot_occupied(db: Session):
    """원래 equipment/시간에 다른 planned task가 있으면 409 — conflicts 반환, 무변경."""
    # r2를 시드 → unassign
    _seed_planned_group(db, "r2")
    unassign_batch_group(db, "r2", reason="자재지연")
    db.flush()

    # r3를 별도 시드 → 하나의 task를 r2의 첫 task 슬롯으로 이동
    _seed_planned_group(db, "r3")
    r2_first_task = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.batch_group == "r2")
        .order_by(ScheduleTask.task_id)
        .first()
    )
    r3_task = db.query(ScheduleTask).filter(ScheduleTask.batch_group == "r3").first()
    r3_task.equipment_code = r2_first_task.equipment_code
    r3_task.start_datetime = r2_first_task.start_datetime
    r3_task.end_datetime = r2_first_task.end_datetime
    db.flush()

    result = restore_batch_group(db, "r2")
    assert result["conflicts"] != []
    assert result["restored_tasks"] == []

    # 무변경 보증: r2는 여전히 unassigned
    batches = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_group == "r2").all()
    )
    assert all(b.status == "unassigned" for b in batches)


def test_restore_idempotent_on_planned(db: Session):
    """이미 planned인 batch_group에 restore 호출 → no-op, idempotent=True."""
    _seed_planned_group(db, "r-idempo")
    # unassign 없이 바로 restore
    result = restore_batch_group(db, "r-idempo")

    assert result["idempotent"] is True
    assert result["restored_tasks"] == []
    assert result["conflicts"] == []


def test_restore_not_found(db: Session):
    """batch_group 없음 → BatchGroupNotFoundError."""
    with pytest.raises(BatchGroupNotFoundError):
        restore_batch_group(db, "nonexistent-r")
