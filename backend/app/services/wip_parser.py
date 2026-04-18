"""재공실사 Excel 파일(.xlsx / .xls) 파싱 → WipInventory 레코드 생성"""

import hashlib
import io as _io
import json as _json

from sqlalchemy.orm import Session

from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.wip_upload_log import WipUploadLog
from app.services.wip_matching import _extract_sq


def _compute_canonical_hash(file_content: bytes) -> str:
    """Excel 을 파싱한 뒤 key 필드 정렬된 튜플 리스트 기준 SHA-256.

    공백/저장시간/sheet 순서 변경 무관. 동일 내용 → 동일 hash.
    파일 바이트가 아닌 파싱된 내용 기반이므로 재저장해도 동일 hash.
    """
    from openpyxl import load_workbook

    wb = load_workbook(_io.BytesIO(file_content), data_only=True)
    ws = wb.active

    # 헤더 추출 (첫 행)
    headers = {c.value: c.column for c in ws[1] if c.value}

    canonical_rows = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        row_dict = {}
        for name, col_idx in headers.items():
            val = row[col_idx - 1].value
            if isinstance(val, str):
                val = val.strip()
            elif isinstance(val, (int, float)):
                val = float(val)
            row_dict[str(name).strip()] = val
        # 완전 빈 row skip
        if any(v not in (None, "") for v in row_dict.values()):
            canonical_rows.append(row_dict)

    # deterministic 정렬 (row 순서 무관하게 동일 hash 보장)
    canonical_rows.sort(
        key=lambda d: _json.dumps(d, sort_keys=True, ensure_ascii=False)
    )
    payload = _json.dumps(canonical_rows, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def parse_wip_excel(
    file_content: bytes, db: Session, run_label: str | None = None
) -> dict:
    """재공실사 Excel 파싱 + 멱등성 체크 (G4).

    동일 canonical_hash 재업로드 시 duplicate=True 를 반환하고
    WipInventory INSERT 를 건너뜀. 신규 업로드는 WipUploadLog 에 기록.
    """
    # ── 멱등성 체크 (G4) ──
    canonical_hash = _compute_canonical_hash(file_content)
    raw_hash = hashlib.sha256(file_content).hexdigest()

    existing = db.query(WipUploadLog).filter_by(canonical_hash=canonical_hash).first()
    if existing:
        return {
            "duplicate": True,
            "existing_upload_id": existing.upload_id,
            "existing_uploaded_at": (
                existing.uploaded_at.isoformat() if existing.uploaded_at else None
            ),
            "total": 0,
            "warnings": [f"동일 내용 이미 업로드됨 (upload_id={existing.upload_id})"],
        }

    # ── 기존 parsing ──
    result = parse_wip_file(file_content, db, run_label=run_label)
    result.setdefault("duplicate", False)

    # ── 업로드 log 기록 (반환 전) ──
    db.add(
        WipUploadLog(
            canonical_hash=canonical_hash,
            raw_file_hash=raw_hash,
            run_label=run_label,
            rows_inserted=result["total"],
            rows_updated=0,
        )
    )

    return result


def parse_wip_file(
    file_content: bytes, db: Session, run_label: str | None = None
) -> dict:
    """재공실사 Excel 파일을 파싱하여 wip_inventory 테이블에 INSERT.

    Args:
        file_content: .xlsx 또는 .xls 파일의 raw bytes
        db:           SQLAlchemy 세션 (commit은 호출부 책임)

    Returns:
        {"total": int, "warnings": list[str]}
    """
    from io import BytesIO

    result = {"total": 0, "warnings": []}

    # .xlsx 시도 → 실패 시 .xls(xlrd)로 폴백
    try:
        from openpyxl import load_workbook

        wb = load_workbook(BytesIO(file_content), data_only=True)
        ws = wb.active
        _parse_openpyxl(ws, db, run_label, result)
    except Exception:
        try:
            import xlrd

            book = xlrd.open_workbook(file_contents=file_content)
            sheet = book.sheet_by_index(0)
            _parse_xlrd(sheet, db, run_label, result)
        except Exception as e2:
            raise ValueError(f"xlsx/xls 모두 파싱 실패: {e2}") from e2

    return result


def _parse_openpyxl(ws, db: Session, run_label: str | None, result: dict) -> None:
    """openpyxl 워크시트 파싱 (xlsx)"""
    # 헤더 행 탐지 — "공정" 컬럼이 있는 행
    header_row = None
    header_map: dict[str, int] = {}
    for r in range(1, min(5, ws.max_row + 1)):
        for c in range(1, ws.max_column + 1):
            val = str(ws.cell(r, c).value or "").strip()
            if val == "공정":
                header_row = r
                break
        if header_row:
            break

    if header_row is None:
        result["warnings"].append("헤더 행 탐지 실패 — '공정' 컬럼을 찾을 수 없습니다.")
        return

    for c in range(1, ws.max_column + 1):
        val = str(ws.cell(header_row, c).value or "").strip()
        if val:
            header_map[val] = c

    for r in range(header_row + 1, ws.max_row + 1):
        process = _get(ws, r, header_map, "공정")
        if not process:
            continue
        _add_wip(
            db,
            run_label,
            result,
            process=process,
            spec_raw=_get(ws, r, header_map, "규격") or "",
            core=_get(ws, r, header_map, "CORE"),
            voltage_class=_get(ws, r, header_map, "전압구분"),
            material=_get(ws, r, header_map, "재질"),
            product_name=_get(ws, r, header_map, "품명"),
            length_m=_get_num(ws, r, header_map, "길이(M)"),
            count=_get_int(ws, r, header_map, "개수"),
            total_m=_get_num(ws, r, header_map, "총량(M)"),
            core_colors=_get(ws, r, header_map, "선심색상"),
            wire_diameter=_get_num(ws, r, header_map, "소선경"),
            wire_count=_get_int(ws, r, header_map, "가닥수"),
        )

    db.flush()


def _parse_xlrd(sheet, db: Session, run_label: str | None, result: dict) -> None:
    """xlrd 시트 파싱 (xls)"""
    header_row = None
    header_map: dict[str, int] = {}
    for r in range(min(5, sheet.nrows)):
        for c in range(sheet.ncols):
            val = str(sheet.cell_value(r, c) or "").strip()
            if val == "공정":
                header_row = r
                break
        if header_row is not None:
            break

    if header_row is None:
        result["warnings"].append("헤더 행 탐지 실패 — '공정' 컬럼을 찾을 수 없습니다.")
        return

    for c in range(sheet.ncols):
        val = str(sheet.cell_value(header_row, c) or "").strip()
        if val:
            header_map[val] = c

    def xget(r: int, col_name: str) -> str | None:
        col = header_map.get(col_name)
        if col is None:
            return None
        val = sheet.cell_value(r, col)
        if val is None or val == "":
            return None
        # xlrd 2.x: 숫자 셀은 float으로 반환됨 — 정수이면 소수점 제거
        if isinstance(val, float):
            val = int(val) if val == int(val) else val
        return str(val).strip() or None

    def xget_num(r: int, col_name: str) -> float | None:
        v = xget(r, col_name)
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    def xget_int(r: int, col_name: str) -> int | None:
        v = xget_num(r, col_name)
        return int(v) if v is not None else None

    for r in range(header_row + 1, sheet.nrows):
        process = xget(r, "공정")
        if not process:
            continue
        _add_wip(
            db,
            run_label,
            result,
            process=process,
            spec_raw=xget(r, "규격") or "",
            core=xget(r, "CORE"),
            voltage_class=xget(r, "전압구분"),
            material=xget(r, "재질"),
            product_name=xget(r, "품명"),
            length_m=xget_num(r, "길이(M)"),
            count=xget_int(r, "개수"),
            total_m=xget_num(r, "총량(M)"),
            core_colors=xget(r, "선심색상"),
            wire_diameter=xget_num(r, "소선경"),
            wire_count=xget_int(r, "가닥수"),
        )

    db.flush()


def _add_wip(
    db: Session,
    run_label: str | None,
    result: dict,
    process: str,
    spec_raw: str,
    core: str | None,
    voltage_class: str | None,
    material: str | None,
    product_name: str | None,
    length_m: float | None,
    count: int | None,
    total_m: float | None,
    core_colors: str | None,
    wire_diameter: float | None,
    wire_count: int | None,
) -> None:
    sq = _extract_sq(spec_raw)
    if total_m is None and length_m is not None and count is not None:
        total_m = length_m * count
    wip = WipInventory(
        process_stage=process,
        voltage_class=voltage_class,
        material=material,
        product_name=product_name,
        spec=spec_raw,
        core=core,
        cross_section=sq,
        length_m=length_m,
        count=count or 1,
        total_length_m=total_m,
        core_colors=core_colors,
        wire_diameter=wire_diameter,
        wire_count=wire_count,
        status="사용가능",
        run_label=run_label,
    )
    db.add(wip)
    result["total"] += 1


def _get(ws, row: int, header_map: dict, col_name: str) -> str | None:
    col = header_map.get(col_name)
    if col is None:
        return None
    val = ws.cell(row, col).value
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _get_num(ws, row: int, header_map: dict, col_name: str) -> float | None:
    val = _get(ws, row, header_map, col_name)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _get_int(ws, row: int, header_map: dict, col_name: str) -> int | None:
    val = _get_num(ws, row, header_map, col_name)
    return int(val) if val is not None else None
