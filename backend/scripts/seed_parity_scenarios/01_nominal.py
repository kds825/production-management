"""01_nominal — baseline mixed workload (nothing special).

Scenario: three routine sales orders spread across 연선 / 저압절연 / 저압시스,
all future-dated with healthy slack. No WIP, no urgent, no edge cases —
this is the canonical "happy path" parity reference.

Mutates tables (all are truncated, then repopulated):
    production_batch, sales_order

Source test: none (built minimally for the nominal case).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

# run_label 앞 8자리 = 2026-04-21 → build_solver_input 이 base_date 를
# datetime(2026, 4, 21, 8, 0, 0) 로 파싱. 실행 시점과 무관해 결정론 확보.
RUN_LABEL = "20260421_parity_01_nominal"


def _reset(db: Session) -> None:
    """run_label 범위로 한정해 기존 데이터만 제거 (전역 트렁케이트 금지).

    Why: 다른 parity 스크립트와 동일 테이블을 공유하므로, run_label 로만
    좁혀야 순서-독립적이고 병행 실행에 안전하다.
    """
    # FK: schedule_task → production_batch 순으로 역순 삭제.
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
    """세 건의 표준 수주 + 대응 배치 3개 (연선/저압절연/저압시스)."""
    _reset(db)

    # (order_id, process_name, sq_mm2, sheath_color, due_date, total_len)
    rows = [
        ("SO-PARITY-01-A", "연선", 120, None, date(2026, 5, 8), 3000.0),
        ("SO-PARITY-01-B", "저압절연", 95, "흑", date(2026, 5, 11), 2000.0),
        ("SO-PARITY-01-C", "저압시스", 70, "청", date(2026, 5, 14), 1500.0),
    ]
    for order_id, proc, sq, color, due, qty in rows:
        db.add(
            SalesOrder(
                order_id=order_id,
                order_line=1,
                order_status="대기",
                product_group="CV",
                voltage="저압",
                spec_raw=f"CV {sq}SQ",
                customer_name="parity-cust",
                due_date=due,
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
                customer_name="parity-cust",
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
    # ──────────────────────────────────────────────────────────────────
    # WARNING: mutates the DB pointed to by DATABASE_URL. Only safe on
    # local dev / scratch DBs. Harness invocations wrap this in SAVEPOINT.
    # ──────────────────────────────────────────────────────────────────
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        seed(db)
        print(f"seeded {RUN_LABEL}")
    finally:
        db.close()
