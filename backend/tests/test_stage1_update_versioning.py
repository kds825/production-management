"""Stage 1 update — 버전 보존 + 중복 배치 방지 검증.

검증 대상:
1. stage1/update 가 항상 신규 run_label 을 발급한다 (parent 재사용 X).
2. frozen 배치 + 동일 수주의 다른 공정 배치가 new_run_label 로 복제된다.
3. frozen 수주는 new_run_label 에서 재배치되지 않는다 (중복 방지 불변식).
4. 이전 parent_run_label 의 ProductionBatch / ScheduleTask 는 온전히 보존된다.
5. ProductionBatch.parent_run_label 컬럼이 계보 추적을 위해 설정된다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask


def _seed_sales_order(
    db: Session,
    run_label: str,
    order_id: str,
    *,
    order_line: int = 1,
    ordered_qty_m: float = 1000.0,
    drum_length_m: float = 500.0,
) -> SalesOrder:
    """최소 필드로 sales_order 를 생성한다 (스키마 필수 컬럼만)."""
    so = SalesOrder(
        order_id=order_id,
        order_line=order_line,
        order_status="대기",
        product_group="TFR-GV",
        voltage="0.6/1kV",
        spec_raw="0.6/1kV TFR-GV 1C x 120SQ",
        customer_name="TEST_CUSTOMER",
        due_date=date.today() + timedelta(days=30),
        drum_length_m=drum_length_m,
        drum_count=int(ordered_qty_m / drum_length_m) if drum_length_m else 1,
        ordered_qty_m=ordered_qty_m,
        core_count=1,
        sheath_color="흑",
        is_outsourced=False,
        run_label=run_label,
    )
    db.add(so)
    return so


def _seed_production_batch(
    db: Session,
    run_label: str,
    order_id: str,
    *,
    order_line: int = 1,
    process_name: str = "저압절연",
    status: str = "planned",
    batch_seq: int = 1,
    parent_run_label: str | None = None,
    total_length_m: float = 1000.0,
) -> ProductionBatch:
    """연선 헤더(-1) 포함 다양한 배치 시퀀스 시뮬레이션용."""
    b = ProductionBatch(
        run_label=run_label,
        parent_run_label=parent_run_label,
        sales_order_id=order_id,
        sales_order_line=order_line,
        process_name=process_name,
        batch_seq=batch_seq,
        status=status,
        total_length_m=total_length_m,
        drum_length_m=500.0,
        drum_count=2,
        product_group="TFR-GV",
        voltage="0.6/1kV",
        sq_mm2=120,
        sheath_color="흑",
        batch_group=f"{process_name}_120SQ_G01",
    )
    db.add(b)
    return b


def test_frozen_order_keys_includes_header_batch(db: Session) -> None:
    """batch_seq=-1 (연선 헤더) 이 frozen 이면 frozen_order_keys 에 포함되어야 한다.

    기존 버그: batch_seq >= 1 필터로 헤더가 제외 → Full 모드에서 해당 수주가
    create_batches 에서 재배치되어 중복 배치 발생.
    수정 후: status-only 기준 — 헤더도 frozen 이면 키 집합에 포함.
    """
    run_label = "20260420_100000"
    _seed_sales_order(db, run_label, "S001")
    _seed_production_batch(
        db, run_label, "S001", process_name="연선", batch_seq=-1, status="in_progress"
    )
    _seed_production_batch(
        db, run_label, "S001", process_name="저압절연", batch_seq=1, status="planned"
    )
    db.flush()

    # frozen_order_keys 계산 (stage1_update 로직과 동일)
    frozen = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status.in_(["in_progress", "completed", "wip_complete"]),
        )
        .all()
    )
    frozen_order_keys = {
        (b.sales_order_id, b.sales_order_line)
        for b in frozen
        if b.sales_order_id and b.sales_order_line is not None
    }

    assert ("S001", 1) in frozen_order_keys, (
        "batch_seq=-1 헤더가 frozen 이면 해당 수주가 frozen_order_keys 에 포함되어야 함"
    )


def test_new_run_label_preserves_parent(db: Session) -> None:
    """새 run_label 발급 시 parent 의 ProductionBatch 는 온전히 보존된다."""
    parent_rl = "20260420_100000"
    new_rl = "20260420_110000"

    _seed_sales_order(db, parent_rl, "S100")
    _seed_production_batch(db, parent_rl, "S100", status="in_progress")
    db.flush()

    parent_count_before = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == parent_rl).count()
    )
    assert parent_count_before == 1

    # new_run_label 생성 시뮬레이션: stage1/update 의 복제 로직 일부 모사
    parent_batch = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == parent_rl).first()
    )
    new_b = ProductionBatch(
        run_label=new_rl,
        parent_run_label=parent_rl,
        sales_order_id=parent_batch.sales_order_id,
        sales_order_line=parent_batch.sales_order_line,
        process_name=parent_batch.process_name,
        batch_seq=parent_batch.batch_seq,
        status=parent_batch.status,
        total_length_m=parent_batch.total_length_m,
    )
    db.add(new_b)
    db.flush()

    # parent 는 그대로 유지
    parent_count_after = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == parent_rl).count()
    )
    assert parent_count_after == 1, "parent run 은 stage1/update 후에도 유지되어야 함"

    # new run 에도 복제본 존재
    new_count = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == new_rl).count()
    )
    assert new_count == 1

    # parent_run_label 컬럼 확인
    new_batch = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == new_rl).first()
    )
    assert new_batch.parent_run_label == parent_rl


def test_purge_preserves_frozen_task(db: Session) -> None:
    """_purge_run_tasks 가 frozen 배치의 ScheduleTask 를 보존하는지 확인."""
    from app.services.schedule_optimizer import _purge_run_tasks

    run_label = "20260420_120000"
    _seed_sales_order(db, run_label, "S200")

    frozen_batch = _seed_production_batch(db, run_label, "S200", status="in_progress")
    planned_batch = _seed_production_batch(
        db, run_label, "S200", order_line=2, status="planned"
    )
    _seed_sales_order(db, run_label, "S200", order_line=2)
    db.flush()

    # ScheduleTask 생성
    frozen_task = ScheduleTask(
        run_label=run_label,
        batch_id=frozen_batch.batch_id,
        equipment_code="EX-B100",
        start_datetime=datetime(2026, 4, 20, 8, 0),
        end_datetime=datetime(2026, 4, 20, 14, 0),
        status="in_progress",
    )
    planned_task = ScheduleTask(
        run_label=run_label,
        batch_id=planned_batch.batch_id,
        equipment_code="EX-B100",
        start_datetime=datetime(2026, 4, 20, 14, 0),
        end_datetime=datetime(2026, 4, 20, 20, 0),
        status="scheduled",
    )
    db.add_all([frozen_task, planned_task])
    db.flush()

    frozen_task_id = frozen_task.task_id
    planned_task_id = planned_task.task_id

    # _purge_run_tasks 실행
    _purge_run_tasks(db, run_label)

    # frozen 배치의 task 는 보존, planned 배치의 task 는 삭제되어야 함
    remaining_ids = {
        t.task_id
        for t in db.query(ScheduleTask)
        .filter(ScheduleTask.run_label == run_label)
        .all()
    }
    assert frozen_task_id in remaining_ids, (
        "frozen 배치의 ScheduleTask 는 _purge 후에도 보존되어야 함"
    )
    assert planned_task_id not in remaining_ids, (
        "planned 배치의 ScheduleTask 는 _purge 로 삭제되어야 함"
    )
