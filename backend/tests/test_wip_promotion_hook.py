"""T2 promotion helper — 배치 completed 시 WIP 예상 → 실적_추정 승격.

Task 9 — 헬퍼 단위테스트 (Part A).
Task 10 — 3 endpoint 통합 테스트 (Part B).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

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
        batch_group=_RUN_LABEL,  # bulk endpoint 테스트에서 조회 가능하도록
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


# ---------------------------------------------------------------------------
# Part B — endpoint 통합 테스트 (Task 10)
# TestClient 는 자체 DB 세션을 사용하므로 데이터를 커밋 후 호출하고,
# db.expire_all() 로 세션 캐시를 갱신해 엔드포인트의 변경을 확인한다.
# ---------------------------------------------------------------------------

_CLIENT: TestClient | None = None


def _get_client() -> TestClient:
    """모듈 레벨 싱글턴 — FastAPI app import 는 한 번만."""
    global _CLIENT
    if _CLIENT is None:
        from app.main import app

        _CLIENT = TestClient(app)
    return _CLIENT


def test_single_endpoint_triggers_promotion(db):
    """PATCH /api/pipeline/batch/{id}/status → completed 시 WIP 승격."""
    _cleanup(db)
    client = _get_client()
    batch = _seed_batch_with_wip(db)
    db.commit()  # TestClient 세션에서 읽을 수 있도록 커밋

    try:
        resp = client.patch(
            f"/api/pipeline/batch/{batch.batch_id}/status",
            json={"status": "completed"},
        )
        assert resp.status_code == 200, f"resp={resp.status_code} body={resp.text}"

        db.expire_all()
        wip = db.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
        assert wip is not None, "WIP 행이 존재해야 함"
        assert wip.status == "실적_추정", f"승격 실패: wip.status={wip.status}"
    finally:
        _cleanup(db)


def test_bulk_endpoint_triggers_promotion(db):
    """PATCH /api/pipeline/batch-group/{grp}/status → completed 시 WIP 승격."""
    _cleanup(db)
    client = _get_client()
    batch = _seed_batch_with_wip(db)
    db.commit()

    # batch_group 이 없으면 run_label 로 대체 (헤더 배치는 batch_group 자동 설정될 수도 있음)
    grp = batch.batch_group or batch.run_label

    try:
        resp = client.patch(
            f"/api/pipeline/batch-group/{grp}/status",
            json={"status": "completed"},
        )
        assert resp.status_code == 200, f"resp={resp.status_code} body={resp.text}"

        db.expire_all()
        wip = db.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
        assert wip is not None, "WIP 행이 존재해야 함"
        assert wip.status == "실적_추정", f"승격 실패: wip.status={wip.status}"
    finally:
        _cleanup(db)


def test_freeform_endpoint_triggers_promotion(db):
    """PATCH /api/pipeline/batch/{id} body 에 status 포함 → completed 시 WIP 승격."""
    _cleanup(db)
    client = _get_client()
    batch = _seed_batch_with_wip(db)
    db.commit()

    try:
        resp = client.patch(
            f"/api/pipeline/batch/{batch.batch_id}",
            json={"status": "completed"},
        )
        assert resp.status_code == 200, f"resp={resp.status_code} body={resp.text}"

        db.expire_all()
        wip = db.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
        assert wip is not None, "WIP 행이 존재해야 함"
        assert wip.status == "실적_추정", f"승격 실패: wip.status={wip.status}"
    finally:
        _cleanup(db)
