"""schedule_optimizer 가 ScheduleTask.predecessor_task_id 를 채우는지 검증.

현 버그: predecessor_task_id 컬럼은 nullable FK 로 선언되어 있으나 task 생성 시
인자로 전달되지 않아 항상 NULL 로 저장 → API `predecessors: []` 항상 빈 배열.

수정 후: 단일 task 분기(~line 944) + split-task 분기(~line 1364) 둘 다에서
`predecessor_map[(rep.sales_order_id, rep.sales_order_line)]` 값을 FK 로 기록.
"""

import pytest
from datetime import date
from sqlalchemy import select

from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder


RUN_LABEL_SERIAL = "test-predecessor-serial"
RUN_LABEL_61 = "test-predecessor-61core"


@pytest.fixture
def seeded_serial_chain(db):
    """단심 주문 1건: 연선→저압절연→저압시스 3개 ProductionBatch 생성 후 optimize."""
    order = SalesOrder(
        order_id="SO-SERIAL-TEST",
        order_line=1,
        order_status="대기",
        product_group="CV",
        voltage="저압",
        spec_raw="CV 120SQ",
        customer_name="test-customer",
        due_date=date(2026, 5, 1),
        core_count=1,
        sheath_color="흑",
        drum_length_m=1000,
        drum_count=1,
        ordered_qty_m=1000,
        run_label=RUN_LABEL_SERIAL,
    )
    db.add(order)
    db.flush()

    for proc in ("연선", "저압절연", "저압시스"):
        db.add(
            ProductionBatch(
                run_label=RUN_LABEL_SERIAL,
                sales_order_id=order.order_id,
                sales_order_line=order.order_line,
                process_name=proc,
                batch_seq=1,
                drum_count=1,
                total_length_m=1000,
                sq_mm2=120,
                sheath_color="흑",
                customer_name="test-customer",
                due_date=order.due_date,
                voltage="저압",
                conductor_material="CU",
                product_group="CV",
                status="planned",
            )
        )
    db.flush()

    from app.services.schedule_optimizer import auto_schedule

    auto_schedule(run_label=RUN_LABEL_SERIAL, db=db)
    db.flush()
    return order


def test_serial_chain_predecessor_populated(db, seeded_serial_chain):
    stmt = (
        select(ScheduleTask)
        .where(ScheduleTask.run_label == RUN_LABEL_SERIAL)
        .order_by(ScheduleTask.start_datetime)
    )
    tasks = db.execute(stmt).scalars().all()
    assert len(tasks) >= 2, (
        "직렬 체인 테스트는 최소 2개 task 필요 (연선/절연/시스 중 2개 이상)"
    )
    assert tasks[0].predecessor_task_id is None, "첫 task 는 predecessor 없어야 함"
    # 그 외에는 predecessor_task_id 가 채워져 있어야 함 (중간 누락도 잡아냄)
    downstream_with_pred = [t for t in tasks[1:] if t.predecessor_task_id is not None]
    assert len(downstream_with_pred) >= 1, (
        "예상: 2번째 이후 task 중 최소 1개는 predecessor_task_id 를 가져야 함. "
        f"실제 predecessor_task_id 값: {[t.predecessor_task_id for t in tasks]}"
    )


@pytest.fixture
def seeded_61core_chain(db):
    """61연선: CORE(batch_seq=0) + ST(batch_seq=1) + 저압절연 + 저압시스."""
    order = SalesOrder(
        order_id="SO-61CORE-TEST",
        order_line=1,
        order_status="대기",
        product_group="CV",
        voltage="저압",
        spec_raw="CV 120SQ",
        customer_name="test-customer",
        due_date=date(2026, 5, 1),
        core_count=1,
        sheath_color="흑",
        drum_length_m=1000,
        drum_count=1,
        ordered_qty_m=1000,
        run_label=RUN_LABEL_61,
    )
    db.add(order)
    db.flush()

    shared = dict(
        run_label=RUN_LABEL_61,
        sales_order_id=order.order_id,
        sales_order_line=order.order_line,
        drum_count=1,
        total_length_m=1000,
        sq_mm2=120,
        sheath_color="흑",
        customer_name="test-customer",
        due_date=order.due_date,
        voltage="저압",
        conductor_material="CU",
        product_group="CV",
        status="planned",
    )
    db.add_all(
        [
            ProductionBatch(
                process_name="연선", batch_seq=0, batch_group="CORE-120-저압", **shared
            ),
            ProductionBatch(process_name="연선", batch_seq=1, **shared),
            ProductionBatch(process_name="저압절연", batch_seq=1, **shared),
            ProductionBatch(process_name="저압시스", batch_seq=1, **shared),
        ]
    )
    db.flush()

    from app.services.schedule_optimizer import auto_schedule

    auto_schedule(run_label=RUN_LABEL_61, db=db)
    db.flush()
    return order


def test_61_core_st_chain_predecessor_populated(db, seeded_61core_chain):
    stmt = select(ScheduleTask).where(ScheduleTask.run_label == RUN_LABEL_61)
    tasks = db.execute(stmt).scalars().all()
    assert len(tasks) > 0, "61연선 체인이 스케줄링되지 않음"
    # 최소 하나 이상의 task 는 predecessor_task_id 가 채워져 있어야 함
    with_pred = [t for t in tasks if t.predecessor_task_id is not None]
    assert len(with_pred) >= 1, (
        f"61연선 체인의 어떤 task 도 predecessor_task_id 를 가지지 않음. "
        f"task count={len(tasks)}, batch_groups={[t.batch_group for t in tasks]}"
    )
