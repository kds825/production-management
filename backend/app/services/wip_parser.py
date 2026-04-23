"""재공실사 Excel 파일(.xlsx / .xls) 파싱 → WipInventory 레코드 생성"""

import hashlib
import io as _io
import json as _json
from datetime import datetime as _dt

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.wip_upload_log import WipUploadLog
from app.services.wip_matching import _extract_sq

# 실사 Excel 행 길이 ±10% 이내면 후보로 인정
_LENGTH_TOLERANCE = 0.10

# 열 이름 별칭 — 테스트/현장 Excel 표기 차이 흡수
# key: 파서 표준 이름, value: 허용 별칭 목록
_COL_ALIASES: dict[str, list[str]] = {
    "전압구분": ["전압"],
    "길이(M)": ["길이"],
    "총량(M)": ["총량"],
    "선심색상": ["색상"],
}


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
            rows_updated=result.get("updated", 0),
        )
    )

    return result


def parse_wip_file(
    file_content: bytes, db: Session, run_label: str | None = None
) -> dict:
    """재공실사 Excel 파일을 파싱하여 wip_inventory 테이블에 INSERT/UPDATE.

    Args:
        file_content: .xlsx 또는 .xls 파일의 raw bytes
        db:           SQLAlchemy 세션 (commit은 호출부 책임)

    Returns:
        {"total": int, "updated": int, "warnings": list[str]}
    """
    from io import BytesIO

    result = {"total": 0, "updated": 0, "warnings": []}

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


def _resolve_aliases(header_map: dict[str, int]) -> dict[str, int]:
    """표준 열 이름이 없으면 별칭으로 보완한 header_map 반환.

    원본 dict를 수정하지 않고 복사본 반환.
    예: 열 '전압' → 표준 '전압구분' 으로 등록 (표준이 이미 있으면 skip).
    """
    resolved = dict(header_map)
    for canonical, aliases in _COL_ALIASES.items():
        if canonical not in resolved:
            for alias in aliases:
                if alias in resolved:
                    resolved[canonical] = resolved[alias]
                    break
    return resolved


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

    # 별칭 해소 — 현장 Excel 열 이름 차이 흡수
    header_map = _resolve_aliases(header_map)

    for r in range(header_row + 1, ws.max_row + 1):
        process = _get(ws, r, header_map, "공정")
        if not process:
            continue
        row_data = _build_row_data(
            process=process,
            spec_raw=_get(ws, r, header_map, "규격") or "",
            voltage_class=_get(ws, r, header_map, "전압구분"),
            length_m=_get_num(ws, r, header_map, "길이(M)"),
            count=_get_int(ws, r, header_map, "개수"),
            total_m=_get_num(ws, r, header_map, "총량(M)"),
            core_colors=_get(ws, r, header_map, "선심색상"),
            core=_get(ws, r, header_map, "CORE"),
            material=_get(ws, r, header_map, "재질"),
            product_name=_get(ws, r, header_map, "품명"),
            wire_diameter=_get_num(ws, r, header_map, "소선경"),
            wire_count=_get_int(ws, r, header_map, "가닥수"),
        )
        _reconcile_or_insert(row_data, db, run_label, result)

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

    # 별칭 해소
    header_map = _resolve_aliases(header_map)

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
        row_data = _build_row_data(
            process=process,
            spec_raw=xget(r, "규격") or "",
            voltage_class=xget(r, "전압구분"),
            length_m=xget_num(r, "길이(M)"),
            count=xget_int(r, "개수"),
            total_m=xget_num(r, "총량(M)"),
            core_colors=xget(r, "선심색상"),
            core=xget(r, "CORE"),
            material=xget(r, "재질"),
            product_name=xget(r, "품명"),
            wire_diameter=xget_num(r, "소선경"),
            wire_count=xget_int(r, "가닥수"),
        )
        _reconcile_or_insert(row_data, db, run_label, result)

    db.flush()


def _build_row_data(
    process: str,
    spec_raw: str,
    voltage_class: str | None,
    length_m: float | None,
    count: int | None,
    total_m: float | None,
    core_colors: str | None,
    core: str | None,
    material: str | None,
    product_name: str | None,
    wire_diameter: float | None,
    wire_count: int | None,
) -> dict:
    """파싱된 셀 값을 _reconcile_or_insert 에 넘길 row_data dict 로 변환."""
    sq = _extract_sq(spec_raw)
    count = count or 1
    if total_m is None and length_m is not None:
        total_m = length_m * count
    return {
        "process_stage": process,
        "cross_section": sq,
        "voltage_class": voltage_class,
        "core_colors": core_colors,
        "length_m": length_m,
        "count": count,
        "total_m": total_m,
        # 이하 필드는 orphan INSERT 에서만 사용
        "spec": spec_raw,
        "core": core,
        "material": material,
        "product_name": product_name,
        "wire_diameter": wire_diameter,
        "wire_count": wire_count,
    }


# ─────────────────────────────────────────────
# Reconciliation (Spec §G2)
# ─────────────────────────────────────────────


