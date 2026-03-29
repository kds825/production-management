"""작업지시서 Excel 생성 — openpyxl 기반, 공정별 시트 + SQ 그룹핑"""

from io import BytesIO
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder


# Visible columns rendered in the sheet
VISIBLE_COLS = [
    "품종",
    "규격",
    "색상",
    "거래처",
    "납기",
    "드럼길이",
    "드럼수",
    "총길이",
    "비고",
]
# Hidden columns appended after visible ones (preserved for downstream use)
HIDDEN_COLS = ["수주번호", "batch_id", "단가", "CU/AL량", "상태", "재공매칭"]

# Columns for 진행/대기 sheets (sales order raw view)
STATUS_SHEET_COLS = [
    "품종",
    "규격",
    "색상",
    "거래처",
    "납기",
    "조장",
    "개수",
    "수량",
]

# Preferred sheet creation order
_SHEET_ORDER = ["연선", "B100", "A100", "A120", "연합", "CV절연", "A150시스"]

# ── Styles ────────────────────────────────────────────────────────────────────

_HEADER_FONT = Font(bold=True, size=10)
_HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
_SUBTOTAL_FONT_LABEL = Font(bold=True, color="4472C4", size=10)
_SUBTOTAL_FONT_NUM = Font(bold=True, size=10)
_WIP_FILL = PatternFill("solid", fgColor="C6EFCE")  # green — WIP 매칭됨
_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
_CENTER = Alignment(horizontal="center", vertical="center")

# Column widths (index-aligned to VISIBLE_COLS + HIDDEN_COLS)
_COL_WIDTHS = [15, 22, 8, 18, 12, 10, 8, 12, 25, 14, 10, 10, 10, 8, 10]

# Column widths for 진행/대기 sheets (aligned to STATUS_SHEET_COLS)
_STATUS_COL_WIDTHS = [15, 22, 8, 18, 12, 10, 8, 12]


# ── Public API ────────────────────────────────────────────────────────────────


def export_plan(run_label: str, db: Session) -> BytesIO:
    """production_batch 데이터를 공정별 시트로 구성한 Excel 파일 반환.

    시트 구성:
      1. 진행  — 수주 상태가 "진행"인 sales_order 원본
      2. 대기  — 수주 상태가 "대기"인 sales_order 원본
      3~N. 공정별 배치 시트 (연선, B100, A100, A120, 연합, CV절연, A150시스 …)

    Args:
        run_label: 계획 실행 식별자 (pipeline에서 생성한 타임스탬프 문자열)
        db:        SQLAlchemy 세션 (읽기 전용; commit은 호출부 책임)

    Returns:
        BytesIO — 호출부에서 seek(0) 없이 바로 StreamingResponse에 전달 가능.

    Raises:
        ValueError: 해당 run_label에 배치 데이터가 전혀 없는 경우.
    """
    batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.run_label == run_label)
        .order_by(
            ProductionBatch.process_name,
            ProductionBatch.sq_mm2.desc(),
            ProductionBatch.sheath_color,
        )
        .all()
    )

    if not batches:
        raise ValueError(f"run_label='{run_label}'에 해당하는 배치 데이터가 없습니다.")

    # ── 신선 공정 제외 — 원본 계획서에 없는 공정 ─────────────────────────────
    # 신선(wire drawing)은 연선의 전처리 공정으로 현장 계획서에 표시하지 않는다.
    batches = [b for b in batches if b.process_name != "신선"]

    # ── 틀분할 배치 병합 — 같은 수주+공정을 1행으로 합산 ──────────────────────
    # batch_grouping의 틀분할(2-3)은 스케줄링에 필요하지만,
    # Excel 계획서는 수주 단위 표시이므로 분할 행을 합쳐 원본과 동일한 행 구조를 만든다.
    batches = _merge_lot_splits(batches)

    # ── WIP 룩업: wip_id → process_stage (비고에 실제 WIP 종류 표시용) ───────
    from app.infrastructure.models.wip_inventory import WipInventory

    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id is not None}
    wip_stage_lookup: dict[int, str] = {}
    if wip_ids:
        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_lookup = {w.wip_id: w.process_stage or "" for w in wips}

    # 진행/대기 수주 데이터 조회
    sales_orders = (
        db.query(SalesOrder)
        .filter(SalesOrder.run_label == run_label)
        .order_by(SalesOrder.due_date, SalesOrder.order_id)
        .all()
    )

    wb = Workbook()
    wb.remove(wb.active)  # 기본 Sheet1 제거

    # ── 앞쪽에 진행/대기 시트 추가 ───────────────────────────────────────────
    for status_label in ("진행", "대기"):
        filtered = [so for so in sales_orders if so.order_status == status_label]
        ws = wb.create_sheet(title=status_label)
        _write_status_sheet(ws, filtered)

    # ── process_name 기준으로 배치를 시트별 버킷에 분류 ─────────────────────
    sheet_data: dict[str, list[ProductionBatch]] = {}
    for batch in batches:
        sname = _resolve_sheet_name(batch)
        sheet_data.setdefault(sname, []).append(batch)

    # 정해진 순서대로 시트 생성; 순서 목록에 없는 공정은 말미에 추가
    known = set(_SHEET_ORDER)
    extra = [s for s in sheet_data if s not in known]
    for sname in _SHEET_ORDER + extra:
        if sname not in sheet_data:
            continue
        ws = wb.create_sheet(title=sname)
        _write_sheet(ws, sheet_data[sname], wip_stage_lookup)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# ── Batch merge helper ────────────────────────────────────────────────────────


