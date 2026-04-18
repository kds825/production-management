"""Reconciliation — canonical hash 멱등성 (Task 11, Part A).

Part B (tiebreaker + UPDATE/INSERT) 는 Task 12.
"""

from __future__ import annotations

import io
from typing import List, Dict

from openpyxl import Workbook

from app.infrastructure.models.wip_upload_log import WipUploadLog
from app.infrastructure.models.wip_inventory import WipInventory


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
    from app.services.wip_parser import _compute_canonical_hash

    rows = [{"공정": "연선재고", "규격": "180SQ", "길이": 300}]
    a = _compute_canonical_hash(_make_excel(rows))
    b = _compute_canonical_hash(_make_excel(rows))
    assert a == b


def test_canonical_hash_differs_on_content_change(db):
    from app.services.wip_parser import _compute_canonical_hash

    a = _compute_canonical_hash(
        _make_excel([{"공정": "연선재고", "규격": "180SQ", "길이": 300}])
    )
    b = _compute_canonical_hash(
        _make_excel([{"공정": "연선재고", "규격": "180SQ", "길이": 400}])
    )
    assert a != b


def test_reupload_same_content_detects_duplicate(db):
    """동일 canonical_hash 재업로드 → duplicate=True 반환, 신규 WIP 생성 없음."""
    from app.services.wip_parser import parse_wip_excel

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
