"""ProductionBatch after_insert listener — 게이트 + ON CONFLICT + rollback.

Task 6. Eng review 블로커 #1: 헤더 배치(batch_seq=-1 + 연선)에만 fire.
Eng review Medium #4: ON CONFLICT DO NOTHING 으로 IntegrityError 차단.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory


_RUN_LABEL = "TEST_WIP_LISTENER_T6"


@pytest.fixture
def listener_registered(db):
    """Listener 명시 등록. 모듈 레벨 event 라 테스트 후 제거는 불필요."""
    from app.services.wip_lifecycle_listener import register_wip_listener

    register_wip_listener()
    yield


def _cleanup(db) -> None:
    db.query(WipInventory).filter(WipInventory.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.commit()


def _make_header(
    *,
    expected: float = 300.0,
    process: str = "연선",
    seq: int = -1,
) -> ProductionBatch:
    return ProductionBatch(
        run_label=_RUN_LABEL,
        process_name=process,
        batch_seq=seq,
        total_length_m=1000,
        wip_output_expected_m=expected,
        sq_mm2=150,
        voltage="0.6/1kV",
        conductor_material="CU",
        status="planned",
    )


def test_listener_creates_wip_on_header_with_surplus(db, listener_registered):
    _cleanup(db)
    batch = _make_header(expected=300)
    db.add(batch)
    db.flush()

    wip = db.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip is not None, "헤더 + surplus > 0 → WIP auto-create"
    assert wip.status == "예상"
    assert float(wip.expected_length_m) == 300.0
    assert float(wip.total_length_m) == 300.0
    _cleanup(db)


def test_listener_skips_non_header(db, listener_registered):
    _cleanup(db)
    b = _make_header(expected=300, seq=1)  # non-header
    db.add(b)
    db.flush()

    wip = db.query(WipInventory).filter_by(source_batch_id=b.batch_id).first()
    assert wip is None, "non-header 배치는 WIP 생성 안 함"
    _cleanup(db)


def test_listener_skips_non_연선_process(db, listener_registered):
    _cleanup(db)
    b = _make_header(expected=300, process="저압절연")
    db.add(b)
    db.flush()

    wip = db.query(WipInventory).filter_by(source_batch_id=b.batch_id).first()
    assert wip is None, "연선 공정이 아닌 헤더는 WIP 생성 안 함"
    _cleanup(db)


def test_listener_skips_zero_surplus(db, listener_registered):
    _cleanup(db)
    b = _make_header(expected=0)
    db.add(b)
    db.flush()

    wip = db.query(WipInventory).filter_by(source_batch_id=b.batch_id).first()
    assert wip is None, "surplus=0 인 헤더는 WIP 생성 안 함"
    _cleanup(db)


def test_listener_on_conflict_do_nothing(db, listener_registered):
    """같은 source_batch_id 로 수동 INSERT 시도해도 G1 UNIQUE 충돌 없이 skip."""
    _cleanup(db)
    batch = _make_header(expected=300)
    db.add(batch)
    db.flush()

    # 수동 중복 시도
    stmt = (
        pg_insert(WipInventory.__table__)
        .values(
            status="예상",
            source_batch_id=batch.batch_id,
            expected_length_m=999,
            total_length_m=999,
            length_m=999,
            count=1,
            run_label=_RUN_LABEL,
        )
        .on_conflict_do_nothing(
            index_elements=["source_batch_id"],
            index_where=text("source_batch_id IS NOT NULL"),
        )
    )
    db.execute(stmt)
    db.flush()

    wips = db.query(WipInventory).filter_by(source_batch_id=batch.batch_id).all()
    assert len(wips) == 1, "ON CONFLICT DO NOTHING — 중복 삽입 무시"
    _cleanup(db)


def test_bulk_save_objects_blocked_for_production_batch(db):
    """ProductionBatch bulk_save_objects 는 RuntimeError — listener bypass 방지."""
    b = ProductionBatch(
        run_label=_RUN_LABEL,
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=300,
        sq_mm2=150,
        status="planned",
    )
    with pytest.raises(RuntimeError, match="bulk.*ProductionBatch.*금지"):
        db.bulk_save_objects([b])
    _cleanup(db)
