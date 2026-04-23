"""07_calendar_edge — sales_order straddles weekend / holiday (calendar engine).

Scenario: 3 batches whose due_dates fall on or near weekend / known
holiday boundaries relative to the 2026-04-21 base_date:
  - due 2026-04-25 (Sat) — 주말
  - due 2026-05-05 (Tue) — 어린이날 공휴일 (공휴일은 `operation_calendar`
    에 'CAL-HOL' rule_code 행으로 등록된 경우만 계산에 반영)
  - due 2026-05-01 (Fri) — 근로자의 날

We also register a couple of `operation_calendar` rows for the holiday
scenario so the calendar_engine has something to consult. Seeding these
doesn't make `build_solver_input` behave differently (it only reads
ProductionBatch / EquipmentMaster / SpeedMaster / ConstraintConfig /
DrumLotMaster), but the calendar rows feed downstream solver paths
triggered by the same `run_label` in Task 1.4.

Mutates tables (all run_label-scoped or idempotent upsert):
    production_batch, sales_order, operation_calendar (rule_code-scoped)

Source tests: `backend/tests/test_calendar_engine_reverse.py` and
`test_calendar_engine_reverse_advance.py` (they operate on fixed
datetimes directly — adapted into a DB-seed form here).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.operation_calendar import OperationCalendar
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_07_calendar_edge"
_HOL_RULE = "CAL-HOL-PARITY07"

# Why explicit calendar_id: operation_calendar.calendar_id is autoincrement.
# Reserved parity range 990701~990702 (prefix 9907 = script #07) so re-runs
# produce identical PKs. _reset reclaims both before insert.
_PARITY_CAL_ID_LABOR = 990701
_PARITY_CAL_ID_CHILDREN = 990702


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
    db.query(OperationCalendar).filter(OperationCalendar.rule_code == _HOL_RULE).delete(
        synchronize_session=False
    )
    db.query(OperationCalendar).filter(
        OperationCalendar.calendar_id.in_(
            [_PARITY_CAL_ID_LABOR, _PARITY_CAL_ID_CHILDREN]
        )
    ).delete(synchronize_session=False)
    db.flush()


def seed(db: Session) -> None:
    _reset(db)

    # ── Holiday rows (scoped to _HOL_RULE so rest of calendar is untouched) ─
    db.add(
        OperationCalendar(
            calendar_id=_PARITY_CAL_ID_LABOR,
            rule_code=_HOL_RULE,
            rule_name="Labor Day 2026",
            day_of_week=None,
            working_hours=0,
            specific_date=date(2026, 5, 1),
            notes="parity fixture 07",
        )
    )
    db.add(
        OperationCalendar(
            calendar_id=_PARITY_CAL_ID_CHILDREN,
            rule_code=_HOL_RULE,
            rule_name="Children's Day 2026",
            day_of_week=None,
            working_hours=0,
            specific_date=date(2026, 5, 5),
            notes="parity fixture 07",
        )
    )

    # ── 3 batches straddling weekend / holiday ────────────────────────
    rows = [
        ("SO-CAL-01", "저압절연", 120, date(2026, 4, 25), 1000.0),  # Sat
        ("SO-CAL-02", "저압절연", 95, date(2026, 5, 1), 1000.0),  # Fri(holiday)
        ("SO-CAL-03", "저압절연", 70, date(2026, 5, 5), 1000.0),  # Tue(holiday)
    ]
    for order_id, proc, sq, due, qty in rows:
        db.add(
            SalesOrder(
                order_id=order_id,
                order_line=1,
                order_status="대기",
                product_group="CV",
                voltage="저압",
                spec_raw=f"CV {sq}SQ",
                customer_name="parity-cal",
                due_date=due,
                customer_priority=10,
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
                process_name=proc,
                batch_seq=1,
                drum_count=1,
                drum_length_m=qty,
                total_length_m=qty,
                sq_mm2=sq,
                core_count=1,
                sheath_color="흑",
                customer_name="parity-cal",
                due_date=due,
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
