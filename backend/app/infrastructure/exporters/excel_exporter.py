"""작업지시서 Excel 생성 — openpyxl 기반, 공정별 시트 + SQ 그룹핑

3.25계획.xls 템플릿 포맷 준수:
  - 헤더 행 없음 (데이터가 row 1부터 시작)
  - SQ 그룹 사이 빈 행 + 소계(SUM 수식) + 주석 행
  - 폰트 색상: 짙은 회색(WIP 재고), 파란색(신규), 주황색 볼드(소계/주석)
  - 그룹 정렬: Stage 2 스케줄 순서(start_datetime) → 미스케줄 시 SQ 내림차순 폴백
  - 연선 분할 배치: "1차 배치 (N틀) / 2차 배치 (M틀)" 주석으로 구분 표기
"""

import re
from io import BytesIO
from datetime import datetime

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch

# Phase 1 Task 1.11 (B-6.1) — helper 함수 sub-module 분할.
from app.infrastructure.exporters.excel_exporter_helpers import (
    VISIBLE_COLS,
    HIDDEN_COLS,
    _merge_lot_splits,
    _resolve_sheet_name,
    _build_remarks,
    _write_data_row,
    _write_subtotal,
    _write_annotation,
    _apply_col_widths,
    _hide_trailing_cols,
    _col_letter,
)

# Preferred sheet creation order
_SHEET_ORDER = ["연선", "B100", "A100", "A120", "연합", "CV절연", "A150시스", "외주"]

# ── Public API ────────────────────────────────────────────────────────────────


