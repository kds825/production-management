"""On-time slack-weighted EDD 테스트 — 납기 임박 그룹이 앞으로 오는지 검증.

Why: 기존 `tardiness_hard=True` 는 on-time 그룹(e ≤ due) 을 hard constraint 로 강제만
할 뿐, 종료 시점에 가중치를 두지 않음 → 슬랙 3일 vs 13일을 동일 취급. 결과적으로
같은 설비를 공유하는 경우 납기 여유 있는 그룹이 납기 임박 그룹보다 앞에 배치되는
현상이 관찰됨 (사용자 KBI PoC 케이스: 150SQ 납기 4/24 vs 300SQ 납기 5/11 이 동일
EX-B100 에서 300SQ 가 먼저 배치됨).

수정 후: 각 그룹에 `weight × (end - base)` 항을 추가. weight ∝ 1/slack_min → 납기
임박한 그룹일수록 큰 페널티 → solver 가 end 를 작게 하려 앞쪽에 배치.

테스트 전략: 저압절연은 EX-B100 단일 설비만 eligible → 두 SQ 그룹이 같은 설비를
공유할 수밖에 없음 → 순서가 의미를 가짐. 연선은 stranding_method 에 따라 설비
후보가 달라져 tie-break 약함 → 본 테스트는 절연으로 단순화.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "test-slack-edd"


@pytest.fixture
def seeded_slack_edd(db):
    """저압절연 공정, SQ 다른 두 그룹 — 납기 차이 13일.

    - 150SQ: 납기 2026-04-24 (slack ~4 근무일)
    - 300SQ: 납기 2026-05-11 (slack ~17 근무일)
    둘 다 EX-B100 단일 설비 공유 → CP-SAT 에서 순서 결정 강제됨.
    """
    rows = [
        ("SO-SLACK-NEAR", 150, date(2026, 4, 24)),
        ("SO-SLACK-FAR", 300, date(2026, 5, 11)),
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
                drum_length_m=300,
                drum_count=1,
                ordered_qty_m=300,
                run_label=RUN_LABEL,
            )
        )
        db.add(
            ProductionBatch(
                run_label=RUN_LABEL,
                sales_order_id=order_id,
                sales_order_line=1,
                process_name="저압절연",
                batch_seq=1,
                drum_count=1,
                total_length_m=300,
                sq_mm2=sq,
                sheath_color="흑",
                customer_name="test-customer",
                due_date=due,
                voltage="저압",
                conductor_material="CU",
                product_group="CV",
                status="planned",
                batch_group="",
            )
        )
    db.flush()

    from app.application.scheduling.greedy.auto_schedule import auto_schedule
    auto_schedule(run_label=RUN_LABEL, db=db)
    db.flush()


def test_near_due_starts_before_far_due(db, seeded_slack_edd):
    """150SQ(납기 4/24) 가 300SQ(납기 5/11) 보다 먼저 시작해야 함."""
    near = db.execute(
        select(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .where(
            ScheduleTask.run_label == RUN_LABEL,
            ProductionBatch.sales_order_id == "SO-SLACK-NEAR",
        )
    ).scalar_one_or_none()
    far = db.execute(
        select(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .where(
            ScheduleTask.run_label == RUN_LABEL,
            ProductionBatch.sales_order_id == "SO-SLACK-FAR",
        )
    ).scalar_one_or_none()

    assert near is not None, "150SQ(근접 납기) 스케줄 task 미생성"
    assert far is not None, "300SQ(원거리 납기) 스케줄 task 미생성"
    assert near.start_datetime < far.start_datetime, (
        "EDD 위반: 납기 임박(150SQ, due 4/24) 이 납기 여유(300SQ, due 5/11) 보다 "
        f"나중에 시작됨. 150SQ start={near.start_datetime}, 300SQ start={far.start_datetime}"
    )
