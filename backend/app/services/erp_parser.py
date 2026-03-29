"""ERP 수주 파일(.xls) 파싱 — 진행/대기 시트에서 SalesOrder 레코드 생성"""

import re
from datetime import datetime

import xlrd
from sqlalchemy.orm import Session

from app.infrastructure.models.sales_order import SalesOrder


def parse_erp_file(file_content: bytes, run_label: str, db: Session) -> dict:
    """
    ERP .xls 파일을 파싱하여 sales_order 테이블에 INSERT.

    Args:
        file_content: .xls 파일의 raw bytes
        run_label:    계획 실행 식별자 (e.g. "2025-03-25")
        db:           SQLAlchemy 세션 (commit은 호출부 책임)

    Returns:
        {
            "total":    int,       # 삽입된 전체 행 수
            "진행":     int,       # 진행 시트 행 수
            "대기":     int,       # 대기 시트 행 수
            "외주_제외": int,       # is_outsourced=True 로 마킹된 행 수
            "warnings": list[str], # 비치명적 이슈 메시지
        }
    """
    workbook = xlrd.open_workbook(file_contents=file_content)
    result: dict = {"total": 0, "진행": 0, "대기": 0, "외주_제외": 0, "warnings": []}

    for sheet_name in ["진행", "대기"]:
        if sheet_name not in workbook.sheet_names():
            result["warnings"].append(f"시트 '{sheet_name}' 없음 — 건너뜀")
            continue

        sheet = workbook.sheet_by_name(sheet_name)

        # ── 헤더 행 탐지: "수주번호"를 포함한 셀이 있는 첫 번째 행 ──────────────
        # 행 0-20은 메타데이터/필터 라벨이므로 최대 25행까지만 탐색
        header_row_idx = _find_header_row(sheet, keyword="수주번호", max_scan=25)
        if header_row_idx is None:
            result["warnings"].append(
                f"시트 '{sheet_name}': 헤더 행 탐지 실패 — 건너뜀"
            )
            continue

        # ── 컬럼명 → 인덱스 맵 구성 ────────────────────────────────────────────
        headers = _build_header_map(sheet, header_row_idx)

        # ── 데이터 행 파싱 ───────────────────────────────────────────────────────
        line_num = 0
        for r in range(header_row_idx + 1, sheet.nrows):
            # 첫 번째 셀(수주번호 위치 또는 컬럼 0)이 비어 있으면 빈 행으로 간주
            if not str(sheet.cell_value(r, 0)).strip():
                continue

            line_num += 1

            try:
                order_id = _get_str(sheet, r, headers, "수주번호")
                if not order_id:
                    # 수주번호 없는 행은 합계/소계 행일 가능성이 높으므로 무시
                    continue

                # 외주 여부 — "Y" (대소문자 무관)를 외주로 해석
                outsource_plan = _get_str(sheet, r, headers, "외주계획") or ""
                is_outsourced = outsource_plan.strip().upper() == "Y"

                # 납품일 — Excel 시리얼 또는 문자열 모두 처리
                due_date_raw = _get_cell(sheet, r, headers, "납품일")
                due_date = _parse_date(due_date_raw, workbook.datemode)

                # 규격에서 심수(Core Count) 추출
                spec_raw = _get_str(sheet, r, headers, "규격") or ""
                core_count = _extract_core_count(spec_raw)

                order = SalesOrder(
                    order_id=order_id,
                    order_line=line_num,
                    order_status=sheet_name,  # 진행 or 대기
                    product_group=_get_str(sheet, r, headers, "제품군"),
                    voltage=_get_str(sheet, r, headers, "전압"),
                    spec_raw=spec_raw,
                    customer_name=_get_str(sheet, r, headers, "거래처명"),
                    due_date=due_date,
                    due_type="출하기준",  # 기본값; customer_master 조회 후 덮어씀
                    drum_length_m=_get_num(sheet, r, headers, "(수주)조장(M)"),
                    drum_count=_get_int(sheet, r, headers, "(수주)개수(ea)"),
                    ordered_qty_m=_get_num(sheet, r, headers, "(수주)수량(M)"),
                    # "(자체)조장" 컬럼이 없을 수 있으므로 None 허용
                    self_plan_qty_m=_get_num(sheet, r, headers, "(자체)조장"),
                    unit_price_krw=_get_num(sheet, r, headers, "원화단가"),
                    amount_krw=_get_num(sheet, r, headers, "원화금액"),
                    cu_weight_kg=_get_num(sheet, r, headers, "CU량"),
                    al_weight_kg=_get_num(sheet, r, headers, "AL량"),
                    core_count=core_count,
                    core_colors=_get_str(sheet, r, headers, "선심색상"),
                    sheath_color=_get_str(sheet, r, headers, "색상"),
                    neutral_wire=_get_str(sheet, r, headers, "중성선"),
                    is_outsourced=is_outsourced,
                    run_label=run_label,
                )

                if is_outsourced:
                    result["외주_제외"] += 1

                db.add(order)
                result[sheet_name] += 1
                result["total"] += 1

            except Exception as e:
                # 단일 행 파싱 실패는 전체를 중단하지 않고 경고로 기록
                result["warnings"].append(f"[{sheet_name}] 행 {r + 1}: {e}")

    # flush — commit은 호출부(라우터/유즈케이스)에서 트랜잭션과 함께 처리
    db.flush()
    return result


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────


