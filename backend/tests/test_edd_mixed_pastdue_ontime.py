"""Mixed past-due / on-time 혼합 케이스에서 EDD 우선 유지 검증.

Why: 기존 두 테스트 (test_edd_over_wspt, test_past_due_severity) 는 각각
"둘 다 on-time" 또는 "둘 다 past-due" 경우만 커버한다. 실제 KBI PoC 에서
관찰된 역전 (54BO 1호 에서 past-due 150SQ 가 on-time 300SQ 뒤에 배치) 은
**혼합 케이스** — 한쪽은 past-due, 다른쪽은 on-time. 이 케이스에서:

  - past-due 그룹: `weight × (end + |past|)` soft penalty — argmin 은 end
    최소화로 유도
  - on-time 그룹: `e ≤ due_wmin` hard constraint + slack 항만 objective 에
    기여. tardiness 항 자체는 0
  - EDD pair penalty (weight 10,000) 만이 두 그룹 간 "납기 빠른 게 먼저" 를
    강제하는 유일한 cross-term

수학적으로 past-due 그룹의 tardiness weight (~1.8e5) × duration (~3000min) 이
EDD pair (10,000) 을 수만 배 압도하므로 solver 는 반드시 past-due 를 먼저
배치해야 한다. 이 테스트가 fail 하면 **solver input 자체가 잘못됐거나
(due_wmin, weight 오계산)**, **frozen 등 외부 제약이 개입**한 신호.

본 테스트는 cp_sat_schedule 직접 호출로 CP-SAT objective 단독 경로만 검증.
greedy fallback 은 별도 테스트가 커버.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

# run_label 에 날짜 prefix 포함 → cp_sat_schedule 이 base_date 를 2026-04-21 로
# 파싱. past_due 계산이 실행 시점과 무관하게 결정론적이 된다.
RUN = "20260421_edd_mixed"


@pytest.fixture
def seeded_edd_mixed(db):
    """저압절연 EX-B100 단일 설비 경합. past-due 긴 작업 vs on-time 짧은 작업.

    - past-due 긴: 150SQ, 2500m (~128min), 납기 2026-04-15 (past ~4 근무일)
    - on-time 짧: 300SQ, 500m  (~26min),  납기 2026-06-01 (on-time ~30+ 근무일)

    WSPT 이익 (짧은 거 먼저 → end 합 최소) 은 300SQ 먼저를 선호.
    EDD + past-due tardiness 는 150SQ 먼저를 강제 (tardiness soft 항이
    past-due 에서 `weight × end` 로 살아남아 argmin 에 기여).

    기대: 150SQ 가 먼저 시작.
    """
    rows = [
        ("SO-MIX-PAST-LONG", 150, date(2026, 4, 15), 2500.0),
        ("SO-MIX-ONTIME-SHORT", 300, date(2026, 6, 1), 500.0),
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

    # CP-SAT 경로 강제 — greedy fallback 은 이 버그 재현과 무관.
    # base_date 를 명시해 실행 시점 의존을 제거 (run_label parse 와 동일 값).
    from app.application.scheduling.cp_sat.orchestrator import cp_sat_schedule

    cp_sat_schedule(
        run_label=RUN,
        db=db,
        base_date=datetime(2026, 4, 21, 8, 0, 0),
    )
    db.flush()


def test_pastdue_starts_before_ontime_even_when_longer(db, seeded_edd_mixed):
    """past-due 긴 작업(150SQ) 이 on-time 짧은 작업(300SQ) 보다 먼저 시작해야 한다.

    실패 시 의미:
      - solver input 의 due_wmin / weight 가 예상과 다르거나 (→
        04_output/solver_snapshots/{run_label}.json 의 groups 섹션 확인)
      - on-time hard constraint 과 past-due soft tardiness 의 objective 균형이
        WSPT 이익을 이기지 못하는 상태 (→ _TARDINESS_WEIGHT / _PAST_SEVERITY_K
        스케일 재검토)
    """
    past_long = db.execute(
        select(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .where(
            ScheduleTask.run_label == RUN,
            ProductionBatch.sales_order_id == "SO-MIX-PAST-LONG",
        )
    ).scalar_one_or_none()
    ontime_short = db.execute(
        select(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .where(
            ScheduleTask.run_label == RUN,
            ProductionBatch.sales_order_id == "SO-MIX-ONTIME-SHORT",
        )
    ).scalar_one_or_none()

    assert past_long is not None, "past-due 긴 그룹 task 미생성"
    assert ontime_short is not None, "on-time 짧은 그룹 task 미생성"
    assert past_long.start_datetime < ontime_short.start_datetime, (
        f"Mixed-case EDD 역전: past-due(150SQ, due 4/15) start="
        f"{past_long.start_datetime} >= on-time(300SQ, due 6/1) start="
        f"{ontime_short.start_datetime}. "
        "past-due soft tardiness 가 WSPT 이익을 이기지 못하는 상태. "
        "04_output/solver_snapshots/ 에서 breakdown 섹션 확인 필요."
    )
