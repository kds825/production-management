"""08_capacity_overflow — demand > equipment capacity (INFEASIBLE escalation).

Scenario: 20 저압절연 batches all with tight due dates (within ~5 근무일
of base_date 2026-04-21), sharing 1 sheath color to force the same
equipment line. This overloads any single-line capacity and should push
the solver toward INFEASIBLE (tardiness_hard=True) or heavy tardiness
penalty (soft). From `build_solver_input`'s POV: 20 rows in `batches`,
all 공정='저압절연', same batch_group keys empty → a clean stress test.

Mutates tables (all run_label-scoped):
    production_batch, sales_order

Source test: none (no existing stress-scale test — built minimally).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_08_capacity_overflow"
_N_BATCHES = 20


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

    # ── 20 batches, all 저압절연/흑색/120SQ, 납기 4/23~4/28 ──────────
    # 결정론 유지: enumerate index 로 due 를 선형 증분. datetime.now() 없음.
    base_due = date(2026, 4, 23)
    for i in range(_N_BATCHES):
        order_id = f"SO-CAP-{i:02d}"
        due = base_due + timedelta(days=i % 5)  # 5 근무일 range 반복
        qty = 1500.0 + (i * 100.0)  # 결정론적 선형 증가
        db.add(
            SalesOrder(
                order_id=order_id,
                order_line=1,
                order_status="대기",
                product_group="CV",
                voltage="저압",
                spec_raw="CV 120SQ",
                customer_name="parity-cap",
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
                sq_mm2=120,
                core_count=1,
                sheath_color="흑",
                customer_name="parity-cap",
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
        print(f"seeded {RUN_LABEL} ({_N_BATCHES} batches)")
    finally:
        db.close()
