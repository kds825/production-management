"""02_past_due_skew — most sales orders past due (tardiness cascade).

Scenario: 4 sales orders on same 저압절연 line, 3 of them already past
due at base_date (2026-04-21) by varying amounts. Exercises the
past-due severity / EDD cascade logic in `cp_sat_schedule`.

Mutates tables (all are run_label-scoped):
    production_batch, sales_order

Source test: `backend/tests/test_edd_mixed_pastdue_ontime.py` (adapted —
drops the `auto_schedule` call; this script only writes rows).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_02_past_due_skew"


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
    """3 past-due + 1 on-time 혼합. 모두 저압절연 1라인 경합."""
    _reset(db)

    # (order_id, sq_mm2, due_date, total_len)  — base_date = 2026-04-21
    rows = [
        ("SO-PD-01", 150, date(2026, 4, 10), 2500.0),  # ~8 근무일 past
        ("SO-PD-02", 120, date(2026, 4, 15), 2000.0),  # ~4 근무일 past
        ("SO-PD-03", 95, date(2026, 4, 17), 1500.0),  # ~2 근무일 past
        ("SO-PD-04", 70, date(2026, 6, 1), 500.0),  # on-time (well future)
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
                customer_name="parity-pd",
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
                customer_name="parity-pd",
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
    # WARNING: mutates DB. See module docstring.
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        seed(db)
        print(f"seeded {RUN_LABEL}")
    finally:
        db.close()
