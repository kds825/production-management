"""공정별 작업 분할, 틀단위 배치 생성, 묶음 정렬 — Stage 1 핵심 로직

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
import re
from datetime import date
from sqlalchemy.orm import Session

from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.item_master import ItemMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.process_routing import ProcessRouting
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.customer_master import CustomerMaster
from app.infrastructure.models.wip_inventory import WipInventory

# WIP 재고 종류별로 해당 재고가 "이미 완료된" 공정 집합
# 절연재고: 연선+절연까지 완료 → 절연 이전 공정 배치 생성 불필요
# 연선재고: 연선까지 완료 → 연선 이전 공정만 제외, 절연부터는 작업 필요
# 연합재고: 연선+절연+연합까지 완료
# 완제품:  모든 공정 완료
_WIP_COVERED_PROCESSES: dict[str, set[str]] = {
    "연선재고": {"신선", "연선"},
    "절연재고": {"신선", "연선", "저압절연", "고압절연"},
    "연합재고": {"신선", "연선", "저압절연", "고압절연", "연합", "T/P"},
    "완제품":   {"신선", "연선", "저압절연", "고압절연", "연합", "T/P", "저압시스", "고압시스"},
}


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

    # ── 마스터 데이터 일괄 로드 (N+1 방지) ──────────────────────────────────
    query = db.query(SalesOrder).filter(
        SalesOrder.run_label == run_label,
    )
    if date_from is not None:
        query = query.filter(SalesOrder.due_date >= date_from)
    if date_to is not None:
        query = query.filter(SalesOrder.due_date <= date_to)
    orders = query.all()

    # ── Frozen orders 제외 (증분 업데이트 시) ───────────────────────────────
    # frozen_order_keys에 해당하는 수주는 이미 배치가 존재하므로 재생성하지 않는다.
    if frozen_order_keys:
        before_count = len(orders)
        orders = [
            o for o in orders if (o.order_id, o.order_line) not in frozen_order_keys
        ]
        excluded = before_count - len(orders)
        if excluded > 0:
            result["warnings"].append(f"동결 수주 {excluded}건 제외 (이미 배치 존재)")

    # ── WIP 매칭 룩업: "order_id:order_line" → WipInventory ───────────────
    # order.wip_id(FK)를 기준으로 구성 — 1개 WIP가 여러 수주에 매칭될 수 있으므로
    # wip.matched_order_id(단일값) 대신 order 쪽 wip_id를 역참조한다.
    wip_by_order_line: dict[str, WipInventory] = {}
    if any(o.use_wip for o in orders):
        # 이번 run에서 WIP를 사용하는 수주들이 참조하는 wip_id 목록
        wip_ids_used = {o.wip_id for o in orders if o.use_wip and o.wip_id is not None}
        if wip_ids_used:
            wip_objects = (
                db.query(WipInventory)
                .filter(WipInventory.wip_id.in_(wip_ids_used))
                .all()
            )
            wip_by_id: dict[int, WipInventory] = {w.wip_id: w for w in wip_objects}
            for o in orders:
                if o.use_wip and o.wip_id is not None:
                    wip = wip_by_id.get(o.wip_id)
                    if wip:
                        wip_by_order_line[f"{o.order_id}:{o.order_line}"] = wip

    # float 변환 후 키로 사용해야 dict lookup이 안전하게 동작한다
    drum_lots: dict[float, DrumLotMaster] = {
        float(d.cross_section): d
        for d in db.query(DrumLotMaster).all()
        if d.cross_section is not None
    }
    routings: dict[str, ProcessRouting] = {
        r.routing_code: r for r in db.query(ProcessRouting).all()
    }
    items: dict[str, ItemMaster] = {i.item_code: i for i in db.query(ItemMaster).all()}
    speeds: list[SpeedMaster] = db.query(SpeedMaster).all()
    customers: dict[str, CustomerMaster] = {
        c.customer_name: c for c in db.query(CustomerMaster).all()
    }

    # ── 여척(extra length) 파라미터 로드 ────────────────────────────────────
    # constraint_id "3-1" = 색상 여척 + 샘플 여척 설정
    extra_cfg = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "3-1")
        .first()
    )
    extra_params: dict = (
        extra_cfg.params_json if extra_cfg and extra_cfg.params_json else {}
    )
    # base_extra: 색상 전환 손실 보정 기본값 7M
    base_extra: float = float(extra_params.get("extra_length_m", 7))
    # sample_extra: 아이마켓코리아 등 샘플 요청 고객 추가 여척 10M
    sample_extra: float = float(extra_params.get("sample_extra_m", 10))

    # ── 불량 재작업 버퍼 파라미터 로드 (7-1) ────────────────────────────────
    defect_cfg = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "7-1")
        .first()
    )
    defect_params: dict = (
        defect_cfg.params_json if defect_cfg and defect_cfg.params_json else {}
    )
    # defect_buffer_pct: 불량 재작업 대비 생산 길이 가산 비율
    # to-be 기준: 버퍼 미적용 (0%) — 틀수가 to-be와 일치하도록
    defect_buffer_pct: float = float(defect_params.get("defect_buffer_pct", 0.0))

    # ── 잔량 흑색 소진 임계값 파라미터 로드 (3-4) ───────────────────────────
    remnant_cfg = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "3-4")
        .first()
    )
    remnant_enabled: bool = bool(remnant_cfg.is_enabled) if remnant_cfg else False
    remnant_params: dict = (
        remnant_cfg.params_json if remnant_cfg and remnant_cfg.params_json else {}
    )
    remnant_threshold_m: float = (
        float(remnant_params.get("remnant_threshold_m", 200)) if remnant_enabled else 0
    )

    # ── 선속 룩업 테이블 빌드 ────────────────────────────────────────────────
    # 키: (equipment_code, product_type, cross_section_float) → SpeedMaster
    speed_lookup: dict[tuple, SpeedMaster] = {}
    for s in speeds:
        cs_key = float(s.cross_section) if s.cross_section is not None else None
        key = (s.equipment_code, s.product_type, cs_key)
        speed_lookup[key] = s

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

        # 외주 분류 조건 — Phase 2와 동일
        pg_upper = (order.product_group or "").upper()
        customer_name_o = order.customer_name or ""
        _is_out = (
            sq <= 10
            or ("TFR-8(" in (order.product_group or "") and sq == 16)
            or (customer_name_o == "아이마켓코리아" and "TFR-GV" in pg_upper)
        )
        if _is_out:
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

        # TFR-GV 소단면 연선 스킵
        if "TFR-GV" in (order.product_group or "").upper() and sq <= 25:
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
        is_61strand_g = sq >= 300

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
        setup_time_g = (
            float(rep_speed_g.setup_spec_min)
            if rep_speed_g and rep_speed_g.setup_spec_min
            else 0.0
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
            )
            batches.append(header_batch)

        # ── 수주별 연선 배치 생성 (display용) ────────────────────────────────
        # scheduling-review·Excel 수주 단위 행 표시용.
        # estimated_duration_min=None — 스케줄러는 헤더 배치(seq=-1) duration 사용.
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
            setup_time_o = (
                float(speed_o.setup_spec_min)
                if speed_o and speed_o.setup_spec_min
                else 0.0
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
            if is_61strand_g and conductor_material_o == "CU" and not skip_strand_work:
                core_speed_o = _find_speed(speed_lookup, "연선", order, 35.0)
                core_spd = (
                    float(core_speed_o.line_speed_mpm)
                    if core_speed_o and core_speed_o.line_speed_mpm
                    else 25.0
                )
                core_setup = (
                    float(core_speed_o.setup_spec_min)
                    if core_speed_o and core_speed_o.setup_spec_min
                    else 210.0
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
                    drum_length_m=float(order.drum_length_m or 0) or (
                        float(order.ordered_qty_m or 0) / int(order.drum_count or 1)
                    ),
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
            if is_61strand_g and conductor_material_o == "AL" and not skip_strand_work:
                # AL6BO speed_master가 없으면 T6B0 기준 fallback
                al_core_speed_o = _find_speed(speed_lookup, "연선", order, 35.0)
                al_core_spd = (
                    float(al_core_speed_o.line_speed_mpm)
                    if al_core_speed_o and al_core_speed_o.line_speed_mpm
                    else 25.0
                )
                al_core_setup = (
                    float(al_core_speed_o.setup_spec_min)
                    if al_core_speed_o and al_core_speed_o.setup_spec_min
                    else 210.0
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
                    drum_length_m=float(order.drum_length_m or 0) or (
                        float(order.ordered_qty_m or 0) / int(order.drum_count or 1)
                    ),
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

        # ── 외주 자동분류 (2-2) ─────────────────────────────────────────────
        # ERP 플래그와 무관하게 아래 조건 중 하나라도 해당하면 외주로 처리한다.
        # (1) SQ <= 10: 소단면적 특수 공정은 사내 설비로 생산 불가
        # (2) 고내화(TFR-8( 제품군) 16SQ: 고온 사양 특수 외주 전용
        # (3) 아이마켓코리아 고객의 TFR-GV 품목: 고객 지정 외주
        pg_upper = (order.product_group or "").upper()
        customer_name = order.customer_name or ""
        _is_outsourced = (
            sq <= 10
            or ("TFR-8(" in (order.product_group or "") and sq == 16)
            or (customer_name == "아이마켓코리아" and "TFR-GV" in pg_upper)
        )
        if _is_outsourced:
            result["outsource_count"] += 1
            result["warnings"].append(
                f"수주 {order_ref}: 외주 자동분류 (SQ={sq}, 제품군={order.product_group}, 고객={customer_name})"
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
        skip_stranding = "TFR-GV" in (order.product_group or "").upper() and sq <= 25

        # ── 61연선(300SQ+) 2단계 분할 (Task #7 조사) ─────────────────────
        # 61연선 = 7연선 코어(batch_seq=0, 35SQ, T6B0 설비) + 본 배치(batch_seq=1, 원래 SQ, 54BO 설비)
        # batch_seq=0이 schedule_optimizer에서 먼저 정렬되어 간트에서 코어가 선행 배치된다.
        # 7연선 코어를 T6B0에서 먼저 제작 후 54BO에서 외층 추가
        is_61strand = sq >= 300 and conductor_material == "CU"

        # 61연선(300SQ+) 여부 감지 — Phase 1에서 그룹 단위 코어 배치 생성 시 참조됨
        # (Phase 2에서는 연선 배치를 생성하지 않으므로 직접 사용되지 않음)
        is_61strand = sq >= 300 and conductor_material == "CU"  # noqa: F841

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
            if wip_stage and process_name in _WIP_COVERED_PROCESSES.get(wip_stage, set()):
                continue
            speed_info = _find_speed(speed_lookup, process_name, order, sq)
            line_speed = (
                float(speed_info.line_speed_mpm)
                if speed_info and speed_info.line_speed_mpm is not None
                else None
            )
            setup_time: float = (
                float(speed_info.setup_spec_min)
                if speed_info and speed_info.setup_spec_min is not None
                else 0.0
            )

            for lot_idx, (lot_length, lot_drums) in enumerate(lot_items, start=1):
                extra_total: float = extra * core_count
                effective_length: float = lot_length + extra_total

                duration_min: float | None = (
                    effective_length / line_speed if line_speed else None
                )

                remarks: str | None = f"틀{lot_idx}" if len(lot_items) > 1 else None

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

    # ── 배치 그룹 부여 ─────────────────────────────────────────────────────────
    # 같은 (process_name, sq_mm2)를 하나의 batch_group으로 묶는다.
    # 원본 계획서의 "120SQ--->1틀(연선5285)" 묶음 = 1 batch_group = 1 간트 블록.
    # 틀분할(lot_stranding)이 있으면 lot_idx별로 별도 그룹 → 틀당 1블록.
    group_counters: dict[str, int] = {}
    for b in batches:
        proc = b.process_name
        sq_key = int(b.sq_mm2 or 0)
        group_key = f"{proc}_{sq_key}SQ"

        # 시스: 설비(색상) + SQ 기준으로 묶음
        # 저압시스를 SQ별로 분리하면 절연과 파이프라인 겹침(pipeline overlap)이 가능:
        # 240SQ 절연 1드럼 완료 → 240SQ 시스 시작, 120SQ 절연 완료 → 120SQ 시스 시작
        # 같은 설비(A100/A120) 위에서 SQ 그룹이 순차 큐잉된다.
        # 고압시스는 단일 SQ(633)이므로 SQ 분리 불필요 — 색상만 사용.
        if proc in ("저압시스", "고압시스"):
            color = (b.sheath_color or "").strip()
            color_key = color.replace("/", "_") if color else "기타"
            if proc == "저압시스":
                sq_suffix = f"_{int(sq_key)}SQ"
                if color in ("흑", "청", "흑/적"):
                    group_key = f"A120_{color_key}{sq_suffix}"
                else:
                    group_key = f"A100_{color_key}{sq_suffix}"
            else:
                group_key = f"{proc}_{color_key}"

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

    for b in batches:
        proc = b.process_name
        result["by_process"][proc] = result["by_process"].get(proc, 0) + 1
    result["total_batches"] = len(batches)

    return result


def detect_split_candidates(
    run_label: str,
    db: Session,
    *,
    gap_days: int = 3,
) -> list[dict]:
    """연선 배치 그룹 중 납기 간격이 큰 그룹을 분할 후보로 반환한다.

    알고리즘:
    1. run_label 기준으로 연선 배치를 조회한다.
       - batch_seq == -1: 그룹 헤더 (drum_count, total_length_m, equipment_code 포함)
       - batch_seq >= 1:  수주별 표시 배치 (due_date 포함)
    2. 헤더의 drum_count > 1인 그룹에 대해서만 검사한다.
    3. 수주별 배치를 due_date 오름차순으로 정렬한 뒤, DrumLotMaster.lot_stranding을
       기준으로 수주들을 드럼에 탐욕적(greedy)으로 할당한다.
    4. 드럼 N의 마지막 수주와 드럼 N+1의 첫 수주의 납기 간격이 gap_days 이상이면
       해당 그룹을 분할 후보로 표시한다.
    5. 같은 설비의 모든 연선 그룹의 estimated_duration_min 합계(설비 부하)도 함께 반환한다.

    Returns:
        [
            {
                "batch_group": str,
                "equipment_code": str | None,
                "sq_mm2": float,
                "lot_count": int,
                "total_length_m": float,
                "proposed_splits": [
                    {
                        "lot_index": int,        # 1-based 드럼 번호
                        "order_count": int,
                        "total_m": float,
                        "min_due": str,
                        "max_due": str,
                    },
                    ...
                ],
                "gaps_days": [int, ...],         # splits[i]와 splits[i+1] 사이 간격
                "equipment_load_hours": float | None,
            },
            ...
        ]
    """
    from app.infrastructure.models.production_batch import ProductionBatch

    # ── 연선 헤더 배치 로드 ────────────────────────────────────────────────────
    headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.process_name == "연선",
            ProductionBatch.batch_seq == -1,
        )
        .all()
    )

    if not headers:
        return []

    # ── drum_lot_master 일괄 로드 ─────────────────────────────────────────────
    drum_lots: dict[float, DrumLotMaster] = {
        float(d.cross_section): d
        for d in db.query(DrumLotMaster).all()
        if d.cross_section is not None
    }

    # ── 수주별 연선 배치 로드 (batch_seq >= 1) ────────────────────────────────
    order_batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.process_name == "연선",
            ProductionBatch.batch_seq >= 1,
        )
        .all()
    )
    # batch_group → 수주 배치 목록
    group_order_map: dict[str, list[ProductionBatch]] = {}
    for b in order_batches:
        group_order_map.setdefault(b.batch_group or "", []).append(b)

    # ── 설비 부하 계산 ────────────────────────────────────────────────────────
    # 헤더 배치(batch_seq=-1) estimated_duration_min 합산 — 설비 코드별
    equip_load: dict[str, float] = {}
    for h in headers:
        eq = h.equipment_code
        if eq is not None and h.estimated_duration_min is not None:
            equip_load[eq] = equip_load.get(eq, 0.0) + float(h.estimated_duration_min)

    # ── 그룹별 분할 후보 검사 ─────────────────────────────────────────────────
    candidates: list[dict] = []

    # batch_group 이름 기준으로 중복 헤더 제거 (같은 그룹에 헤더가 2개 있을 수 있음)
    seen_groups: set[str] = set()
    unique_headers = []
    for h in headers:
        bg = h.batch_group or ""
        if bg not in seen_groups:
            seen_groups.add(bg)
            unique_headers.append(h)

    for header in unique_headers:
        lot_count = int(header.drum_count or 1)
        if lot_count <= 1:
            # 단일 드럼 그룹은 분할 불필요
            continue

        bg = header.batch_group or ""
        sq = float(header.sq_mm2 or 0)
        lot_info = drum_lots.get(sq)
        lot_stranding = (
            float(lot_info.lot_stranding)
            if lot_info and lot_info.lot_stranding
            else None
        )

        if lot_stranding is None or lot_stranding <= 0:
            # lot_stranding 정보 없으면 드럼 할당 불가 — 스킵
            continue

        order_rows = group_order_map.get(bg, [])
        if not order_rows:
            continue

        # due_date 오름차순 정렬 — None은 끝으로
        order_rows_sorted = sorted(
            order_rows,
            key=lambda b: (b.due_date or date.max, b.sales_order_id or ""),
        )

        # ── 탐욕적 드럼 할당 (용량 + 납기 경계 인식) ─────────────────────────
        # 행 단위로 드럼을 채운다. 두 가지 조건으로 다음 드럼 전환:
        #   1) 용량 초과: current_fill + order > lot_stranding
        #   2) 납기 경계: 다른 수주번호로 넘어갈 때 납기 gap >= threshold이고
        #      현재 드럼이 80% 이상 찼으면 새 드럼으로 (거의 찬 드럼에
        #      여유 납기 수주를 억지로 넣지 않는다)
        _FILL_RATIO_FOR_GAP_SPLIT = 0.8
        drums: list[list[ProductionBatch]] = []
        current_drum: list[ProductionBatch] = []
        current_fill: float = 0.0

        for idx, b in enumerate(order_rows_sorted):
            order_len = float(b.total_length_m or 0)

            # 조건 1: 용량 초과
            if current_drum and current_fill + order_len > lot_stranding:
                drums.append(current_drum)
                current_drum = [b]
                current_fill = order_len
                continue

            # 조건 2: 납기 경계 + 드럼 80%+ 충전
            if (
                current_drum
                and idx > 0
                and current_fill >= lot_stranding * _FILL_RATIO_FOR_GAP_SPLIT
            ):
                prev = order_rows_sorted[idx - 1]
                if (
                    (prev.sales_order_id or "") != (b.sales_order_id or "")
                    and prev.due_date
                    and b.due_date
                    and (b.due_date - prev.due_date).days >= gap_days
                ):
                    drums.append(current_drum)
                    current_drum = [b]
                    current_fill = order_len
                    continue

            current_drum.append(b)
            current_fill += order_len

        if current_drum:
            drums.append(current_drum)

        if len(drums) <= 1:
            # 실제로 드럼이 1개로 수렴하면 분할 불필요
            continue

        # ── 드럼 간 납기 간격 계산 ───────────────────────────────────────────
        gaps: list[int] = []
        for i in range(len(drums) - 1):
            last_due_in_drum = max(
                (b.due_date for b in drums[i] if b.due_date), default=None
            )
            first_due_next_drum = min(
                (b.due_date for b in drums[i + 1] if b.due_date), default=None
            )
            if last_due_in_drum and first_due_next_drum:
                gap = (first_due_next_drum - last_due_in_drum).days
            else:
                gap = 0
            gaps.append(gap)

        max_gap = max(gaps) if gaps else 0
        if max_gap < gap_days:
            continue

        # ── gap=0 연속 드럼을 하나의 청크로 병합 ─────────────────────────────
        # greedy 분할로 소량이 별도 드럼으로 넘어갈 수 있다 (예: 835m).
        # gap=0이면 같은 납기 그룹이므로 하나의 제안 청크로 합산한다.
        merged_chunks: list[list[ProductionBatch]] = [drums[0]]
        merged_gaps: list[int] = []
        for i, gap_val in enumerate(gaps):
            if gap_val == 0:
                # 이전 청크에 병합
                merged_chunks[-1].extend(drums[i + 1])
            else:
                merged_gaps.append(gap_val)
                merged_chunks.append(drums[i + 1])
        gaps = merged_gaps
        drums = merged_chunks

        if len(drums) <= 1:
            continue

        # ── 분할 제안 구성 ────────────────────────────────────────────────────
        today = date.today()
        proposed_splits = []
        drum_details = []
        has_urgent_in_later_drum = False

        for i, drum in enumerate(drums, start=1):
            dues = [b.due_date for b in drum if b.due_date]
            batch_ids = [b.batch_id for b in drum if b.batch_id]
            min_priority = min((b.customer_priority or 99 for b in drum), default=99)
            earliest_due = min(dues) if dues else None
            days_until = (earliest_due - today).days if earliest_due else 999
            is_urgent = min_priority <= 7 or (earliest_due is not None and days_until <= 7)
            if i >= 2 and is_urgent:
                has_urgent_in_later_drum = True

            proposed_splits.append(
                {
                    "lot_index": i,
                    "order_count": len(drum),
                    "total_m": round(
                        sum(float(b.total_length_m or 0) for b in drum), 1
                    ),
                    "min_due": str(min(dues)) if dues else None,
                    "max_due": str(max(dues)) if dues else None,
                    "order_ids": sorted(
                        set(b.sales_order_id for b in drum if b.sales_order_id)
                    ),
                    "batch_ids": batch_ids,
                    "has_urgent": is_urgent,
                    "min_priority": min_priority,
                    "days_until_due": days_until,
                }
            )
            drum_details.append(
                {
                    "lot_index": i,
                    "order_count": len(drum),
                    "min_priority": min_priority,
                    "earliest_due": str(earliest_due) if earliest_due else None,
                    "days_until_due": days_until,
                    "has_urgent": is_urgent,
                }
            )

        auto_split_recommended = has_urgent_in_later_drum
        urgency_reason = (
            f"후순위 드럼에 긴급/납기임박 수주 포함 (우선순위≤7 또는 납기7일 이내)"
            if auto_split_recommended
            else ""
        )

        eq_code = header.equipment_code
        load_hours = (
            round(equip_load[eq_code] / 60.0, 2)
            if eq_code and eq_code in equip_load
            else None
        )

        candidates.append(
            {
                "batch_group": bg,
                "equipment_code": eq_code,
                "sq_mm2": sq,
                "lot_count": lot_count,
                "total_length_m": float(header.total_length_m or 0),
                "proposed_splits": proposed_splits,
                "gaps_days": gaps,
                "equipment_load_hours": load_hours,
                "drum_details": drum_details,
                "auto_split_recommended": auto_split_recommended,
                "urgency_reason": urgency_reason,
            }
        )

    return candidates


# ── 자동 분할 ──────────────────────────────────────────────────────────────────


def _apply_auto_split(
    batch_group: str,
    split_batch_ids: list[int],
    suffix: str,
    db: Session,
) -> dict:
    """배치 그룹을 2개로 분할한다 (WIP 재매칭 없이 단순 이동).

    split_batch_ids의 배치들을 '{batch_group}_{suffix}' 신규 그룹으로 이동하고,
    헤더 배치(batch_seq=-1)를 비율로 분할한다.
    Stage 1 직후 스케줄링 전에 호출되므로 schedule_task 정리는 불필요.
    """
    import math as _math

    if not split_batch_ids:
        return {"skipped": True, "reason": "split_batch_ids 없음"}

    batch_id_set = set(split_batch_ids)
    new_group = f"{batch_group}_{suffix}"

    header = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq == -1,
        )
        .first()
    )
    if not header:
        return {"skipped": True, "reason": "헤더 없음"}

    all_individual = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq >= 1,
        )
        .all()
    )
    split_off = [b for b in all_individual if b.batch_id in batch_id_set]
    remaining = [b for b in all_individual if b.batch_id not in batch_id_set]

    if not split_off:
        return {"skipped": True, "reason": "이동 대상 배치 없음"}

    orig_dur = float(header.estimated_duration_min or 0)
    orig_len = float(header.total_length_m or 0)
    lot_size = float(header.drum_length_m or 0)
    core_mul = int(header.core_count or 1)

    def _calc_lots_len(batches: list) -> tuple[int, float]:
        net = sum(float(b.total_length_m or 0) for b in batches) * core_mul
        if lot_size > 0 and net > 0:
            lots = _math.ceil(net / lot_size)
            return lots, lots * lot_size
        elif net > 0:
            return 1, net
        return 0, 0.0

    split_lots, split_len = _calc_lots_len(split_off)
    remain_lots, remain_len = _calc_lots_len(remaining)

    split_len_raw = sum(float(b.total_length_m or 0) for b in split_off)
    remain_len_raw = sum(float(b.total_length_m or 0) for b in remaining)

    total_work = split_len + remain_len
    split_dur = orig_dur * split_len / total_work if total_work > 0 else 0
    remain_dur = orig_dur * remain_len / total_work if total_work > 0 else 0

    split_due = min((b.due_date for b in split_off if b.due_date), default=header.due_date)
    remain_due = min((b.due_date for b in remaining if b.due_date), default=header.due_date)
    split_pri = min((b.customer_priority or 99 for b in split_off), default=99)
    remain_pri = min((b.customer_priority or 99 for b in remaining), default=99)

    # 신규 그룹 헤더 생성
    new_header = ProductionBatch(
        run_label=header.run_label,
        sales_order_id=split_off[0].sales_order_id if split_off else header.sales_order_id,
        sales_order_line=split_off[0].sales_order_line if split_off else header.sales_order_line,
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
        customer_name=split_off[0].customer_name if split_off else header.customer_name,
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
            f" (자동분할 {suffix}, 틀단위 {lot_size:.0f}m)"
        ),
    )
    db.add(new_header)

    # 원본 헤더 업데이트
    header.drum_count = remain_lots
    header.total_length_m = remain_len
    header.estimated_duration_min = remain_dur
    header.due_date = remain_due
    header.customer_priority = remain_pri
    header.remarks = (
        f"연선그룹 {len(remaining)}건 {remain_lots}틀 / "
        f"수주총량 {remain_len_raw:.0f}m → 연선작업량 {remain_len:.0f}m"
        f" (자동분할 잔여, 틀단위 {lot_size:.0f}m)"
    )

    # 개별 배치들 그룹 이동
    db.query(ProductionBatch).filter(
        ProductionBatch.batch_id.in_(split_batch_ids),
        ProductionBatch.batch_group == batch_group,
    ).update({ProductionBatch.batch_group: new_group}, synchronize_session=False)

    return {
        "original_group": batch_group,
        "new_group": new_group,
        "moved_count": len(split_off),
    }


def execute_auto_splits(
    run_label: str,
    db: Session,
    *,
    gap_days: int = 3,
    urgency_priority_threshold: int = 7,
    urgency_days_threshold: int = 7,
) -> dict:
    """납기 긴급 수주가 후순위 드럼에 포함된 그룹을 자동으로 분할한다.

    detect_split_candidates 결과 중 auto_split_recommended=True인 항목에 대해
    proposed_splits[1:] 의 batch_ids를 '{batch_group}_B' 신규 그룹으로 분리한다.
    Stage 1의 create_batches + db.commit() 직후에 호출한다.

    Returns:
        {"auto_split_count": int, "splits": [{"original_group": ..., "new_group": ...}, ...]}
    """
    candidates = detect_split_candidates(
        run_label,
        db,
        gap_days=gap_days,
    )

    results = []
    for c in candidates:
        if not c.get("auto_split_recommended"):
            continue
        proposed = c.get("proposed_splits", [])
        if len(proposed) < 2:
            continue

        # proposed_splits[1:] 의 batch_ids 수집
        split_ids: list[int] = []
        for chunk in proposed[1:]:
            split_ids.extend(chunk.get("batch_ids") or [])

        if not split_ids:
            continue

        result = _apply_auto_split(
            batch_group=c["batch_group"],
            split_batch_ids=split_ids,
            suffix="B",
            db=db,
        )
        if not result.get("skipped"):
            results.append(result)

    if results:
        db.commit()

    return {
        "auto_split_count": len(results),
        "splits": results,
    }


# ── 헬퍼 함수 ─────────────────────────────────────────────────────────────────


def extract_sq(spec_raw: str | None) -> float | None:
    """
    규격 텍스트에서 SQ(mm²) 값을 추출한다.

    지원 패턴:
      - "4C x 35SQ"        → 35.0
      - "300SQ"            → 300.0
      - "1250KCMIL(633SQ)" → 633.0  (괄호 내 SQ 우선)
      - "500KCMIL"         → 253.0  (KCMIL → mm² 변환표)
    """
    if not spec_raw:
        return None

    # SQ 단위가 명시된 경우 — 괄호 안이든 밖이든 가장 먼저 매칭
    m = re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec_raw, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # KCMIL 표기 → 근사 SQ 변환 (IEC 62890 기준 반올림값)
    kcmil_to_sq: dict[str, float] = {
        "4/0": 107.0,
        "500": 253.0,
        "750": 380.0,
        "1000": 507.0,
        "1250": 633.0,
        "1500": 759.0,
        "2000": 1013.0,
    }
    upper = spec_raw.upper()
    for kcmil, sq_val in kcmil_to_sq.items():
        # KCMIL 숫자가 단독으로 등장하는지 확인 (예: "500KCMIL" but not "1500KCMIL" when checking "500")
        pattern = rf"(?<!\d){re.escape(kcmil)}(?:\s*KCMIL)?"
        if re.search(pattern, upper):
            return sq_val

    return None


def format_spec_display(
    spec_raw: str | None,
    core_count: int,
    sq_mm2: float,
) -> str:
    """
    규격 표시 문자열을 반환한다.

    고압 제품(AWG / KCMIL 단위)은 원본 단위를 그대로 사용하고,
    일반 저압 제품은 "{core_count}C x {sq_mm2}SQ" 형태를 반환한다.

    지원 패턴 (spec_raw 기준):
      - "4C x 4/0AWG(107SQ)"   → "4C x 4/0AWG"
      - "1C x 500KCMIL(253SQ)" → "1C x 500KCMIL"
      - "4C x 35SQ"            → "4C x 35SQ"  (기본)
    """
    if spec_raw:
        # AWG 표기 — "4/0AWG" 또는 "2AWG" 등
        m_awg = re.search(r"(\d+/\d+|\d+)\s*AWG", spec_raw, re.IGNORECASE)
        if m_awg:
            return f"{core_count}C x {m_awg.group(1)}AWG"

        # KCMIL 표기 — "500KCMIL", "1250 KCMIL" 등
        m_kcmil = re.search(r"(\d+(?:\.\d+)?)\s*KCMIL", spec_raw, re.IGNORECASE)
        if m_kcmil:
            return f"{core_count}C x {m_kcmil.group(1)}KCMIL"

        # SQ 표기 — spec_raw의 SQ 값을 우선 사용 (sq_mm2 파라미터 무시)
        # CORE 배치처럼 sq_mm2가 내부 작업용으로 변경된 경우에도 원본 규격 표시
        m_sq = re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec_raw, re.IGNORECASE)
        if m_sq:
            return f"{core_count}C x {int(float(m_sq.group(1)))}SQ"

    return f"{core_count}C x {int(sq_mm2)}SQ"


def _find_item(order: SalesOrder, items: dict[str, ItemMaster]) -> ItemMaster | None:
    """
    수주에 매칭되는 ItemMaster를 찾는다.

    1순위: order.item_code 직접 매칭 (ERP 수주에 품목 코드가 있는 경우)
    2순위: product_group + voltage 조합으로 퍼지 매칭 (품목 코드 없는 경우)

    퍼지 매칭은 첫 번째 일치 항목을 반환하므로 동일 그룹/전압에 여러 품목이 있으면
    오매칭 가능성이 있다. 근본 해결은 ERP 품목 코드 입력률 개선이다.
    """
    if order.item_code and order.item_code in items:
        return items[order.item_code]

    # 퍼지 매칭: product_group + voltage 동시 일치
    if order.product_group and order.voltage:
        for item in items.values():
            if (
                item.product_group == order.product_group
                and item.voltage == order.voltage
            ):
                return item

    return None


def _infer_routing(order: SalesOrder) -> str:
    """
    ItemMaster 매칭 실패 시 제품군·전압·심선수 조합으로 라우팅 코드를 추론한다.

    RT-001/002: 저압 CV (단심/다심)
    RT-003/004: HFCO (단심/다심)
    RT-005/006 : TFR-8 (단심/다심)
    RT-006/007 : TFR-8 고내화 (단심/다심)
    RT-HP1:     6/10kV 고압
    RT-HP2:     22.9kV/35kV URD
    RT-TFR:     TFR-GV 내화케이블
    """
    pg = (order.product_group or "").upper()
    voltage = order.voltage or ""
    core_count = int(order.core_count or 1)

    if "URD" in pg or "35" in voltage:
        return "RT-HP3"
    if "22.9" in voltage:
        return "RT-HP2"
    if "6/10" in voltage:
        return "RT-HP1"
    if "TFR-GV" in pg:
        return "RT-TFR"
    if "TFR-CV" in pg:
        return "RT-002" if core_count > 1 else "RT-001"
    if "TFR-8(" in pg:
        return "RT-008" if core_count > 1 else "RT-007"
    if "TFR-8" in pg:
        return "RT-006" if core_count > 1 else "RT-005"
    if "연동선" in pg or "나동선" in pg:
        return "RT-BARE"  # 연동선/나동선: 연선만 (절연/시스 불필요)
    # 기본값: 저압 CV
    return "RT-002" if core_count > 1 else "RT-001"


def _get_processes(routing: ProcessRouting) -> list[str]:
    """
    라우팅 레코드에서 None이 아닌 공정명 목록을 순서대로 반환한다.
    process_1 ~ process_6 컬럼을 순회하며 빈 문자열도 제외한다.
    """
    procs: list[str] = []
    for attr in (
        "process_1",
        "process_2",
        "process_3",
        "process_4",
        "process_5",
        "process_6",
    ):
        val = getattr(routing, attr, None)
        if val and val.strip():
            procs.append(val.strip())
    return procs


def _find_speed(
    lookup: dict[tuple, SpeedMaster],
    process_name: str,
    order: SalesOrder,
    sq: float,
) -> SpeedMaster | None:
    """
    (equipment_code, product_type, cross_section) 조합으로 선속 레코드를 조회한다.

    공정명 → 설비 코드 목록 매핑은 현장 설비 구성 기준이다.
    product_type 키는 SpeedMaster.product_type 컬럼값과 정확히 일치해야 한다.
    """
    # 공정명 → 설비 코드 후보 목록
    equipment_map: dict[str, list[str]] = {
        "저압절연": ["EX-B100"],
        "저압시스": ["SH-A100", "SH-A120"],
        "고압절연": ["EX-CV1", "EX-CV2"],
        "고압시스": ["SH-A150", "SH-B100"],
        "연선": ["ST-T6B0", "ST-AL6BO", "ST-54BO1", "ST-54BO2", "ST-54BO3", "ST-30BO"],
        "신선": ["WD-A100"],
        "연합": ["AS-A100"],
        "T/P": ["TP-2"],
    }

    pg = order.product_group or ""
    core_count = int(order.core_count or 1)

    # product_type 결정 — SpeedMaster.product_type 컬럼 값과 일치시킴
    # 연선/신선 공정은 제품군 무관하게 설비 기준 선속 적용
    if process_name in ("연선", "신선", "T/P"):
        product_type: str | None = process_name
    elif "HFCO" in pg.upper():
        product_type = "HFCO"
    elif "TFR-GV" in pg.upper():
        product_type = "TFR-GV"
    elif process_name == "저압절연":
        product_type = "저압절연"
    elif process_name in ("저압시스", "고압시스"):
        # 10-5: core_count == 4 일 때 SpeedMaster 키를 "4C"로 직접 조회한다.
        # 4C 초과 심선수도 현장 기준 4C 테이블을 그대로 적용한다.
        if core_count >= 4:
            product_type = "4C"
        else:
            product_type = f"TFR-CV {core_count}C"
    else:
        product_type = None

    equip_codes = equipment_map.get(process_name, [])
    for eq in equip_codes:
        key = (eq, product_type, sq)
        if key in lookup:
            return lookup[key]

    return None


def _infer_material(order: SalesOrder) -> str:
    """
    도체 재질을 추론한다.
    - URD 제품군은 알루미늄 도체 사용
    - al_weight_kg > 0이면 알루미늄
    - 그 외 기본값 구리(CU)
    """
    pg = (order.product_group or "").upper()
    if "URD" in pg:
        return "AL"
    al_kg = order.al_weight_kg
    if al_kg is not None and float(al_kg) > 0:
        return "AL"
    return "CU"


def _process_order(process_name: str | None) -> int:
    """
    공정 정렬 우선순위 반환 (낮을수록 먼저).
    신선 → 연선 → 절연 → 연합/T·P → 시스 → 기타 → 외주
    """
    order_map: dict[str, int] = {
        "신선": 0,
        "연선": 1,
        "저압절연": 2,
        "고압절연": 2,
        "연합": 3,
        "T/P": 3,
        "저압시스": 4,
        "고압시스": 4,
        "HFCO시스": 4,
        "외주": 99,
    }
    return order_map.get(process_name or "", 50)
