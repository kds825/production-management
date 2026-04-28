"""Excel 내보내기 helper 함수 — batch 병합 / 시트 이름 / 행 작성.

excel_exporter.py 분할 (Task 1.11, B-6.1).
원본 import path 보존 — excel_exporter 가 본 모듈에서 re-export.
"""

import re
from datetime import date

from openpyxl.styles import Font, Border, Side

from app.infrastructure.models.production_batch import ProductionBatch


# Visible columns rendered in the sheet (헤더 행은 없지만 열 순서 정의용)
# 조장(m) = 드럼길이, 조수 = 드럼수 (현장 용어)
VISIBLE_COLS = [
    "품종",  # col 1 (A)
    "규격",  # col 2 (B)
    "색상",  # col 3 (C)
    "거래처",  # col 4 (D)
    "납기",  # col 5 (E)
    "조장(m)",  # col 6 (F) — drum_length_m
    "조수",  # col 7 (G) — drum_count
    "총길이(m)",  # col 8 (H) — total_length_m
    "비고",  # col 9 (I)
]
# Hidden columns appended after visible ones (preserved for downstream use)
HIDDEN_COLS = ["수주번호", "batch_id", "단가", "CU/AL량", "상태", "재공매칭"]

# ── Styles ────────────────────────────────────────────────────────────────────

# 폰트 색상 코딩 (3.25계획.xls 템플릿 기준)
_WIP_FONT = Font(color="333333", size=10)  # 짙은 회색 — WIP 재공재고 사용 행
_NEW_FONT = Font(color="0066CC", size=10)  # 파란색 — 신규 스케줄 행
_SUBTOTAL_FONT = Font(color="FF6600", bold=True, size=10)  # 주황색 볼드 — 소계/주석
_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
# Column widths (index-aligned to VISIBLE_COLS + HIDDEN_COLS)
_COL_WIDTHS = [15, 22, 8, 18, 12, 10, 8, 12, 25, 14, 10, 10, 10, 8, 10]


def _merge_lot_splits(batches: list[ProductionBatch]) -> list[ProductionBatch]:
    """두 단계 병합으로 원본 계획서와 동일한 행 구조를 만든다.

    Stage 1: 틀분할 병합 — (order_id, order_line, process_name) 동일한 배치 합산.
    Stage 2: 진행/대기 중복 제거 — 같은 수주가 진행/대기에 각각 존재할 때,
             (order_id, process, sq, color, drum_length, total_length)가 완전 동일하면 중복 제거.
    """
    from collections import OrderedDict
    import re as _re

    # ── Stage 1: 틀분할 배치 합산 ────────────────────────────────────────────
    groups: OrderedDict[tuple, list[ProductionBatch]] = OrderedDict()
    for b in batches:
        # drum_count를 키에 포함 — 파이프라인 분할(dc=10+4)은 별도 행 유지,
        # 틀분할(같은 dc)은 합산됨
        key = (
            b.sales_order_id,
            b.sales_order_line,
            b.process_name,
            int(b.drum_count or 1),
        )
        groups.setdefault(key, []).append(b)

    stage1: list[ProductionBatch] = []
    for key, group in groups.items():
        base = group[0]
        if len(group) == 1:
            stage1.append(base)
            continue

        total_len = sum(float(b.total_length_m or 0) for b in group)
        extra_len = sum(float(b.extra_length_m or 0) for b in group)
        duration = sum(float(b.estimated_duration_min or 0) for b in group) or None

        base.total_length_m = total_len
        base.extra_length_m = extra_len
        base.estimated_duration_min = duration
        # drum_count는 합산하지 않음 — 틀분할은 길이를 나누는 것이지 드럼 수를 바꾸지 않음
        if base.remarks:
            base.remarks = _re.sub(r"틀\d+\s*/?", "", base.remarks).strip(" /") or None

        stage1.append(base)

    # Stage 2 dedup 제거 — 진행/대기 중복은 ERP 데이터 구조에 따른 것으로
    # 별개 order_line을 잘못 제거하는 부작용이 더 크므로 비활성화.
    # 진행/대기 중복(+3 35SQ)은 허용 가능한 차이.
    return stage1


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
        case "T/P":
            return "TP"
        case _:
            # Excel 시트 이름 금지 문자 제거: / \ * ? [ ] :
            safe = re.sub(r"[/\\*?\[\]:]", "_", proc)
            return safe[:31]  # Excel 시트명 최대 31자


def _build_remarks(
    batch: ProductionBatch,
    *,
    wip_stage: str | None = None,
    wip_total_len_m: float | None = None,
) -> str:
    """Excel 비고 문자열 — WIP 사용 문구만 출력, 그 외 텍스트 없음.

    wip_stage: WIP의 실제 process_stage ("연선재고", "절연재고" 등).
    wip_total_len_m: 동일 wip_matched_id에 매칭된 총 길이(m).
    """
    if batch.wip_matched_id is None:
        return ""

    wip_len = float(wip_total_len_m or 0)
    wip_len_int = int(wip_len) if wip_len > 0 else 0

    if wip_stage:
        return (
            f"{wip_stage}{wip_len_int}m 사용"
            if wip_len_int > 0
            else f"{wip_stage} 사용"
        )
    return f"재공재고 {wip_len_int}m 사용" if wip_len_int > 0 else "재공재고 사용"


