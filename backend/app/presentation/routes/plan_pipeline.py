"""Stage 1 파이프라인 API — ERP 업로드 → 작업지시서 생성 → Excel 다운로드"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.application.ingest import (
    format_spec_display,
)
from app.application.validation.constraint_checker import validate_all  # noqa: F401 — used in stage2
from app.application.ingest.run_labeler import (  # noqa: F401 — re-export for tests
    new_run_label as _alloc_run_label,
    parse_base_date_yyyymmdd,
    parse_date_yyyymmdd,
)
from app.application.ingest.pipeline_orchestrator import (  # noqa: F401
    execute_stage1_ingest,
    execute_stage2,
)
from app.application.ingest.stage1 import run_solver_stage  # noqa: F401
from app.application.ingest.stage2 import run_greedy_stage  # noqa: F401

# Public re-export under the helper's canonical name (kept importable from
# the route module so callers / tests can reach it as plan_pipeline.new_run_label
# without going through services.pipeline). Aliased above to avoid colliding
# with the local variable named ``new_run_label`` inside run_stage1_update().
new_run_label = _alloc_run_label  # noqa: F811 — intentional re-export alias
from app.application.scheduling.greedy.auto_schedule import auto_schedule  # noqa: F401, E402 — alias 후 import

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["파이프라인"])


# ---------------------------------------------------------------------------
# AI 분석 결과 인메모리 캐시 — _pipeline_shared 모듈로 이동 (Task 1.1)
# 외부 import path 보존: from app.presentation.routes.plan_pipeline import _ai_cache
# ---------------------------------------------------------------------------
from app.presentation.routes._pipeline_shared import (  # noqa: E402, F401
    _ai_cache,
    _ai_cache_lock,
    _run_ai_background,
    _start_ai_background,
)


def _parse_stage2_body(body: dict) -> tuple[str, datetime | None, str]:
    """POST /stage2 body 파싱 공통 루틴 (sync/async 경로 공유)."""
    run_label = body.get("run_label")
    if not run_label:
        raise HTTPException(status_code=400, detail="run_label 필수")

    base_date_dt = parse_base_date_yyyymmdd(body.get("base_date"))
    optimizer = body.get("optimizer", "cpsat")  # "cpsat" | "greedy"
    return run_label, base_date_dt, optimizer


# _start_ai_background 는 _pipeline_shared 로 이동 (Task 1.1) — 위 import 가 re-export.


def _execute_stage2_core(
    run_label: str,
    base_date_dt: datetime | None,
    optimizer: str,
    db: Session,
) -> dict:
    """Stage2 핵심 로직 thin shim — orchestrator.execute_stage2 위임.

    sync `/pipeline/stage2` 와 async `/pipeline/stage2/async` 의 공유 구현.
    SchedulerOverlapError 는 여기서 잡지 않고 호출자가 매핑하도록 전파한다
    (sync 는 200 + overlap_alert 응답, async job 은 status=overlap_alert
    저장).

    Why a shim and not a direct alias to execute_stage2: 본 함수 자체를
    monkeypatch 하는 테스트 (test_stage2_async_job) 가 있어 함수 객체가
    plan_pipeline 모듈 namespace 에 살아 있어야 한다. 또한 orchestrator 가
    auto_schedule / validate_all 을 DI 로 받게 했기 때문에, 라우트 모듈에서
    monkeypatch 가능한 두 심볼을 호출 시점에 전달할 수 있다.
    """
    return execute_stage2(
        run_label,
        base_date_dt,
        optimizer,
        db,
        auto_schedule_fn=auto_schedule,
        validate_all_fn=validate_all,
        ai_background_starter=_start_ai_background,
    )


@router.get("/batch-group/{batch_group:path}/orders", summary="배치 그룹 내 수주 목록")
def list_batch_group_orders(batch_group: str, db: Session = Depends(get_db)):
    """지정한 batch_group에 속하는 production_batch 목록을 반환한다.

    연선 그룹(ST- 접두사):
      - batch_seq=-1 헤더(틀단위 집계)와 batch_seq=1 개별 수주 배치로 구성된다.
      - 개별 수주 배치를 run_label+process_name+sq_mm2로 추가 조회하여 항상 포함한다.
        (batch_group이 구버전 값으로 저장돼 batch_group 필터로 찾히지 않는 경우 대비)
    기타 그룹: batch_group 필터 결과를 그대로 반환한다.
    """
    by_group = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == batch_group)
        .order_by(ProductionBatch.batch_seq.asc(), ProductionBatch.due_date.asc())
        .all()
    )

    if not by_group:
        raise HTTPException(
            status_code=404,
            detail=f"batch_group '{batch_group}'에 해당하는 배치가 없습니다.",
        )

    # batch_group 필터 결과를 그대로 사용 (분할된 그룹은 각자의 batch_group만 표시)
    batches = by_group

    # WIP 재고 수량 조회 — 연선/절연재고 사용 배치는 net qty에서 차감해야 함
    from app.infrastructure.models.wip_inventory import WipInventory as WipModel

    wip_ids = [b.wip_matched_id for b in batches if b.wip_matched_id is not None]
    wip_qty_map: dict[int, float] = {}
    wip_stage_map2: dict[int, str] = {}
    if wip_ids:
        wip_rows = (
            db.query(WipModel.wip_id, WipModel.total_length_m, WipModel.process_stage)
            .filter(WipModel.wip_id.in_(wip_ids))
            .all()
        )
        wip_qty_map = {wid: float(tl or 0) for wid, tl, _ in wip_rows}
        wip_stage_map2 = {wid: (ps or "") for wid, _, ps in wip_rows}

    from app.domain.batch_sheath_keys import _WIP_COVERED_PROCESSES

    def _to_dict(b: ProductionBatch) -> dict:
        raw_len = float(b.total_length_m or 0)
        wip_stage = wip_stage_map2.get(b.wip_matched_id, "") if b.wip_matched_id else ""
        # 이 배치의 공정이 WIP에 의해 커버되는 경우에만 차감
        # 예: 절연재고 WIP + 절연 공정 → 차감 / 절연재고 WIP + 시스 공정 → 차감 안 함
        is_wip = bool(wip_stage) and (
            b.process_name or ""
        ) in _WIP_COVERED_PROCESSES.get(wip_stage, set())
        wip_len = wip_qty_map.get(b.wip_matched_id, 0.0) if is_wip else 0.0
        net_len = max(raw_len - wip_len, 0.0)
        return {
            "batch_id": b.batch_id,
            "batch_seq": b.batch_seq,
            "sales_order_id": b.sales_order_id,
            "spec_raw": format_spec_display(
                getattr(b, "spec_raw", None), b.core_count or 1, float(b.sq_mm2 or 0)
            ),
            "sheath_color": b.sheath_color or "",
            "customer_name": b.customer_name or "",
            "due_date": str(b.due_date or ""),
            "drum_length_m": float(b.drum_length_m or 0),
            "drum_count": b.drum_count or 1,
            "total_length_m": raw_len,
            "wip_length_m": wip_len,  # WIP 재고 커버량
            "net_length_m": net_len,  # 실제 작업지시량 (WIP 제외)
            "wip_matched_id": b.wip_matched_id,
            "wip_stage": wip_stage or None,
            "product_group": b.product_group or "",
            "status": b.status or "",
            "core_count": int(b.core_count or 1),
        }

    return [_to_dict(b) for b in batches]


@router.get(
    "/batch-group/{batch_group:path}/process-flow",
    summary="배치 그룹의 연관 공정 흐름 조회",
)
def get_process_flow(batch_group: str, db: Session = Depends(get_db)):
    """주어진 batch_group과 동일 수주를 공유하는 모든 batch_group을 공정 순서로 반환한다.

    이전공정/다음공정 네비게이션에 사용된다.
    예: 연선(ST-240) → 절연(저압절연_240SQ) → 시스(A120_흑_240SQ) 순서로 반환.
    """
    from sqlalchemy import tuple_

    from app.domain.constants import PROCESS_ORDER
    from app.infrastructure.models.schedule_task import (
        ScheduleTask as ScheduleTaskModel,
    )

    # 1. 현재 batch_group의 run_label과 수주 키 조회
    current_batches = (
        db.query(
            ProductionBatch.sales_order_id,
            ProductionBatch.sales_order_line,
            ProductionBatch.run_label,
        )
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq != -1,  # 헤더 제외
        )
        .all()
    )

    if not current_batches:
        return []

    order_keys = list({(b.sales_order_id, b.sales_order_line) for b in current_batches})
    run_label = current_batches[0].run_label

    # 2. 동일 run_label 내에서 동일 수주를 포함하는 모든 batch_group 조회
    related = (
        db.query(
            ProductionBatch.batch_group,
            ProductionBatch.process_name,
        )
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq != -1,
            tuple_(
                ProductionBatch.sales_order_id,
                ProductionBatch.sales_order_line,
            ).in_(order_keys),
        )
        .distinct()
        .all()
    )

    # 3. batch_group별 공정 정보 집계
    groups: dict[str, dict] = {}
    for r in related:
        bg = r.batch_group
        if bg and bg not in groups:
            groups[bg] = {
                "batch_group": bg,
                "process_name": r.process_name,
                "order": PROCESS_ORDER.get(r.process_name, 50),
            }

    # 4. schedule_task에서 설비·시간 정보 보강
    for bg_info in groups.values():
        task = (
            db.query(
                ScheduleTaskModel.equipment_code,
                ScheduleTaskModel.start_datetime,
                ScheduleTaskModel.end_datetime,
            )
            .filter(ScheduleTaskModel.batch_group == bg_info["batch_group"])
            .first()
        )
        if task:
            bg_info["equipment_code"] = task.equipment_code
            bg_info["start_datetime"] = (
                task.start_datetime.isoformat() if task.start_datetime else None
            )
            bg_info["end_datetime"] = (
                task.end_datetime.isoformat() if task.end_datetime else None
            )

    # 5. 공정 순서 → batch_group 이름 순으로 정렬
    result = sorted(groups.values(), key=lambda x: (x["order"], x["batch_group"]))
    return result


@router.post("/batch-group/{batch_group:path}/split", summary="배치 그룹 분할")
def split_batch_group(
    batch_group: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """배치 그룹을 2개로 분할한다.

    지정된 batch_ids의 batch_group을 '{원래그룹}_{suffix}'로 변경하고,
    헤더 배치(batch_seq=-1)를 비율로 분할하여 신규 그룹에 새 헤더를 생성한다.
    기존 schedule_task는 삭제되며 프론트에서 재스케줄링(Stage 2)을 트리거해야 한다.

    body:
        batch_ids: list[int]  — 새 그룹으로 이동할 batch_id 목록 (seq >= 1)
        suffix: str           — 새 그룹 이름 접미사 (기본값: "B")
    """
    from app.infrastructure.models.schedule_task import (
        ScheduleTask as ScheduleTaskModel,
    )

    batch_ids: list[int] = body.get("batch_ids", [])
    # 프론트는 "suffix" 키로 전송; "new_group_suffix" 레거시도 허용
    suffix: str = body.get("suffix") or body.get("new_group_suffix") or "B"

    if not batch_ids:
        raise HTTPException(status_code=400, detail="분리할 batch_ids가 비어 있습니다.")

    batch_id_set = set(batch_ids)

    # 원래 그룹에 해당 배치가 존재하는지 검증
    existing = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_id.in_(batch_ids),
            ProductionBatch.batch_group == batch_group,
        )
        .count()
    )
    if existing == 0:
        raise HTTPException(
            status_code=404,
            detail=f"batch_group '{batch_group}'에서 지정된 batch_ids를 찾을 수 없습니다.",
        )

    new_group = f"{batch_group}_{suffix}"

    # ── 헤더 배치 및 개별 배치 조회 ─────────────────────────────────────────
    header = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq == -1,
        )
        .first()
    )
    all_individual = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq >= 1,
        )
        .all()
    )
    # 사용자가 선택한 분할 배치 (초기값 — WIP 최적화 후 변경될 수 있음)
    split_off = [b for b in all_individual if b.batch_id in batch_id_set]
    remaining = [b for b in all_individual if b.batch_id not in batch_id_set]

    remain_lots: int = -1  # -1 = 미계산 (Phase 2 그룹 또는 헤더 없는 경우)

    # ── 헤더 배치 분할 처리 ──────────────────────────────────────────────────
    if header and (split_off or remaining):
        import math as _math
        from app.infrastructure.models.wip_inventory import WipInventory as WipModel

        orig_dur = float(header.estimated_duration_min or 0)
        lot_size = float(header.drum_length_m or 0)  # 틀단위 (m)
        core_mul = int(header.core_count or 1)

        # ── WIP 연선/절연재고 배치 파악 및 수량 로드 ────────────────────────
        all_wip_ids = [
            b.wip_matched_id for b in all_individual if b.wip_matched_id is not None
        ]
        wip_stage_map: dict[int, str] = {}
        wip_len_by_id: dict[int, float] = {}  # wip_id → WIP 재고량 (cable m)
        if all_wip_ids:
            wip_rows = (
                db.query(
                    WipModel.wip_id, WipModel.process_stage, WipModel.total_length_m
                )
                .filter(WipModel.wip_id.in_(all_wip_ids))
                .all()
            )
            wip_stage_map = {wid: (ps or "") for wid, ps, _ in wip_rows}
            wip_len_by_id = {wid: float(tl or 0) for wid, _, tl in wip_rows}

        def _is_wip_strand(b: ProductionBatch) -> bool:
            """연선/절연재고 WIP 사용 배치"""
            if not b.wip_matched_id:
                return False
            return wip_stage_map.get(b.wip_matched_id, "") in ("연선재고", "절연재고")

        def _net_qty(b: ProductionBatch) -> float:
            """배치의 실제 생산 필요량 (strand m).
            WIP 커버량을 차감하되, WIP qty < 수주 qty이면 잔여분을 포함.
            """
            full = float(b.total_length_m or 0) * core_mul
            if not _is_wip_strand(b):
                return full
            wip_cov = wip_len_by_id.get(b.wip_matched_id, 0.0) * core_mul
            return max(0.0, full - wip_cov)

        # ── WIP 재매칭 최적화 ────────────────────────────────────────────────
        # 배치(수주)는 그룹 이동 없이 그대로 유지.
        # WIP 전체를 split 또는 remain 한 쪽에만 몰아서 총 틀 수를 최소화.
        #
        # 시나리오 A: WIP 전부 → split
        # 시나리오 B: WIP 전부 → remain
        # → 둘 중 총 틀 수가 적은 쪽 적용. 같으면 현재 상태 유지.

        def _lot_count(net: float) -> int:
            if net <= 0:
                return 0
            return _math.ceil(net / lot_size)

        # ── WIP를 wip_id 단위로 묶어서 처리하는 헬퍼 ──────────────────────────
        from collections import defaultdict as _dd

        def _group_net(batches_list: list) -> float:
            """배치 목록의 실제 생산 필요량 (strand m).
            같은 wip_id를 공유하는 배치들의 합계에서 WIP 재고량을 차감.
            """
            wip_groups: dict = _dd(list)
            non_wip = 0.0
            for b in batches_list:
                if _is_wip_strand(b):
                    wip_groups[b.wip_matched_id].append(b)
                else:
                    non_wip += float(b.total_length_m or 0) * core_mul
            wip_net = 0.0
            for wid, grp in wip_groups.items():
                orders_total = sum(float(b.total_length_m or 0) for b in grp) * core_mul
                wip_qty = wip_len_by_id.get(wid, 0.0) * core_mul
                wip_net += max(0.0, orders_total - wip_qty)
            return non_wip + wip_net

        def _select_receivers(candidates: list, wip_budget_m: float) -> list:
            """WIP 예산(cable m) 내에서 수신 배치를 내림차순으로 greedy 선택.
            선택된 배치들의 합계 ≤ wip_budget_m 을 보장.
            """
            budget = wip_budget_m
            selected = []
            for b in sorted(
                candidates, key=lambda b: float(b.total_length_m or 0), reverse=True
            ):
                sz = float(b.total_length_m or 0)
                if sz <= budget:
                    selected.append(b)
                    budget -= sz
            return selected

        if lot_size > 0:
            wip_in_split = [b for b in split_off if _is_wip_strand(b)]
            wip_in_remain = [b for b in remaining if _is_wip_strand(b)]
            non_wip_split = [b for b in split_off if not _is_wip_strand(b)]
            non_wip_remain = [b for b in remaining if not _is_wip_strand(b)]

            # 현재 net (WIP 공유 합계 기준 차감)
            base_split_net = _group_net(split_off)
            base_remain_net = _group_net(remaining)
            lots_current = _lot_count(base_split_net) + _lot_count(base_remain_net)

            # wip_id 별로 묶기
            wip_ids_in_remain = set(b.wip_matched_id for b in wip_in_remain)
            wip_ids_in_split = set(b.wip_matched_id for b in wip_in_split)

            # 시나리오 A: remain WIP 전부 → split
            # 각 wip_id별 WIP 예산 내에서 split 비-WIP 배치를 선택해 수신
            avail_for_A = list(non_wip_split)  # 수신 후보 (중복 배정 방지용)
            plan_A: list[tuple[int, list]] = []  # (wip_id, 수신배치 목록)
            sim_split_A = base_split_net
            sim_remain_A = base_remain_net

            for wid in wip_ids_in_remain:
                wip_qty_m = wip_len_by_id.get(wid, 0.0)  # cable m
                src_batches = [b for b in wip_in_remain if b.wip_matched_id == wid]
                # remain_net: WIP 해제 → 해당 배치들이 full 생산으로 복귀
                src_total = (
                    sum(float(b.total_length_m or 0) for b in src_batches) * core_mul
                )
                covered_now = max(0.0, src_total - wip_qty_m * core_mul)
                sim_remain_A += src_total - covered_now  # = min(src_total, wip_qty*cm)
                # split_net: WIP 예산 내에서 수신 배치 선택
                recvs = _select_receivers(avail_for_A, wip_qty_m)
                recv_total = sum(float(b.total_length_m or 0) for b in recvs) * core_mul
                sim_split_A -= min(recv_total, wip_qty_m * core_mul)
                plan_A.append((wid, recvs))
                for r in recvs:
                    avail_for_A.remove(r)

            sim_split_A = max(0.0, sim_split_A)
            sim_remain_A = max(0.0, sim_remain_A)
            lots_A = _lot_count(sim_split_A) + _lot_count(sim_remain_A)

            # 시나리오 B: split WIP 전부 → remain
            avail_for_B = list(non_wip_remain)
            plan_B: list[tuple[int, list]] = []
            sim_split_B = base_split_net
            sim_remain_B = base_remain_net

            for wid in wip_ids_in_split:
                wip_qty_m = wip_len_by_id.get(wid, 0.0)
                src_batches = [b for b in wip_in_split if b.wip_matched_id == wid]
                src_total = (
                    sum(float(b.total_length_m or 0) for b in src_batches) * core_mul
                )
                covered_now = max(0.0, src_total - wip_qty_m * core_mul)
                sim_split_B += src_total - covered_now
                recvs = _select_receivers(avail_for_B, wip_qty_m)
                recv_total = sum(float(b.total_length_m or 0) for b in recvs) * core_mul
                sim_remain_B -= min(recv_total, wip_qty_m * core_mul)
                plan_B.append((wid, recvs))
                for r in recvs:
                    avail_for_B.remove(r)

            sim_split_B = max(0.0, sim_split_B)
            sim_remain_B = max(0.0, sim_remain_B)
            lots_B = _lot_count(sim_split_B) + _lot_count(sim_remain_B)

            # 최적 시나리오 적용
            if lots_A < lots_current and lots_A <= lots_B:
                # remain WIP 해제
                for b in wip_in_remain:
                    b.wip_matched_id = None
                # split 수신 배치에 wip_id 부여
                for wid, recvs in plan_A:
                    for r in recvs:
                        r.wip_matched_id = wid
            elif lots_B < lots_current and lots_B < lots_A:
                # split WIP 해제
                for b in wip_in_split:
                    b.wip_matched_id = None
                # remain 수신 배치에 wip_id 부여
                for wid, recvs in plan_B:
                    for r in recvs:
                        r.wip_matched_id = wid

        def _calc_lots(batches: list) -> tuple[int, float]:
            """WIP 공유 합계 기준 net qty로 틀 수와 연선 작업량 계산"""
            net = _group_net(batches)
            if lot_size > 0 and net > 0:
                lots = _math.ceil(net / lot_size)
                return lots, lots * lot_size
            elif net > 0:
                return 1, net
            else:
                return 0, 0.0

        # ── 각 그룹 net qty 및 틀 수 계산 (WIP 연선/절연재고 제외) ──────────
        split_lots, split_len_work = _calc_lots(split_off)
        remain_lots, remain_len_work = _calc_lots(remaining)

        split_len_raw = sum(float(b.total_length_m or 0) for b in split_off)
        remain_len_raw = sum(float(b.total_length_m or 0) for b in remaining)
        split_len = split_len_work  # 헤더 total_length_m = 실제 연선 작업량
        remain_len = remain_len_work

        # duration은 원본 비율로 배분 (작업량 기준)
        total_work = split_len + remain_len
        split_dur = orig_dur * split_len / total_work if total_work > 0 else 0
        remain_dur = orig_dur * remain_len / total_work if total_work > 0 else 0

        split_due = min(
            (b.due_date for b in split_off if b.due_date), default=header.due_date
        )
        remain_due = min(
            (b.due_date for b in remaining if b.due_date), default=header.due_date
        )
        split_pri = min((b.customer_priority or 99 for b in split_off), default=99)
        remain_pri = min((b.customer_priority or 99 for b in remaining), default=99)

        split_wip_count = sum(1 for b in split_off if _is_wip_strand(b))
        remain_wip_count = sum(1 for b in remaining if _is_wip_strand(b))

        # 신규 그룹 헤더 생성
        new_header = ProductionBatch(
            run_label=header.run_label,
            sales_order_id=split_off[0].sales_order_id
            if split_off
            else header.sales_order_id,
            sales_order_line=split_off[0].sales_order_line
            if split_off
            else header.sales_order_line,
            item_code=header.item_code,
            routing_code=header.routing_code,
            process_name=header.process_name,
            batch_seq=-1,
            drum_count=split_lots,
            drum_length_m=header.drum_length_m,
            total_length_m=split_len,
            extra_length_m=0,
            sq_mm2=header.sq_mm2,
            core_count=header.core_count,
            core_colors=header.core_colors,
            sheath_color=header.sheath_color,
            customer_name=split_off[0].customer_name
            if split_off
            else header.customer_name,
            due_date=split_due,
            customer_priority=split_pri,
            line_speed_mpm=header.line_speed_mpm,
            setup_time_min=header.setup_time_min,
            estimated_duration_min=split_dur,
            status="planned",
            product_group=header.product_group,
            voltage=header.voltage,
            conductor_material=header.conductor_material,
            stranding_type=header.stranding_type,
            batch_group=new_group,
            spec_raw=header.spec_raw,
            remarks=(
                f"연선그룹 {len(split_off)}건 {split_lots}틀 / "
                f"수주총량 {split_len_raw:.0f}m → 연선작업량 {split_len:.0f}m"
                + (f" (WIP {split_wip_count}건 제외)" if split_wip_count else "")
                + f" (분할 {suffix}, 틀단위 {lot_size:.0f}m)"
            ),
        )
        db.add(new_header)

        # 원본 헤더 업데이트 (잔여 그룹)
        header.drum_count = remain_lots
        header.total_length_m = remain_len
        header.estimated_duration_min = remain_dur
        header.due_date = remain_due
        header.customer_priority = remain_pri
        header.remarks = (
            f"연선그룹 {len(remaining)}건 {remain_lots}틀 / "
            f"수주총량 {remain_len_raw:.0f}m → 연선작업량 {remain_len:.0f}m"
            + (f" (WIP {remain_wip_count}건 제외)" if remain_wip_count else "")
            + f" (분할 잔여, 틀단위 {lot_size:.0f}m)"
        )

    # ── 헤더 잔여 작업 0인 경우 wip_complete 처리 (Phase 1 연선 그룹) ─────────
    # remain_lots == 0이면 잔여 배치 전부 WIP 재고로 충당됨 → 연선 작업 불필요.
    # Stage 2가 이 그룹을 1분짜리 불가시 태스크로 생성하지 않도록 미리 처리.
    # (remain_lots == -1은 Phase 2 그룹 / 헤더 없음 → 이 처리 불필요)
    if header and remain_lots == 0:
        header.status = "wip_complete"

    # ── 개별 배치들의 그룹 변경 ──────────────────────────────────────────────
    # batch_id_set: WIP greedy 재배정 이후 최종 split_off 집합 (line 1191에서 갱신됨)
    final_batch_ids = list(batch_id_set)
    updated = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_id.in_(final_batch_ids),
            ProductionBatch.batch_group == batch_group,
        )
        .update({ProductionBatch.batch_group: new_group}, synchronize_session=False)
    )

    # ── schedule_task 정리 (FK: audit_log → schedule_task) ───────────────────
    task_ids_old = [
        t.task_id
        for t in db.query(ScheduleTaskModel.task_id)
        .filter(ScheduleTaskModel.batch_group == batch_group)
        .all()
    ]
    task_ids_new = [
        t.task_id
        for t in db.query(ScheduleTaskModel.task_id)
        .filter(ScheduleTaskModel.batch_group == new_group)
        .all()
    ]
    all_task_ids = task_ids_old + task_ids_new
    if all_task_ids:
        db.execute(
            text("DELETE FROM audit_log WHERE task_id IN :ids"),
            {"ids": tuple(all_task_ids)},
        )

    db.query(ScheduleTaskModel).filter(
        ScheduleTaskModel.batch_group == batch_group
    ).delete(synchronize_session=False)

    db.query(ScheduleTaskModel).filter(
        ScheduleTaskModel.batch_group == new_group
    ).delete(synchronize_session=False)

    db.commit()

    # ── 잔여 planned 배치 수 조회 (프론트 메시지용) ──────────────────────────
    # Stage 2 재실행 후 원본 그룹의 간트 블록 생성 여부를 클라이언트에 알린다.
    # "planned" 배치가 0이면 전체 WIP 충당 → 간트 블록 미표시 예상.
    original_remaining_planned = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.status == "planned",
        )
        .count()
    )

    return {
        "original_group": batch_group,
        "new_group": new_group,
        "moved_batches": updated,
        "original_remaining_planned": original_remaining_planned,
    }


# ---------------------------------------------------------------------------
# Task 2.3: batch_group 미배정 (unassign) — 단일 트랜잭션 엔드포인트
#
# 왜 여기서 commit하는가:
#   batch_group_lifecycle.unassign_batch_group은 flush만 수행 (Eng Critical #1).
#   라우트가 서비스 flush 직후 audit_log INSERT를 추가하고 단 한 번만 commit하여,
#   "unassign 상태 전이 + 감사 로그"가 원자적으로 함께 persist되거나 둘 다 롤백되게 한다.
# ---------------------------------------------------------------------------


@router.post(
    "/batch-group/{batch_group}/unassign",
    summary="batch_group 전체를 미배정으로 soft-delete (사유 기록)",
)
def unassign_batch_group_endpoint(
    batch_group: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """단일 트랜잭션: 서비스 flush + audit_log INSERT + commit.

    Body:
        { "reason": "자재지연" | "설비고장" | "납기재협상" | "기타" }
        생략 또는 None이면 서비스가 '기타'로 저장.

    Responses:
        200: { batch_group, affected_batches, affected_tasks, reason, idempotent }
        400: 상태(planned 외) / WIP 매칭 / reason 검증 오류
        404: batch_group 없음
    """
    from app.infrastructure.models.audit_log import AuditLog
    from app.application.validation.batch_group_lifecycle import (
        BatchGroupNotFoundError,
        BatchGroupReasonError,
        BatchGroupStatusError,
        BatchGroupWipMatchedError,
        unassign_batch_group,
    )

    reason = (body or {}).get("reason")

    try:
        result = unassign_batch_group(db, batch_group, reason=reason)
    except BatchGroupNotFoundError as exc:
        # 404: 존재하지 않는 batch_group — 상태 전이 없이 즉시 실패
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        BatchGroupStatusError,
        BatchGroupWipMatchedError,
        BatchGroupReasonError,
    ) as exc:
        # 400: 비즈니스 제약 위반 (상태/WIP/reason) — 서비스는 변경 전에 예외를 던지므로
        # rollback까지 할 필요는 없으나 세션 상태를 명시적으로 되돌려 다음 쿼리 안전 보장.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 멱등 호출(이미 모두 unassigned)에는 새 audit row를 남기지 않는다 — 감사 로그가
    # 실제 상태 전이에 1:1 대응하도록 유지 (중복 '변경 없음' 기록 방지).
    if not result.get("idempotent"):
        # run_label은 서비스 결과에 없으므로 첫 affected_batch에서 조회 (정통 소스).
        # 없으면 'manual' (sm_inventory.py 기존 컨벤션: wip.run_label or "manual").
        batch_run_label: str | None = None
        first_batch_id = (
            result["affected_batches"][0] if result["affected_batches"] else None
        )
        if first_batch_id is not None:
            batch_run_label = (
                db.query(ProductionBatch.run_label)
                .filter(ProductionBatch.batch_id == first_batch_id)
                .scalar()
            )

        db.add(
            AuditLog(
                run_label=batch_run_label or "manual",
                stage="stage1",
                action_type="BATCH_GROUP_UNASSIGNED",
                batch_id=first_batch_id,
                decision_reason=(
                    f"batch_group {batch_group} unassigned "
                    f"(reason={result['reason']}, "
                    f"tasks={len(result['affected_tasks'])})"
                ),
            )
        )

    db.commit()
    return result


# ---------------------------------------------------------------------------
# POST /pipeline/batch-group/{bg}/restore — unassigned → planned 복원 (Task 3.3)
#
# 왜 단일 트랜잭션:
#   batch_group_lifecycle.restore_batch_group은 flush만 수행 (Eng Critical #2).
#   라우트가 서비스 flush 직후 audit_log INSERT를 추가하고 한 번만 commit하여,
#   "상태 복원 + 감사 로그"가 원자적으로 persist 되거나 둘 다 롤백되게 한다.
#
# conflicts가 있을 경우:
#   서비스는 mutate하지 않고 반환하므로 commit/rollback 없이 바로 409로 매핑.
# ---------------------------------------------------------------------------


@router.post(
    "/batch-group/{batch_group}/restore",
    summary="unassigned batch_group을 원래 자리로 복원 (status flip)",
)
def restore_batch_group_endpoint(
    batch_group: str, db: Session = Depends(get_db)
) -> dict:
    """단일 트랜잭션: 서비스 flush → audit_log INSERT → commit.

    Responses:
        200: 성공 — restored_tasks 배열 (멱등이면 빈 배열)
        404: batch_group 없음
        400: unassigned 외 상태 혼재
        409: 원래 자리 점유됨 — detail.conflicts 에 상세 반환
    """
    from app.infrastructure.models.audit_log import AuditLog
    from app.application.validation.batch_group_lifecycle import (
        BatchGroupNotFoundError,
        BatchGroupStatusError,
        restore_batch_group,
    )

    try:
        result = restore_batch_group(db, batch_group)
    except BatchGroupNotFoundError as exc:
        # 404: 존재하지 않는 batch_group — 상태 전이 없이 즉시 실패
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BatchGroupStatusError as exc:
        # 400: planned 외 상태 혼재 — 서비스가 변경 전 예외 throw. 세션 clean 유지.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result["conflicts"]:
        # 서비스는 아무것도 mutate하지 않고 반환 — commit 하지 않아도 세션 clean.
        # detail은 dict로 전달해 프론트가 conflicts 배열을 바로 파싱할 수 있게 함.
        raise HTTPException(
            status_code=409,
            detail={"conflicts": result["conflicts"]},
        )

    # 멱등 호출(이미 모두 planned)에는 새 audit row를 남기지 않는다 — 감사 로그가
    # 실제 상태 전이에 1:1 대응하도록 유지 (unassign 엔드포인트와 동일 규칙).
    if not result.get("idempotent"):
        # run_label은 서비스 결과에 없으므로 복원된 batch 중 하나에서 조회.
        # 없으면 'manual' (unassign 엔드포인트와 동일 컨벤션).
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_group == batch_group)
            .first()
        )
        run_label = (batch.run_label if batch else None) or "manual"
        first_batch_id = batch.batch_id if batch else None

        db.add(
            AuditLog(
                run_label=run_label,
                stage="stage1",
                action_type="BATCH_GROUP_RESTORED",
                batch_id=first_batch_id,
                decision_reason=(
                    f"batch_group {batch_group} restored, "
                    f"tasks={len(result['restored_tasks'])}"
                ),
            )
        )

    db.commit()
    return result


# ---------------------------------------------------------------------------
# POST /pipeline/batch-group/{bg}/restore-at — anchor 기준 재배치 preview (Task 1.2)
#
# no-mutation preview: 서비스가 flush/commit 없이 계산 결과만 반환한다.
# 프론트가 CascadePreviewModal로 렌더 후 확정 시 bulk-update-v2로 일괄 반영.
# ---------------------------------------------------------------------------


from app.presentation.schemas.restore_at import RestoreAtRequest, RestoreAtResponse  # noqa: E402
from app.application.validation.batch_group_lifecycle import (  # noqa: E402
    BatchGroupNotFoundError,
    BatchGroupStatusError,
    compute_restore_at_plan,
)


@router.post(
    "/batch-group/{batch_group}/restore-at",
    summary="unassigned batch_group 을 anchor 위치 기준으로 재배치 preview (no mutation)",
)
def restore_batch_group_at_endpoint(
    batch_group: str,
    body: RestoreAtRequest,
    db: Session = Depends(get_db),
) -> RestoreAtResponse:
    """no-mutation preview — 프론트가 이 결과를 CascadePreviewModal 로 렌더 →
    확정 시 bulk-update-v2 로 일괄 반영.

    Responses:
        200: RestoreAtResponse (task_positions + cascade-preview-v2 호환 필드)
        400: unassigned 외 상태 혼재 (BatchGroupStatusError)
        404: batch_group 없음 (BatchGroupNotFoundError)
        422: body 검증 실패 (Pydantic)
    """
    try:
        result = compute_restore_at_plan(
            db,
            batch_group=batch_group,
            anchor_equipment_code=body.anchor_equipment_code,
            anchor_start=body.anchor_start,
        )
    except BatchGroupNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BatchGroupStatusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RestoreAtResponse(
        batch_group=result.batch_group,
        task_positions=[tp.__dict__ for tp in result.task_positions],
        pushes=result.pushes,
        pulls=result.pulls,
        unresolved=result.unresolved,
        request_id=result.request_id,
        can_auto_resolve=result.can_auto_resolve,
        iter_count=result.iter_count,
        truncated=result.truncated,
    )


# ---------------------------------------------------------------------------
# GET /pipeline/batch-group-snapshots — unassigned batch_group 목록 (Task 3.3)
#
# 프론트 OrderInbox가 새로고침 시 호출. batch_group 단위 집계 + unassign_reason 포함.
#
# equipment_group 관련:
#   백엔드 ProductionBatch에는 equipment_group 컬럼이 없다 (Task 3.1 검증).
#   process_name을 그대로 노출하고, 프론트의 toEquipmentGroup(process_name, batch_group)
#   헬퍼가 canonicalize 한다 — 백엔드/프론트 계약 단순화.
# ---------------------------------------------------------------------------


@router.get(
    "/batch-group-snapshots",
    summary="unassigned 상태 batch_group 목록 (사유 + 공정체인 포함)",
)
def list_batch_group_snapshots(db: Session = Depends(get_db)) -> dict:
    """Returns: { "groups": [BatchGroupSnapshot] } — 프론트 OrderInbox 용."""
    rows = (
        db.query(ProductionBatch).filter(ProductionBatch.status == "unassigned").all()
    )
    groups: dict[str, dict] = {}
    for b in rows:
        g = groups.setdefault(
            b.batch_group,
            {
                "batch_group": b.batch_group,
                "customer": b.customer_name or "",
                "spec": b.spec_raw or "",
                "color": b.sheath_color or "",
                "total_length_m": 0.0,
                "delivery_date": b.due_date.isoformat() if b.due_date else "",
                "processes": [],
                "order_count": 0,
                "unassign_reason": b.unassign_reason or "기타",
            },
        )
        g["total_length_m"] += float(b.total_length_m or 0)
        g["order_count"] += 1
        # equipment_group은 백엔드 모델에 없음 — process_name을 그대로 노출.
        # 프론트의 toEquipmentGroup(process_name, batch_group)이 canonicalize.
        g["processes"].append(
            {
                "process": b.process_name or "",
                "equipment_group": b.process_name or "",
            }
        )
    return {"groups": list(groups.values())}


# ---------------------------------------------------------------------------
# Sub-router includes — runs/batch (Task 1.2) + stage1/stage2 (Task 1.3)
# ---------------------------------------------------------------------------
from app.presentation.routes import (  # noqa: E402
    plan_pipeline_batch,
    plan_pipeline_runs,
    plan_pipeline_stage1,
    plan_pipeline_stage2,
)

router.include_router(plan_pipeline_runs.router)
router.include_router(plan_pipeline_batch.router)
router.include_router(plan_pipeline_stage1.router)
router.include_router(plan_pipeline_stage2.router)

# Re-export — 외부 import path 보존 (test_stage1_update_versioning.py:177 등)
# from app.presentation.routes.plan_pipeline import compare_runs, list_runs, delete_run
from app.presentation.routes.plan_pipeline_runs import (  # noqa: E402, F401
    compare_runs,
    list_runs,
    delete_run,
)
