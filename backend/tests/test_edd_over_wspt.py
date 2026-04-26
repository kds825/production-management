"""EDD (Earliest Due Date) 우선이 WSPT (Shortest-Job-First) 를 이기는지 검증.

Why: on-time 그룹 간엔 past-due tardiness 가 트리거 안 되고 slack weight 도
듀레이션 차이가 크면 WSPT 이익을 못 이김. 관찰 사례: 긴 작업(150SQ 51h,
납기 임박) 이 짧은 작업(300SQ 16h, 납기 여유) 뒤로 밀림.

수정 후: `_EDD_PAIR_WEIGHT` (10_000) 로 EDD 위반 쌍당 큰 penalty 부과 →
같은 설비 후보 그룹 간엔 납기 빠른 게 무조건 먼저.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN = "test-edd-over-wspt"


@pytest.fixture
def seeded_edd_wspt(db):
    """저압절연 EX-B100 단일 설비, 같은 공정에 긴-임박 vs 짧은-여유 구성.

    - 긴+임박: 150SQ, 2500m (~128min@19.5m/min), 납기 2026-04-24 (slack ~3d)
    - 짧은+여유: 300SQ, 500m (~26min), 납기 2026-05-11 (slack ~17d)
    WSPT: 짧은 게 먼저 (300SQ). EDD: 납기 빠른 게 먼저 (150SQ).
    기대 (EDD penalty 상향 후): 150SQ 가 300SQ 보다 먼저 시작.
    """
    rows = [
        ("SO-EDD-LONG-URGENT", 150, date(2026, 4, 24), 2500.0),
        ("SO-EDD-SHORT-LOOSE", 300, date(2026, 5, 11), 500.0),
    ]
    for order_id, sq, due, qty in rows:
        db.add(
            SalesOrder(
                order_id=order_id,
                order_line=1,
                order_status="대기",
                product_group="CV",
                voltage="저압",
                spec_raw=f"CV {sq}SQ",
                customer_name="test-customer",
                due_date=due,
                core_count=1,
                sheath_color="흑",
                drum_length_m=qty,
                drum_count=1,
                ordered_qty_m=qty,
                run_label=RUN,
            )
        )
        db.add(
            ProductionBatch(
                run_label=RUN,
                sales_order_id=order_id,
                sales_order_line=1,
                process_name="저압절연",
                batch_seq=1,
                drum_count=1,
                total_length_m=qty,
                sq_mm2=sq,
                sheath_color="흑",
                customer_name="test-customer",
                due_date=due,
                customer_priority=5,
                voltage="저압",
                conductor_material="CU",
                product_group="CV",
                status="planned",
                batch_group="",
            )
        )
    db.flush()

    from app.application.scheduling.greedy.auto_schedule import auto_schedule
    auto_schedule(run_label=RUN, db=db)
    db.flush()


def test_near_due_starts_before_far_due_even_if_longer(db, seeded_edd_wspt):
    """긴-임박(150SQ, 납기 4/24) 이 짧은-여유(300SQ, 납기 5/11) 보다 먼저 시작."""
    urgent = db.execute(
        select(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .where(
            ScheduleTask.run_label == RUN,
            ProductionBatch.sales_order_id == "SO-EDD-LONG-URGENT",
        )
    ).scalar_one_or_none()
    loose = db.execute(
        select(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .where(
            ScheduleTask.run_label == RUN,
            ProductionBatch.sales_order_id == "SO-EDD-SHORT-LOOSE",
        )
    ).scalar_one_or_none()

    assert urgent is not None, "긴-임박 그룹 task 미생성"
    assert loose is not None, "짧은-여유 그룹 task 미생성"
    assert urgent.start_datetime < loose.start_datetime, (
        f"EDD 위반: 납기 임박(150SQ, 4/24) start={urgent.start_datetime} "
        f">= 납기 여유(300SQ, 5/11) start={loose.start_datetime}. "
        "WSPT 가 EDD 를 이긴 상태 — _EDD_PAIR_WEIGHT 가 부족하거나 미적용."
    )