def _merge_lot_splits(batches: list[ProductionBatch]) -> list[ProductionBatch]:
    """같은 수주+공정의 틀분할 배치를 1행으로 병합한다.

    batch_grouping의 틀단위 분할(2-3)이 만든 복수 배치를 합쳐
    원본 계획서와 동일한 수주 단위 행 구조를 만든다.

    병합 대상: (sales_order_id, sales_order_line, process_name)이 동일한 배치 그룹.
    합산 항목: total_length_m, extra_length_m, estimated_duration_min.
    대표 배치: 그룹의 첫 번째 배치 (정렬 순서 유지).
    remarks에서 '틀N' 표기를 제거한다.
    """
    from collections import OrderedDict

    groups: OrderedDict[tuple, list[ProductionBatch]] = OrderedDict()
    for b in batches:
        key = (b.sales_order_id, b.sales_order_line, b.process_name)
        groups.setdefault(key, []).append(b)

    merged: list[ProductionBatch] = []
    for key, group in groups.items():
        base = group[0]
        if len(group) == 1:
            merged.append(base)
            continue

        # 합산: total_length, extra_length, duration
        total_len = sum(float(b.total_length_m or 0) for b in group)
        extra_len = sum(float(b.extra_length_m or 0) for b in group)
        duration = sum(float(b.estimated_duration_min or 0) for b in group) or None

        # DB 객체를 직접 수정 (export는 읽기 전용, commit하지 않음)
        base.total_length_m = total_len
        base.extra_length_m = extra_len
        base.estimated_duration_min = duration

        # remarks에서 '틀N' 제거 — 병합했으므로 불필요
        if base.remarks:
            import re

            base.remarks = re.sub(r"틀\d+\s*/?", "", base.remarks).strip(" /") or None

        merged.append(base)

    return merged


# ── Sheet-level helpers ───────────────────────────────────────────────────────


def _resolve_sheet_name(batch: ProductionBatch) -> str:
    """배치의 process_name + sheath_color를 조합하여 시트명을 결정한다.

    저압시스 공정은 색상에 따라 A100/A120으로 분기:
      A120 대상: 흑, 청 계열 (국내 표준 2색 시스 조합)
      A100 대상: 그 외 모든 색상
    """
    proc = batch.process_name

    match proc:
        case "연선":
            # 고압 제품은 별도 시트
            v = (batch.voltage or "").strip()
            if "22.9" in v or "35" in v or "URD" in (batch.product_group or "").upper():
                return "고압연선"
            return "연선"
        case "저압절연":
            return "B100"
        case "저압시스":
            color = (batch.sheath_color or "").strip().upper()
            # 흑/청 표기 변형 모두 포함 (한글 약어, 영문 표기)
            if color in {"흑", "청", "흑색", "청색", "BLACK", "BLUE", "BK", "BL"}:
                return "A120"
            return "A100"
        case "연합":
            return "연합"
        case "고압절연":
            return "CV절연"
        case "고압시스":
            return "A150시스"
        case _:
            # 미정의 공정은 이름 그대로 시트로 생성 (확장성 유지)
            return proc


def _write_status_sheet(ws, orders: list) -> None:
    """진행/대기 시트: 수주 원본 데이터를 컬럼별로 출력한다."""
    _write_header(ws, STATUS_SHEET_COLS)
    _apply_col_widths_list(ws, _STATUS_COL_WIDTHS)

    ws.freeze_panes = "A2"
    last_letter = _col_letter(len(STATUS_SHEET_COLS))
    ws.auto_filter.ref = f"A1:{last_letter}1"

    for row_num, so in enumerate(orders, start=2):
        due_str = (
            so.due_date.strftime("%Y%m%d")
            if isinstance(so.due_date, date)
            else str(so.due_date or "")
        )
        color_val = so.sheath_color or so.core_colors or ""
        values = [
            so.product_group or "",
            so.spec_raw or "",
            color_val,
            so.customer_name or "",
            due_str,
            so.drum_length_m,
            so.drum_count,
            so.ordered_qty_m,
        ]
        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=row_num, column=col_idx, value=val)
            cell.border = _THIN_BORDER


