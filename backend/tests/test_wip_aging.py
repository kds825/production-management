"""S2 #5 — WIP aging — 30일+ 묵은 재공의 loss tolerance 상향."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.application.ingest.wip_matching import match_wip
from app.infrastructure.models.decision_criteria import DecisionCriteria
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.wip_inventory import WipInventory


_RUN_LABEL = "test-aging-s2"


def _cleanup(db) -> None:
    db.query(SalesOrder).filter(SalesOrder.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(WipInventory).filter(WipInventory.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.commit()


def _seed_criteria(db) -> None:
    rows = [
        ("Loss 허용 한도", "8"),
        ("조장 부족 허용율", "5"),
        ("WIP 노화 임계일", "30"),
        ("WIP 노화 loss 보너스", "7"),
    ]
    for name, val in rows:
        exist = db.query(DecisionCriteria).filter_by(criteria_name=name).first()
        if not exist:
            db.add(DecisionCriteria(criteria_name=name, criteria_value=val))
    db.flush()


def _seed_wip(
    db,
    *,
    length_m: float,
    age_days: int,
    status: str = "사용가능",
) -> WipInventory:
    w = WipInventory(
        process_stage="연선재고",
        cross_section=150,
        voltage_class="저압",
        length_m=length_m,
        count=1,
        total_length_m=length_m,
        status=status,
        run_label=_RUN_LABEL,
        created_at=datetime.utcnow() - timedelta(days=age_days),
    )
    db.add(w)
    db.flush()
    return w


def _seed_order(
    db, *, drum_length_m: float, qty_m: float, order_id: str = "ORD-AGE-1"
) -> SalesOrder:
    o = SalesOrder(
        run_label=_RUN_LABEL,
        order_id=order_id,
        order_line=1,
        spec_raw="150SQ",
        ordered_qty_m=qty_m,
        core_count=1,
        voltage="0.6/1kV",
        drum_length_m=drum_length_m,
    )
    db.add(o)
    db.flush()
    return o


def test_aged_wip_matches_with_bonus_loss(db):
    """31일+ 묵은 WIP. drum 1000m → 수주 drum 1150m (loss 13%).

    Loss 8% 만으론 reject (1000 < 1150*0.92=1058 True), 8%+7%=15% 적용 시
    pass (1000 < 1150*0.85=977.5 False). aging 보너스가 매칭을 살린다.
    """
    _cleanup(db)
    _seed_criteria(db)
    _seed_wip(db, length_m=1000, age_days=31)
    _seed_order(db, drum_length_m=1150, qty_m=900)

    result = match_wip(run_label=_RUN_LABEL, db=db)

    assert result["matched"] >= 1, f"aged WIP 는 보너스로 매칭되어야 함: {result}"
    _cleanup(db)


def test_fresh_wip_rejects_high_loss(db):
    """신규(5일) WIP. 동일 수주. 기본 8% 만 적용되어 reject 되어야 함."""
    _cleanup(db)
    _seed_criteria(db)
    _seed_wip(db, length_m=1000, age_days=5)
    _seed_order(db, drum_length_m=1150, qty_m=900)

    result = match_wip(run_label=_RUN_LABEL, db=db)

    assert result["matched"] == 0, f"fresh WIP 는 13% loss reject 해야 함: {result}"
    _cleanup(db)


def test_threshold_boundary_inclusive(db):
    """임계 30일 정확히 일치 시 미적용 (>임계 일 때만 보너스)."""
    _cleanup(db)
    _seed_criteria(db)
    _seed_wip(db, length_m=1000, age_days=30)
    _seed_order(db, drum_length_m=1150, qty_m=900)

    result = match_wip(run_label=_RUN_LABEL, db=db)

    # 정확히 30일은 reject (loss 13% > 8%)
    assert result["matched"] == 0, f"30일 경계는 보너스 미적용: {result}"
    _cleanup(db)
