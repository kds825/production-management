"""Past-due 심각도 기반 가중치 테스트.

Why: 기존 tardiness soft 공식 `weight × max(0, e - due_wmin)` 은 past-due 시
`weight × (e + |past|) = weight × e + const` 로 전개되어 |past| 상수항이
argmin 에 기여하지 못함. 같은 priority 의 past-due 그룹들은 모두 `weight × e`
로 동일 gradient → 솔버가 WSPT (짧은 작업 먼저) 로 정렬 → 가장 길게 밀린
그룹이 맨 뒤로 가는 역전 발생.

수정 후: `weight_effective = weight × (1 + past_days / K)` 을 group_meta
weight 에 반영. past 가 클수록 gradient 가 커져 solver 가 앞으로 끌어당김.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN = "test-past-due-severity"


@pytest.fixture
def seeded_past_due_pair(db):
    """저압절연 EX-B100 단일 설비, 같은 SQ 두 그룹은 그룹 키 충돌로 1개로 합쳐지므로
    서로 다른 SQ + 동일 priority 구성으로 past-due 2건을 만든다.

    - 120SQ: 납기 2026-04-01 (today 2026-04-20 기준 대략 past 14 근무일)
    - 300SQ: 납기 2026-04-17 (past 약 3 근무일)
    둘 다 normal priority. 같은 EX-B100 공유 → 순서 결정 강제.
    기대: 120SQ (더 심각한 past-due) 가 먼저 끝남.
    """
    rows = [
        ("SO-PAST-HIGH", 120, date(2026, 4, 1)),  # 14일 과거
        ("SO-PAST-LOW", 300, date(2026, 4, 17)),  # 3일 과거
    ]
    for order_id, sq, due in rows:
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
                drum_length_m=400,
                drum_count=1,
                ordered_qty_m=400,
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
                total_length_m=400,
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


def test_more_past_due_finishes_earlier(db, seeded_past_due_pair):
    """14일 과거 > 3일 과거 → 더 심각한 과거가 먼저 종료되어야 함."""
    high = db.execute(
        select(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .where(
            ScheduleTask.run_label == RUN,
            ProductionBatch.sales_order_id == "SO-PAST-HIGH",
        )
    ).scalar_one_or_none()
    low = db.execute(
        select(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .where(
            ScheduleTask.run_label == RUN,
            ProductionBatch.sales_order_id == "SO-PAST-LOW",
        )
    ).scalar_one_or_none()

    assert high is not None, "14일 과거 그룹 task 미생성"
    assert low is not None, "3일 과거 그룹 task 미생성"
    assert high.end_datetime <= low.end_datetime, (
        f"심각도 역전: 14일 과거(120SQ) end={high.end_datetime} > "
        f"3일 과거(300SQ) end={low.end_datetime}. "
        "심각도 기반 가중치가 적용되지 않음."
    )
