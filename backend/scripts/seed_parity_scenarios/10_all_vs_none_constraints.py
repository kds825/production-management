"""10_all_vs_none_constraints — run once with ALL constraints on, once with ALL off.

Two sub-fixtures controlled by the `variant` argument to `seed()`:

    seed(db, variant="all")   # every ConstraintConfig row is_enabled=True
    seed(db, variant="none")  # every ConstraintConfig row is_enabled=False

Default (`seed(db)` with no kwarg) → "all". The harness can capture both
variants by calling `seed(db, variant="all")`, running
`build_solver_input`, then `seed(db, variant="none")` + rerun.

Scenario payload (identical across both variants — only the
ConstraintConfig toggles change):
  - 3 batches of mixed 공정 (연선 / 저압절연 / 저압시스)
  - Same due_dates / priorities
  - Same sheath colors

Only `ConstraintConfig` rows change between variants, so the
`SolverInput.constraint_params` / `welding_min` fields differ; all
other fields are byte-identical across variants.

Mutates tables:
    production_batch, sales_order  (run_label-scoped)
    constraint_config              (constraint_id-scoped: keys we own)

Source test: none (two-sub-fixture shape is unique to this scenario).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

RUN_LABEL = "20260421_parity_10_all_vs_none_constraints"

# Rows we own. Keeping constraint_id prefix `PAR10-*` so the seed never
# collides with real constraints deployed in Supabase.
_OWNED_CONSTRAINTS: list[tuple[str, str, str, int, str, dict]] = [
    # (constraint_id, constraint_name, category, priority, impact_level, params_json)
    ("PAR10-4-2", "색상교체 분 (parity)", "공정", 50, "중", {"sheath_color_min": 120}),
    ("PAR10-4-4", "용접 분 (parity)", "공정", 50, "중", {"welding_min": 30}),
    ("PAR10-5-1", "납기 우선 (parity)", "납기", 80, "상", {}),
    ("PAR10-6-1", "시스 색상 체인 (parity)", "품질", 40, "중", {}),
]


def _reset_constraints(db: Session) -> None:
    ids = [c[0] for c in _OWNED_CONSTRAINTS]
    db.query(ConstraintConfig).filter(ConstraintConfig.constraint_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.flush()


def _reset_payload(db: Session) -> None:
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


def _insert_constraints(db: Session, *, enabled: bool) -> None:
    for cid, name, cat, pri, impact, params in _OWNED_CONSTRAINTS:
        db.add(
            ConstraintConfig(
                constraint_id=cid,
                constraint_name=name,
                category=cat,
                is_enabled=enabled,
                priority=pri,
                impact_level=impact,
                params_json=params,
                applicable_processes=[],
                implementation_type="soft",
                notes="parity seed 10",
            )
        )


def _insert_payload(db: Session) -> None:
    rows = [
        ("SO-AVN-01", "연선", 120, None, date(2026, 5, 8), 2000.0),
        ("SO-AVN-02", "저압절연", 95, "흑", date(2026, 5, 11), 1500.0),
        ("SO-AVN-03", "저압시스", 70, "청", date(2026, 5, 14), 1000.0),
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
                customer_name="parity-avn",
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
                customer_name="parity-avn",
                due_date=due,
                customer_priority=10,
                voltage="저압",
                conductor_material="CU",
                product_group="CV",
                status="planned",
                batch_group="",
            )
        )


def seed(db: Session, variant: Literal["all", "none"] = "all") -> None:
    """Seed the fixture. `variant` toggles the owned ConstraintConfig rows.

    Why two variants share a single function: the plan specifies one seed
    script that produces two sub-fixtures. The harness (Task 1.3) will
    call this twice with different `variant` values and capture both.
    """
    _reset_payload(db)
    _reset_constraints(db)
    _insert_constraints(db, enabled=(variant == "all"))
    _insert_payload(db)
    db.commit()


if __name__ == "__main__":
    # WARNING: mutates DB. See module docstring.
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        # Manual smoke: default = all-enabled.
        seed(db, variant="all")
        print(f"seeded {RUN_LABEL} variant=all")
    finally:
        db.close()
