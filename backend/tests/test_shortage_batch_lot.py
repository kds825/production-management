"""Shortage batch lot expansion + recursion tests.

Task 13 — variance_m < 0 인 실사_확정 WIP 에 대해 lot 확장 후 신규 배치 생성.
Listener (T6) 가 이 신규 배치에서 자동으로 새 예상 WIP 를 생성해 recursion 완성.
"""

from __future__ import annotations


from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory
from app.services.sm_inventory import create_shortage_batches


_RUN_LABEL = "TEST_SHORTAGE_T13"


def _cleanup(db) -> None:
    db.query(WipInventory).filter(WipInventory.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.commit()


def _seed_drum(db, sq: float, lot: float) -> None:
    exist = db.query(DrumLotMaster).filter_by(cross_section=sq).first()
    if not exist:
        db.add(DrumLotMaster(cross_section=sq, lot_stranding=lot))
        db.flush()


def _seed_shortage_wip(
    db, sq: float, shortage: float, voltage: str = "저압"
) -> WipInventory:
    w = WipInventory(
        process_stage="연선재고",
        cross_section=sq,
        voltage_class=voltage,
        material="CU",
        length_m=300 - shortage,  # 대충 값
        total_length_m=300 - shortage,
        expected_length_m=300,
        actual_length_m=300 - shortage,
        variance_m=-shortage,  # 음수 (부족)
        status="실사_확정",
        run_label=_RUN_LABEL,
        matched_order_id="ORD-T13-001",
    )
    db.add(w)
    db.flush()
    return w


def test_shortage_batch_expands_to_lot(db):
    """shortage=5m + lot_stranding=1000m → 신규 배치 1드럼 1000m."""
    _cleanup(db)
    _seed_drum(db, sq=170, lot=1000)
    _seed_shortage_wip(db, sq=170, shortage=5)
    db.commit()

    r = create_shortage_batches(_RUN_LABEL, db)
    db.flush()

    assert r["shortage_batches_created"] == 1
    new_batches = (
        db.query(ProductionBatch)
        .filter_by(run_label=_RUN_LABEL, process_name="연선")
        .all()
    )
    assert len(new_batches) == 1
    nb = new_batches[0]
    assert float(nb.total_length_m) == 1000.0
    assert nb.batch_seq == -1
    assert float(nb.wip_output_expected_m or 0) == 995.0  # 1000 - 5
    _cleanup(db)


def test_shortage_batch_triggers_listener_recursion(db):
    """신규 보정 배치 → listener 가 새 예상 WIP 995m auto-create."""
    _cleanup(db)
    _seed_drum(db, sq=171, lot=1000)
    _seed_shortage_wip(db, sq=171, shortage=5)
    db.commit()

    # Listener 자동 등록 (T7 이후 import 시 등록됨)
    from app.services.wip_lifecycle_listener import register_wip_listener

    register_wip_listener()

    create_shortage_batches(_RUN_LABEL, db)
    db.flush()

    new_batch = (
        db.query(ProductionBatch)
        .filter_by(run_label=_RUN_LABEL, process_name="연선")
        .first()
    )
    assert new_batch is not None

    new_wip = (
        db.query(WipInventory)
        .filter_by(source_batch_id=new_batch.batch_id, status="예상")
        .first()
    )
    assert new_wip is not None, "listener 가 새 예상 WIP 자동 생성"
    assert float(new_wip.expected_length_m) == 995.0
    _cleanup(db)


def test_no_drum_lot_master_falls_back(db):
    """DrumLotMaster 에 SQ 가 없으면 work_qty = shortage, wip_output = 0."""
    _cleanup(db)
    # SQ=9999 — 시드에 없음
    _seed_shortage_wip(db, sq=9999, shortage=100)
    db.commit()

    r = create_shortage_batches(_RUN_LABEL, db)
    db.flush()

    b = db.query(ProductionBatch).filter_by(run_label=_RUN_LABEL).first()
    assert b is not None
    assert float(b.total_length_m) == 100.0
    assert float(b.wip_output_expected_m or 0) == 0.0
    _cleanup(db)


def test_shortage_below_1m_skipped(db):
    _cleanup(db)
    _seed_drum(db, sq=172, lot=1000)
    _seed_shortage_wip(db, sq=172, shortage=0.5)
    db.commit()

    r = create_shortage_batches(_RUN_LABEL, db)
    assert r["shortage_batches_created"] == 0
    _cleanup(db)
