"""12_all_due_met — 모든 batch 가 납기 충족 가능 (lex Phase A → T* = 0).

Scenario: 3 sales orders on 저압절연 line, all due_dates well in the future
(2026-06-01 onwards). base_date 2026-04-21 기준 약 30+ 근무일 여유 →
모든 그룹이 납기 충족 가능. lex_min_time 호출 시 Phase A 가 max_tardiness=0
을 반환하는 분기 검증용 (Phase 3 step 6).

weighted-sum 모드 baseline 도 동일 fixture 로 hash 고정 (parity harness 가
default 로 weighted-sum 호출 — Phase 3 step 6 에서는 본 fixture 의 결정론
적 통과를 검증).

Mutates tables (all run_label-scoped):
    production_batch, sales_order

Source: scenario 09_single_batch + 02_past_due_skew 패턴 종합.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_12_all_due_met"


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
    """3 batches all on 저압절연 1라인, 납기 모두 ample future (lex T* = 0 예상)."""
    _reset(db)

    # base_date = 2026-04-21 (월). 모든 due 가 6월 이후 → 30+ 근무일 여유.
    rows = [
        ("SO-DM-01", 150, date(2026, 6, 1), 2000.0),
        ("SO-DM-02", 120, date(2026, 6, 5), 1500.0),
        ("SO-DM-03", 95, date(2026, 6, 10), 1000.0),
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
                customer_name="parity-due-met",
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
                customer_name="parity-due-met",
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
