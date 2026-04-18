"""T2 promotion helper — 배치 completed 시 WIP 예상 → 실적_추정 승격.

Task 9 — 헬퍼 단위테스트만. 3 endpoint 통합은 Task 10.
"""

from __future__ import annotations


from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory

_RUN_LABEL = "TEST_WIP_PROMOTION_T9"


def _cleanup(db) -> None:
    db.query(WipInventory).filter(WipInventory.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.commit()


def _seed_batch_with_wip(db) -> ProductionBatch:
    """헤더 배치 INSERT → wip_lifecycle_listener 가 자동으로 예상 WIP 생성.

    wip_lifecycle_listener 의 after_insert 이벤트(ON CONFLICT DO NOTHING)가
    source_batch_id 에 WIP 행을 만들므로 수동 삽입 불필요. 중복 삽입 시
    idx_wip_source_batch_unique 유니크 위반 발생.
    """
    batch = ProductionBatch(
        run_label=_RUN_LABEL,
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=300,
        sq_mm2=150,
        voltage="0.6/1kV",
        conductor_material="CU",
        status="planned",
    )
    db.add(batch)
    db.flush()
    # listener 가 raw connection 으로 WIP 를 삽입했으므로 세션 캐시 갱신
    db.expire_all()
    return batch


def test_promote_on_completed(db):
    _cleanup(db)
    from app.services.wip_promotion import _promote_expected_to_estimated

    batch = _seed_batch_with_wip(db)
    ok = _promote_expected_to_estimated(batch.batch_id, "completed", db)
    db.flush()

    wip = db.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert ok is True
    assert wip.status == "실적_추정"
    assert float(wip.actual_length_m or 0) == float(wip.expected_length_m)
    _cleanup(db)


def test_promote_skip_non_completed(db):
    _cleanup(db)
    from app.services.wip_promotion import _promote_expected_to_estimated

    batch = _seed_batch_with_wip(db)
    for s in ("in_progress", "scheduled", "wip_complete", "planned"):
        ok = _promote_expected_to_estimated(batch.batch_id, s, db)
        assert ok is False, f"status={s} 는 승격 skip"
    db.flush()
    wip = db.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip.status == "예상"
    _cleanup(db)


def test_promote_idempotent(db):
    _cleanup(db)
    from app.services.wip_promotion import _promote_expected_to_estimated

    batch = _seed_batch_with_wip(db)
    ok1 = _promote_expected_to_estimated(batch.batch_id, "completed", db)
    ok2 = _promote_expected_to_estimated(batch.batch_id, "completed", db)
    db.flush()

    assert ok1 is True, "첫 호출은 승격"
    assert ok2 is False, "두 번째는 no-op (이미 실적_추정)"
    wip = db.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip.status == "실적_추정"
    _cleanup(db)


def test_promote_no_op_when_no_wip(db):
    """WIP 행 없는 배치 (surplus=0) 의 completed 전환은 조용히 False."""
    _cleanup(db)
    from app.services.wip_promotion import _promote_expected_to_estimated

    b = ProductionBatch(
        run_label=_RUN_LABEL,
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=0,
        status="planned",
    )
    db.add(b)
    db.flush()

    ok = _promote_expected_to_estimated(b.batch_id, "completed", db)
    assert ok is False
    _cleanup(db)
