"""작업지시서 Excel 생성 — openpyxl 기반, 공정별 시트 + SQ 그룹핑

3.25계획.xls 템플릿 포맷 준수:
  - 헤더 행 없음 (데이터가 row 1부터 시작)
  - SQ 그룹 사이 빈 행 + 소계(SUM 수식) + 주석 행
  - 폰트 색상: 짙은 회색(WIP 재고), 파란색(신규), 주황색 볼드(소계/주석)
"""

from io import BytesIO
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font, Border, Side
from sqlalchemy.orm import Session

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

# Preferred sheet creation order
_SHEET_ORDER = ["연선", "B100", "A100", "A120", "연합", "CV절연", "A150시스", "외주"]

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

# ── Public API ────────────────────────────────────────────────────────────────


def export_plan(run_label: str, db: Session) -> BytesIO:
    """production_batch 데이터를 공정별 시트로 구성한 Excel 파일 반환.

    시트 구성:
      공정별 배치 시트 (연선, B100, A100, A120, 연합, CV절연, A150시스 …)

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

    wb = Workbook()
    wb.remove(wb.active)  # 기본 Sheet1 제거

    # ── process_name 기준으로 배치를 시트별 버킷에 분류 ─────────────────────
    # 모든 배치를 해당 시트에 표시 — WIP 매칭 항목도 표시하되 비고에 "재고 사용" 알람.
    # WIP 공정 스킵은 간트차트(Stage 2) 스케줄링에서 처리한다.
    sheet_data: dict[str, list[ProductionBatch]] = {}
    for batch in batches:
        sname = _resolve_sheet_name(batch)
        sheet_data.setdefault(sname, []).append(batch)

    # ── 외주 수주 시트 추가 ─────────────────────────────────────────────────
    from app.infrastructure.models.sales_order import SalesOrder

    outsourced = (
        db.query(SalesOrder)
        .filter(SalesOrder.run_label == run_label, SalesOrder.is_outsourced == True)  # noqa: E712
        .order_by(SalesOrder.order_id)
        .all()
    )
    if outsourced:
        import re as _re

        def _parse_sq(spec_raw: str | None) -> float:
            """spec_raw(예: '3C x 95SQ')에서 SQ 값을 추출한다."""
            if not spec_raw:
                return 0.0
            m = _re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec_raw, _re.IGNORECASE)
            return float(m.group(1)) if m else 0.0

        # 외주 수주를 pseudo-batch 형태로 변환 (시트 작성 호환용)
        outsource_batches = []
        for o in outsourced:
            sq = _parse_sq(o.spec_raw)
            pseudo = ProductionBatch(
                product_group=o.product_group,
                sq_mm2=sq,
                sheath_color=o.sheath_color or "",
                customer_name=o.customer_name,
                due_date=o.due_date,
                drum_length_m=o.drum_length_m,
                drum_count=o.drum_count,
                total_length_m=o.ordered_qty_m
                or ((o.drum_length_m or 0) * (o.drum_count or 1)),
                core_count=o.core_count or 1,
                remarks="외주생산",
                batch_group=f"외주_{int(sq)}SQ",
            )
            outsource_batches.append(pseudo)
        sheet_data["외주"] = outsource_batches

    # 정해진 순서대로 시트 생성; 순서 목록에 없는 공정은 말미에 추가
    known = set(_SHEET_ORDER)
    extra = [s for s in sheet_data if s not in known]
    for sname in _SHEET_ORDER + extra:
        if sname not in sheet_data:
            continue
        ws = wb.create_sheet(title=sname)
        _write_sheet(ws, sheet_data[sname], wip_stage_lookup, sname)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# ── Batch merge helper ────────────────────────────────────────────────────────


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


def _write_sheet(
    ws,
    batches: list[ProductionBatch],
    wip_stage_lookup: dict[int, str],
    sheet_name: str,
) -> None:
    """단일 시트에 데이터 행(batch_group 소계 포함) → 서식 적용.

    3.25계획.xls 포맷: 헤더 없음, row 1부터 데이터 시작.
    batch_group 기준으로 배치를 그룹핑하여 순서대로 출력하고,
    각 그룹 마지막 행 다음에 소계행 + 주석행을 삽입한다.
    """
    from collections import OrderedDict

    all_cols = VISIBLE_COLS + HIDDEN_COLS
    total_cols = len(all_cols)

    # 헤더 행 없음 — 3.25계획.xls 템플릿 준수
    _apply_col_widths(ws, total_cols)
    _hide_trailing_cols(ws, len(VISIBLE_COLS) + 1, total_cols)

    # wip_matched_id별 총 길이(m) 누적
    # - 비고에서 "절연/연선재고 XXX사용"의 XXX를 wip_id가 아니라 길이 합으로 표시하기 위함
    wip_total_len_lookup: dict[int, float] = {}
    for b in batches:
        if b.wip_matched_id is None:
            continue
        wip_total_len_lookup[b.wip_matched_id] = (
            wip_total_len_lookup.get(b.wip_matched_id, 0.0) + float(b.total_length_m or 0)
        )

    # batch_group 기준 그룹핑 — 원본 정렬 순서를 유지하기 위해 OrderedDict 사용
    groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        bg = batch.batch_group or f"_unknown_{int(batch.sq_mm2 or 0)}SQ"
        groups.setdefault(bg, []).append(batch)

    row_num = 1  # 헤더 없으므로 row 1부터 시작
    is_first_group = True

    for batch_group_key, group_batches in groups.items():
        # SQ 그룹 사이에 빈 구분 행 삽입 (첫 그룹 제외)
        if not is_first_group:
            row_num += 1  # 빈 행 1줄
        is_first_group = False

        group_start_row = row_num  # SUM 수식 범위 시작점
        group_drum_count_total: int = 0

        remarks_col_idx = VISIBLE_COLS.index("비고") + 1  # Excel column index (1-based)

        # batch_group 안에서 wip_matched_id가 "연속으로 동일"한 구간만 병합
        idx = 0
        while idx < len(group_batches):
            block_wip_id = group_batches[idx].wip_matched_id
            end_idx = idx + 1
            while end_idx < len(group_batches) and group_batches[end_idx].wip_matched_id == block_wip_id:
                end_idx += 1

            block_first_row = row_num
            block_first_remarks_override: str | None = None
            if block_wip_id is not None and (end_idx - idx) > 1:
                merged_base_remarks = " / ".join(
                    b.remarks.strip()
                    for b in group_batches[idx:end_idx]
                    if b.remarks and b.remarks.strip()
                )
                wip_stage = wip_stage_lookup.get(block_wip_id)
                wip_total_len_m = wip_total_len_lookup.get(block_wip_id, 0.0)
                block_first_remarks_override = _build_remarks(
                    group_batches[idx],
                    merged_base_remarks or None,
                    wip_stage=wip_stage,
                    wip_total_len_m=wip_total_len_m,
                )

            for j in range(idx, end_idx):
                batch = group_batches[j]
                # 공정 시트는 sheath_color로 1행 표시 (원본 계획서와 동일)
                # 다심 케이블(4C 등)도 sheath_color 기준 1행 — 심선색상 분리는 하지 않음
                color_label = batch.sheath_color or batch.core_colors or ""
                length_m = float(batch.total_length_m or 0)
                group_drum_count_total += int(batch.drum_count or 1)

                row_num = _write_data_row(
                    ws,
                    row_num,
                    batch,
                    all_cols,
                    color_label,
                    length_m,
                    wip_stage_lookup,
                    wip_total_len_lookup,
                    remarks_override=(
                        block_first_remarks_override
                        if j == idx
                        else ("" if (block_wip_id is not None and j > idx) else None)
                    ),
                )

            block_last_row = row_num - 1

            # wip_matched_id가 있는 구간에 대해서만 병합
            if block_wip_id is not None and block_last_row > block_first_row:
                ws.merge_cells(
                    start_row=block_first_row,
                    start_column=remarks_col_idx,
                    end_row=block_last_row,
                    end_column=remarks_col_idx,
                )

            idx = end_idx

        # batch_group에서 SQ 추출하여 소계 레이블 생성
        sq = (
            float(group_batches[0].sq_mm2)
            if group_batches[0].sq_mm2 is not None
            else 0.0
        )
        group_end_row = row_num - 1  # 마지막 데이터 행

        row_num = _write_subtotal(
            ws,
            row_num,
            sq,
            group_start_row,
            group_end_row,
            len(group_batches),
            total_cols,
        )

        # 주석 행: SQ 그룹 요약 (예: "240SQ--->2틀(절연1570)")
        row_num = _write_annotation(
            ws,
            row_num,
            sq,
            group_drum_count_total,
            group_batches,
            wip_stage_lookup,
            sheet_name,
        )

    # 헤더 없으므로 freeze_panes, auto_filter 불필요


# ── Row-level helpers ─────────────────────────────────────────────────────────


def _build_remarks(
    batch: ProductionBatch,
    base_remarks: str | None = None,
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
        return f"{wip_stage}{wip_len_int}m 사용" if wip_len_int > 0 else f"{wip_stage} 사용"
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
            batch.remarks,
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
) -> int:
    """SQ 그룹 소계 행을 쓰고 다음 row_num을 반환한다.

    총길이(col H=8) 셀에 =SUM() 수식을 삽입한다.
    소계 폰트: 주황색 볼드 (RGB 255,102,0).
    """
    sq_label = int(sq) if sq == int(sq) else sq
    total_length_col = 8  # H열 = 총길이(m)

    label_cell = ws.cell(row=row_num, column=1, value=f"{sq_label}SQ → {count}건")
    label_cell.font = _SUBTOTAL_FONT

    # 총길이(H열)에 SUM 수식 — 영문 함수명 사용 (openpyxl 요건)
    col_letter = _col_letter(total_length_col)
    sum_formula = f"=SUM({col_letter}{start_row}:{col_letter}{end_row})"
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
) -> int:
    """SQ 그룹 주석 행을 쓰고 다음 row_num을 반환한다.

    연선 시트: "240SQ--->2틀" (drum_count 합계)
    기타 시트: WIP 참조 정보가 있으면 "(절연1570)" 등 포함.
    포맷: 주황색 볼드 (RGB 255,102,0).
    """
    sq_label = int(sq) if sq == int(sq) else sq

    # WIP 참조 텍스트 수집 — 그룹 내 WIP 매칭된 배치의 process_stage + wip_id
    wip_refs: list[str] = []
    for b in group_batches:
        if b.wip_matched_id is not None:
            stage = wip_stage_lookup.get(b.wip_matched_id, "")
            # "절연재고" → "절연", "연선재고" → "연선" (접미사 제거)
            stage_short = stage.replace("재고", "") if stage else ""
            wip_refs.append(f"{stage_short}{b.wip_matched_id}")

    if sheet_name == "연선":
        # 연선 시트: 틀 수(drum count) 표시
        annotation = f"{sq_label}SQ--->{drum_count_total}틀"
        if wip_refs:
            annotation += f"({', '.join(wip_refs)})"
    else:
        # 기타 시트: WIP 참조 정보만 표시 (없으면 틀 수만)
        annotation = f"{sq_label}SQ--->{drum_count_total}틀"
        if wip_refs:
            annotation += f"({', '.join(wip_refs)})"

    cell = ws.cell(row=row_num, column=1, value=annotation)
    cell.font = _SUBTOTAL_FONT

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