def _find_reconciliation_candidates(
    db: Session,
    *,
    process_stage: str,
    cross_section,
    voltage: str | None,
    colors: str | None,
    length: float | None,
) -> list:
    """후보 검색 — 예상/실적_추정 + spec 필터. unassigned batch WIP 제외."""
    q = db.query(WipInventory).filter(
        WipInventory.status.in_(["예상", "실적_추정"]),
        WipInventory.process_stage == process_stage,
    )
    if cross_section is not None:
        q = q.filter(WipInventory.cross_section == cross_section)
    candidates = q.all()

    # unassigned 배치에서 생성된 WIP 제외
    # — 미배치 배치의 예상 재공은 실사 대상이 아님
    filtered = []
    for w in candidates:
        if w.source_batch_id:
            b = db.get(ProductionBatch, w.source_batch_id)
            if b and b.status == "unassigned":
                continue
        filtered.append(w)

    # length tolerance ±10%
    if length is not None:
        lo = length * (1 - _LENGTH_TOLERANCE)
        hi = length * (1 + _LENGTH_TOLERANCE)
        filtered = [
            w
            for w in filtered
            if w.expected_length_m is None or (lo <= float(w.expected_length_m) <= hi)
        ]

    # 색상 set-overlap; WIP 에 색상 정보 없으면 wildcard 로 간주
    if colors:
        target = {s.strip() for s in colors.split(",") if s.strip()}

        def _ok(w: WipInventory) -> bool:
            if not w.core_colors:
                return True  # wildcard
            w_set = {s.strip() for s in w.core_colors.split(",") if s.strip()}
            return bool(target & w_set) if target else True

        filtered = [w for w in filtered if _ok(w)]

    return filtered


def _pick_by_tiebreaker(
    candidates: list, target_length: float, target_voltage: str | None
):
    """결정적 tiebreaker: exact voltage → narrowest delta → earliest created_at.

    반환: 단일 WipInventory 또는 None (최후까지 동점 — ambiguous 처리 위임).
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # 1) exact voltage 일치
    if target_voltage:
        exact_v = [w for w in candidates if w.voltage_class == target_voltage]
        if len(exact_v) == 1:
            return exact_v[0]
        if exact_v:
            candidates = exact_v  # 범위 축소 후 다음 단계

    # 2) narrowest delta (|expected - actual|)
    def _delta(w: WipInventory) -> float:
        return abs(float(w.expected_length_m or 0) - target_length)

    min_delta = min(_delta(w) for w in candidates)
    narrow = [w for w in candidates if _delta(w) == min_delta]
    if len(narrow) == 1:
        return narrow[0]

    # 3) earliest created_at — 가장 오래된 예상 WIP 우선
    narrow.sort(key=lambda w: w.created_at or _dt.max)
    return narrow[0]  # 동점이어도 첫 번째 선택 (결정적)


def _reconcile_or_insert(
    row_data: dict, db: Session, run_label: str | None, result: dict
) -> None:
    """Excel row → 후보 검색 → 1건 UPDATE (실사_확정) / 0건 orphan INSERT.

    ambiguous 는 tiebreaker 로 결정적으로 해소하므로 실질적으로 발생하지 않음.
    candidates == 0 이면 orphan ('사용가능') 으로 INSERT.
    """
    candidates = _find_reconciliation_candidates(
        db,
        process_stage=row_data["process_stage"],
        cross_section=row_data.get("cross_section"),
        voltage=row_data.get("voltage_class"),
        colors=row_data.get("core_colors"),
        length=row_data.get("length_m"),
    )

    if not candidates:
        # ── orphan INSERT ──
        length = row_data.get("length_m") or 0
        count = row_data.get("count") or 1
        w = WipInventory(
            process_stage=row_data["process_stage"],
            cross_section=row_data.get("cross_section"),
            voltage_class=row_data.get("voltage_class"),
            core_colors=row_data.get("core_colors"),
            spec=row_data.get("spec", ""),
            core=row_data.get("core"),
            material=row_data.get("material"),
            product_name=row_data.get("product_name"),
            wire_diameter=row_data.get("wire_diameter"),
            wire_count=row_data.get("wire_count"),
            length_m=length,
            count=count,
            total_length_m=row_data.get("total_m") or length * count,
            status="사용가능",
            source_batch_id=None,
            run_label=run_label,
        )
        db.add(w)
        result["total"] += 1
        return

    pick = _pick_by_tiebreaker(
        candidates,
        target_length=row_data.get("length_m") or 0,
        target_voltage=row_data.get("voltage_class"),
    )

    if pick is None:
        # 이론상 도달 불가 (tiebreaker 가 항상 1건 반환), 방어적 처리
        result.setdefault("ambiguous", []).append(
            {
                "excel_row": row_data,
                "candidates": [
                    {
                        "wip_id": w.wip_id,
                        "source_batch_id": w.source_batch_id,
                        "expected_length_m": float(w.expected_length_m or 0),
                    }
                    for w in candidates
                ],
            }
        )
        return

    # ── auto-UPDATE → 실사_확정 ──
    length = row_data.get("length_m") or 0
    pick.status = "실사_확정"
    pick.actual_length_m = length
    pick.variance_m = length - float(pick.expected_length_m or 0)
    pick.total_length_m = length
    result["updated"] = result.get("updated", 0) + 1
    result["total"] += 1


# ─────────────────────────────────────────────
# 저수준 셀 접근 헬퍼
# ─────────────────────────────────────────────


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
