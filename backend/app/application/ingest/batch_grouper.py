"""batch_grouping 패키지 — Stage 1 본체 (`create_batches`, `deduplicate_group_headers`).

설계 의도:
- 수주 1건은 라우팅에 정의된 공정 수만큼 ProductionBatch 행으로 펼쳐진다.
- 모든 마스터 데이터를 함수 진입 시점에 메모리로 로드해 N+1 쿼리를 원천 차단한다.
- 정렬은 DB flush 전에 Python 레벨에서 수행해 INSERT 순서가 곧 작업 순서가 된다.

적용 제약 조건:
  2-2  외주 자동분류: SQ<=10 또는 product_group에 '고내화' 포함 시 외주 처리
  2-3  틀단위 분할: total_length_m > lot_stranding 이면 배치를 복수로 분할
  3-4  잔량 흑색 소진: total_length_m < 200m 배치는 sheath_color='흑' 강제
  5-2  연선방식 구분: 정렬 키에 stranding_type 추가 (압축/원형/수밀 혼합 방지)
  5-3  다심 우선 완성: 납기 3일 이내 차이 시 core_count>1 우선
  7-1  불량 재작업 버퍼: total_length_m에 defect_buffer_pct(기본 5%) 가산
  10-4 전압별 드럼 분류: 정렬 키에 voltage 추가
  10-5 4심 계산법: core_count==4 시 product_type 키를 '4C'로 사용
"""

import math
from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.domain.batch_sheath_keys import (
    _SHEATH_COLOR_RANK,
    _TFR8_PATTERN,
    _WIP_COVERED_PROCESSES,
    _compose_sheath_group_key,
)
from app.application.ingest.batch_helpers import (
    _find_item,
    _find_speed,
    _get_processes,
    _infer_material,
    _infer_routing,
    _process_order,
    extract_sq,
)
from app.application.ingest._batch_grouper_loader import (
    load_grouper_inputs,
)
from app.application._shared.constraint_params import ConstraintParams
from app.domain.constraint_rules import resolve_spec_setup_min


def _is_outsource_rule(
    sq: float,
    product_group: str | None,
    customer_name: str | None,
) -> bool:
    """외주 자동분류 룰 (constraint 2-2). 호출측에서 sq/제품군/고객명 추출 후 위임.

    조건 (Phase 2 동일):
      (1) SQ ≤ 10  : 소단면적 특수 공정은 사내 설비로 생산 불가
      (2) TFR-8(... + SQ == 16  : 고온 사양 특수 외주 전용
      (3) 아이마켓코리아 + TFR-GV 품목  : 고객 지정 외주
    """
    pg_orig = product_group or ""
    pg_upper = pg_orig.upper()
    customer = customer_name or ""
    return (
        sq <= 10
        or ("TFR-8(" in pg_orig and sq == 16)
        or (customer == "아이마켓코리아" and "TFR-GV" in pg_upper)
    )


def is_outsource_batch(batch: ProductionBatch) -> bool:
    """ProductionBatch 가 외주 룰 (2-2) 에 해당하는지 판정.

    decision_card phrasing._resolve_key 가 "outsource" 키로 dispatch 할 때 사용.
    동일 룰을 SalesOrder 단계 (`create_batches`/`build_strand_batches`) 와
    공유하기 위해 `_is_outsource_rule` 에 위임.

    Note: is_enabled 토글은 이미 분류된 batch 를 재해석하는 게 아니므로 무시.
    분류 시점의 토글이 SalesOrder 단계 wrapper (`_is_outsource_rule_with_toggle`)
    에서 적용된다.
    """
    return _is_outsource_rule(
        sq=float(batch.sq_mm2 or 0),
        product_group=batch.product_group,
        customer_name=batch.customer_name,
    )


