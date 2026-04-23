"""03_urgent_reschedule — urgent order injected late (frozen_group_keys path).

Scenario: 2 previously-scheduled (frozen) batches + 1 newly-inserted
urgent batch with tighter due date. The two frozen batches already have
`ScheduleTask` rows whose start/end/equipment_code must be preserved by
the solver — hence they get `status='in_progress'` and a non-empty
`batch_group` the harness can pass to `cp_sat_schedule(frozen_group_keys=...)`.

The parity capture (Task 1.3) doesn't invoke the solver, but this DB
shape exercises the `frozen` field load and `status != 'planned'` skip
branches inside `build_solver_input`. (Recall: `build_solver_input`
filters `status == 'planned'`, so the frozen ones are intentionally
excluded from `batches`; that exclusion itself is part of parity.)

Mutates tables (all run_label-scoped):
    schedule_task, production_batch, sales_order

Source test: `backend/tests/test_urgent_reoptimize.py` — the C1 frozen-
in-progress regression fixture is the closest standalone analogue.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_03_urgent_reschedule"

# Frozen batches 는 base_date (= 2026-04-21 08:00) 근처에 고정 start 를
# 가져야 의미가 있다. 결정론 확보를 위해 전부 고정 상수.
_FROZEN_1_START = datetime(2026, 4, 21, 8, 0, 0)
_FROZEN_1_END = datetime(2026, 4, 21, 13, 0, 0)
_FROZEN_2_START = datetime(2026, 4, 22, 8, 0, 0)
_FROZEN_2_END = datetime(2026, 4, 22, 14, 0, 0)


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

    # ── 2 frozen (in_progress) + 1 urgent planned ─────────────────────
    # (order_id, process, sq, due, qty, status, batch_group, sched_start, sched_end)
    frozen_rows = [
        (
            "SO-URG-FROZEN-1",
            "저압절연",
            120,
            date(2026, 5, 10),
            1500.0,
            "in_progress",
            "BG_URG_FROZEN_1",
            _FROZEN_1_START,
            _FROZEN_1_END,
        ),
        (
            "SO-URG-FROZEN-2",
            "저압절연",
            95,
            date(2026, 5, 12),
            1200.0,
            "in_progress",
            "BG_URG_FROZEN_2",
            _FROZEN_2_START,
            _FROZEN_2_END,
        ),
    ]
    urgent_row = (
        "SO-URG-NEW-1",
        "저압절연",
        70,
        date(2026, 4, 24),
        800.0,
        "planned",
        "",
        None,
        None,
    )

    for order_id, proc, sq, due, qty, status, bg, sched_start, sched_end in [
        *frozen_rows,
        urgent_row,
    ]:
        db.add(
            SalesOrder(
                order_id=order_id,
                order_line=1,
                order_status="대기",
                product_group="CV",
                voltage="저압",
                spec_raw=f"CV {sq}SQ",
                customer_name="parity-urg",
                due_date=due,
                customer_priority=1 if order_id.startswith("SO-URG-NEW") else 10,
                core_count=1,
                sheath_color="흑",
                drum_length_m=qty,
                drum_count=1,
                ordered_qty_m=qty,
                run_label=RUN_LABEL,
            )
        )
        batch = ProductionBatch(
            run_label=RUN_LABEL,
            sales_order_id=order_id,
            sales_order_line=1,
            process_name=proc,
            batch_seq=1,
            equipment_code="EX-B100" if status == "in_progress" else None,
            drum_count=1,
            drum_length_m=qty,
            total_length_m=qty,
            sq_mm2=sq,
            core_count=1,
            sheath_color="흑",
            customer_name="parity-urg",
            due_date=due,
            customer_priority=1 if order_id.startswith("SO-URG-NEW") else 10,
            voltage="저압",
            conductor_material="CU",
            product_group="CV",
            status=status,
            batch_group=bg,
        )
        db.add(batch)
        db.flush()  # PK 확보 — ScheduleTask.batch_id FK 필요

        if sched_start is not None and status == "in_progress":
            db.add(
                ScheduleTask(
                    batch_id=batch.batch_id,
                    equipment_code="EX-B100",
                    start_datetime=sched_start,
                    end_datetime=sched_end,
                    status="in_progress",
                    run_label=RUN_LABEL,
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
