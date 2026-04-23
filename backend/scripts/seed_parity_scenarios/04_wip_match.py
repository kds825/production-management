"""04_wip_match — WIP inventory consumes some sales_order demand (WIP-skip branch).

Scenario: 3 production_batch rows; one is 연선 공정 but has
`wip_matched_id` pointing at a `wip_inventory` row with
`process_stage='연선재고'` → `build_solver_input` WIP-skip path should
mutate that batch's `status` to `'wip_complete'` and it should be excluded
from `batches` (counted in `wip_skipped`).

Mutates tables (all run_label-scoped):
    production_batch, sales_order, wip_inventory

Source test: `backend/tests/test_wip_matching_levels.py` (adapted — we
don't call `match_wip()`; we directly set `wip_matched_id` to force the
solver-side WIP-skip branch).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.wip_inventory import WipInventory

RUN_LABEL = "20260421_parity_04_wip_match"

# Why an explicit wip_id: wip_inventory.wip_id is autoincrement. Letting
# Supabase assign it means different PKs on successive re-runs → breaks the
# determinism contract (byte-identical DB state across re-runs). We reserve
# wip_id = 990404 ("99" = parity fixture range, "0404" = script #04) and
# reclaim it in _reset before inserting so the state is stable.
_PARITY_WIP_ID = 990404


def _reset(db: Session) -> None:
    # FK: sales_order.wip_id → wip_inventory, production_batch.wip_matched_id →
    # wip_inventory. 따라서 먼저 참조측 테이블을 정리.
    db.query(ScheduleTask).filter(ScheduleTask.run_label == RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(SalesOrder).filter(SalesOrder.run_label == RUN_LABEL).delete(
        synchronize_session=False
    )
    # run_label scope 뿐만 아니라 고정 PK 도 함께 정리 (이전 실행 잔여물 방지)
    db.query(WipInventory).filter(WipInventory.run_label == RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(WipInventory).filter(WipInventory.wip_id == _PARITY_WIP_ID).delete(
        synchronize_session=False
    )
    db.flush()


def seed(db: Session) -> None:
    _reset(db)

    # ── WIP 재고 1 건 (연선재고), 고정 PK ─────────────────────────────
    wip = WipInventory(
        wip_id=_PARITY_WIP_ID,  # 결정론: autoincrement 우회
        process_stage="연선재고",
        voltage_class="저압",
        material="CU",
        product_name="연선재고 150SQ",
        spec="150SQ",
        cross_section=150,
        length_m=1500.0,
        count=1,
        total_length_m=1500.0,
        status="사용가능",
        run_label=RUN_LABEL,
        expected_length_m=1500.0,
        actual_length_m=1500.0,
    )
    db.add(wip)
    db.flush()

    # ── 3 배치: [연선 wip-matched] + [저압절연] + [저압시스] ──────────
    rows = [
        # (order_id, process, sq, sheath, qty, wip_matched)
        ("SO-WIP-01", "연선", 150, None, 1500.0, _PARITY_WIP_ID),
        ("SO-WIP-02", "저압절연", 120, "흑", 2000.0, None),
        ("SO-WIP-03", "저압시스", 95, "청", 1000.0, None),
    ]
    for order_id, proc, sq, color, qty, wip_id in rows:
        db.add(
            SalesOrder(
                order_id=order_id,
                order_line=1,
                order_status="대기",
                product_group="CV",
                voltage="저압",
                spec_raw=f"CV {sq}SQ",
                customer_name="parity-wip",
                due_date=date(2026, 5, 15),
                customer_priority=10,
                core_count=1,
                sheath_color=color,
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
                sheath_color=color,
                customer_name="parity-wip",
                due_date=date(2026, 5, 15),
                customer_priority=10,
                voltage="저압",
                conductor_material="CU",
                product_group="CV",
                status="planned",
                batch_group="",
                wip_matched_id=wip_id,
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
