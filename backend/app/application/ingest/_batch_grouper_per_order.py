"""create_batches() 의 Phase 2 — 절연 / 시스 등 수주별 배치 생성.

Phase 2 refactor (Task 2.3): batch_grouper.create_batches() 의 line 603~797
'Phase 2: 수주별 배치 생성' 블록을 분리. 연선 외 공정 (저압/고압 절연,
저압/고압 시스, T·P 등) 의 배치를 수주 단위로 생성한다.

result dict 는 호출자가 소유 — helper 는 warnings / outsource_count 를
직접 mutate 하여 동일 메시지/카운트 invariant 를 보존한다 (외부 시그니처
변경 회피).
"""

from app.application.ingest._batch_grouper_loader import _GrouperInputs
from app.application.ingest.batch_helpers import (
    _find_item,
    _find_speed,
    _get_processes,
    _infer_material,
    _infer_routing,
    extract_sq,
)
from app.domain.batch_sheath_keys import _WIP_COVERED_PROCESSES
from app.domain.constraint_rules import resolve_spec_setup_min
from app.infrastructure.models.production_batch import ProductionBatch


def create_per_order_batches(
    ctx: _GrouperInputs,
    result: dict,
    *,
    is_outsource_rule_with_toggle,
    should_skip_stranding,
    is_61strand_rule,
) -> list[ProductionBatch]:
    """Phase 2 — 절연 / 시스 등 수주별 배치 생성.

    원본: batch_grouper.py:603-797 본문. 연선 외 공정의 배치를 수주 단위로
    생성하고, 외주 자동분류 / SQ 파싱 실패 / 라우팅 부재 / 수량 누락 등
    각 단계의 warnings 를 result["warnings"] 에 그대로 append. 외주 분류
    카운트는 result["outsource_count"] 에 누적.

    rule helper 들 (`_is_outsource_rule_with_toggle`, `_should_skip_stranding`,
    `_is_61strand_rule`) 은 batch_grouper 모듈에 정의되어 ConstraintParams
    토글 게이트를 캡슐화한다. 순환 import 회피를 위해 호출자가 callable
    로 주입 (DI). default 인자 없음 — 항상 명시적으로 전달.
    """
    run_label = ctx.run_label
    orders = ctx.orders
    items = ctx.items
    routings = ctx.routings
    customers = ctx.customers
    constraint_params = ctx.constraint_params
    base_extra = ctx.base_extra
    sample_extra = ctx.sample_extra
    defect_buffer_pct = ctx.defect_buffer_pct
    speed_lookup = ctx.speed_lookup
    wip_by_order_line = ctx.wip_by_order_line

    batches: list[ProductionBatch] = []

    for order in orders:
        order_ref = f"{order.order_id}-{order.order_line}"

        # SQ 추출 실패 시 스킵 (규격 텍스트 품질 문제)
        sq = extract_sq(order.spec_raw)
        if sq is None:
            result["warnings"].append(
                f"수주 {order_ref}: SQ 파싱 실패 (spec_raw={order.spec_raw!r})"
            )
            continue

        # ── 외주 자동분류 (2-2) — 2-2 is_enabled 게이트 통과 후 룰 본문 ──
        if is_outsource_rule_with_toggle(
            sq, order.product_group, order.customer_name, params=constraint_params
        ):
            result["outsource_count"] += 1
            result["warnings"].append(
                f"수주 {order_ref}: 외주 자동분류 "
                f"(SQ={sq}, 제품군={order.product_group}, 고객={order.customer_name or ''})"
            )
            continue

        # 품목 마스터 매칭 → 라우팅 코드 결정
        # item_master core_count ≠ 수주 core_count이면 라우팅 추론 폴백
        item = _find_item(order, items)
        o_cores = int(order.core_count or 1)
        i_cores = int(item.core_count or 1) if item else 1
        if item and o_cores != i_cores:
            routing_code = _infer_routing(order)
        else:
            routing_code = item.routing_code if item else _infer_routing(order)

        routing = routings.get(routing_code)
        if routing is None:
            result["warnings"].append(f"수주 {order_ref}: 라우팅 '{routing_code}' 없음")
            continue

        # 고객 정보 — 우선순위 및 샘플 여부
        customer = customers.get(order.customer_name)
        priority: int = (
            int(customer.priority) if customer and customer.priority is not None else 99
        )
        require_sample: bool = bool(customer.require_sample) if customer else False

        # 여척 계산: 색상 손실 기본값 + 샘플 필요 고객 추가분
        extra: float = base_extra + (sample_extra if require_sample else 0.0)

        # 수량 파싱 — ordered_qty_m이 없으면 drum_length × drum_count로 대체
        total_qty: float = float(order.ordered_qty_m or 0)
        if total_qty <= 0:
            result["warnings"].append(
                f"수주 {order_ref}: ordered_qty_m 누락 또는 0, 건너뜀"
            )
            continue

        # ── 불량 재작업 버퍼 가산 (7-1) ─────────────────────────────────────
        total_qty = total_qty * (1.0 + defect_buffer_pct)

        drum_count: int = int(order.drum_count or 1)
        drum_length: float = float(order.drum_length_m or 0) or (
            float(order.ordered_qty_m or 0) / drum_count
        )
        core_count: int = int(order.core_count or 1)

        # 공정 목록 추출
        processes = _get_processes(routing)
        if not processes:
            result["warnings"].append(
                f"수주 {order_ref}: 라우팅 '{routing_code}'에 공정 없음"
            )
            continue

        conductor_material = _infer_material(order)
        stranding_type: str = (
            item.stranding_type if item and item.stranding_type else "압축"
        )

        # ── 틀단위 정보 계산 (2-3) — 행 분할 없이 배치 1건 유지 ──────────────
        # 원본 계획서는 ERP 1행 = 배치 시트 1행 (분할 없음).
        # 틀 수는 배치 헤더에만 표시하고, duration 계산에만 반영한다.
        # 분할은 batch_group 레벨에서 schedule_optimizer가 처리.
        lot_items: list[tuple[float, int]] = [(total_qty, drum_count)]

        # WIP 매칭된 수주 → 배치에 wip_matched_id 전파
        # Stage 1에서는 모든 공정에 배치를 생성 (가시성 확보)
        # Stage 2에서 WIP 커버 공정은 스케줄링 스킵
        order_line_key = f"{order.order_id}:{order.order_line}"
        matched_wip = wip_by_order_line.get(order_line_key)
        wip_id: int | None = matched_wip.wip_id if matched_wip else None

        # TFR-GV 소단면(SQ ≤ 25): 단선 접지선 → 연선(stranding) 불필요
        # 원본 계획서에서 16SQ/25SQ TFR-GV는 연선 시트에 미표시, 시스만 표시
        # 5-5 is_enabled 게이트는 _should_skip_stranding 안에서 처리.
        skip_stranding = should_skip_stranding(
            order.product_group, sq, params=constraint_params
        )

        # ── 61연선(300SQ+) 2단계 분할 (Task #7 조사) ─────────────────────
        # 61연선 = 7연선 코어(batch_seq=0, 35SQ, T6B0 설비) + 본 배치(batch_seq=1, 원래 SQ, 54BO 설비)
        # batch_seq=0이 schedule_optimizer에서 먼저 정렬되어 간트에서 코어가 선행 배치된다.
        # 7연선 코어를 T6B0에서 먼저 제작 후 54BO에서 외층 추가
        # 2-4 is_enabled 게이트는 _is_61strand_rule 안에서 처리.
        is_61strand = is_61strand_rule(  # noqa: F841
            sq, conductor_material, params=constraint_params
        )

        # 61연선(300SQ+) 여부 감지 — Phase 1에서 그룹 단위 코어 배치 생성 시 참조됨
        # (Phase 2에서는 연선 배치를 생성하지 않으므로 직접 사용되지 않음)
        is_61strand = is_61strand_rule(  # noqa: F841
            sq, conductor_material, params=constraint_params
        )

        # WIP process_stage 조회 — Phase 2 배치 생략 판단에 사용
        wip_stage: str = ""
        if matched_wip:
            wip_stage = getattr(matched_wip, "process_stage", "") or ""

        # 공정별 배치 생성 — 절연·시스 등 연선 외 공정만 (수주 1:1)
        # 연선 공정은 Phase 1에서 SQ 그룹 단위로 틀단위 작업지시로 이미 생성됨
        for batch_seq, process_name in enumerate(processes, start=1):
            if process_name == "신선":
                continue
            if process_name == "연선":
                continue  # Phase 1(연선 그룹 배치)에서 처리
            if skip_stranding and process_name == "연선":
                continue
            # WIP 재고가 이 공정을 이미 커버하면 배치 생성 불필요
            # 예: 절연재고 → 절연 공정 배치 생략 / 연선재고 → 절연은 그대로 생성
            if wip_stage and process_name in _WIP_COVERED_PROCESSES.get(
                wip_stage, set()
            ):
                continue
            speed_info = _find_speed(speed_lookup, process_name, order, sq)
            line_speed = (
                float(speed_info.line_speed_mpm)
                if speed_info and speed_info.line_speed_mpm is not None
                else None
            )
            setup_time: float = resolve_spec_setup_min(
                process_name=process_name,
                sm_spec_min=(
                    float(speed_info.setup_spec_min)
                    if speed_info and speed_info.setup_spec_min is not None
                    else None
                ),
                params=constraint_params,
            )

            for lot_idx, (lot_length, lot_drums) in enumerate(lot_items, start=1):
                extra_total: float = extra * core_count
                effective_length: float = lot_length + extra_total

                duration_min: float | None = (
                    effective_length / line_speed if line_speed else None
                )

                remarks: str | None = f"틀{lot_idx}" if len(lot_items) > 1 else None

                # wip_output_expected_m 은 헤더 전용 (batch_seq == -1 AND "연선"). 여기선 default 0.
                # Listener (Task 6) 가 batch_seq 로 gate 하므로 이 배치는 WIP auto-create 안 됨.
                batch = ProductionBatch(
                    run_label=run_label,
                    sales_order_id=order.order_id,
                    sales_order_line=order.order_line,
                    item_code=item.item_code if item else None,
                    routing_code=routing_code,
                    process_name=process_name,
                    batch_seq=batch_seq,
                    drum_length_m=drum_length,
                    drum_count=lot_drums,
                    total_length_m=float(order.ordered_qty_m or 0),
                    extra_length_m=extra_total,
                    sq_mm2=sq,
                    core_count=core_count,
                    core_colors=order.core_colors,
                    sheath_color=order.sheath_color,
                    customer_name=order.customer_name,
                    due_date=order.due_date,
                    customer_priority=priority,
                    line_speed_mpm=line_speed,
                    setup_time_min=setup_time,
                    estimated_duration_min=duration_min,
                    status="planned",
                    product_group=order.product_group,
                    voltage=order.voltage,
                    conductor_material=conductor_material,
                    stranding_type=stranding_type,
                    remarks=remarks,
                    equipment_code=None,
                    wip_matched_id=wip_id,
                    spec_raw=order.spec_raw,
                )
                batches.append(batch)

    return batches