def _write_data_row(
    ws,
    row_num: int,
    batch: ProductionBatch,
    all_cols: list[str],
    color_label: str,
    length_m: float,
    wip_stage_lookup: dict[int, str],
    wip_total_len_lookup: dict[int, float],
    *,
    remarks_override: str | None = None,
) -> int:
    """배치 1건을 데이터 행으로 작성하고 다음 row_num을 반환한다.

    WIP 매칭 여부에 따라 폰트 색상을 분기한다:
      - WIP 매칭 → 짙은 회색(_WIP_FONT) — 재공재고 사용 행
      - 신규 → 파란색(_NEW_FONT) — 새로 편성된 행
    """
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
    wip_total_len_m: float | None = None
    if batch.wip_matched_id is not None:
        wip_stage = wip_stage_lookup.get(batch.wip_matched_id)
        wip_total_len_m = wip_total_len_lookup.get(batch.wip_matched_id, 0.0)

    remarks = (
        remarks_override
        if remarks_override is not None
        else _build_remarks(
            batch,
            wip_stage=wip_stage,
            wip_total_len_m=wip_total_len_m,
        )
    )

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

    # 폰트 색상: WIP 매칭 → 짙은 회색, 신규 → 파란색
    row_font = _WIP_FONT if batch.wip_matched_id else _NEW_FONT

    for col_idx, val in enumerate(visible_values + hidden_values, 1):
        cell = ws.cell(row=row_num, column=col_idx, value=val)
        cell.border = _THIN_BORDER
        cell.font = row_font

    return row_num + 1


def _write_subtotal(
    ws,
    row_num: int,
    sq: float,
    start_row: int,
    end_row: int,
    count: int,
    total_cols: int,
    *,
    is_stranding: bool = False,
    split_label: str | None = None,
) -> int:
    """SQ 그룹 소계 행을 쓰고 다음 row_num을 반환한다.

    연선 시트: WIP 재공 사용 행("재공매칭"="Y")을 제외한 SUMIF 수식 삽입.
    기타 시트: 전체 SUM 수식.
    소계 폰트: 주황색 볼드 (RGB 255,102,0).
    split_label: "1차", "2차" 등 분할 배치 표기 (연선 시트에서만 사용).
    """
    sq_label = int(sq) if sq == int(sq) else sq
    total_length_col = 8  # H열 = 총길이(m)
    # "재공매칭" hidden 열 인덱스: VISIBLE_COLS(9) + HIDDEN_COLS 내 index 5 → col 15
    wip_flag_col = len(VISIBLE_COLS) + HIDDEN_COLS.index("재공매칭") + 1

    unit = "틀" if is_stranding else "건"
    # 분할 배치인 경우 "120SQ → 2틀 (1차 배치)" 형식으로 표시
    split_suffix = f" ({split_label} 배치)" if split_label else ""
    label_cell = ws.cell(
        row=row_num, column=1, value=f"{sq_label}SQ → {count}{unit}{split_suffix}"
    )
    label_cell.font = _SUBTOTAL_FONT

    h_col = _col_letter(total_length_col)
    w_col = _col_letter(wip_flag_col)
    if is_stranding:
        # 연선 시트: WIP 재공 사용 행 제외 (실제 연선 작업량만 합산)
        sum_formula = (
            f'=SUMIF({w_col}{start_row}:{w_col}{end_row},"<>Y",'
            f"{h_col}{start_row}:{h_col}{end_row})"
        )
    else:
        sum_formula = f"=SUM({h_col}{start_row}:{h_col}{end_row})"
    total_cell = ws.cell(row=row_num, column=total_length_col, value=sum_formula)
    total_cell.font = _SUBTOTAL_FONT

    # 소계 행 전체에 테두리 적용
    for col_idx in range(1, total_cols + 1):
        ws.cell(row=row_num, column=col_idx).border = _THIN_BORDER

    return row_num + 1


def _write_annotation(
    ws,
    row_num: int,
    sq: float,
    drum_count_total: int,
    group_batches: list[ProductionBatch],
    wip_stage_lookup: dict[int, str],
    sheet_name: str,
    *,
    split_label: str | None = None,
) -> int:
    """SQ 그룹 주석 행을 쓰고 다음 row_num을 반환한다.

    포맷 (일반):   {SQ}SQ--->{틀수}틀
    포맷 (분할):   {SQ}SQ--->{틀수}틀 [{N차 배치}]
    """
    sq_label = int(sq) if sq == int(sq) else sq
    # 분할 배치인 경우 "[1차 배치]" / "[2차 배치]" 접미사 추가
    split_suffix = f" [{split_label} 배치]" if split_label else ""
    annotation = f"{sq_label}SQ--->{drum_count_total}틀{split_suffix}"
    cell = ws.cell(row=row_num, column=1, value=annotation)
    cell.font = _SUBTOTAL_FONT
    return row_num + 1


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
