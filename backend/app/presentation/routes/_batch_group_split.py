"""batch_group split 비즈니스 로직 — endpoint 본체 분리 (Task 1.4).

원본: ``plan_pipeline.py`` 의 ``split_batch_group`` endpoint 본문 (~410줄).
endpoint 자체는 ``plan_pipeline_batch_group.py`` 에 남고, 본 모듈의
``split_batch_group(*, db, batch_group, body)`` 를 호출만 한다.

Side-effects (review patch Appendix A.4 명시 — 회귀 게이트):
    - ``ProductionBatch`` 신규 헤더 INSERT (분할된 신규 그룹용)
    - ``ProductionBatch.status = "wip_complete"`` 일부 row (Phase 1 연선 그룹)
    - ``ProductionBatch.wip_matched_id`` 재배정 (WIP 최적화)
    - ``ProductionBatch.batch_group`` 변경 (분할 대상 batch 들 → new_group)
    - ``ProductionBatch.drum_count / total_length_m / estimated_duration_min``
      등 헤더 필드 갱신 (잔여 그룹용)
    - ``audit_log`` row delete (해당 batch_group 의 task_id 참조 제거)
    - ``schedule_task`` row delete (해당 batch_group)
    - 마지막 ``db.commit()``

키워드-only 시그니처 (Appendix A.4 강제 — positional 호출 금지):
    split_batch_group(*, db=..., batch_group=..., body=...)
"""

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch


def split_batch_group(
    *,
    db: Session,
    batch_group: str,
    body: dict,
) -> dict:
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
