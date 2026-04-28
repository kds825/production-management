"""Reconciliation — canonical hash 멱등성 (Task 11, Part A) +
tiebreaker + UPDATE/orphan INSERT (Task 12, Part B).
"""

from __future__ import annotations

import io
from typing import List, Dict

from openpyxl import Workbook

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.wip_upload_log import WipUploadLog


_RUN_LABEL = "TEST_WIP_RECON_T11"


def _cleanup(db) -> None:
    db.query(WipUploadLog).filter(WipUploadLog.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(WipInventory).filter(WipInventory.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.commit()


def _make_excel(rows: List[Dict]) -> bytes:
    """실사 Excel 포맷: 공정/규격/전압/길이/개수/색상 컬럼."""
    wb = Workbook()
    ws = wb.active
    ws.append(["공정", "규격", "전압", "길이", "개수", "색상"])
    for r in rows:
        ws.append(
            [
                r.get("공정", "연선재고"),
                r.get("규격", "180SQ"),
                r.get("전압", "저압"),
                r.get("길이", 300),
                r.get("개수", 1),
                r.get("색상", ""),
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_canonical_hash_stable_across_regeneration(db):
    """동일 row 내용으로 재생성된 Excel 은 동일 canonical hash."""
    from app.infrastructure.parsers.wip_parser import _compute_canonical_hash

    rows = [{"공정": "연선재고", "규격": "180SQ", "길이": 300}]
    a = _compute_canonical_hash(_make_excel(rows))
    b = _compute_canonical_hash(_make_excel(rows))
    assert a == b


def test_canonical_hash_differs_on_content_change(db):
    from app.infrastructure.parsers.wip_parser import _compute_canonical_hash

    a = _compute_canonical_hash(
        _make_excel([{"공정": "연선재고", "규격": "180SQ", "길이": 300}])
    )
    b = _compute_canonical_hash(
        _make_excel([{"공정": "연선재고", "규격": "180SQ", "길이": 400}])
    )
    assert a != b


def test_reupload_same_content_detects_duplicate(db):
    """동일 canonical_hash 재업로드 → duplicate=True 반환, 신규 WIP 생성 없음."""
    from app.infrastructure.parsers.wip_parser import parse_wip_excel

    _cleanup(db)
    rows = [{"공정": "연선재고", "규격": "181SQ", "길이": 300}]
    file_bytes = _make_excel(rows)

    r1 = parse_wip_excel(file_bytes, db, run_label=_RUN_LABEL)
    db.commit()
    assert r1.get("duplicate", False) is False
    assert r1["total"] >= 1
    first_count = r1["total"]

    r2 = parse_wip_excel(file_bytes, db, run_label=_RUN_LABEL)
    assert r2.get("duplicate") is True
    assert r2.get("existing_upload_id") is not None

    # 신규 WIP 가 추가 생성되지 않았는지 확인
    wips = db.query(WipInventory).filter_by(run_label=_RUN_LABEL).all()
    assert len(wips) == first_count, "재업로드 시 새 WIP INSERT 없음"
    _cleanup(db)


# ─────────────────────────────────────────────
# Part B — tiebreaker + UPDATE / orphan INSERT (Task 12)
# ─────────────────────────────────────────────


def _cleanup_batches(db) -> None:
    """_seed_expected_wip 가 만든 배치와 WIP 를 함께 삭제."""
    # 먼저 연결된 WipInventory 삭제 (FK 참조)
    db.query(WipInventory).filter(WipInventory.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.query(WipUploadLog).filter(WipUploadLog.run_label == _RUN_LABEL).delete(
        synchronize_session=False
    )
    db.commit()


def _seed_expected_wip(
    db,
    sq: float = 185,
    expected: float = 300,
    voltage: str = "저압",
) -> WipInventory:
    """배치 + 연결된 예상 WIP 수동 생성 (listener 대체).

    listener 가 이미 WIP 를 만든 경우 해당 WIP 를 재활용.
    """
    b = ProductionBatch(
        run_label=_RUN_LABEL,
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=expected,
        sq_mm2=sq,
        voltage="0.6/1kV",
        status="planned",
    )
    db.add(b)
    db.flush()

    # listener 가 생성한 WIP 조회
    w = db.query(WipInventory).filter_by(source_batch_id=b.batch_id).first()
    if not w:
        # listener 미작동 환경 — 수동 생성
        w = WipInventory(
            process_stage="연선재고",
            cross_section=sq,
            voltage_class=voltage,
            length_m=expected,
            count=1,
            total_length_m=expected,
            expected_length_m=expected,
            status="예상",
            source_batch_id=b.batch_id,
            run_label=_RUN_LABEL,
        )
        db.add(w)
        db.flush()

    return w


def test_reconciliation_single_match_auto_update(db):
    """정확히 매칭되는 예상 WIP 1건 → 실사_확정 전환 + actual/variance 기록."""
    _cleanup_batches(db)
    wip = _seed_expected_wip(db, sq=185, expected=300)
    db.commit()

    rows = [{"공정": "연선재고", "규격": "185SQ", "전압": "저압", "길이": 275}]
    from app.infrastructure.parsers.wip_parser import parse_wip_excel

    parse_wip_excel(_make_excel(rows), db, run_label=_RUN_LABEL)
    db.flush()
    db.expire_all()

    db.refresh(wip)
    assert wip.status == "실사_확정", (
        f"단일 매칭 후 실사_확정 전환 실패, 실제={wip.status}"
    )
    assert float(wip.actual_length_m or 0) == 275.0
    assert float(wip.variance_m or 0) == -25.0
    _cleanup_batches(db)


def test_reconciliation_zero_match_inserts_orphan(db):
    """매칭 후보 0건 → orphan WIP (사용가능, source_batch_id=None) INSERT.

    Why 241SQ: reconciliation 후보 검색은 run_label 무관하게 cross_section
    기준으로 전역 탐색하므로(운영 업로드 시맨틱) KBI 표준 SQ(240 등)로
    테스트하면 타 run 의 예상 WIP 와 충돌해 orphan 대신 UPDATE 경로로 감.
    표준 단계표에 없는 241 은 현장 데이터와 영구 격리.
    """
    _cleanup_batches(db)
    rows = [{"공정": "연선재고", "규격": "241SQ", "전압": "저압", "길이": 500}]
    from app.infrastructure.parsers.wip_parser import parse_wip_excel

    parse_wip_excel(_make_excel(rows), db, run_label=_RUN_LABEL)
    db.flush()

    orphan = (
        db.query(WipInventory)
        .filter(
            WipInventory.run_label == _RUN_LABEL,
            WipInventory.cross_section == 241,
        )
        .first()
    )
    assert orphan is not None, "orphan WIP INSERT 되어야 함"
    assert orphan.status == "사용가능"
    assert orphan.source_batch_id is None
    _cleanup_batches(db)


def test_reconciliation_tiebreaker_narrowest_delta(db):
    """후보 2개 — exact voltage 일치 시 narrowest delta 로 결정."""
    _cleanup_batches(db)
    # 실사 길이 275 기준: delta 5 vs delta 35
    w_close = _seed_expected_wip(db, sq=186, expected=280)  # |280-275|=5
    w_far = _seed_expected_wip(db, sq=186, expected=320)  # |320-275|=45 → ±10% 범위 밖
    db.commit()

    rows = [{"공정": "연선재고", "규격": "186SQ", "전압": "저압", "길이": 275}]
    from app.infrastructure.parsers.wip_parser import parse_wip_excel

    parse_wip_excel(_make_excel(rows), db, run_label=_RUN_LABEL)
    db.flush()
    db.expire_all()

    db.refresh(w_close)
    db.refresh(w_far)
    assert w_close.status == "실사_확정", "narrowest delta 후보가 매칭되어야 함"
    assert w_far.status == "예상", "delta 먼 후보는 변경 없어야 함"
    _cleanup_batches(db)


def test_reconciliation_unassigned_batch_skipped(db):
    """unassigned 배치 WIP 는 후보에서 제외 → orphan INSERT 발생."""
    _cleanup_batches(db)
    wip = _seed_expected_wip(db, sq=150, expected=400)

    # 배치를 unassigned 상태로 변경
    batch = db.get(ProductionBatch, wip.source_batch_id)
    batch.status = "unassigned"
    db.flush()
    db.commit()

    rows = [{"공정": "연선재고", "규격": "150SQ", "전압": "저압", "길이": 390}]
    from app.infrastructure.parsers.wip_parser import parse_wip_excel

    parse_wip_excel(_make_excel(rows), db, run_label=_RUN_LABEL)
    db.flush()
    db.expire_all()

    db.refresh(wip)
    # unassigned WIP 는 그대로 '예상' 유지
    assert wip.status == "예상", "unassigned 배치 WIP 는 실사 매칭 제외"

    # orphan 이 새로 INSERT 되었어야 함
    orphan = (
        db.query(WipInventory)
        .filter(
            WipInventory.run_label == _RUN_LABEL,
            WipInventory.cross_section == 150,
            WipInventory.source_batch_id.is_(None),
        )
        .first()
    )
    assert orphan is not None, "unassigned 제외 → orphan INSERT 기대"
    _cleanup_batches(db)
