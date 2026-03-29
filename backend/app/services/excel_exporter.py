"""작업지시서 Excel 생성 — openpyxl 기반, 공정별 시트 + SQ 그룹핑"""

from io import BytesIO
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch


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


# ── Public API ────────────────────────────────────────────────────────────────


def export_plan(run_label: str, db: Session) -> BytesIO:
    """production_batch 데이터를 공정별 시트로 구성한 Excel 파일 반환.

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

    wb = Workbook()
    wb.remove(wb.active)  # 기본 Sheet1 제거

    # process_name 기준으로 배치를 시트별 버킷에 분류
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
        _write_sheet(ws, sheet_data[sname])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


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


def _write_sheet(ws, batches: list[ProductionBatch]) -> None:
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
        sq_total_m += float(batch.total_length_m or 0)
        sq_batch_count += 1

        row_num = _write_data_row(ws, row_num, batch, all_cols)

    # 마지막 그룹 소계
    if current_sq is not None:
        _write_subtotal(ws, row_num, current_sq, sq_total_m, sq_batch_count, total_cols)

    # 헤더 고정 + 자동 필터 (가시 열 범위만)
    ws.freeze_panes = "A2"
    last_visible_letter = _col_letter(len(VISIBLE_COLS))
    ws.auto_filter.ref = f"A1:{last_visible_letter}1"


# ── Row-level helpers ─────────────────────────────────────────────────────────


def _write_header(ws, all_cols: list[str]) -> None:
    """행 1에 헤더 셀을 쓰고 스타일을 적용한다."""
    for col_idx, col_name in enumerate(all_cols, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.border = _THIN_BORDER
        cell.alignment = _CENTER


def _write_data_row(
    ws, row_num: int, batch: ProductionBatch, all_cols: list[str]
) -> int:
    """배치 1건을 데이터 행으로 작성하고 다음 row_num을 반환한다."""
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

    visible_values = [
        batch.product_group or "",
        spec_str,
        batch.sheath_color or batch.core_colors or "",
        batch.customer_name or "",
        due_str,
        batch.drum_length_m,
        batch.drum_count,
        batch.total_length_m,
        batch.remarks or "",
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
