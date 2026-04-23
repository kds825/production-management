"""09_single_batch — exactly 1 production_batch (minimal edge case).

Scenario: the absolutely smallest non-empty input — 1 sales_order, 1
production_batch. Guards against `len(batches) == 1` off-by-one paths
inside the solver-input sort / WIP-skip loops and the downstream
cp_sat_schedule group-count assumptions.

Mutates tables (all run_label-scoped):
    production_batch, sales_order

Source test: none (edge-case built minimally).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_09_single_batch"


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
    _reset(db)

    db.add(
        SalesOrder(
            order_id="SO-SINGLE-01",
            order_line=1,
            order_status="대기",
            product_group="CV",
            voltage="저압",
            spec_raw="CV 120SQ",
            customer_name="parity-single",
            due_date=date(2026, 5, 10),
            customer_priority=10,
            core_count=1,
            sheath_color="흑",
            drum_length_m=1000.0,
            drum_count=1,
            ordered_qty_m=1000.0,
            run_label=RUN_LABEL,
        )
    )
    db.add(
        ProductionBatch(
            run_label=RUN_LABEL,
            sales_order_id="SO-SINGLE-01",
            sales_order_line=1,
            process_name="저압절연",
            batch_seq=1,
            drum_count=1,
            drum_length_m=1000.0,
            total_length_m=1000.0,
            sq_mm2=120,
            core_count=1,
            sheath_color="흑",
            customer_name="parity-single",
            due_date=date(2026, 5, 10),
            customer_priority=10,
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
