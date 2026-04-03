"""재공(WIP) 매칭 로직 — 기존 재고를 수주에 매칭하여 공정 생략

매칭 정책:
  - WIP 1건의 total_length_m(= length_m × count)을 기준으로
    동일 규격/전압 수주들을 우선순위·납기 순으로 쌓아가며 배분한다.
  - 1개 WIP → 여러 수주 적용 가능
  - 마지막 수주는 shortage_tolerance 범위 내 부족 허용
"""

from sqlalchemy.orm import Session
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.decision_criteria import DecisionCriteria
from app.services.audit_logger import log_decision


def match_wip(run_label: str, db: Session) -> dict:
    """재공 재고를 수주에 매칭. Returns: {"matched": int, "skipped": int, "details": list}"""
    result = {"matched": 0, "skipped": 0, "details": []}

    # 판단 기준 로드
    criteria = {
        c.criteria_name: c.criteria_value for c in db.query(DecisionCriteria).all()
    }
    loss_limit = float(criteria.get("Loss 허용 한도", "8")) / 100
    shortage_tolerance = float(criteria.get("조장 부족 허용율", "5")) / 100

    # 사용 가능한 WIP 로드 (total_length_m 큰 순 — 큰 재고를 먼저 소진)
    wip_items = (
        db.query(WipInventory)
        .filter(WipInventory.status == "사용가능")
        .order_by(WipInventory.cross_section.desc(), WipInventory.total_length_m.desc())
        .all()
    )
    if not wip_items:
        return result

    # 이번 run의 수주 로드 (외주 제외)
    orders = (
        db.query(SalesOrder)
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.is_outsourced == False,  # noqa: E712
        )
        .all()
    )

    # 수주 정렬: 고객 우선순위 → 납기 → order_id (재현성)
    from datetime import date as _date
    orders.sort(key=lambda o: (
        o.customer_priority or 99,
        o.due_date or _date.max,
        o.order_id or "",
    ))

    for wip in wip_items:
        wip_sq = float(wip.cross_section) if wip.cross_section else None
        # total_length_m = length_m(드럼 1개) × count(드럼 수)
        wip_total = float(wip.total_length_m or 0)
        if not wip_sq or wip_total <= 0:
            continue

        remaining = wip_total  # 이 WIP에서 아직 배분 가능한 잔여 길이
        matched_orders: list[SalesOrder] = []

        for order in orders:
            if order.use_wip:
                continue  # 이미 다른 WIP에 매칭됨

            # ── 규격 매칭 ──
            order_sq = _extract_sq(order.spec_raw)
            if order_sq is None or abs(order_sq - wip_sq) > 0.01:
                continue

            # ── 전압 매칭 ──
            if wip.voltage_class and order.voltage:
                order_volt = (
                    "저압"
                    if "0.6" in (order.voltage or "") or "1kV" in (order.voltage or "")
                    else "고압"
                )
                if wip.voltage_class != order_volt:
                    continue

            # ── 수량 체크 ──
            order_qty = float(order.ordered_qty_m or 0)
            if order_qty <= 0:
                continue

            # 잔여량이 수주 수량을 커버하는지 (shortage_tolerance 허용)
            if remaining < order_qty * (1 - shortage_tolerance):
                continue  # 잔여 재고 부족 — 이 수주는 스킵

            matched_orders.append(order)
            remaining -= order_qty

            # 잔여량이 loss_limit 이하로 떨어지면 더 이상 배분하지 않음
            if remaining <= wip_total * loss_limit:
                break

        if not matched_orders:
            continue

        # ── WIP 상태 갱신 ──
        wip.status = "사용완료"
        # 참조용으로 첫 번째 수주 저장 (다중 매칭은 order.wip_id로 역참조)
        wip.matched_order_id = f"{matched_orders[0].order_id}:{matched_orders[0].order_line}"

        # ── 수주별 매칭 정보 설정 ──
        for order in matched_orders:
            order.use_wip = True
            order.wip_type = wip.process_stage
            order.actual_length_m = float(order.ordered_qty_m or 0)
            order.wip_id = wip.wip_id  # sales_order → wip_inventory 직접 참조

            result["matched"] += 1
            result["details"].append({
                "order_id": order.order_id,
                "order_line": order.order_line,
                "wip_id": wip.wip_id,
                "wip_process": wip.process_stage,
                "wip_sq": wip_sq,
                "wip_total_m": wip_total,
                "order_qty_m": float(order.ordered_qty_m or 0),
            })

            log_decision(
                db=db,
                run_label=run_label,
                stage="stage1",
                action_type="wip_matched",
                constraints_applied=[{
                    "id": "2-1",
                    "name": "재공 활용",
                    "result": "pass",
                    "detail": (
                        f"WIP {wip.wip_id}({wip.process_stage} {wip_sq}SQ "
                        f"총{wip_total}m) → 수주 {order.order_id}:{order.order_line} "
                        f"({float(order.ordered_qty_m or 0)}m)"
                    ),
                }],
                reason=(
                    f"재공 매칭: {wip.process_stage} {wip_sq}SQ 총{wip_total}m → "
                    f"{order.order_id}:{order.order_line}"
                ),
            )

    db.flush()
    return result


def _extract_sq(spec_raw: str | None) -> float | None:
    """규격 문자열에서 SQ 값(숫자)을 추출"""
    import re
    if not spec_raw:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec_raw, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None
