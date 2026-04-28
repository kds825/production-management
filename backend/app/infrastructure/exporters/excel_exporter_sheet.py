"""Excel 시트 작성 — _write_sheet 단일 함수.

excel_exporter.py 분할 (Task 1.12, B-6.2).
원본 import path 보존 — excel_exporter 가 본 모듈에서 re-export.
"""

import re
from datetime import datetime

from app.infrastructure.exporters.excel_exporter_helpers import (
    HIDDEN_COLS,
    VISIBLE_COLS,
    _apply_col_widths,
    _build_remarks,
    _hide_trailing_cols,
    _write_annotation,
    _write_data_row,
    _write_subtotal,
)
from app.infrastructure.models.production_batch import ProductionBatch


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
