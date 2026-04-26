"""13_past_due_forced — 모든 batch 가 past-due (lex Phase A → T* > 0).

Scenario: 3 sales orders on 저압절연 line, ALL due_dates in the past relative
to base_date 2026-04-21. lex_min_time 호출 시 Phase A 가 max_tardiness > 0
을 반환하는 분기 + Phase B (makespan 최소) 가 실행되는 분기 검증용
(Phase 3 step 6).

weighted-sum baseline 도 동일 fixture 로 hash 고정. lex 와 weighted 의
schedule_task 결과가 어떻게 갈리는지는 Phase 5 §9.2 dual-run 비교 영역.

scenario 02_past_due_skew 와 다른 점: 02 는 3 past-due + 1 on-time 혼합
이지만, 13 은 모두 past-due — Phase A 가 어떤 그룹을 골라도 T* > 0 보장.

Mutates tables (all run_label-scoped):
    production_batch, sales_order
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_13_past_due_forced"


def _reset(db: Session) -> None:
    db.query(ScheduleTask).filter(ScheduleTask.run_label == RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(SalesOrder).filter(SalesOrder.run_label == RUN_LABEL).delete(
        synchronize_session=False
    )
    db.flush()


def seed(db: Session) -> None:
    """3 batches all on 저압절연 1라인, 납기 모두 past-due (lex T* > 0 강제)."""
    _reset(db)

    # base_date = 2026-04-21 (월). 모든 due 가 4/10 ~ 4/15 → 4 ~ 8 근무일 past.
    rows = [
        ("SO-PF-01", 150, date(2026, 4, 10), 2500.0),  # ~8 근무일 past
        ("SO-PF-02", 120, date(2026, 4, 13), 2000.0),  # ~6 근무일 past
        ("SO-PF-03", 95, date(2026, 4, 15), 1500.0),  # ~4 근무일 past
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
                customer_name="parity-pdf",
                due_date=due,
                customer_priority=5,
                core_count=1,
                sheath_color="흑",
                drum_length_m=qty,
                drum_count=1,
                ordered_qty_m=qty,
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
                drum_length_m=qty,
                total_length_m=qty,
                sq_mm2=sq,
                core_count=1,
                sheath_color="흑",
                customer_name="parity-pdf",
                due_date=due,
                customer_priority=5,
                voltage="저압",
                conductor_material="CU",
                product_group="CV",
                status="planned",
                batch_group="",
            )
        )
    db.commit()


if __name__ == "__main__":
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        seed(db)
        print(f"seeded {RUN_LABEL}")
    finally:
        db.close()
