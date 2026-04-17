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
from app.services.batch_group_lifecycle import (
    unassign_batch_group,
    BatchGroupReasonError,
    VALID_REASONS,
)


# schedule_task.equipment_code가 equipment_master FK라서 실재 코드를 써야 한다.
# 테스트 seed 단계에서 실 장비를 "소유"하지 않으므로 공정 무관하게 아무 FK-valid 코드 3개로 충분.
_REAL_EQUIPMENT_CODES = ["DS-C11D", "DS-N11D", "DS-A11D"]


def _seed_planned_group(
    db: Session, batch_group: str = "test-group-1", with_wip: bool = False
) -> list[int]:
    """연선·절연·시스 3공정 planned 배치 시드 (flush만; rollback으로 정리)."""
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
            wip_matched_id=None,  # WIP FK — 테스트 DB에 wip row 없이 매칭 에러 검증은 별도 단위 로직에서
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
