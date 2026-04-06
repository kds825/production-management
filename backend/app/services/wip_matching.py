"""재공(WIP) 매칭 로직 — 기존 재고를 수주에 매칭하여 공정 생략

매칭 정책:
  - WIP 1건의 total_length_m(= length_m × count)을 기준으로
    동일 규격/전압 수주들을 대상으로 잔여량이 최소화되는 최적 조합을 찾는다.
  - 1개 WIP → 여러 수주 적용 가능
  - 최적 조합 탐색: n ≤ 22 완전 탐색(2^n), n > 22 동적 계획법(10m 이산화)
"""

import re

from sqlalchemy.orm import Session
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.decision_criteria import DecisionCriteria
from app.services.audit_logger import log_decision

_EXACT_SEARCH_LIMIT = 22  # 완전 탐색 최대 수주 건수 (2^22 ≈ 4M)
_DP_GRANULARITY_M = 10    # DP 이산화 단위 (10m)


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
        wip_drum_length = float(wip.length_m or 0)   # 드럼 1개 기준 길이
        wip_total = float(wip.total_length_m or 0)   # 전체 재고 (length_m × count)
        if not wip_sq or wip_total <= 0 or wip_drum_length <= 0:
            continue

        # ── 이 WIP에 매칭 가능한 후보 수주 필터링 ──────────────────────────
        candidates: list[SalesOrder] = []
        for order in orders:
            if order.use_wip:
                continue

            order_sq = _extract_sq(order.spec_raw)
            if order_sq is None or abs(order_sq - wip_sq) > 0.01:
                continue

            if wip.process_stage == "절연재고":
                if not _product_group_matches(wip.product_name, order.product_group):
                    continue

            if wip.voltage_class and order.voltage:
                order_volt = (
                    "저압"
                    if "0.6" in (order.voltage or "") or "1kV" in (order.voltage or "")
                    else "고압"
                )
                if wip.voltage_class != order_volt:
                    continue

            order_drum_length = float(order.drum_length_m or order.ordered_qty_m or 0)
            if order_drum_length > 0:
                if wip_drum_length < order_drum_length * (1 - loss_limit):
                    continue

            order_qty = float(order.ordered_qty_m or 0)
            if order_qty <= 0:
                continue

            candidates.append(order)

        if not candidates:
            continue

        # ── 최적 조합 탐색: WIP 잔여량 최소화 ──────────────────────────────
        matched_orders = _find_best_combo(
            candidates, wip_total, shortage_tolerance
        )

        if not matched_orders:
            continue

        # ── WIP 상태 갱신 ──
        wip.status = "사용완료"
        wip.matched_order_id = f"{matched_orders[0].order_id}:{matched_orders[0].order_line}"

        # ── 수주별 매칭 정보 설정 ──
        for order in matched_orders:
            order.use_wip = True
            order.wip_type = wip.process_stage
            order.actual_length_m = float(order.ordered_qty_m or 0)
            order.wip_id = wip.wip_id

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


def _find_best_combo(
    candidates: list[SalesOrder],
    wip_total: float,
    shortage_tolerance: float,
) -> list[SalesOrder]:
    """WIP 잔여량을 최소화하는 최적 수주 조합 탐색 (0-1 Knapsack).

    shortage_tolerance: 마지막 수주를 부분 충당할 때 허용 부족률.
      → 유효 capacity를 wip_total / (1 - shortage_tolerance)까지 소폭 확장하여
        거의 다 쓰는 조합도 후보로 포함.

    n ≤ 22: 완전 탐색 (2^n 부분집합 열거)
    n > 22: DP (10m 단위 이산화, O(n × cap/gran))
    """
    n = len(candidates)
    qtys = [float(o.ordered_qty_m or 0) for o in candidates]

    # shortage_tolerance만큼 capacity를 늘려 마지막 수주 부분 충당 허용
    effective_cap = wip_total / max(1 - shortage_tolerance, 0.01)

    best_used = 0.0
    best_mask: int = 0  # 선택된 후보 인덱스를 비트마스크로 표현

    if n <= _EXACT_SEARCH_LIMIT:
        # ── 완전 탐색 ────────────────────────────────────────────────────────
        for mask in range(1, 1 << n):
            total = sum(qtys[i] for i in range(n) if mask & (1 << i))
            if total <= effective_cap and total > best_used:
                best_used = total
                best_mask = mask

        return [candidates[i] for i in range(n) if best_mask & (1 << i)]

    else:
        # ── 동적 계획법 (10m 이산화) ─────────────────────────────────────────
        gran = _DP_GRANULARITY_M
        cap_disc = int(effective_cap / gran)

        # dp[c] = capacity c*gran 이하에서 달성 가능한 최대 총 수량 (float)
        dp = [0.0] * (cap_disc + 1)
        # kept[i][c] = i번째 후보를 capacity c에서 선택했는지
        kept = [[False] * (cap_disc + 1) for _ in range(n)]

        for i, qty in enumerate(qtys):
            qty_disc = max(1, round(qty / gran))
            # 역순 순회 — 같은 아이템을 중복 선택하지 않도록
            for c in range(cap_disc, qty_disc - 1, -1):
                val = dp[c - qty_disc] + qty
                if val > dp[c]:
                    dp[c] = val
                    kept[i][c] = True

        # 역추적으로 선택된 후보 복원
        selected: list[SalesOrder] = []
        c = cap_disc
        for i in range(n - 1, -1, -1):
            if kept[i][c]:
                selected.append(candidates[i])
                qty_disc = max(1, round(qtys[i] / gran))
                c -= qty_disc

        return selected


def _product_group_matches(wip_product_name: str | None, order_product_group: str | None) -> bool:
    """WIP 제품명과 수주 제품군이 호환되는지 판별.

    wip_product_name 이 없거나 order_product_group 이 없으면 필터 없이 통과.
    """
    if not wip_product_name or not order_product_group:
        return True

    wpn = wip_product_name.strip()
    opg = order_product_group.strip()

    if wpn == "TFR-CV(WB)":
        return opg == "TFR-CV" or opg.startswith("TFR-CV-WB") or opg.startswith("TFR-CV(")
    if wpn == "TFR-8 고내화":
        return opg.startswith("TFR-8(")
    if wpn == "TFR-8":
        return opg == "TFR-8" or (opg.startswith("TFR-8") and "(" not in opg)
    if "URD" in wpn:
        return "URD" in opg

    return True


def _extract_sq(spec_raw: str | None) -> float | None:
    """규격 문자열에서 SQ 값(숫자)을 추출"""
    if not spec_raw:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec_raw, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None
