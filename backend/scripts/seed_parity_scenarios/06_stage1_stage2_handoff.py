"""06_stage1_stage2_handoff — stage1 plan frozen, stage2 builds on it.

Scenario interpretation (plan §Task 1.2 is underdefined for this name):
we model a two-run handoff where Stage-1 produced `parent_run_label` batches
already marked `scheduled` (i.e. not `'planned'` — filtered out by
`build_solver_input`), and Stage-2 now presents a new `run_label` whose
batches carry `parent_run_label` back-pointers to the frozen parent run.

The fixture captures two ingredients `build_solver_input` must handle:
 1. Stage-1 rows (parent_run_label=PARENT_RUN, status='scheduled') — these
    are in the DB but excluded from the Stage-2 `batches` list because the
    solver-input query filters `run_label == <current>` AND
    `status == 'planned'`.
 2. Stage-2 rows (run_label=RUN_LABEL, parent_run_label=PARENT_RUN, status=
    'planned') — these form the actual solver input.

Mutates tables (both run_labels, scoped):
    production_batch, sales_order

Source test: `backend/tests/test_stage1_update_versioning.py` (seed
helpers adapted to a standalone deterministic shape — no pytest fixtures).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

# Stage-1 (parent) + Stage-2 (child). 접두 8자리 → base_date 2026-04-21.
PARENT_RUN = "20260421_parity_06_stage1_parent"
RUN_LABEL = "20260421_parity_06_stage2_child"


def _reset(db: Session) -> None:
    for rl in (PARENT_RUN, RUN_LABEL):
        db.query(ScheduleTask).filter(ScheduleTask.run_label == rl).delete(
            synchronize_session=False
        )
        db.query(ProductionBatch).filter(ProductionBatch.run_label == rl).delete(
            synchronize_session=False
        )
        db.query(SalesOrder).filter(SalesOrder.run_label == rl).delete(
            synchronize_session=False
        )
    db.flush()


def _add_pair(
    db: Session,
    *,
    run_label: str,
    parent_run_label: str | None,
    order_id: str,
    process: str,
    sq: int,
    due: date,
    qty: float,
    status: str,
) -> None:
    db.add(
        SalesOrder(
            order_id=order_id,
            order_line=1,
            order_status="대기",
            product_group="CV",
            voltage="저압",
            spec_raw=f"CV {sq}SQ",
            customer_name="parity-handoff",
            due_date=due,
            customer_priority=10,
            core_count=1,
            sheath_color="흑",
            drum_length_m=qty,
            drum_count=1,
            ordered_qty_m=qty,
            run_label=run_label,
        )
    )
    db.add(
        ProductionBatch(
            run_label=run_label,
            parent_run_label=parent_run_label,
            sales_order_id=order_id,
            sales_order_line=1,
            process_name=process,
            batch_seq=1,
            drum_count=1,
            drum_length_m=qty,
            total_length_m=qty,
            sq_mm2=sq,
            core_count=1,
            sheath_color="흑",
            customer_name="parity-handoff",
            due_date=due,
            customer_priority=10,
            voltage="저압",
            conductor_material="CU",
            product_group="CV",
            status=status,
            batch_group="",
        )
    )


def seed(db: Session) -> None:
    _reset(db)

    # ── Stage-1 parent: 2 batches already 'scheduled' (frozen) ────────
    _add_pair(
        db,
        run_label=PARENT_RUN,
        parent_run_label=None,
        order_id="SO-HOFF-PARENT-1",
        process="연선",
        sq=120,
        due=date(2026, 5, 5),
        qty=2000.0,
        status="scheduled",
    )
    _add_pair(
        db,
        run_label=PARENT_RUN,
        parent_run_label=None,
        order_id="SO-HOFF-PARENT-2",
        process="저압절연",
        sq=120,
        due=date(2026, 5, 7),
        qty=2000.0,
        status="scheduled",
    )

    # ── Stage-2 child: 2 planned batches pointing back at parent ──────
    _add_pair(
        db,
        run_label=RUN_LABEL,
        parent_run_label=PARENT_RUN,
        order_id="SO-HOFF-CHILD-1",
        process="저압절연",
        sq=95,
        due=date(2026, 5, 11),
        qty=1500.0,
        status="planned",
    )
    _add_pair(
        db,
        run_label=RUN_LABEL,
        parent_run_label=PARENT_RUN,
        order_id="SO-HOFF-CHILD-2",
        process="저압시스",
        sq=95,
        due=date(2026, 5, 13),
        qty=1500.0,
        status="planned",
    )
    db.commit()


if __name__ == "__main__":
    # WARNING: mutates DB. See module docstring.
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        seed(db)
        print(f"seeded parent={PARENT_RUN} child={RUN_LABEL}")
    finally:
        db.close()