def _write_sheet(
    ws, batches: list[ProductionBatch], wip_stage_lookup: dict[int, str]
) -> None:
    """단일 시트에 헤더 → 데이터 행(SQ 그룹 소계 포함) → 서식 적용."""
    all_cols = VISIBLE_COLS + HIDDEN_COLS
    total_cols = len(all_cols)

    _write_header(ws, all_cols)
    _apply_col_widths(ws, total_cols)
    _hide_trailing_cols(ws, len(VISIBLE_COLS) + 1, total_cols)

    row_num = 2
    current_sq = None
    sq_total_m: float = 0.0
    sq_batch_count: int = 0

    for batch in batches:
        sq = float(batch.sq_mm2) if batch.sq_mm2 is not None else 0.0

        # SQ 경계 — 이전 그룹 소계 행 삽입
        if current_sq is not None and sq != current_sq:
            row_num = _write_subtotal(
                ws, row_num, current_sq, sq_total_m, sq_batch_count, total_cols
            )
            sq_total_m = 0.0
            sq_batch_count = 0

        current_sq = sq

        # 멀티코어 배치는 색상별로 행 분리
        color_rows = _expand_color_rows(batch)
        for color_label, length_m in color_rows:
            sq_total_m += length_m
            sq_batch_count += 1
            row_num = _write_data_row(
                ws,
                row_num,
                batch,
                all_cols,
                color_label,
                length_m,
                wip_stage_lookup,
            )

    # 마지막 그룹 소계
    if current_sq is not None:
        _write_subtotal(ws, row_num, current_sq, sq_total_m, sq_batch_count, total_cols)

    # 헤더 고정 + 자동 필터 (가시 열 범위만)
    ws.freeze_panes = "A2"
    last_visible_letter = _col_letter(len(VISIBLE_COLS))
    ws.auto_filter.ref = f"A1:{last_visible_letter}1"


# ── Row-level helpers ─────────────────────────────────────────────────────────


def _expand_color_rows(batch: ProductionBatch) -> list[tuple[str, float]]:
    """배치를 색상별 (색상 레이블, 길이) 목록으로 변환한다.

    멀티코어(core_count > 1)이고 core_colors에 복수 색상이 있으면
    총길이를 색상 수로 균등 분배하여 행을 분리한다.
    싱글코어 또는 색상 정보가 없으면 단일 행으로 반환한다.
    """
    core_count = batch.core_count or 1
    total_m = float(batch.total_length_m or 0)

    # 단일 코어는 분리 불필요
    if core_count <= 1:
        color = batch.sheath_color or batch.core_colors or ""
        return [(color, total_m)]

    # core_colors 파싱 — "갈/흑/회" 또는 "갈,흑,회" 형식 지원
    raw_colors = batch.core_colors or ""
    if "/" in raw_colors:
        colors = [c.strip() for c in raw_colors.split("/") if c.strip()]
    elif "," in raw_colors:
        colors = [c.strip() for c in raw_colors.split(",") if c.strip()]
    else:
        # 파싱 불가 — 단일 행으로 fallback
        color = batch.sheath_color or raw_colors or ""
        return [(color, total_m)]

    if not colors:
        color = batch.sheath_color or raw_colors or ""
        return [(color, total_m)]

    # 총길이를 색상 수로 균등 분배 (소수점 버림, 마지막 행에서 나머지 보정)
    n = len(colors)
    per_color_m = round(total_m / n, 1)
    rows: list[tuple[str, float]] = []
    allocated = 0.0
    for i, color in enumerate(colors):
        if i == n - 1:
            # 마지막 행: 반올림 오차를 흡수
            length = round(total_m - allocated, 1)
        else:
            length = per_color_m
            allocated += length
        rows.append((color, length))
    return rows


def _build_remarks(
    batch: ProductionBatch,
    base_remarks: str | None = None,
    *,
    wip_stage: str | None = None,
) -> str:
    """비고 문자열을 조합한다.

    wip_stage: WIP의 실제 process_stage ("연선재고", "절연재고" 등).
    절연재고는 연선·절연 공정 모두에서 "절연재고 사용"으로 표시한다.
    (이전: batch.process_name에서 유도 → 연선 시트에서 잘못 표시되던 버그 수정)
    """
    parts: list[str] = []

    # 원본 비고 우선
    if base_remarks:
        parts.append(base_remarks.strip())

    # WIP 매칭 텍스트 — 실제 WIP 종류를 그대로 사용
    if batch.wip_matched_id is not None:
        if wip_stage:
            parts.append(f"{wip_stage} 사용")
        else:
            parts.append("재공재고 사용")

    # SM 재고 발생 예정량
    wip_out = float(batch.wip_output_expected_m or 0)
    if wip_out > 0:
        parts.append(f"SM {int(wip_out)}m")

    return " / ".join(parts)


