"""재공실사 Excel 파일(.xlsx) 파싱 → WipInventory 레코드 생성"""

import re

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.infrastructure.models.wip_inventory import WipInventory


def parse_wip_file(file_content: bytes, db: Session, run_label: str | None = None) -> dict:
    """재공실사 Excel 파일을 파싱하여 wip_inventory 테이블에 INSERT.

    Args:
        file_content: .xlsx 파일의 raw bytes
        db:           SQLAlchemy 세션 (commit은 호출부 책임)

    Returns:
        {"total": int, "warnings": list[str]}
    """
    from io import BytesIO

    result = {"total": 0, "warnings": []}

    wb = load_workbook(BytesIO(file_content), data_only=True)
    ws = wb.active

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
        return result

    # 헤더 맵 구성
    for c in range(1, ws.max_column + 1):
        val = str(ws.cell(header_row, c).value or "").strip()
        if val:
            header_map[val] = c

    # 데이터 행 파싱
    for r in range(header_row + 1, ws.max_row + 1):
        process = _get(ws, r, header_map, "공정")
        if not process:
            continue

        spec_raw = _get(ws, r, header_map, "규격") or ""
        sq = _extract_sq(spec_raw)

        length_m = _get_num(ws, r, header_map, "길이(M)")
        count = _get_int(ws, r, header_map, "개수")
        total_m = _get_num(ws, r, header_map, "총량(M)")

        # 총량이 없으면 길이×개수로 계산
        if total_m is None and length_m is not None and count is not None:
            total_m = length_m * count

        wip = WipInventory(
            process_stage=process,
            voltage_class=_get(ws, r, header_map, "전압구분"),
            material=_get(ws, r, header_map, "재질"),
            product_name=_get(ws, r, header_map, "품명"),
            spec=spec_raw,
            cross_section=sq,
            length_m=length_m,
            count=count or 1,
            total_length_m=total_m,
            core_colors=_get(ws, r, header_map, "선심색상"),
            wire_diameter=_get_num(ws, r, header_map, "소선경"),
            wire_count=_get_int(ws, r, header_map, "가닥수"),
            status="사용가능",
            run_label=run_label,
        )
        db.add(wip)
        result["total"] += 1

    db.flush()
    return result


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


def _extract_sq(spec: str) -> float | None:
    if not spec:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None
