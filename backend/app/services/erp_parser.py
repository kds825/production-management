"""ERP 수주 파일(.xls) 파싱 — 진행/대기 시트에서 SalesOrder 레코드 생성"""

import re
from datetime import datetime

import xlrd
from sqlalchemy.orm import Session

from app.infrastructure.models.sales_order import SalesOrder


def parse_erp_file(file_content: bytes, run_label: str, db: Session) -> dict:
    """
    ERP .xls 파일을 파싱하여 sales_order 테이블에 UPSERT.
    수주번호(order_id) 기준으로 신규건은 INSERT, 기존재건은 UPDATE.

    Args:
        file_content: .xls 파일의 raw bytes
        run_label:    계획 실행 식별자 (e.g. "2025-03-25")
        db:           SQLAlchemy 세션 (commit은 호출부 책임)

    Returns:
        {
            "total":    int,       # 처리된 전체 행 수
            "진행":     int,       # 진행 시트 행 수
            "대기":     int,       # 대기 시트 행 수
            "외주_제외": int,       # is_outsourced=True 로 마킹된 행 수
            "inserted": int,       # 신규 삽입 건수
            "updated":  int,       # 기존 업데이트 건수
            "warnings": list[str], # 비치명적 이슈 메시지
        }
    """
    workbook = xlrd.open_workbook(file_contents=file_content)
    result: dict = {
        "total": 0, "진행": 0, "대기": 0, "외주_제외": 0,
        "inserted": 0, "updated": 0, "warnings": [],
    }

    # ── 기존 레코드 로드 — (order_id, product_group, spec_raw, drum_length_m) 복합키 ──
    # 수주번호+제품군+규격+조장이 모두 같은 경우만 동일 행으로 간주
    existing_orders: dict[tuple, SalesOrder] = {
        (o.order_id, o.product_group or "", o.spec_raw or "", float(o.drum_length_m or 0)): o
        for o in db.query(SalesOrder).all()
    }
    # 신규 삽입 시 order_line 중복 방지: 기존 최대값 이후부터 부여
    existing_max_line: int = max(
        (o.order_line for o in existing_orders.values()), default=0
    )
    new_line_counter = existing_max_line

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

        # 수주번호 컬럼 인덱스 — 빈 행 체크에 사용 (컬럼 0 고정이 아닌 실제 위치)
        order_id_col_idx = headers.get("수주번호")

        # ── 데이터 행 파싱 ───────────────────────────────────────────────────────
        for r in range(header_row_idx + 1, sheet.nrows):
            # 수주번호 컬럼이 비어 있으면 빈 행(합계/소계 행 등)으로 간주 → 건너뜀
            check_col = order_id_col_idx if order_id_col_idx is not None else 0
            if not str(sheet.cell_value(r, check_col)).strip():
                continue

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

                drum_length_m = _get_num(sheet, r, headers, "(수주)조장(M)")
                product_group = _get_str(sheet, r, headers, "제품군") or ""

                # ── UPSERT: (수주번호, 제품군, 규격, 조장) 복합키 기준 ──
                upsert_key = (order_id, product_group, spec_raw, float(drum_length_m or 0))

                if upsert_key in existing_orders:
                    # 기존 레코드 UPDATE — order_line(PK)은 유지
                    existing = existing_orders[upsert_key]
                    existing.order_status = sheet_name
                    existing.run_label = run_label
                    existing.product_group = product_group
                    existing.voltage = _get_str(sheet, r, headers, "전압")
                    existing.spec_raw = spec_raw
                    existing.customer_name = _get_str(sheet, r, headers, "거래처명")
                    existing.due_date = due_date
                    existing.drum_length_m = drum_length_m
                    existing.drum_count = _get_int(sheet, r, headers, "(수주)개수(ea)")
                    existing.ordered_qty_m = _get_num(sheet, r, headers, "(수주)수량(M)")
                    existing.self_plan_qty_m = _get_num(sheet, r, headers, "(자체)조장")
                    existing.unit_price_krw = _get_num(sheet, r, headers, "원화단가")
                    existing.amount_krw = _get_num(sheet, r, headers, "원화금액")
                    existing.cu_weight_kg = _get_num(sheet, r, headers, "CU량")
                    existing.al_weight_kg = _get_num(sheet, r, headers, "AL량")
                    existing.core_count = core_count
                    existing.core_colors = _get_str(sheet, r, headers, "선심색상")
                    existing.sheath_color = _get_str(sheet, r, headers, "색상")
                    existing.neutral_wire = _get_str(sheet, r, headers, "중성선")
                    existing.is_outsourced = is_outsourced
                    result["updated"] += 1
                else:
                    # 신규 INSERT — order_line은 기존 최대값 이후 순번 부여
                    new_line_counter += 1
                    order = SalesOrder(
                        order_id=order_id,
                        order_line=new_line_counter,
                        order_status=sheet_name,
                        product_group=product_group,
                        voltage=_get_str(sheet, r, headers, "전압"),
                        spec_raw=spec_raw,
                        customer_name=_get_str(sheet, r, headers, "거래처명"),
                        due_date=due_date,
                        due_type="출하기준",
                        drum_length_m=drum_length_m,
                        drum_count=_get_int(sheet, r, headers, "(수주)개수(ea)"),
                        ordered_qty_m=_get_num(sheet, r, headers, "(수주)수량(M)"),
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
                    db.add(order)
                    existing_orders[upsert_key] = order  # 동일 시트 내 같은 키 중복 방지
                    result["inserted"] += 1

                if is_outsourced:
                    result["외주_제외"] += 1

                result[sheet_name] += 1
                result["total"] += 1

            except Exception as e:
                # 단일 행 파싱 실패는 전체를 중단하지 않고 경고로 기록
                result["warnings"].append(f"[{sheet_name}] 행 {r + 1}: {e}")

    # ── 진행/대기 중복 제거 ─────────────────────────────────────────────────
    # ERP에 같은 수주가 진행과 대기에 동시 등장하면 대기 쪽을 삭제 (진행 우선).
    from collections import defaultdict

    db.flush()  # dedup 쿼리 전에 INSERT를 DB에 반영해야 조회 가능
    all_orders = db.query(SalesOrder).filter(SalesOrder.run_label == run_label).all()
    key_status: defaultdict[tuple, list] = defaultdict(list)
    for o in all_orders:
        # UPSERT 복합키와 동일: (수주번호, 제품군, 규격, 조장)
        key = (o.order_id, o.product_group or "", o.spec_raw or "", float(o.drum_length_m or 0))
        key_status[key].append(o)

    dup_removed = 0
    for key, orders in key_status.items():
        if len(orders) <= 1:
            continue
        statuses = {o.order_status for o in orders}
        if "진행" in statuses and "대기" in statuses:
            # 진행 유지, 대기 삭제
            for o in orders:
                if o.order_status == "대기":
                    db.delete(o)
                    dup_removed += 1

    if dup_removed:
        result["warnings"].append(
            f"진행/대기 중복 {dup_removed}건 제거 (진행 우선 유지)"
        )
        result["dup_removed"] = dup_removed

    # flush — commit은 호출부(라우터/유즈케이스)에서 트랜잭션과 함께 처리
    db.flush()
    return result


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────


def _find_header_row(sheet, keyword: str, max_scan: int) -> int | None:
    """헤더 행 탐지 — keyword를 포함하면서 다른 컬럼 키워드도 함께 있는 행.
    메타데이터 행("수주번호 :")과 실제 헤더 행을 구분."""
    confirm_keywords = {"전압", "제품군", "규격", "거래처", "납품일"}
    for r in range(min(max_scan, sheet.nrows)):
        row_texts = [str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
        has_keyword = any(
            keyword == t or keyword == t.rstrip(":").strip() for t in row_texts
        )
        if not has_keyword:
            continue
        # 다른 컬럼 키워드 최소 2개 이상 존재하면 헤더 행으로 인정
        confirm_count = sum(
            1 for ck in confirm_keywords if any(ck in t for t in row_texts)
        )
        if confirm_count >= 2:
            return r
    return None


def _build_header_map(sheet, header_row_idx: int) -> dict[str, int]:
    """헤더 행을 순회하여 {컬럼명: 열인덱스} 딕셔너리를 반환.

    동일 컬럼명이 여러 번 등장하면 첫 번째 위치를 사용한다.
    """
    headers: dict[str, int] = {}
    for c in range(sheet.ncols):
        val = str(sheet.cell_value(header_row_idx, c)).strip()
        # 줄바꿈을 공백으로 치환 (ERP 엑셀의 셀 내 줄바꿈 처리)
        val = val.replace("\n", " ").replace("\r", " ").strip()
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
        # 부분 일치 폴백 — 공백/줄바꿈 변형에 대응
        norm = col_name.replace(" ", "").replace("\n", "")
        for h, idx in headers.items():
            h_norm = h.replace(" ", "").replace("\n", "")
            if norm in h_norm or h_norm in norm:
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
        # YYYYMMDD 형식 숫자 (예: 20260529.0) → 문자열 변환 후 파싱
        int_val = int(val)
        if 19000101 <= int_val <= 29991231:
            s = str(int_val)
            try:
                return datetime.strptime(s, "%Y%m%d").date()
            except ValueError:
                pass
        # Excel serial date (작은 숫자)
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