def _write_header(ws, all_cols: list[str]) -> None:
    """행 1에 헤더 셀을 쓰고 스타일을 적용한다."""
    for col_idx, col_name in enumerate(all_cols, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.border = _THIN_BORDER
        cell.alignment = _CENTER


def _write_data_row(
    ws,
    row_num: int,
    batch: ProductionBatch,
    all_cols: list[str],
    color_label: str,
    length_m: float,
    wip_stage_lookup: dict[int, str],
) -> int:
    """배치 1건(색상 분리 후 단일 행)을 데이터 행으로 작성하고 다음 row_num을 반환한다."""
    due_str = (
        batch.due_date.strftime("%Y%m%d")
        if isinstance(batch.due_date, date)
        else str(batch.due_date or "")
    )

    # 규격 표기: "NcxMSQ" 형식으로 조합; 둘 다 없으면 빈 문자열
    spec_str = ""
    if batch.sq_mm2 is not None:
        sq_int = int(batch.sq_mm2)
        cores = batch.core_count or 1
        spec_str = f"{cores}C x {sq_int}SQ"

    # WIP 실제 종류 조회: wip_stage_lookup에서 process_stage 가져옴
    wip_stage: str | None = None
    if batch.wip_matched_id is not None:
        wip_stage = wip_stage_lookup.get(batch.wip_matched_id)
    remarks = _build_remarks(batch, batch.remarks, wip_stage=wip_stage)

    visible_values = [
        batch.product_group or "",
        spec_str,
        color_label,
        batch.customer_name or "",
        due_str,
        batch.drum_length_m,
        batch.drum_count,
        length_m,
        remarks,
    ]
    hidden_values = [
        batch.sales_order_id or "",
        batch.batch_id,
        None,  # 단가 — 미사용 (ERP 원본 유지용 자리)
        None,  # CU/AL량 — 미사용
        batch.status or "",
        "Y" if batch.wip_matched_id else "",  # 재공매칭 여부
    ]

    row_fill = _WIP_FILL if batch.wip_matched_id else None

    for col_idx, val in enumerate(visible_values + hidden_values, 1):
        cell = ws.cell(row=row_num, column=col_idx, value=val)
        cell.border = _THIN_BORDER
        if row_fill:
            cell.fill = row_fill

    return row_num + 1


def _write_subtotal(
    ws,
    row_num: int,
    sq: float,
    total_length: float,
    count: int,
    total_cols: int,
) -> int:
    """SQ 그룹 소계 행을 쓰고 다음 row_num을 반환한다."""
    sq_label = int(sq) if sq == int(sq) else sq

    label_cell = ws.cell(row=row_num, column=1, value=f"{sq_label}SQ → {count}건")
    label_cell.font = _SUBTOTAL_FONT_LABEL

    # 총길이(8번째 가시 열)에 소계 합산값 표시
    total_cell = ws.cell(row=row_num, column=8, value=round(total_length, 1))
    total_cell.font = _SUBTOTAL_FONT_NUM

    # 소계 행 전체에 테두리 적용
    for col_idx in range(1, total_cols + 1):
        ws.cell(row=row_num, column=col_idx).border = _THIN_BORDER

    return row_num + 1


# ── Formatting utilities ──────────────────────────────────────────────────────


def _apply_col_widths(ws, total_cols: int) -> None:
    """열 너비 적용 — _COL_WIDTHS보다 열이 많으면 기본 너비(12) 사용."""
    for i in range(1, total_cols + 1):
        letter = _col_letter(i)
        width = _COL_WIDTHS[i - 1] if i - 1 < len(_COL_WIDTHS) else 12
        ws.column_dimensions[letter].width = width


def _apply_col_widths_list(ws, widths: list[int]) -> None:
    """명시적 너비 리스트로 열 너비 적용."""
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[_col_letter(i)].width = width


def _hide_trailing_cols(ws, start_col: int, end_col: int) -> None:
    """start_col ~ end_col 범위의 열을 숨김 처리한다."""
    for col_idx in range(start_col, end_col + 1):
        ws.column_dimensions[_col_letter(col_idx)].hidden = True


def _col_letter(col_idx: int) -> str:
    """1-based 열 인덱스를 Excel 열 문자로 변환 (openpyxl 내장 함수 래퍼).

    openpyxl의 get_column_letter는 26열 초과(AA, AB…)도 올바르게 처리한다.
    직접 chr(64 + i) 계산을 쓰면 27열 이상에서 깨지므로 이 함수를 사용한다.
    """
    from openpyxl.utils import get_column_letter

    return get_column_letter(col_idx)