# ─── Track A (2026-04-28): 하드코딩 룰 → DB-toggle wrapper helper ─────────
# 2-2 외주 / 2-4 61연선 / 5-5 TFR-GV. ConstraintParams.is_rule_enabled 게이트.
# 룰 본문은 변경 X — wrapper 가 게이트 통과 시에만 본문 위임 (DRY).


def _is_outsource_rule_with_toggle(
    sq: float,
    product_group: str | None,
    customer_name: str | None,
    *,
    params: ConstraintParams,
) -> bool:
    """`_is_outsource_rule` + 2-2 is_enabled 게이트.

    is_enabled=False → 항상 False (외주 분류 미적용 → 사내 routing 폴백).
    행 미존재 (legacy DB) → True 폴백 → 기존 동작 유지.
    """
    if not params.is_rule_enabled("2-2", default=True):
        return False
    return _is_outsource_rule(sq, product_group, customer_name)


def _should_skip_stranding(
    product_group: str | None,
    sq: float,
    *,
    params: ConstraintParams,
) -> bool:
    """5-5 TFR-GV 절연 생략 룰 + is_enabled 게이트.

    Default rule: TFR-GV 제품군 + sq <= 25 → stranding 공정 생략 (단선 접지선).
    is_enabled=False → 항상 False (모든 TFR-GV sq<=25 가 normal stranding 수행).
    """
    if not params.is_rule_enabled("5-5", default=True):
        return False
    return "TFR-GV" in (product_group or "").upper() and sq <= 25


def _is_61strand_rule(
    sq: float,
    conductor_material: str | None,
    *,
    params: ConstraintParams,
) -> bool:
    """2-4 61연선 분리 룰 + is_enabled 게이트.

    Default rule: sq >= 300 + conductor_material == "CU" → 61연선 2단계 (T6B0 → 54BO).
    is_enabled=False → 항상 False (sq>=300 CU 도 normal 단일 stranding).
    """
    if not params.is_rule_enabled("2-4", default=True):
        return False
    return sq >= 300 and conductor_material == "CU"


