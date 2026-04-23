"""05_sheath_color_chain — heavy sheath color clusters (chain constraints).

Scenario: 6 저압시스 batches across 3 colors (흑/청/갈) spanning two
ISO weeks. This exercises `_sheath_chain_key` 2차 정렬 경로in
`build_solver_input`'s post-sort (via the underlying `cp_sat_schedule`
load block — though the pure-function snapshot only captures the sort
order itself; the solver-side chain objective is touched via the
captured batch ordering).

Mutates tables (all run_label-scoped):
    production_batch, sales_order

Source test: `backend/tests/test_sheath_color_chain.py` (adapted to
standalone — the test uses in-memory `ProductionBatch` instances and
does not commit; here we commit run_label-scoped rows).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_05_sheath_color_chain"


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

    # (order_id, color, sq, due, batch_group)
    # W18 = 2026-04-27 주, W19 = 2026-05-04 주 — base_date 2026-04-21 이후
    # 두 주차에 걸쳐 3색상 혼합 → chain 정렬에서 [흑×2, 청×2, 갈×2] 순으로
    # 응집되는 게 정상 동작.
    rows = [
        ("SO-SC-01", "흑", 120, date(2026, 4, 27), "A120_흑_2026W18"),
        ("SO-SC-02", "흑", 95, date(2026, 5, 4), "A120_흑_2026W19"),
        ("SO-SC-03", "청", 120, date(2026, 4, 27), "A120_청_2026W18"),
        ("SO-SC-04", "청", 95, date(2026, 5, 4), "A120_청_2026W19"),
        ("SO-SC-05", "갈", 120, date(2026, 4, 27), "A120_갈_2026W18"),
        ("SO-SC-06", "갈", 95, date(2026, 5, 4), "A120_갈_2026W19"),
    ]
    for order_id, color, sq, due, bg in rows:
        db.add(
            SalesOrder(
                order_id=order_id,
                order_line=1,
                order_status="대기",
                product_group="CV",
                voltage="저압",
                spec_raw=f"CV {sq}SQ",
                customer_name="parity-chain",
                due_date=due,
                customer_priority=10,
                core_count=1,
                sheath_color=color,
                drum_length_m=1000.0,
                drum_count=1,
                ordered_qty_m=1000.0,
                run_label=RUN_LABEL,
            )
        )
        db.add(
            ProductionBatch(
                run_label=RUN_LABEL,
                sales_order_id=order_id,
                sales_order_line=1,
                process_name="저압시스",
                batch_seq=1,
                drum_count=1,
                drum_length_m=1000.0,
                total_length_m=1000.0,
                sq_mm2=sq,
                core_count=1,
                sheath_color=color,
                customer_name="parity-chain",
                due_date=due,
                customer_priority=10,
                voltage="저압",
                conductor_material="CU",
                product_group="CV",
                status="planned",
                batch_group=bg,
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
