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
)

# Phase 1 Task 1.12 (B-6.2) — _write_sheet 단일 함수 sub-module 분할.
from app.infrastructure.exporters.excel_exporter_sheet import _write_sheet

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
