"""create_batches() 의 입력 로드 — DB + 파라미터 fetch + speed_lookup 빌드.

Phase 2 refactor (Task 2.1): batch_grouper.create_batches() 의 line 180~289
'마스터 데이터 일괄 로드' 블록을 분리. 모든 phase helper 가 공유하는 read-only
context 를 _GrouperInputs frozen-style dataclass 로 캡슐화하여 12+ args
positional 전달 대신 (ctx) 단일 인자로 통일한다.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.application._shared.constraint_params import ConstraintParams
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.customer_master import CustomerMaster
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.item_master import ItemMaster
from app.infrastructure.models.process_routing import ProcessRouting
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.wip_inventory import WipInventory


@dataclass
class _GrouperInputs:
    """create_batches() 의 phase helper 가 공유하는 read-only context.

    warnings 는 외부 누적 list — 모든 phase 에서 mutation 허용.
    그 외 dict / float / list 필드는 phase 진행 중 변경 금지 (frozen 의도).
    excluded_count 는 load 단계에서 frozen 수주 제외 통계용.
    """

    db: Session
    run_label: str
    orders: list  # SalesOrder
    wip_by_order_line: dict  # str → WipInventory
    drum_lots: dict  # float → DrumLotMaster
    routings: dict  # str → ProcessRouting
    items: dict  # str → ItemMaster
    speeds: list  # SpeedMaster
    speed_lookup: dict  # tuple → SpeedMaster
    customers: dict  # str → CustomerMaster
    constraint_params: ConstraintParams
    base_extra: float
    sample_extra: float
    defect_buffer_pct: float
    remnant_threshold_m: float
    remnant_enabled: bool
    warnings: list  # mutable — 외부 누적
    excluded_count: int = 0  # frozen orders 제외 카운트


def load_grouper_inputs(
    run_label: str,
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    frozen_order_keys: set[tuple] | None = None,
) -> _GrouperInputs:
    """create_batches() 의 입력 로드 단계.

    원본 batch_grouper.py:180-289 본문 그대로 옮김. SalesOrder + WIP 매칭 +
    마스터 (drum_lots/routings/items/speeds/customers) 일괄 로드 +
    extra/defect/remnant 파라미터 fetch + speed_lookup 빌드 → _GrouperInputs 반환.

    warnings 는 빈 list 로 초기화하고 frozen 수주 제외 시 1건 추가 (계산은
    호출자에서 excluded_count 와 함께 처리).
    """
    warnings: list[str] = []

    # ── ConstraintConfig 스냅샷 프리페치 (4-1 규격교체 fallback 등) ─────────
    # Why: 루프 내부 fallback 시 재조회로 인한 N+1 방지. 1회 load 후 재사용.
    constraint_params = ConstraintParams.load(db)

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
    excluded_count = 0
    if frozen_order_keys:
        before_count = len(orders)
        orders = [
            o for o in orders if (o.order_id, o.order_line) not in frozen_order_keys
        ]
        excluded_count = before_count - len(orders)

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

    return _GrouperInputs(
        db=db,
        run_label=run_label,
        orders=orders,
        wip_by_order_line=wip_by_order_line,
        drum_lots=drum_lots,
        routings=routings,
        items=items,
        speeds=speeds,
        speed_lookup=speed_lookup,
        customers=customers,
        constraint_params=constraint_params,
        base_extra=base_extra,
        sample_extra=sample_extra,
        defect_buffer_pct=defect_buffer_pct,
        remnant_threshold_m=remnant_threshold_m,
        remnant_enabled=remnant_enabled,
        warnings=warnings,
        excluded_count=excluded_count,
    )
