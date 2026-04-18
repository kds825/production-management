"""Test wip_output_expected_m calculation on group headers.

Eng review 블로커 #1: 헤더 배치(batch_seq=-1)에만 surplus 계산.
다른 5개 생성지점은 default 0 유지.
"""

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.services.batch_grouping import create_batches


def test_group_header_has_correct_surplus(db):
    """160SQ 수주 700m, lot_stranding=1000m → 헤더 surplus 양수 (defect_buffer 포함).

    160SQ 는 seed DrumLotMaster 에 없으므로 테스트 내부에서 직접 삽입해
    기존 시드값(150SQ=11000m 등)과 충돌 없이 결정적으로 검증한다.
    """
    # Arrange — 테스트 전용 SQ=160, lot=1000m (seed에 없는 값)
    db.add(DrumLotMaster(cross_section=160, lot_stranding=1000))
    db.flush()
    db.add(
        SalesOrder(
            run_label="T4",
            order_id="ORD-T4-001",
            order_line=1,
            spec_raw="160SQ",
            ordered_qty_m=700,
            core_count=1,
            voltage="0.6/1kV",
        )
    )
    db.flush()

    # Act
    create_batches(run_label="T4", db=db)

    # Assert
    headers = (
        db.query(ProductionBatch)
        .filter_by(
            run_label="T4",
            batch_seq=-1,
            process_name="연선",
        )
        .all()
    )
    assert len(headers) >= 1, "단일 그룹 헤더 생성"
    h = headers[0]
    assert float(h.total_length_m or 0) == 1000  # lot 확장 후 생산량
    surplus = float(h.wip_output_expected_m or 0)
    assert surplus > 0, f"헤더에 surplus 설정돼야 함, 실제={surplus}"
    # 700m 수주 + defect_buffer (기본 5%) = 735m → 1000 - 735 = 265m
    assert 200 <= surplus <= 300, f"surplus 범위 벗어남: {surplus}"


def test_non_header_batch_has_zero_surplus(db):
    """per-order 배치 (batch_seq != -1) 는 surplus 0 (default 유지)."""
    db.add(DrumLotMaster(cross_section=161, lot_stranding=1000))
    db.flush()
    db.add(
        SalesOrder(
            run_label="T4b",
            order_id="ORD-T4b-001",
            order_line=1,
            spec_raw="161SQ",
            ordered_qty_m=700,
            core_count=1,
            voltage="0.6/1kV",
        )
    )
    db.flush()
    create_batches(run_label="T4b", db=db)

    non_headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == "T4b",
            ProductionBatch.batch_seq != -1,
        )
        .all()
    )
    for b in non_headers:
        actual = float(b.wip_output_expected_m or 0)
        assert actual == 0, (
            f"non-header 배치는 surplus=0 이어야 함: batch_id={b.batch_id}, seq={b.batch_seq}, actual={actual}"
        )
