"""공정별 작업 분할, 틀단위 배치 생성, 묶음 정렬 — Stage 1 핵심 로직

설계 의도:
- 수주 1건은 라우팅에 정의된 공정 수만큼 ProductionBatch 행으로 펼쳐진다.
- 모든 마스터 데이터를 함수 진입 시점에 메모리로 로드해 N+1 쿼리를 원천 차단한다.
- 정렬은 DB flush 전에 Python 레벨에서 수행해 INSERT 순서가 곧 작업 순서가 된다.
"""

import re
from sqlalchemy.orm import Session

from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.item_master import ItemMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.process_routing import ProcessRouting
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.customer_master import CustomerMaster


def create_batches(run_label: str, db: Session) -> dict:
    """
    run_label에 해당하는 sales_order를 읽어서 production_batch를 생성한다.

    외주 품목(is_outsourced=True)은 건너뛰고, 라우팅이 없거나 SQ 파싱에 실패하면
    warnings 목록에 기록한 뒤 계속 진행한다 (Fail-Fast 대신 Best-Effort).

    Returns:
        {
            "total_batches": int,
            "by_process": {process_name: count, ...},
            "warnings": [str, ...]
        }
    """
    result: dict = {"total_batches": 0, "by_process": {}, "warnings": []}

    # ── 마스터 데이터 일괄 로드 (N+1 방지) ──────────────────────────────────
    orders = (
        db.query(SalesOrder)
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.is_outsourced == False,  # noqa: E712
        )
        .all()
    )

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

    # ── 선속 룩업 테이블 빌드 ────────────────────────────────────────────────
    # 키: (equipment_code, product_type, cross_section_float) → SpeedMaster
    speed_lookup: dict[tuple, SpeedMaster] = {}
    for s in speeds:
        cs_key = float(s.cross_section) if s.cross_section is not None else None
        key = (s.equipment_code, s.product_type, cs_key)
        speed_lookup[key] = s

    # ── 수주별 배치 생성 ─────────────────────────────────────────────────────
    batches: list[ProductionBatch] = []

    for order in orders:
        order_ref = f"{order.order_id}-{order.order_line}"

        # SQ 추출 실패 시 스킵 (규격 텍스트 품질 문제)
        sq = _extract_sq(order.spec_raw)
        if sq is None:
            result["warnings"].append(
                f"수주 {order_ref}: SQ 파싱 실패 (spec_raw={order.spec_raw!r})"
            )
            continue

        # 품목 마스터 매칭 → 라우팅 코드 결정
        item = _find_item(order, items)
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

        drum_length: float = float(order.drum_length_m or total_qty)
        drum_count: int = int(order.drum_count or 1)
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

        # 공정별 배치 생성
        for batch_seq, process_name in enumerate(processes, start=1):
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

            # 유효 길이: 총 수량 + (여척 × 심선수)
            # 여척은 색상/컴파운드 전환 낭비분이므로 심선수에 비례한다
            extra_total: float = extra * core_count
            effective_length: float = total_qty + extra_total

            duration_min: float | None = (
                effective_length / line_speed if line_speed else None
            )

            batch = ProductionBatch(
                run_label=run_label,
                sales_order_id=order.order_id,
                sales_order_line=order.order_line,
                item_code=item.item_code if item else None,
                routing_code=routing_code,
                process_name=process_name,
                batch_seq=batch_seq,
                drum_length_m=drum_length,
                drum_count=drum_count,
                total_length_m=total_qty,
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
                # equipment_code는 Stage 2 스케줄링 단계에서 설비 배정 후 기입
                equipment_code=None,
            )
            batches.append(batch)

    # ── 정렬: 공정 순서 → SQ 내림차순 → 색상 그루핑 ──────────────────────────
    # 동일 공정 내에서 SQ가 같은 것끼리 모이고, 그 안에서 색상 전환을 최소화한다.
    batches.sort(
        key=lambda b: (
            _process_order(b.process_name),
            -(b.sq_mm2 or 0),
            b.sheath_color or "",
            b.core_colors or "",
        )
    )

    # ── DB 기록 및 집계 ───────────────────────────────────────────────────────
    db.add_all(batches)
    db.flush()  # batch_id 자동 채번 (autoincrement)을 트리거하되 커밋은 호출자에게 위임

    for b in batches:
        proc = b.process_name
        result["by_process"][proc] = result["by_process"].get(proc, 0) + 1
    result["total_batches"] = len(batches)

    return result


# ── 헬퍼 함수 ─────────────────────────────────────────────────────────────────


def _extract_sq(spec_raw: str | None) -> float | None:
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
    RT-HP1:     6/10kV 고압
    RT-HP2:     22.9kV/35kV URD
    RT-TFR:     TFR-GV 내화케이블
    """
    pg = (order.product_group or "").upper()
    voltage = order.voltage or ""
    core_count = int(order.core_count or 1)

    if "URD" in pg or "22.9" in voltage or "35" in voltage:
        return "RT-HP2"
    if "6/10" in voltage:
        return "RT-HP1"
    if "TFR-GV" in pg:
        return "RT-TFR"
    if "HFCO" in pg:
        return "RT-004" if core_count > 1 else "RT-003"
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
        "연선": ["ST-A100", "ST-A200"],
        "신선": ["WD-A100"],
        "연합": ["AS-A100"],
    }

    pg = order.product_group or ""
    core_count = int(order.core_count or 1)

    # product_type 결정 — SpeedMaster.product_type 컬럼 값과 일치시킴
    if "HFCO" in pg.upper():
        product_type: str | None = "HFCO"
    elif "TFR-GV" in pg.upper():
        product_type = "TFR-GV"
    elif process_name == "저압절연":
        product_type = "저압절연"
    elif process_name in ("저압시스", "고압시스"):
        # 다심 4C 초과는 4C 규격으로 적용 (테이블 한계)
        nc = min(core_count, 4)
        product_type = f"TFR-CV {nc}C"
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