def export_plan(run_label: str, db: Session) -> BytesIO:
    """production_batch 데이터를 공정별 시트로 구성한 Excel 파일 반환.

    시트 구성:
      공정별 배치 시트 (연선, B100, A100, A120, 연합, CV절연, A150시스 …)

    그룹 정렬 우선순위:
      1순위: Stage 2 스케줄 순서(start_datetime) — 실제 작업 순서 반영
      2순위: SQ 내림차순 (Stage 2 미실행 시 폴백)

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

    # ── Stage 2 스케줄 순서 로드 (batch_group → start_datetime) ─────────────
    # 스케줄이 존재하는 경우 실제 작업 순서대로 시트 내 그룹을 정렬한다.
    # 미스케줄 배치 그룹은 SQ 내림차순 폴백(schedule_order에 없으면 후순위).
    from app.infrastructure.models.schedule_task import ScheduleTask

    schedule_order: dict[str, datetime] = {}
    tasks = (
        db.query(ScheduleTask.batch_group, ScheduleTask.start_datetime)
        .filter(ScheduleTask.run_label == run_label)
        .all()
    )
    for bg, sdt in tasks:
        if bg and sdt and bg not in schedule_order:
            schedule_order[bg] = sdt

    if not batches:
        raise ValueError(
            f"run_label='{run_label}'에 해당하는 배치 데이터가 없습니다. Stage 1을 먼저 실행하세요."
        )

    # ── 신선·헤더 배치 제외 ───────────────────────────────────────────────
    # 신선(wire drawing)은 연선의 전처리 공정으로 현장 계획서에 표시하지 않는다.
    # batch_seq=-1 헤더 배치는 스케줄러 duration 전용 — 계획서 행으로 출력하지 않는다.
    all_count = len(batches)
    batches = [
        b
        for b in batches
        if b.process_name != "신선"
        and b.batch_seq != -1
        and b.stranding_type
        != "7연선코어"  # 61연선 CORE(T6BO/AL6BO) 배치는 엑셀 미출력
    ]
    if not batches:
        raise ValueError(
            f"run_label='{run_label}'의 배치 {all_count}건이 모두 신선 또는 헤더 배치(seq=-1)입니다. "
            "Stage 1을 다시 실행하세요."
        )

    # ── 틀분할 배치 병합 — 같은 수주+공정을 1행으로 합산 ──────────────────────
    # batch_grouping의 틀분할(2-3)은 스케줄링에 필요하지만,
    # Excel 계획서는 수주 단위 표시이므로 분할 행을 합쳐 원본과 동일한 행 구조를 만든다.
    batches = _merge_lot_splits(batches)

    # ── WIP 룩업: wip_id → process_stage (비고에 실제 WIP 종류 표시용) ───────
    from app.infrastructure.models.wip_inventory import WipInventory

    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id is not None}
    wip_stage_lookup: dict[int, str] = {}
    wip_total_len_lookup: dict[int, float] = {}  # wip_id → WIP 재고 실제 길이(m)
    if wip_ids:
        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_lookup = {w.wip_id: w.process_stage or "" for w in wips}
        wip_total_len_lookup = {w.wip_id: float(w.total_length_m or 0) for w in wips}

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
        _write_sheet(
            ws,
            sheet_data[sname],
            wip_stage_lookup,
            wip_total_len_lookup,
            sname,
            schedule_order=schedule_order,
        )

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def _write_sheet(
    ws,
    batches: list[ProductionBatch],
    wip_stage_lookup: dict[int, str],
    wip_total_len_lookup: dict[int, float],
    sheet_name: str,
    *,
    schedule_order: dict[str, datetime] | None = None,
) -> None:
    """단일 시트에 데이터 행(batch_group 소계 포함) → 서식 적용.

    3.25계획.xls 포맷: 헤더 없음, row 1부터 데이터 시작.
    batch_group 기준으로 배치를 그룹핑하여 순서대로 출력하고,
    각 그룹 마지막 행 다음에 소계행 + 주석행을 삽입한다.

    정렬: schedule_order(Stage 2 start_datetime) 있으면 작업 순서대로,
          없으면 SQ 내림차순 폴백.
    """
    from collections import OrderedDict

    schedule_order = schedule_order or {}

    all_cols = VISIBLE_COLS + HIDDEN_COLS
    total_cols = len(all_cols)

    # 헤더 행 없음 — 3.25계획.xls 템플릿 준수
    _apply_col_widths(ws, total_cols)
    _hide_trailing_cols(ws, len(VISIBLE_COLS) + 1, total_cols)

    # 시스 시트에서는 WIP 표시를 하지 않음 — 어차피 시스 생산은 수행해야 함
    is_sheath = "시스" in sheet_name
    if is_sheath:
        wip_stage_lookup = {}
        wip_total_len_lookup = {}
        for b in batches:
            b.wip_matched_id = None  # type: ignore[assignment]

    # batch_group 기준 그룹핑 — 원본 정렬 순서를 유지하기 위해 OrderedDict 사용
    groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        bg = batch.batch_group or f"_unknown_{int(batch.sq_mm2 or 0)}SQ"
        groups.setdefault(bg, []).append(batch)

    # ── 그룹 정렬: 스케줄 순서 우선 → SQ 내림차순 폴백 ──────────────────────
    _MAX_DT = datetime.max

    def _group_sort_key(item: tuple) -> tuple:
        bg, gb = item
        sq = float(gb[0].sq_mm2 or 0) if gb else 0.0
        sdt = schedule_order.get(bg)
        # 분할 그룹(_B, _C)이 없는 경우 기본 그룹의 schedule_order를 가져옴
        if sdt is None:
            m = re.match(r"^(.+)_([A-Z])$", bg)
            if m:
                sdt = schedule_order.get(m.group(1))
        return (
            0 if sdt is not None else 1,  # 스케줄 있는 그룹 우선
            sdt or _MAX_DT,  # 시작시각 오름차순
            -sq,  # 같은 시각이면 SQ 내림차순
            bg,
        )

    sorted_groups = sorted(groups.items(), key=_group_sort_key)

    # ── 연선 분할 배치 감지: 동일 기본 그룹에서 파생된 _B/_C 쌍 ─────────────
    # batch_group 예: "ST-120-0.6kV-압축연선" (원본) + "ST-120-0.6kV-압축연선_B" (분할)
    # split_label_map[bg] = "1차" | "2차" | "3차" (표기용)
    # 라벨은 스케줄 start_datetime 순서 기준 — 없으면 알파벳(접미사) 순서 폴백.
    split_label_map: dict[str, str] = {}
    if sheet_name in ("연선", "고압연선"):
        all_bgs = list(groups.keys())
        bg_set = set(all_bgs)
        # 각 그룹키에 대해 분할 접미사(_B, _C, …) 제거 후 원본이 같은 시트에 있으면 쌍으로 처리
        origin_to_splits: dict[str, list[str]] = {}
        for bg in all_bgs:
            m = re.match(r"^(.+)_([A-Z])$", bg)
            if m and m.group(1) in bg_set:
                origin_to_splits.setdefault(m.group(1), []).append(bg)
        # 라벨 부여: 실제 스케줄 start_datetime 오름차순으로 1차, 2차, 3차 …
        for origin_bg, split_bgs in origin_to_splits.items():
            all_in_family = [origin_bg] + sorted(split_bgs)  # 알파벳 폴백 순서
            # 스케줄 순서로 재정렬 (없는 것은 마지막)
            all_in_family.sort(
                key=lambda bg: (
                    0 if bg in schedule_order else 1,
                    schedule_order.get(bg, _MAX_DT),
                    bg,
                )
            )
            for rank, bg in enumerate(all_in_family, start=1):
                split_label_map[bg] = f"{rank}차"

    row_num = 1  # 헤더 없으므로 row 1부터 시작
    is_first_group = True

    for batch_group_key, group_batches in sorted_groups:
        # SQ 그룹 사이에 빈 구분 행 삽입 (첫 그룹 제외)
        if not is_first_group:
            row_num += 1  # 빈 행 1줄
        is_first_group = False

        # 같은 규격(batch_group) 내 행 정렬:
        # 1) product_group — 품목 기준 정렬
        # 2) wip_matched_id 유무 — WIP 사용 행끼리 모으기 (비사용 → 사용 순)
        # 3) wip_matched_id 값 — 같은 WIP 재고를 사용하는 행끼리 연속 배치
        #    → 셀병합 로직이 연속 구간만 병합하므로, 같은 WIP 행이 모여야 정상 동작
        group_batches = sorted(
            group_batches,
            key=lambda b: (
                b.product_group or "",
                (0, b.wip_matched_id) if b.wip_matched_id is not None else (1, 0),
            ),
        )

        group_start_row = row_num  # SUM 수식 범위 시작점
        group_drum_count_total: int = 0

        remarks_col_idx = VISIBLE_COLS.index("비고") + 1  # Excel column index (1-based)

        # batch_group 안에서 wip_matched_id가 "연속으로 동일"한 구간만 병합
        idx = 0
        while idx < len(group_batches):
            block_wip_id = group_batches[idx].wip_matched_id
            end_idx = idx + 1
            while (
                end_idx < len(group_batches)
                and group_batches[end_idx].wip_matched_id == block_wip_id
            ):
                end_idx += 1

            block_first_row = row_num
            block_first_remarks_override: str | None = None
            if block_wip_id is not None and (end_idx - idx) > 1:
                wip_stage = wip_stage_lookup.get(block_wip_id)
                wip_total_len_m = wip_total_len_lookup.get(block_wip_id, 0.0)
                block_first_remarks_override = _build_remarks(
                    group_batches[idx],
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

        # 연선 시트: remarks에서 실제 틀(lot) 수 추출 — 수주 건수와 구분
        # remarks 형식: "연선그룹 20건 1틀 / 그룹총량 ..." 또는 "분할 잔여/B" 형식
        is_strand_sheet = sheet_name in ("연선", "고압연선")
        if is_strand_sheet:
            # 헤더 배치(batch_seq=-1)가 이미 제외되므로 remarks에서 파싱
            hdr_remarks = group_batches[0].remarks or ""
            m = re.search(r"\d+건\s+(\d+)틀", hdr_remarks)
            lot_count = int(m.group(1)) if m else group_drum_count_total
        else:
            lot_count = None  # 연선 외엔 미사용

        # 분할 배치 라벨 ("1차", "2차", …) — 연선 시트에서만
        split_label: str | None = (
            split_label_map.get(batch_group_key) if is_strand_sheet else None
        )

        row_num = _write_subtotal(
            ws,
            row_num,
            sq,
            group_start_row,
            group_end_row,
            lot_count if lot_count is not None else len(group_batches),
            total_cols,
            is_stranding=is_strand_sheet,
            split_label=split_label,
        )

        # 주석 행: SQ 그룹 요약 (예: "240SQ--->2틀(절연1570)")
        row_num = _write_annotation(
            ws,
            row_num,
            sq,
            lot_count if lot_count is not None else group_drum_count_total,
            group_batches,
            wip_stage_lookup,
            sheet_name,
            split_label=split_label,
        )

    # 헤더 없으므로 freeze_panes, auto_filter 불필요

