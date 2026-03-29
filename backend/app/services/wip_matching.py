"""재공(WIP) 매칭 로직 — 기존 재고를 수주에 매칭하여 공정 생략"""

from sqlalchemy.orm import Session
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.decision_criteria import DecisionCriteria
from app.services.audit_logger import log_decision


def match_wip(run_label: str, db: Session) -> dict:
    """재공 재고를 수주에 매칭. Returns: {"matched": int, "skipped": int, "details": list}"""
    result = {"matched": 0, "skipped": 0, "details": []}

    # Load decision criteria
    criteria = {
        c.criteria_name: c.criteria_value for c in db.query(DecisionCriteria).all()
    }
    loss_limit = float(criteria.get("Loss 허용 한도", "8")) / 100
    min_remainder = float(criteria.get("최소 잔여 조장 보유", "50"))
    shortage_tolerance = float(criteria.get("조장 부족 허용율", "5")) / 100

    # Load available WIP
    wip_items = db.query(WipInventory).filter(WipInventory.status == "사용가능").all()

    if not wip_items:
        return result

    # Load orders for this run
    orders = (
        db.query(SalesOrder)
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.is_outsourced == False,  # noqa: E712
        )
        .all()
    )

    for wip in wip_items:
        wip_sq = float(wip.cross_section) if wip.cross_section else None
        wip_length = float(wip.total_length_m) if wip.total_length_m else 0
        if not wip_sq or wip_length <= 0:
            continue

        # Find matching order
        for order in orders:
            if order.use_wip:  # already matched
                continue

            # Extract SQ from spec
            order_sq = _extract_sq(order.spec_raw)
            if order_sq is None:
                continue

            # SQ exact match (허용 오차 0)
            if abs(order_sq - wip_sq) > 0.01:
                continue

            # Voltage match: derive voltage class from order voltage string
            if wip.voltage_class and order.voltage:
                wip_volt = (
                    "저압"
                    if "0.6" in (order.voltage or "") or "1kV" in (order.voltage or "")
                    else "고압"
                )
                if wip.voltage_class != wip_volt:
                    continue

            # Material match placeholder — inferred from order downstream
            if wip.material and order.voltage:
                pass

            order_qty = float(order.ordered_qty_m or 0)
            if order_qty <= 0:
                continue

            # Loss check: wip_length must cover order_qty within allowed loss rate
            if wip_length < order_qty * (1 - loss_limit):
                continue

            # Shortage tolerance: wip can be slightly shorter than order qty
            if wip_length < order_qty * (1 - shortage_tolerance):
                continue

            # Remainder too small → treat as scrap, still use the WIP
            remainder = wip_length - order_qty
            if 0 < remainder < min_remainder:
                pass

            order.use_wip = True
            order.wip_type = wip.process_stage
            order.actual_length_m = wip_length
            wip.status = "사용완료"

            result["matched"] += 1
            result["details"].append(
                {
                    "order_id": order.order_id,
                    "wip_id": wip.wip_id,
                    "wip_process": wip.process_stage,
                    "sq": wip_sq,
                    "wip_length": wip_length,
                    "order_qty": order_qty,
                }
            )

            log_decision(
                db=db,
                run_label=run_label,
                stage="stage1",
                action_type="wip_matched",
                constraints_applied=[
                    {
                        "id": "2-1",
                        "name": "재공 활용",
                        "result": "pass",
                        "detail": (
                            f"WIP {wip.wip_id}({wip.process_stage} {wip_sq}SQ {wip_length}m)"
                            f" → 수주 {order.order_id}"
                        ),
                    }
                ],
                reason=(
                    f"재공 매칭: {wip.process_stage} {wip_sq}SQ {wip_length}m"
                    f" → {order.order_id} ({order_qty}m)"
                ),
            )
            break  # One WIP per order

    db.flush()
    return result


def _extract_sq(spec_raw):
    """규격 문자열에서 SQ 값(숫자)을 추출"""
    import re

    if not spec_raw:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec_raw, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None