def _find_header_row(sheet, keyword: str, max_scan: int) -> int | None:
    """keyword를 포함하는 첫 번째 행의 인덱스를 반환. 없으면 None."""
    for r in range(min(max_scan, sheet.nrows)):
        for c in range(sheet.ncols):
            if keyword in str(sheet.cell_value(r, c)).strip():
                return r
    return None


def _build_header_map(sheet, header_row_idx: int) -> dict[str, int]:
    """헤더 행을 순회하여 {컬럼명: 열인덱스} 딕셔너리를 반환.

    동일 컬럼명이 여러 번 등장하면 첫 번째 위치를 사용한다.
    """
    headers: dict[str, int] = {}
    for c in range(sheet.ncols):
        val = str(sheet.cell_value(header_row_idx, c)).strip()
        if val and val not in headers:
            headers[val] = c
    return headers


def _get_cell(sheet, row: int, headers: dict[str, int], col_name: str):
    """헤더 맵에서 col_name을 찾아 셀 값을 반환.

    정확히 일치하는 키가 없으면 col_name을 부분 문자열로 포함하는 첫 번째 키로
    폴백한다 (e.g. "(수주)조장(M)" vs 공백 포함 변형).
    컬럼 자체가 없으면 None 반환.
    """
    col_idx = headers.get(col_name)
    if col_idx is None:
        # 부분 일치 폴백 — 컬럼명 표기 변형에 대응
        for h, idx in headers.items():
            if col_name in h:
                col_idx = idx
                break
    if col_idx is None:
        return None
    return sheet.cell_value(row, col_idx)


def _get_str(sheet, row: int, headers: dict[str, int], col_name: str) -> str | None:
    """셀 값을 문자열로 반환. 빈 문자열은 None으로 정규화."""
    val = _get_cell(sheet, row, headers, col_name)
    if val is None:
        return None
    # xlrd는 숫자 셀을 float으로 반환하므로 정수형 표현으로 변환
    if isinstance(val, float) and val == int(val):
        s = str(int(val))
    else:
        s = str(val).strip()
    return s if s else None


def _get_num(sheet, row: int, headers: dict[str, int], col_name: str) -> float | None:
    """셀 값을 float으로 반환. 변환 불가 시 None."""
    val = _get_cell(sheet, row, headers, col_name)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _get_int(sheet, row: int, headers: dict[str, int], col_name: str) -> int | None:
    """셀 값을 int로 반환. 변환 불가 시 None."""
    val = _get_num(sheet, row, headers, col_name)
    return int(val) if val is not None else None


def _parse_date(val, datemode: int):
    """Excel 날짜 값(시리얼 float 또는 문자열)을 `datetime.date`로 변환.

    - float: xlrd.xldate_as_tuple을 이용해 시리얼 번호 변환
    - str:  %Y-%m-%d / %Y%m%d / %Y.%m.%d 포맷 순서로 시도
    변환 실패 시 None 반환 (예외를 외부로 전파하지 않음).
    """
    if val is None or val == "":
        return None

    if isinstance(val, float):
        try:
            dt_tuple = xlrd.xldate_as_tuple(val, datemode)
            return datetime(*dt_tuple).date()
        except Exception:
            return None

    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _extract_core_count(spec: str) -> int:
    """규격 문자열에서 심수(Core Count)를 추출한다.

    Examples:
        "4C x 35SQ"   → 4
        "1C x 300SQ"  → 1
        "3C+1C x 6SQ" → 3   (첫 번째 숫자)
        ""            → 1   (파싱 불가 시 단심 기본값)
    """
    m = re.match(r"(\d+)\s*[Cc]", spec.strip())
    return int(m.group(1)) if m else 1
