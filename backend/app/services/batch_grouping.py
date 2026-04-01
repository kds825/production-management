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


def create_batches(
    run_label: str,
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
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
    result: dict = {
        "total_batches": 0,
        "by_process": {},
        "warnings": [],
        "outsource_count": 0,
    }

    # ── 마스터 데이터 일괄 로드 (N+1 방지) ──────────────────────────────────
    query = db.query(SalesOrder).filter(
        SalesOrder.run_label == run_label,
        SalesOrder.is_outsourced == False,  # noqa: E712
    )
    if date_from is not None:
        query = query.filter(SalesOrder.due_date >= date_from)
    if date_to is not None:
        query = query.filter(SalesOrder.due_date <= date_to)
    orders = query.all()

    # ── WIP 매칭 룩업: "order_id:order_line" → wip ────────────────────────
    # matched_order_id 형식: "S1202602260013M:37" (order_id:order_line)
    # order_line 단위로 매칭해야 같은 수주번호의 다른 규격/색상은 스킵하지 않음
    wip_by_order_line: dict[str, WipInventory] = {}
    if any(o.use_wip for o in orders):
        matched_wips = (
            db.query(WipInventory)
            .filter(WipInventory.matched_order_id.isnot(None))
            .all()
        )
        for w in matched_wips:
            wip_by_order_line[w.matched_order_id] = w

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
    # defect_buffer_pct: 불량 재작업 대비 생산 길이 가산 비율, 기본값 5%
    defect_buffer_pct: float = float(defect_params.get("defect_buffer_pct", 0.05))

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

        # ── 외주 자동분류 (2-2) ─────────────────────────────────────────────
        # ERP 플래그와 무관하게 SQ 기준 또는 고내화 제품군이면 외주로 처리한다.
        # SQ <= 10: 소단면적 특수 공정은 사내 설비로 생산 불가
        # 고내화: 고내화 케이블은 전문 외주 업체 전용 품목
        if sq <= 10 or "고내화" in (order.product_group or ""):
            result["outsource_count"] += 1
            result["warnings"].append(
                f"수주 {order_ref}: 외주 자동분류 (SQ={sq}, 제품군={order.product_group})"
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

        # ── 불량 재작업 버퍼 가산 (7-1) ─────────────────────────────────────
        total_qty = total_qty * (1.0 + defect_buffer_pct)

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

        # 공정별 배치 생성 — ERP 수주 1행 = 배치 1행
        # WIP 매칭 배치도 생성 (Stage 1 가시성) → Stage 2에서 스킵
        for batch_seq, process_name in enumerate(processes, start=1):
            if process_name == "신선":
                continue
            if skip_stranding and process_name == "연선":
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
                    total_length_m=lot_length,
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
                )
                batches.append(batch)

                # 61연선: 7연선 코어 선행 배치 추가 (연선 공정일 때만)
                # T6B0(7연선기)에서 코어 제작 → 54BO(대형 연선기)에서 61연선 완성
                if is_61strand and process_name == "연선":
                    # 7연선 코어 선속: T6B0에서 25mpm (7연선 35SQ 기준)
                    core_speed_info = _find_speed(speed_lookup, "연선", order, 35.0)
                    core_speed = (
                        float(core_speed_info.line_speed_mpm)
                        if core_speed_info and core_speed_info.line_speed_mpm
                        else 25.0
                    )
                    core_setup = (
                        float(core_speed_info.setup_spec_min)
                        if core_speed_info and core_speed_info.setup_spec_min
                        else 210.0
                    )
                    core_dur = (
                        (lot_length + extra_total) / core_speed
                        if core_speed > 0
                        else None
                    )

                    core_batch = ProductionBatch(
                        run_label=run_label,
                        sales_order_id=order.order_id,
                        sales_order_line=order.order_line,
                        item_code=item.item_code if item else None,
                        routing_code=routing_code,
                        process_name="연선",
                        batch_seq=0,  # 선행 공정 = seq 0
                        drum_length_m=drum_length,
                        drum_count=drum_count,
                        total_length_m=lot_length,
                        extra_length_m=extra_total,
                        sq_mm2=35,  # 7연선 코어는 소형 SQ로 T6B0에 배정
                        core_count=core_count,
                        core_colors=order.core_colors,
                        sheath_color=order.sheath_color,
                        customer_name=order.customer_name,
                        due_date=order.due_date,
                        customer_priority=priority,
                        line_speed_mpm=core_speed,
                        setup_time_min=core_setup,
                        estimated_duration_min=core_dur,
                        status="planned",
                        product_group=order.product_group,
                        voltage=order.voltage,
                        conductor_material=conductor_material,
                        stranding_type="7연선코어",
                        remarks=f"61연선 코어 ({int(sq)}SQ용)",
                        equipment_code=None,
                        wip_matched_id=wip_id,
                    )
                    batches.append(core_batch)

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

        # 저압시스: 색상 기준 설비 분리 (원본 계획서 A100/A120 시트 구조)
        # A120 = 흑/청, A100 = 갈/회/녹황 등 나머지
        if proc == "저압시스":
            color = (b.sheath_color or "").strip()
            if color in ("흑", "청", "흑/적"):
                group_key = f"A120_{sq_key}SQ"
            else:
                group_key = f"A100_{sq_key}SQ"

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
        "연선": ["ST-T6B0", "ST-54BO1", "ST-54BO2", "ST-54BO3", "ST-30BO"],
        "신선": ["WD-A100"],
        "연합": ["AS-A100"],
    }

    pg = order.product_group or ""
    core_count = int(order.core_count or 1)

    # product_type 결정 — SpeedMaster.product_type 컬럼 값과 일치시킴
    # 연선/신선 공정은 제품군 무관하게 설비 기준 선속 적용
    if process_name in ("연선", "신선"):
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
