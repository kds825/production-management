"""End-to-end backend smoke test — WIP Lifecycle 시연 시나리오 4단계.

Task 15 (간소화: Playwright E2E 대신 backend 통합 smoke).

시연 스토리:
  1) 월 오전 — 수주 A(150SQ 700m) → 헤더 배치 → listener 가 예상 WIP 300m
  2) 월 오후 — 배치 completed → hook 이 예상 → 실적_추정 승격
  3) 화 — 긴급수주 B(150SQ 280m) → Level 2 매칭 → 재공 활용
  4) 수 — 실사 Excel(275m) → reconciliation → 실사_확정, variance=-25
         → create_shortage_batches → lot 확장 신규 배치 → 새 예상 WIP (recursion)

기대 최종 상태:
  - 원 WIP: status=사용완료 (수주 B 에 소비됨) 또는 실사_확정
  - 보정 배치: 1 드럼 생성 + 새 예상 WIP auto-create
"""

from __future__ import annotations

import io
from typing import Dict, List

from openpyxl import Workbook

from app.infrastructure.models.decision_criteria import DecisionCriteria
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.wip_inventory import WipInventory
from app.services.sm_inventory import create_shortage_batches
from app.services.wip_matching import match_wip
from app.services.wip_parser import parse_wip_excel
from app.services.wip_promotion import _promote_expected_to_estimated


_RUN_A = "TEST_SMOKE_A"  # 1차 run — 수주 A
_RUN_B = "TEST_SMOKE_B"  # 2차 run — 긴급수주 B
_SQ = 175.0  # seed DrumLotMaster 에 없는 값 (격리)
_LOT = 1000.0


def _cleanup(db) -> None:
    """FK 순서 준수: 지우려는 WipInventory 를 참조하는 모든 SalesOrder /
    ProductionBatch 의 FK 를 먼저 NULL 로 해제 후 WIP → Batch → Order 삭제.
    """
    for label in (_RUN_A, _RUN_B):
        # 삭제 대상 WIP id 확보
        wip_ids = [
            w.wip_id
            for w in db.query(WipInventory.wip_id).filter(
                WipInventory.run_label == label
            )
        ]
        if wip_ids:
            # run_label 무관하게, 해당 WIP id 참조하는 모든 행의 FK 해제
            db.query(SalesOrder).filter(SalesOrder.wip_id.in_(wip_ids)).update(
                {"wip_id": None}, synchronize_session=False
            )
            db.query(ProductionBatch).filter(
                ProductionBatch.wip_matched_id.in_(wip_ids)
            ).update({"wip_matched_id": None}, synchronize_session=False)

        db.query(WipInventory).filter(WipInventory.run_label == label).delete(
            synchronize_session=False
        )
        db.query(ProductionBatch).filter(ProductionBatch.run_label == label).delete(
            synchronize_session=False
        )
        db.query(SalesOrder).filter(SalesOrder.run_label == label).delete(
            synchronize_session=False
        )
    db.commit()


def _seed_masters(db) -> None:
    if not db.query(DrumLotMaster).filter_by(cross_section=_SQ).first():
        db.add(DrumLotMaster(cross_section=_SQ, lot_stranding=_LOT))
    for name, val in [("Loss 허용 한도", "8"), ("조장 부족 허용율", "5")]:
        if not db.query(DecisionCriteria).filter_by(criteria_name=name).first():
            db.add(DecisionCriteria(criteria_name=name, criteria_value=val))
    db.flush()


