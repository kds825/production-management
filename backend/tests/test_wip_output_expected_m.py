"""Test wip_output_expected_m calculation on group headers.

Eng review 블로커 #1: 헤더 배치(batch_seq=-1)에만 surplus 계산.
다른 5개 생성지점은 default 0 유지.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.wip_inventory import WipInventory
from app.services.batch_grouping import create_batches, execute_auto_splits

_RUN_LABEL_SPLIT = "TEST_WIP_SPLIT_T5"
_LOT_SIZE = 1000.0  # drum_length_m for 162SQ fixture


def _cleanup_split_run(db) -> None:
    """execute_auto_splits 내부 db.commit() 이후 잔여 행 정리.

    Why WipInventory 먼저 삭제:
    T7 listener 자동 등록 이후 연선 헤더(batch_seq=-1) INSERT 시 WipInventory 가
    자동 생성된다. WipInventory.source_batch_id → ProductionBatch FK 가 있으므로
    ProductionBatch 삭제 전에 WipInventory 를 먼저 제거해야 FK 위반을 막는다.
    """
    db.query(WipInventory).filter(WipInventory.run_label == _RUN_LABEL_SPLIT).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(
        ProductionBatch.run_label == _RUN_LABEL_SPLIT
    ).delete(synchronize_session=False)
    db.query(SalesOrder).filter(SalesOrder.run_label == _RUN_LABEL_SPLIT).delete(
        synchronize_session=False
    )
    db.commit()


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
    # 테스트 환경에서 ConstraintConfig 미시드 → defect_buffer_pct 기본 0
    # 따라서 700m 수주 → net_qty_g=700, surplus = 1000 − 700 = 300m (exact).
    # 운영 환경에선 defect_buffer(5%) 적용돼 surplus ≈ 265m. 이는 운영 경로 검증이
    # 별도 필요함을 의미 (OQ for Phase 2). 이 test 는 공식 자체만 검증.
    assert surplus == pytest.approx(300.0, abs=0.5), (
        f"예상 surplus 300m (±0.5), 실제={surplus}"
    )


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
            f"non-header 배치는 surplus=0 이어야 함: "
            f"batch_id={b.batch_id}, seq={b.batch_seq}, actual={actual}"
        )


def test_split_header_recomputes_surplus(db):
    """파이프라인 분할 시 split 헤더의 surplus 는 split 후 lot 기준 재계산.

    원 헤더의 surplus 를 proportional 복사하면 안 되고, split 후 실제 lot/orders 로
    surplus = split_work_qty - split_net_qty 를 독립 계산해야 한다.

    픽스처 설계:
    - 162SQ, lot_stranding=1000m
    - 3건 수주 각 900m (core_count=1): 각 드럼을 꽉 채워 독립 드럼 3개 생성
      → net=2700m, lots=3, work=3000m, header_surplus=300m
    - equipment_code=None (FK 제약 회피), estimated_duration_min 대폭 과부하
      → lot_count=3 ≥ 3 AND required_h >> available_h → is_overload=True
    - execute_auto_splits: is_overload → ceil(N/2)=2 분할 → split_idx=2
      → 앞 2틀(seq 1,2) 원본 유지, 뒷 1틀(seq 3) 신규 그룹
    - 기대값 after split:
        original header: remain=[seq1 900m, seq2 900m] → 2틀, work=2000m, surplus=200m
        new header:      split=[seq3 900m]             → 1틀, work=1000m, surplus=100m
    - 핵심 검증: 각 헤더의 wip_output_expected_m == total_length_m - net_qty_of_its_group
    - 보존 법칙: total_work(3000) - total_surplus(300) = total_net(2700) = 3×900
    """
    _cleanup_split_run(db)

    # 162SQ DrumLotMaster (seed 에 없음)
    if not db.query(DrumLotMaster).filter_by(cross_section=162).first():
        db.add(DrumLotMaster(cross_section=162, lot_stranding=int(_LOT_SIZE)))
        db.flush()

    # 납기 3일 후 (overload 판정용)
    due = date.today() + timedelta(days=3)
    BATCH_GROUP = f"ST-162-0.6/1kV-{_RUN_LABEL_SPLIT}"
    CORE_COUNT = 1
    ORDER_QTY = 900.0  # 각 수주 900m — 1틀(1000m) 내 수용, 3건이라 3틀 필요
    ORDER_COUNT = 3
    net_total = ORDER_QTY * ORDER_COUNT  # 2700m
    lot_count = math.ceil(
        net_total / _LOT_SIZE
    )  # 3틀 (lot_count >= 3 → overload 체크 활성화)
    work_total = lot_count * _LOT_SIZE  # 3000m
    header_surplus = work_total - net_total  # 300m

    # 헤더 배치 (batch_seq=-1)
    # equipment_code=None: FK 제약 없음. detect_split_candidates 는 None → "default" category 사용.
    # estimated_duration_min 대폭 과부하 → required_h >> available_h(3일 ≈ 66h) → is_overload=True
    header = ProductionBatch(
        run_label=_RUN_LABEL_SPLIT,
        sales_order_id="SO-T5-001",
        sales_order_line=1,
        process_name="연선",
        batch_seq=-1,
        drum_count=lot_count,
        drum_length_m=_LOT_SIZE,
        total_length_m=work_total,
        sq_mm2=162,
        core_count=CORE_COUNT,
        equipment_code=None,  # FK 제약 회피 — None 허용
        due_date=due,
        customer_priority=5,
        line_speed_mpm=10.0,
        setup_time_min=210,
        # 9999h >> available capacity (~66h) → overload=True
        estimated_duration_min=9999 * 60,
        status="planned",
        batch_group=BATCH_GROUP,
        wip_output_expected_m=header_surplus,  # Task 4 로직이 설정한 초기값
    )
    db.add(header)
    db.flush()

    # 개별 수주 배치 (batch_seq=1,2,3): total_length_m = ordered_qty (defect_buffer=0 in test)
    order_qtys: list[float] = [ORDER_QTY] * ORDER_COUNT
    batch_ids: list[int] = []
    for i, qty in enumerate(order_qtys, 1):
        b = ProductionBatch(
            run_label=_RUN_LABEL_SPLIT,
            sales_order_id=f"SO-T5-{i:03d}",
            sales_order_line=1,
            process_name="연선",
            batch_seq=i,
            drum_count=1,
            drum_length_m=_LOT_SIZE,
            total_length_m=qty,
            sq_mm2=162,
            core_count=CORE_COUNT,
            equipment_code=None,
            due_date=due,
            customer_priority=5,
            status="planned",
            batch_group=BATCH_GROUP,
        )
        db.add(b)
        db.flush()
        batch_ids.append(b.batch_id)

    db.commit()

    # Act — execute_auto_splits: overload → auto_split_recommended=True → _apply_auto_split 호출
    result = execute_auto_splits(_RUN_LABEL_SPLIT, db)

    # Assert — split 이 실제로 발생했는지 먼저 확인
    assert result["auto_split_count"] >= 1, (
        f"split 미발생: {result}. "
        "헤더 estimated_duration_min=9999×60 이 available_h 초과해야 overload=True"
    )

    # split 후 전체 헤더 조회 (원본 + 신규)
    all_headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == _RUN_LABEL_SPLIT,
            ProductionBatch.batch_seq == -1,
            ProductionBatch.process_name == "연선",
        )
        .all()
    )
    assert len(all_headers) >= 2, (
        f"split 후 헤더 2개 이상 기대, 실제={len(all_headers)}"
    )

    # 핵심 검증: 각 헤더별 보존 법칙
    # header.wip_output_expected_m == header.total_length_m
    #                                  - sum(individual.total_length_m) * core_count
    for h in all_headers:
        grp = h.batch_group
        individuals = (
            db.query(ProductionBatch)
            .filter(
                ProductionBatch.run_label == _RUN_LABEL_SPLIT,
                ProductionBatch.batch_group == grp,
                ProductionBatch.batch_seq >= 1,
            )
            .all()
        )
        work_qty = float(h.total_length_m or 0)
        net_qty = sum(float(b.total_length_m or 0) for b in individuals) * CORE_COUNT
        expected_surplus = max(0.0, work_qty - net_qty)
        actual_surplus = float(h.wip_output_expected_m or 0)

        assert actual_surplus == pytest.approx(expected_surplus, abs=0.5), (
            f"그룹 {grp}: surplus 재계산 불일치. "
            f"work={work_qty}, net={net_qty}, "
            f"expected_surplus={expected_surplus}, actual={actual_surplus}"
        )

    # 전체 보존 법칙: (total_work - total_surplus) == total ordered qty
    total_work = sum(float(h.total_length_m or 0) for h in all_headers)
    total_surplus = sum(float(h.wip_output_expected_m or 0) for h in all_headers)
    total_net = total_work - total_surplus
    expected_net = net_total  # 2700m
    assert abs(total_net - expected_net) < 1.0, (
        f"전체 보존 법칙 위반: "
        f"work={total_work}, surplus={total_surplus}, "
        f"net={total_net}, expected_net={expected_net}"
    )

    # 정리 (execute_auto_splits 가 commit 했으므로 명시적으로)
    _cleanup_split_run(db)
