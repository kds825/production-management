"""Test match_wip status filter levels — Level 2 default, Level 3 opt-in."""

from __future__ import annotations

import pytest

from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.decision_criteria import DecisionCriteria
from app.services.wip_matching import match_wip


_RUN_LABEL = "TEST_WIP_MATCHING_T8"


def _cleanup(db) -> None:
    # SalesOrder 먼저 삭제 — wip_id FK 가 WipInventory 를 참조하므로 순서 중요
    db.query(SalesOrder).filter(SalesOrder.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(WipInventory).filter(WipInventory.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.commit()


def _seed_criteria(db):
    """Loss 허용/조장 부족 허용율 기본값."""
    for name, val in [("Loss 허용 한도", "8"), ("조장 부족 허용율", "5")]:
        exist = db.query(DecisionCriteria).filter_by(criteria_name=name).first()
        if not exist:
            db.add(DecisionCriteria(criteria_name=name, criteria_value=val))
    db.flush()


def _seed_wip(db, status: str, qty: float = 300.0) -> WipInventory:
    w = WipInventory(
        process_stage="연선재고",
        cross_section=150,
        voltage_class="저압",
        length_m=qty,
        count=1,
        total_length_m=qty,
        status=status,
        run_label=_RUN_LABEL,
    )
    db.add(w)
    db.flush()
    return w


def _seed_order(db, qty: float = 280.0) -> SalesOrder:
    o = SalesOrder(
        run_label=_RUN_LABEL,
        order_id="ORD-T8-001",
        order_line=1,
        spec_raw="150SQ",
        ordered_qty_m=qty,
        core_count=1,
        voltage="0.6/1kV",
        drum_length_m=qty,
    )
    db.add(o)
    db.flush()
    return o


@pytest.mark.parametrize("status", ["사용가능", "실사_확정", "실적_추정"])
def test_level2_default_matches(db, status):
    _cleanup(db)
    _seed_criteria(db)
    _seed_wip(db, status)
    _seed_order(db)
    result = match_wip(run_label=_RUN_LABEL, db=db)
    assert result["matched"] == 1, f"Level 2 default 는 {status} 매칭해야 함"
    _cleanup(db)


def test_level2_skips_예상(db):
    _cleanup(db)
    _seed_criteria(db)
    _seed_wip(db, "예상")
    _seed_order(db)
    result = match_wip(run_label=_RUN_LABEL, db=db)
    assert result["matched"] == 0, "예상 상태는 Level 2 기본에서 skip"
    _cleanup(db)


def test_level3_emergency_mode_matches_예상(db):
    _cleanup(db)
    _seed_criteria(db)
    _seed_wip(db, "예상")
    _seed_order(db)
    result = match_wip(run_label=_RUN_LABEL, db=db, emergency_mode=True)
    assert result["matched"] == 1, "emergency_mode 시 예상도 매칭"
    _cleanup(db)


def test_skips_사용완료(db):
    _cleanup(db)
    _seed_criteria(db)
    _seed_wip(db, "사용완료")
    _seed_order(db)
    result = match_wip(run_label=_RUN_LABEL, db=db)
    assert result["matched"] == 0
    _cleanup(db)