def _make_wip_excel(rows: List[Dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["공정", "규격", "전압", "길이", "개수", "색상"])
    for r in rows:
        ws.append(
            [
                r.get("공정", "연선재고"),
                r.get("규격"),
                r.get("전압", "저압"),
                r.get("길이"),
                r.get("개수", 1),
                r.get("색상", ""),
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_wip_lifecycle_full_demo_flow(db):
    """시연 스토리 4단계 end-to-end."""
    _cleanup(db)
    _seed_masters(db)
    db.commit()

    # ── 1. 월 오전: 수주 A 헤더 배치 → listener 가 예상 WIP 300m ──
    header = ProductionBatch(
        run_label=_RUN_A,
        process_name="연선",
        batch_seq=-1,
        total_length_m=_LOT,
        wip_output_expected_m=300,
        sq_mm2=_SQ,
        voltage="저압",
        conductor_material="CU",
        status="planned",
        batch_group=f"ST-{_SQ}-SMOKE",
    )
    db.add(header)
    db.flush()
    db.expire_all()

    wip = db.query(WipInventory).filter_by(source_batch_id=header.batch_id).first()
    assert wip is not None, "Step 1 — listener 가 예상 WIP 생성"
    assert wip.status == "예상"
    assert float(wip.expected_length_m) == 300.0

    # ── 2. 월 오후: 배치 completed → 예상 → 실적_추정 ──
    _promote_expected_to_estimated(header.batch_id, "completed", db)
    db.flush()
    db.refresh(wip)
    assert wip.status == "실적_추정", "Step 2 — T2 승격"
    assert float(wip.actual_length_m or 0) == 300.0

    db.commit()

    # ── 3. 화: 긴급수주 B 280m → match_wip Level 2 기본 → 재공 활용 ──
    db.add(
        SalesOrder(
            run_label=_RUN_B,
            order_id="ORD-SMOKE-B-001",
            order_line=1,
            spec_raw=f"{int(_SQ)}SQ",
            ordered_qty_m=280,
            core_count=1,
            voltage="0.6/1kV",
            drum_length_m=280,
        )
    )
    db.flush()
    result = match_wip(run_label=_RUN_B, db=db)
    assert result["matched"] >= 1, "Step 3 — 긴급수주 B 가 기존 재공에 매칭"
    # 매칭 확인: 수주 B 의 use_wip=True + 원 WIP 의 status 변경 (사용완료) 또는 matched_order_id 설정
    db.expire_all()
    db.refresh(wip)
    assert wip.status == "사용완료" or wip.matched_order_id is not None, (
        f"Step 3 — WIP 사용 흔적 기록. status={wip.status}, matched={wip.matched_order_id}"
    )

    db.commit()

    # ── 4. 수: 실사 Excel 275m → reconciliation 실패(이미 사용완료) → orphan INSERT ──
    # (사용완료 된 WIP 는 reconciliation 대상이 아니므로, 275m Excel 은 orphan 으로 들어감)
    # 따라서 이 시나리오에선 사전에 wip 를 '실사_확정' 으로 두고 variance 시뮬.
    # 본 smoke 는 recursion 입증이 핵심이므로 별도 wip 를 직접 실사_확정으로 세팅:
    header2 = ProductionBatch(
        run_label=_RUN_A,
        process_name="연선",
        batch_seq=-1,
        total_length_m=_LOT,
        wip_output_expected_m=300,
        sq_mm2=_SQ,
        voltage="저압",
        conductor_material="CU",
        status="planned",
        batch_group=f"ST-{_SQ}-SMOKE-2",
    )
    db.add(header2)
    db.flush()
    db.expire_all()

    wip2 = db.query(WipInventory).filter_by(source_batch_id=header2.batch_id).first()
    assert wip2 is not None

    # 실사확정으로 전환 + variance -25 설정 (실사 Excel 업로드 결과 시뮬)
    wip2.status = "실사_확정"
    wip2.actual_length_m = 275
    wip2.variance_m = -25
    db.commit()

    # create_shortage_batches → lot 확장 신규 배치 → listener 새 예상 WIP (recursion)
    r = create_shortage_batches(_RUN_A, db)
    assert r["shortage_batches_created"] >= 1, "Step 4 — 보정배치 생성"
    db.flush()

    new_batch = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == _RUN_A,
            ProductionBatch.remarks.like("SM부족 보정%"),
        )
        .first()
    )
    assert new_batch is not None, "Step 4 — 보정 배치 존재"
    assert float(new_batch.total_length_m) == _LOT, "Step 4 — 틀단 확장"
    assert new_batch.batch_seq == -1
    assert float(new_batch.wip_output_expected_m or 0) == _LOT - 25  # 975m 잉여

    new_wip = (
        db.query(WipInventory)
        .filter_by(source_batch_id=new_batch.batch_id, status="예상")
        .first()
    )
    assert new_wip is not None, "Step 4 — listener recursion: 새 예상 WIP 생성"
    assert float(new_wip.expected_length_m) == _LOT - 25

    _cleanup(db)


def test_wip_upload_idempotency_smoke(db):
    """실사 Excel 재업로드 시 duplicate 감지 — 운영 실수 방지 검증."""
    _cleanup(db)
    db.commit()

    rows = [{"공정": "연선재고", "규격": f"{int(_SQ)}SQ", "전압": "저압", "길이": 275}]
    file_bytes = _make_wip_excel(rows)

    r1 = parse_wip_excel(file_bytes, db, run_label=_RUN_A)
    db.commit()
    assert r1.get("duplicate", False) is False
    first_count = r1["total"]

    # 재업로드
    r2 = parse_wip_excel(file_bytes, db, run_label=_RUN_A)
    assert r2.get("duplicate") is True
    assert r2["total"] == 0, "재업로드 시 신규 INSERT 없음"

    # WIP 행 수 불변 확인
    wips = db.query(WipInventory).filter(WipInventory.run_label == _RUN_A).count()
    assert wips == first_count

    _cleanup(db)