def create_batches(
    run_label: str,
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    frozen_order_keys: set[tuple] | None = None,
) -> dict:
    """
    run_label에 해당하는 sales_order를 읽어서 production_batch를 생성한다.

    ERP 외주계획 플래그(is_outsourced)와 무관하게 모든 수주를 대상으로 배치를 생성한다.
    (외주 플래그는 특정 공정 외주를 의미하며, 연선/절연/시스 계획 대상에서 제외하지 않는다.)
    코드 내 하드코딩된 설비 제약 조건(SQ≤10 등)에 의한 외주 분류만 적용한다.
    라우팅이 없거나 SQ 파싱에 실패하면 warnings에 기록 후 계속 진행한다.

    Args:
        frozen_order_keys: 증분 업데이트 시 동결된 수주 키 set. (order_id, order_line)
            이 키에 해당하는 수주는 이미 배치가 존재하므로 배치 생성에서 제외한다.

    Returns:
        {
            "total_batches": int,
            "by_process": {process_name: count, ...},
            "warnings": [str, ...]
        }
    """
    result: dict = {
        "total_batches": 0,
        "by_process": {},
        "warnings": [],
        "outsource_count": 0,
    }

    # ── 입력 로드 (Task 2.1 추출) ──────────────────────────────────────────
    # ConstraintConfig 프리페치 + SalesOrder + WIP 매칭 + 마스터 일괄 로드 +
    # 파라미터 fetch + speed_lookup 빌드 → _GrouperInputs.
    ctx = load_grouper_inputs(
        run_label,
        db,
        date_from=date_from,
        date_to=date_to,
        frozen_order_keys=frozen_order_keys,
    )
    if ctx.excluded_count > 0:
        result["warnings"].append(
            f"동결 수주 {ctx.excluded_count}건 제외 (이미 배치 존재)"
        )

    # 본문 변수 매핑 — 기존 로직 (line 290~) 의 변수 참조를 ctx 에서 풀어준다.
    constraint_params = ctx.constraint_params
    orders = ctx.orders
    wip_by_order_line = ctx.wip_by_order_line
    drum_lots = ctx.drum_lots
    routings = ctx.routings
    items = ctx.items
    customers = ctx.customers
    base_extra = ctx.base_extra
    sample_extra = ctx.sample_extra
    defect_buffer_pct = ctx.defect_buffer_pct
    remnant_threshold_m = ctx.remnant_threshold_m
    speed_lookup = ctx.speed_lookup

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
        if _is_outsource_rule_with_toggle(
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
        if _should_skip_stranding(order.product_group, sq, params=constraint_params):
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

    # ── Phase 2: 수주별 배치 생성 (절연·시스 등 연선 외 공정) ──────────────────

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
        if _is_outsource_rule_with_toggle(
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
        skip_stranding = _should_skip_stranding(
            order.product_group, sq, params=constraint_params
        )

        # ── 61연선(300SQ+) 2단계 분할 (Task #7 조사) ─────────────────────
        # 61연선 = 7연선 코어(batch_seq=0, 35SQ, T6B0 설비) + 본 배치(batch_seq=1, 원래 SQ, 54BO 설비)
        # batch_seq=0이 schedule_optimizer에서 먼저 정렬되어 간트에서 코어가 선행 배치된다.
        # 7연선 코어를 T6B0에서 먼저 제작 후 54BO에서 외층 추가
        # 2-4 is_enabled 게이트는 _is_61strand_rule 안에서 처리.
        is_61strand = _is_61strand_rule(
            sq, conductor_material, params=constraint_params
        )

        # 61연선(300SQ+) 여부 감지 — Phase 1에서 그룹 단위 코어 배치 생성 시 참조됨
        # (Phase 2에서는 연선 배치를 생성하지 않으므로 직접 사용되지 않음)
        is_61strand = _is_61strand_rule(  # noqa: F841
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

    # ── 잔량 흑색 소진 후처리 (3-4) ─────────────────────────────────────────
    # 배치 생성이 모두 끝난 후에 길이 기준으로 일괄 처리한다.
    # 잔량(짧은 배치)의 외피 색상을 흑색으로 맞춰 흑색 원재료 재고를 우선 소진한다.
    for b in batches:
        batch_len = float(b.total_length_m) if b.total_length_m is not None else 0.0
        if 0 < batch_len < remnant_threshold_m:
            b.sheath_color = "흑"
            existing_remarks = b.remarks or ""
            b.remarks = (
                f"{existing_remarks} 잔량흑색소진".strip()
                if existing_remarks
                else "잔량흑색소진"
            )

    # ── 정렬: 공정 → SQ 내림차순 → 전압 → 연선방식 → 다심 우선 → 색상 그루핑 ──
    # SQ 내림차순을 최우선으로 — 원본 계획서와 동일한 400SQ→300SQ→240SQ 정렬
    # 5-2: stranding_type으로 연선방식 격리, 10-4: voltage로 전압별 드럼 분류
    # 5-3: due_date 3일 이내 차이 시 다심(core_count>1) 우선 처리
    today: date = date.today()

    def _sort_key(b: ProductionBatch) -> tuple:
        due = b.due_date or date.max
        days_until_due = (due - today).days if due != date.max else 9999
        due_bucket = math.floor(days_until_due / 3)
        multi_core_penalty = 0 if (b.core_count or 1) > 1 else 1
        return (
            _process_order(b.process_name),  # 공정 순서
            b.batch_seq or 0,  # batch_seq: 61연선 코어(0)가 메인(1)보다 먼저
            -(b.sq_mm2 or 0),  # SQ 내림차순 (최우선)
            b.voltage or "",  # 전압별 드럼 분류 (10-4)
            b.stranding_type or "",  # 연선방식 구분 (5-2)
            due_bucket,  # 납기 버킷 (빠른 납기 우선)
            multi_core_penalty,  # 다심 우선 완성 (5-3)
            b.sheath_color or "",  # 색상 전환 최소화
            b.core_colors or "",
        )

    batches.sort(key=_sort_key)

    # ── 시스(저압/고압) 전용 2차 정렬: 납기 주차 → 색상 ──────────────────────
    # 납기 최우선, 같은 주차 내에서만 색상 묶기 (체인지오버 최소화).
    # Python sort 가 stable 이므로 비시스 배치의 상대 순서는 _sort_key 결과 유지.
    def _sheath_chain_key(b: ProductionBatch) -> tuple:
        if b.process_name not in ("저압시스", "고압시스"):
            # 비시스는 고정 키 → 원순서 유지 (stable sort)
            return (0, 0, 0, 0)
        color = (b.sheath_color or "").strip() or "기타"
        color_rank = _SHEATH_COLOR_RANK.get(color, 99)
        if b.due_date:
            yr, wk, _ = b.due_date.isocalendar()
            due_wk_int = yr * 100 + wk
            due_ord = b.due_date.toordinal()
        else:
            due_wk_int = 999999
            due_ord = 9999999
        return (1, due_wk_int, color_rank, due_ord)

    batches.sort(key=_sheath_chain_key)

    # ── 배치 그룹 부여 ─────────────────────────────────────────────────────────
    # 같은 (process_name, sq_mm2)를 하나의 batch_group으로 묶는다.
    # 원본 계획서의 "120SQ--->1틀(연선5285)" 묶음 = 1 batch_group = 1 간트 블록.
    # 틀분할(lot_stranding)이 있으면 lot_idx별로 별도 그룹 → 틀당 1블록.
    group_counters: dict[str, int] = {}
    for b in batches:
        proc = b.process_name
        sq_key = int(b.sq_mm2 or 0)
        group_key = f"{proc}_{sq_key}SQ"

        # 저압절연: 고내화 제품군(TFR-8(…))은 일반 제품과 혼합 생산 불가 → 별도 그룹
        # 공백/대소문자 변형("tfr-8 (", "TFR-8 (830℃/120min)" 등) 전부 커버
        if (
            proc == "저압절연"
            and b.product_group
            and _TFR8_PATTERN.search(b.product_group)
        ):
            group_key = f"{proc}_{sq_key}SQ_고내화"

        # 시스: 색상 + 반주차(H1/H2) + SQ 기준으로 묶음 (`_compose_sheath_group_key`)
        # - 같은 색상은 연속 생산해 색상 교체 최소화
        # - 같은 주 내 월·목 납기 차이도 H1/H2 분리 → EDD 우선 보장
        # - 규격(SQ)이 다르면 별도 런으로 분할 → 회사 수기 양식 일치
        # 설비 라우팅: A120(흑/청/흑적) vs A100(갈/회/녹/황 등).
        # 색상 체인 연속성은 `_sheath_chain_key` 가 색상+납기만 보므로 SQ 분할 후에도
        # stable sort 로 보존됨 (같은 색상 그룹이 인접 배치).
        if proc in ("저압시스", "고압시스"):
            group_key = _compose_sheath_group_key(
                proc=proc,
                color=b.sheath_color,
                due_date=b.due_date,
                sq=b.sq_mm2,
            )

        # CORE-/ST- 등 Phase 1에서 이미 할당된 batch_group은 보존
        if b.batch_group:
            if b.batch_group not in group_counters:
                group_counters[b.batch_group] = len(group_counters) + 1
            continue

        if group_key not in group_counters:
            group_counters[group_key] = len(group_counters) + 1
        b.batch_group = group_key

    # ── DB 기록 및 집계 ───────────────────────────────────────────────────────
    db.add_all(batches)
    db.flush()  # batch_id 자동 채번 (autoincrement)을 트리거하되 커밋은 호출자에게 위임

    # ── 중복 배치 정리 — "한 batch_group = 한 헤더" 불변식 방어 ────────────
    # Why: create_batches / stage1 incremental update / auto-split 등 복수 경로
    # 에서 같은 수주가 재처리되어 (batch_group, sales_order_id,
    # sales_order_line, batch_seq) 동일 row 가 중복 insert 되는 현상을 관측.
    # 이로 인해 CP-SAT 가 같은 batch_group 에 헤더(batch_seq=-1) 를 2개 이상
    # 갖는 경우 한 헤더만 스케줄링하고 나머지는 loss. UI 에서는 실제 처리량이
    # 헤더 라벨과 불일치 ("1틀 9500m" 라벨이지만 실제 3틀 28500m). 근본 경로
    # 수정 전이라도 post-hoc 정리로 불변식을 복원한다.
    dedupe_stats = deduplicate_group_headers(run_label, db)
    result["dedupe"] = dedupe_stats

    for b in batches:
        proc = b.process_name
        result["by_process"][proc] = result["by_process"].get(proc, 0) + 1
    result["total_batches"] = len(batches)

    return result


def deduplicate_group_headers(run_label: str, db: Session) -> dict:
    """배치 중복 정리 — "한 batch_group = 한 헤더, 수주-라인별 1 row" 불변식 복원.

    Why: create_batches / incremental Stage1 update / auto-split 복합 경로에서
    같은 (batch_group, sales_order_id, sales_order_line, batch_seq) 튜플이
    다중 insert 되는 현상 확인 (KBI PoC run 기준 전 공정 362건 중복). 대표
    증상:
      - batch_seq=-1 헤더 2개 공존 → CP-SAT 가 한 헤더만 스케줄링 → 다른
        수주 통째로 누락 (예: 300SQ 9500m 분량이 계획에 안 나타남)
      - UI 라벨 ("1틀 9500m") 이 bar 가 실제 처리량 (3틀 28500m) 와 불일치
        — label 은 첫 헤더에서, 처리시간은 두번째 헤더에서 오는 혼선

    본 함수는 근본 경로 수정 전 post-hoc 방어선으로 invariant 를 보장한다.
    근본 수정 (create_batches idempotent 화 / split 잔재 정리) 은 후속 과제.

    Dedupe 절차:
      1. batch_seq != -1 (CORE=0, 공정 sub-batch >= 1) 먼저 dedup — 같은
         (batch_group, so, line, seq, process_name) 튜플은 최소 batch_id
         하나만 유지, 나머지 delete.
      2. batch_seq = -1 (연선 aggregate header) 의 중복 처리 — 같은 batch_group
         에 헤더 2개 이상이면 canonical (가장 이른 납기 → 우선순위 → batch_id)
         하나만 남김. 남은 sub-batch (1번에서 정리된 상태) 기준으로
         total_length_m / drum_count / est_duration / due / priority 재집계.

    Returns: {
        "non_header_deleted": int,   # seq != -1 중복 삭제 row 수
        "headers_deleted": int,      # seq = -1 중복 삭제 row 수
        "groups_rebalanced": int,    # 재집계된 batch_group 수 (헤더 재집계 트리거된 그룹)
    }
    """
    from collections import defaultdict

    stats: dict = {
        "non_header_deleted": 0,
        "headers_deleted": 0,
        "groups_rebalanced": 0,
    }

    # ── 1. Non-header 중복 제거 (batch_seq != -1) ──────────────────────────
    # 키: (batch_group, sales_order_id, sales_order_line, batch_seq, process_name).
    # process_name 까지 포함 이유: batch_group 문자열에 공정명이 내재돼 있지만
    # CORE 배치처럼 cross-process 가능성 방어. 동일 키 2회 이상 → 최소 batch_id
    # 만 canonical, 나머지 delete.
    non_headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq != -1,
        )
        .order_by(ProductionBatch.batch_id)
        .all()
    )
    seen: set[tuple] = set()
    to_delete_nh: list = []
    for b in non_headers:
        if b.batch_group is None:
            continue
        key = (
            b.batch_group,
            b.sales_order_id,
            b.sales_order_line,
            b.batch_seq,
            b.process_name,
        )
        if key in seen:
            to_delete_nh.append(b)
        else:
            seen.add(key)
    for b in to_delete_nh:
        db.delete(b)
    stats["non_header_deleted"] = len(to_delete_nh)
    if to_delete_nh:
        db.flush()

    # ── 2. Header (batch_seq=-1) 중복 제거 + 재집계 ────────────────────────
    headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq == -1,
        )
        .all()
    )
    by_group: dict[str, list] = defaultdict(list)
    for h in headers:
        if h.batch_group:
            by_group[h.batch_group].append(h)

    for bg, hs in by_group.items():
        # Canonical: 가장 이른 납기 → 가장 높은 우선순위(작은 숫자) → 작은 batch_id
        hs_sorted = sorted(
            hs,
            key=lambda h: (
                h.due_date or date.max,
                h.customer_priority or 99,
                h.batch_id or 0,
            ),
        )
        canon = hs_sorted[0]
        others = hs_sorted[1:]

        # 재집계 source: 1번에서 dedup 완료된 sub-batch.
        group_subs = (
            db.query(ProductionBatch)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.batch_group == bg,
                ProductionBatch.batch_seq >= 1,
            )
            .all()
        )
        if not group_subs:
            # 헤더만 있고 sub 없는 특수 케이스 — 재집계 skip, 중복 헤더만 삭제.
            for h in others:
                db.delete(h)
            stats["headers_deleted"] += len(others)
            if others:
                stats["groups_rebalanced"] += 1
            continue

        core_mul = int(canon.core_count or 1)
        raw_total = sum(float(s.total_length_m or 0) for s in group_subs)
        net_qty = raw_total * core_mul
        lot_size = float(canon.drum_length_m or 0)

        if lot_size > 0 and net_qty > 0:
            drum_count = max(math.ceil(net_qty / lot_size), 1)
            total_len_m = drum_count * lot_size
        elif net_qty > 0:
            drum_count = 1
            total_len_m = net_qty
        else:
            # net=0 인 그룹은 헤더 자체가 무의미 — 현 헤더 값 유지.
            drum_count = int(canon.drum_count or 0)
            total_len_m = float(canon.total_length_m or 0)

        earliest_due = min(
            (s.due_date for s in group_subs if s.due_date),
            default=canon.due_date,
        )
        best_priority = min(
            (s.customer_priority or 99 for s in group_subs),
            default=99,
        )
        line_speed = float(canon.line_speed_mpm) if canon.line_speed_mpm else 0.0
        est_dur = (
            total_len_m / line_speed if line_speed > 0 else canon.estimated_duration_min
        )
        surplus = max(0.0, total_len_m - raw_total * core_mul)

        # 재집계로 실제 변경이 발생했는지 여부 판단 (통계용).
        rebalanced = (
            len(others) > 0
            or abs(float(canon.total_length_m or 0) - total_len_m) > 0.5
            or int(canon.drum_count or 0) != drum_count
        )

        canon.total_length_m = total_len_m
        canon.drum_count = drum_count
        canon.due_date = earliest_due
        canon.customer_priority = best_priority
        canon.estimated_duration_min = est_dur
        canon.wip_output_expected_m = surplus
        if len(hs) > 1:
            canon.remarks = (
                (canon.remarks or "") + f" [dedup:헤더{len(hs)}→1 병합]"
            ).strip()

        for h in others:
            db.delete(h)

        stats["headers_deleted"] += len(others)
        if rebalanced:
            stats["groups_rebalanced"] += 1

    if stats["headers_deleted"] or stats["groups_rebalanced"]:
        db.flush()

    return stats
