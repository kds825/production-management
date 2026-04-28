"""create_batches() 의 Phase 1 — 연선 그룹 배치 생성 (가장 큰 phase).

Phase 1 refactor (Task 2.4): batch_grouper.create_batches() 의 line 208~601
'Phase 1: 연선 그룹 배치 생성' 블록을 분리. 연선(stranding) 공정은 동일
SQ·전압·연선방식 수주들을 묶어 ceil(합계수량 / lot_stranding) × lot_stranding
으로 틀단위 작업지시를 생성한다. 수주 1:1 배치가 아니라 SQ 그룹 단위로
1개(또는 그 이상)의 배치를 만든다.

토글 wrapper (`_is_outsource_rule_with_toggle`, `_should_skip_stranding`)
은 batch_grouper 모듈 내부에 머물러 있고, helper 가 callable 로 주입받는다
(순환 import 회피).
"""

import math
from datetime import date

from app.application.ingest._batch_grouper_loader import _GrouperInputs
from app.application.ingest.batch_helpers import (
    _find_item,
    _find_speed,
    _get_processes,
    _infer_material,
    _infer_routing,
    extract_sq,
)
from app.domain.constraint_rules import resolve_spec_setup_min
from app.infrastructure.models.production_batch import ProductionBatch


def create_strand_batches(
    ctx: _GrouperInputs,
    *,
    is_outsource_rule_with_toggle,
    should_skip_stranding,
) -> list[ProductionBatch]:
    """원본: batch_grouper.py 의 'Phase 1 연선' 블록 ~395줄.

    연선 공정의 SQ+전압+연선방식 그룹별 배치 생성.
    ceil(합계수량 / lot_stranding) × lot_stranding 으로 틀단위 작업지시.
    """
    # 본문 변수 매핑 — 기존 로직 (line 290~) 의 변수 참조를 ctx 에서 풀어준다.
    constraint_params = ctx.constraint_params
    orders = ctx.orders
    wip_by_order_line = ctx.wip_by_order_line
    drum_lots = ctx.drum_lots
    routings = ctx.routings
    items = ctx.items
    defect_buffer_pct = ctx.defect_buffer_pct
    speed_lookup = ctx.speed_lookup
    run_label = ctx.run_label

    # ── Phase 1: 연선 그룹 배치 생성 ────────────────────────────────────────
    # 연선(stranding) 공정은 동일 SQ·전압·연선방식 수주들을 묶어
    # ceil(합계수량 / lot_stranding) × lot_stranding 으로 틀단위 작업지시를 생성한다.
    # 수주 1:1 배치가 아니라 SQ 그룹 단위로 1개(또는 그 이상)의 배치를 만든다.
    batches: list[ProductionBatch] = []

    # 그룹 키: (sq, voltage) — 연선은 SQ+전압만으로 그루핑
    # stranding_type은 제품별 속성이지 공정 구분이 아님
    # 값: {total_qty, lot_size, orders, routing_code, rep_item}
    _strand_groups: dict[tuple, dict] = {}

    for order in orders:
        sq = extract_sq(order.spec_raw)
        if sq is None:
            continue

        # 외주 분류 조건 — 2-2 is_enabled 게이트 통과 후 룰 본문 적용
        if is_outsource_rule_with_toggle(
            sq, order.product_group, order.customer_name, params=constraint_params
        ):
            continue

        item_o = _find_item(order, items)
        # item_master의 core_count가 수주와 다르면 라우팅 추론으로 폴백
        # (ERP에서 1C 아이템으로 잘못 매핑된 다심 수주 대응)
        order_cores = int(order.core_count or 1)
        item_cores = int(item_o.core_count or 1) if item_o else 1
        if item_o and order_cores != item_cores:
            routing_code_o = _infer_routing(order)
        else:
            routing_code_o = item_o.routing_code if item_o else _infer_routing(order)
        routing_o = routings.get(routing_code_o)
        if routing_o is None:
            continue
        processes_o = _get_processes(routing_o)
        if "연선" not in processes_o:
            continue

        # TFR-GV 소단면 연선 스킵 — 5-5 is_enabled 게이트
        if should_skip_stranding(order.product_group, sq, params=constraint_params):
            continue

        stranding_type_raw = (
            item_o.stranding_type if item_o and item_o.stranding_type else "압축연선"
        )
        # 5-2 정규화: "압축"과 "압축연선"은 같은 물리적 연선방식 → 통합
        # "단선", "집합연선" 등 진짜 다른 방식만 분리
        stranding_type_o = (
            "압축연선"
            if stranding_type_raw in ("압축", "압축연선")
            else stranding_type_raw
        )
        voltage_o = order.voltage or ""
        gkey = (sq, voltage_o, stranding_type_o)

        lot_info = drum_lots.get(sq)
        lot_size = (
            float(lot_info.lot_stranding)
            if lot_info and lot_info.lot_stranding
            else None
        )

        order_qty = float(order.ordered_qty_m or 0) * (1.0 + defect_buffer_pct)
        if order_qty <= 0:
            continue

        # 다심(multi-core) 케이블: 각 코어를 개별 연선하므로 작업량 = 수주량 × core_count
        core_count_o = int(order.core_count or 1)
        strand_qty = order_qty * core_count_o

        if gkey not in _strand_groups:
            _strand_groups[gkey] = {
                "total_qty": 0.0,
                "wip_strand_qty": 0.0,  # 연선재고 WIP 사용 수주 수량 합계 (틀 계산 제외)
                "lot_size": lot_size,
                "routing_code": routing_code_o,
                "stranding_type": stranding_type_o,
                "orders": [],
            }
        _strand_groups[gkey]["total_qty"] += strand_qty
        # WIP 사용 수주: 연선이 이미 완료된 재고 → 틀 계산 대상에서 차감
        # 연선재고: 연선 완료 / 절연재고: 연선+절연 완료 → 둘 다 연선 작업 불요
        # wip_type: "연선" 또는 "연선재고" → 연선 작업 불필요, 틀 계산 제외
        _wip_type = (getattr(order, "wip_type", "") or "").replace("재고", "")
        if getattr(order, "use_wip", False) and _wip_type in ("연선", "절연"):
            _strand_groups[gkey]["wip_strand_qty"] += strand_qty
        _strand_groups[gkey]["orders"].append(order)

    for (sq, voltage_g, stranding_type_g), grp in _strand_groups.items():
        total_qty_g: float = grp["total_qty"]
        wip_strand_qty_g: float = grp.get("wip_strand_qty", 0.0)
        net_qty_g: float = max(total_qty_g - wip_strand_qty_g, 0.0)  # 실제 연선 작업량
        lot_size_g: float | None = grp["lot_size"]
        orders_g: list = sorted(
            grp["orders"],
            key=lambda o: (o.customer_priority or 99, o.due_date or date.max),
        )

        # WIP 전량 활용 시(net_qty_g ≈ 0) 연선 작업 불요 → 틀·작업량 0
        skip_strand_work = net_qty_g < 1.0  # 1m 미만이면 사실상 0
        if skip_strand_work:
            lot_count_g = 0
            work_qty_g = 0.0
        elif lot_size_g and lot_size_g > 0:
            lot_count_g = max(math.ceil(net_qty_g / lot_size_g), 1)
            work_qty_g = lot_count_g * lot_size_g
        else:
            lot_count_g = 1
            lot_size_g = net_qty_g or total_qty_g
            work_qty_g = lot_size_g

        routing_code_g = grp["routing_code"]
        # 2-4 is_enabled 게이트 + sq>=300 사전 조건. CU 재질 체크는 use site
        # (line 563/624) 에서 conductor_material_o 와 함께 수행.
        is_61strand_g = (sq >= 300) and constraint_params.is_rule_enabled(
            "2-4", default=True
        )

        # ── 연선 그룹 공통 정보 ──────────────────────────────────────────────
        strand_batch_group = f"ST-{int(sq)}-{voltage_g}-{stranding_type_g}"
        if skip_strand_work:
            remarks_group = (
                f"연선그룹 {len(orders_g)}건 / "
                f"WIP 전량 활용 — 연선 작업 불요 "
                f"(총량 {total_qty_g:.0f}m, WIP {wip_strand_qty_g:.0f}m)"
            )
        else:
            remarks_group = (
                f"연선그룹 {len(orders_g)}건 {lot_count_g}틀 / "
                f"그룹총량 {total_qty_g:.0f}m→{work_qty_g:.0f}m (틀단위 {lot_size_g:.0f}m)"
            )

        # 그룹 대표 선속·준비시간 (첫 번째 수주 기준, 동일 SQ 그룹이므로 동일 선속)
        rep_order_g = orders_g[0]
        rep_speed_g = _find_speed(speed_lookup, "연선", rep_order_g, sq)
        line_speed_g = (
            float(rep_speed_g.line_speed_mpm)
            if rep_speed_g and rep_speed_g.line_speed_mpm
            else None
        )
        # 연선 헤더 배치 (batch_seq=-1) — ConstraintConfig 4-1 stranding_min 우선.
        # SpeedMaster.setup_spec_min 은 이 공정에선 무시 (UI 4-1 편집이 즉시 반영되게).
        setup_time_g = resolve_spec_setup_min(
            process_name="연선",
            sm_spec_min=(
                float(rep_speed_g.setup_spec_min)
                if rep_speed_g and rep_speed_g.setup_spec_min is not None
                else None
            ),
            params=constraint_params,
        )
        rep_item_g = _find_item(rep_order_g, items)
        rep_material_g = _infer_material(rep_order_g)
        earliest_due_g = min((o.due_date for o in orders_g if o.due_date), default=None)
        best_priority_g = min(o.customer_priority or 99 for o in orders_g)

        # batch_seq=-1: 그룹 헤더 배치 ─────────────────────────────────────
        # 스케줄러(schedule_optimizer)가 이 배치의 estimated_duration_min으로
        # 실제 틀단위 작업량(work_qty_g) 기준 간트 블록 크기를 결정한다.
        # Excel·scheduling-review 표시에서는 제외(batch_seq=-1 필터)된다.
        # WIP 전량 활용(skip_strand_work) 시 헤더 배치를 생성하지 않는다 —
        # 스케줄러가 불필요한 연선 작업을 배정하지 않도록.
        if not skip_strand_work:
            header_duration_g = work_qty_g / line_speed_g if line_speed_g else None
            # wip_output_expected_m: 틀단위 생산량(work_qty_g)에서 실제 필요량(net_qty_g)을
            # 빼면 연선 후 잉여(SM재고 예정)가 된다. defect_buffer는 net_qty_g에 이미 포함.
            # 이 값은 헤더 배치(batch_seq=-1)에서만 계산한다.
            # Task 6 Listener가 wip_output_expected_m > 0 조건으로 예상 WIP를 자동 생성한다.
            header_wip_surplus_g = max(0.0, work_qty_g - net_qty_g)
            header_batch = ProductionBatch(
                run_label=run_label,
                sales_order_id=rep_order_g.order_id,
                sales_order_line=rep_order_g.order_line,
                item_code=rep_item_g.item_code if rep_item_g else None,
                routing_code=routing_code_g,
                process_name="연선",
                batch_seq=-1,
                drum_length_m=lot_size_g,
                drum_count=lot_count_g,
                total_length_m=work_qty_g,
                extra_length_m=0,
                sq_mm2=sq,
                core_count=int(rep_order_g.core_count or 1),
                core_colors=rep_order_g.core_colors,
                sheath_color=rep_order_g.sheath_color,
                customer_name=rep_order_g.customer_name,
                due_date=earliest_due_g,
                customer_priority=best_priority_g,
                line_speed_mpm=line_speed_g,
                setup_time_min=setup_time_g,
                estimated_duration_min=header_duration_g,
                status="planned",
                product_group=rep_order_g.product_group,
                voltage=rep_order_g.voltage,
                conductor_material=rep_material_g,
                stranding_type=stranding_type_g,
                remarks=remarks_group,
                equipment_code=None,
                wip_matched_id=None,
                spec_raw=rep_order_g.spec_raw,
                batch_group=strand_batch_group,
                wip_output_expected_m=header_wip_surplus_g,
            )
            batches.append(header_batch)

        # ── 수주별 연선 배치 생성 (display용) ────────────────────────────────
        # scheduling-review·Excel 수주 단위 행 표시용.
        # estimated_duration_min=None — 스케줄러는 헤더 배치(seq=-1) duration 사용.
        # wip_output_expected_m 은 헤더 전용 (batch_seq == -1 AND "연선"). 여기선 default 0.
        # Listener (Task 6) 가 batch_seq 로 gate 하므로 이 배치는 WIP auto-create 안 됨.
        for order in orders_g:
            order_qty_o: float = float(order.ordered_qty_m or 0) * (
                1.0 + defect_buffer_pct
            )
            item_o = _find_item(order, items)
            conductor_material_o = _infer_material(order)
            speed_o = _find_speed(speed_lookup, "연선", order, sq)
            line_speed_o = (
                float(speed_o.line_speed_mpm)
                if speed_o and speed_o.line_speed_mpm
                else None
            )
            # 연선 strand_batch (batch_seq=1) — 동일하게 4-1 stranding_min 우선.
            setup_time_o = resolve_spec_setup_min(
                process_name="연선",
                sm_spec_min=(
                    float(speed_o.setup_spec_min)
                    if speed_o and speed_o.setup_spec_min is not None
                    else None
                ),
                params=constraint_params,
            )

            matched_wip_o = wip_by_order_line.get(
                f"{order.order_id}:{order.order_line}"
            )
            wip_id_o: int | None = matched_wip_o.wip_id if matched_wip_o else None

            strand_batch = ProductionBatch(
                run_label=run_label,
                sales_order_id=order.order_id,
                sales_order_line=order.order_line,
                item_code=item_o.item_code if item_o else None,
                routing_code=routing_code_g,
                process_name="연선",
                batch_seq=1,
                drum_length_m=float(order.drum_length_m or order.ordered_qty_m or 0),
                drum_count=int(order.drum_count or 1),
                total_length_m=float(order.ordered_qty_m or 0),
                extra_length_m=0,
                sq_mm2=sq,
                core_count=int(order.core_count or 1),
                core_colors=order.core_colors,
                sheath_color=order.sheath_color,
                customer_name=order.customer_name,
                due_date=order.due_date,
                customer_priority=order.customer_priority or 99,
                line_speed_mpm=line_speed_o,
                setup_time_min=setup_time_o,
                estimated_duration_min=None,
                status="planned",
                product_group=order.product_group,
                voltage=order.voltage,
                conductor_material=conductor_material_o,
                stranding_type=stranding_type_g,
                remarks=remarks_group,
                equipment_code=None,
                wip_matched_id=wip_id_o,
                spec_raw=order.spec_raw,
                batch_group=strand_batch_group,
            )
            batches.append(strand_batch)

            # 61연선(300SQ+ CU) — 7연선 코어 선행 배치도 수주 단위로 생성
            # WIP 전량 활용 시 코어 선행 배치도 불요
            # wip_output_expected_m 은 헤더 전용 (batch_seq == -1 AND "연선"). 여기선 default 0.
            # Listener (Task 6) 가 batch_seq 로 gate 하므로 이 배치는 WIP auto-create 안 됨.
            if is_61strand_g and conductor_material_o == "CU" and not skip_strand_work:
                core_speed_o = _find_speed(speed_lookup, "연선", order, 35.0)
                core_spd = (
                    float(core_speed_o.line_speed_mpm)
                    if core_speed_o and core_speed_o.line_speed_mpm
                    else 25.0
                )
                core_setup = resolve_spec_setup_min(
                    process_name="연선",
                    sm_spec_min=(
                        float(core_speed_o.setup_spec_min)
                        if core_speed_o and core_speed_o.setup_spec_min is not None
                        else None
                    ),
                    params=constraint_params,
                )
                core_dur = order_qty_o / core_spd if core_spd > 0 else None
                core_batch = ProductionBatch(
                    run_label=run_label,
                    sales_order_id=order.order_id,
                    sales_order_line=order.order_line,
                    item_code=item_o.item_code if item_o else None,
                    routing_code=routing_code_g,
                    process_name="연선",
                    batch_seq=0,
                    drum_count=int(order.drum_count or 1),
                    drum_length_m=float(order.drum_length_m or 0)
                    or (float(order.ordered_qty_m or 0) / int(order.drum_count or 1)),
                    total_length_m=float(order.ordered_qty_m or 0),
                    extra_length_m=0,
                    sq_mm2=35,  # T6BO SQ 범위(≤35) 매칭용 — 실제 선심 소선경 기준
                    core_count=int(order.core_count or 1),
                    core_colors=order.core_colors,
                    sheath_color=order.sheath_color,
                    customer_name=order.customer_name,
                    due_date=order.due_date,
                    customer_priority=order.customer_priority or 99,
                    line_speed_mpm=core_spd,
                    setup_time_min=core_setup,
                    estimated_duration_min=core_dur,
                    status="planned",
                    product_group=order.product_group,
                    voltage=order.voltage,
                    conductor_material=conductor_material_o,
                    stranding_type="7연선코어",
                    remarks=f"61연선 코어 ({int(sq)}SQ용)",
                    equipment_code=None,
                    wip_matched_id=wip_id_o,
                    spec_raw=order.spec_raw,
                    # 7연선 코어는 별도 그룹 — optimizer가 T6B0에 독립 배치하기 위해
                    # "CORE-{main_sq}-{voltage}" 키를 사용한다.
                    # ST-{sq} 그룹 처리 전에 먼저 스케줄링하여 선행관계를 만족시킨다.
                    batch_group=f"CORE-{int(sq)}-{voltage_g}",
                )
                batches.append(core_batch)

            # AL 61연선(633SQ 등) — AL 7연선 코어 선행 배치 (AL6BO 설비)
            # 633SQ 고압 케이블은 CU 도체 + AL 시스 구조이므로,
            # AL 시스용 7연선 코어를 AL6BO에서 별도 생산해야 한다.
            # wip_output_expected_m 은 헤더 전용 (batch_seq == -1 AND "연선"). 여기선 default 0.
            # Listener (Task 6) 가 batch_seq 로 gate 하므로 이 배치는 WIP auto-create 안 됨.
            if is_61strand_g and conductor_material_o == "AL" and not skip_strand_work:
                # AL6BO speed_master가 없으면 T6B0 기준 fallback
                al_core_speed_o = _find_speed(speed_lookup, "연선", order, 35.0)
                al_core_spd = (
                    float(al_core_speed_o.line_speed_mpm)
                    if al_core_speed_o and al_core_speed_o.line_speed_mpm
                    else 25.0
                )
                al_core_setup = resolve_spec_setup_min(
                    process_name="연선",
                    sm_spec_min=(
                        float(al_core_speed_o.setup_spec_min)
                        if al_core_speed_o
                        and al_core_speed_o.setup_spec_min is not None
                        else None
                    ),
                    params=constraint_params,
                )
                al_core_dur = order_qty_o / al_core_spd if al_core_spd > 0 else None
                al_core_batch = ProductionBatch(
                    run_label=run_label,
                    sales_order_id=order.order_id,
                    sales_order_line=order.order_line,
                    item_code=item_o.item_code if item_o else None,
                    routing_code=routing_code_g,
                    process_name="연선",
                    batch_seq=0,
                    drum_count=int(order.drum_count or 1),
                    drum_length_m=float(order.drum_length_m or 0)
                    or (float(order.ordered_qty_m or 0) / int(order.drum_count or 1)),
                    total_length_m=float(order.ordered_qty_m or 0),
                    extra_length_m=0,
                    sq_mm2=35,  # AL6BO SQ 범위(25~50) 매칭용
                    core_count=int(order.core_count or 1),
                    core_colors=order.core_colors,
                    sheath_color=order.sheath_color,
                    customer_name=order.customer_name,
                    due_date=order.due_date,
                    customer_priority=order.customer_priority or 99,
                    line_speed_mpm=al_core_spd,
                    setup_time_min=al_core_setup,
                    estimated_duration_min=al_core_dur,
                    status="planned",
                    product_group=order.product_group,
                    voltage=order.voltage,
                    conductor_material="AL",  # AL6BO material_limit 매칭
                    stranding_type="7연선코어",
                    remarks=f"AL 61연선 코어 ({int(sq)}SQ용)",
                    equipment_code=None,
                    wip_matched_id=wip_id_o,
                    spec_raw=order.spec_raw,
                    # AL 7연선 코어 전용 그룹 — optimizer가 AL6BO에 배치
                    # "AL-CORE-{main_sq}-{voltage}" 키로 ST-{sq} 선행관계 유지
                    batch_group=f"AL-CORE-{int(sq)}-{voltage_g}",
                )
                batches.append(al_core_batch)

    return batches
