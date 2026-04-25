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
from app.application._shared.audit_logger import log_decision

_EXACT_SEARCH_LIMIT = 22  # 완전 탐색 최대 수주 건수 (2^22 ≈ 4M)
_DP_GRANULARITY_M = 1  # DP 이산화 단위 (1m — 정확한 매칭)

# Level 정책 (spec §G3)
# Cross-run policy: run_label 로 filter 하지 않음 (by design) — 이전 run 재공도 활용 가능.
_LEVEL2_STATUSES = ("사용가능", "실사_확정", "실적_추정")  # 기본 (Level 2)
_LEVEL3_STATUSES = _LEVEL2_STATUSES + (
    "예상",
)  # opt-in, G5 temporal guard 는 별도 (Level 3)


def match_wip(
    run_label: str,
    db: Session,
    *,
    exclude_wip_ids: set[int] | None = None,
    emergency_mode: bool = False,
) -> dict:
    """재공 재고를 수주에 매칭. Returns: {"matched": int, "skipped": int, "details": list}

    Args:
        exclude_wip_ids: 증분 업데이트 시 동결된 배치에 매칭된 WIP ID set.
            이 WIP들은 이미 사용 중이므로 매칭 풀에서 제외한다.
        emergency_mode: True 면 Level 3 — "예상" 상태 WIP 까지 매칭 풀에 포함.
            False(기본) 면 Level 2 — "사용가능", "실사_확정", "실적_추정" 만 포함.
    """
    result = {"matched": 0, "skipped": 0, "details": []}

    # 판단 기준 로드
    criteria = {
        c.criteria_name: c.criteria_value for c in db.query(DecisionCriteria).all()
    }
    loss_limit = float(criteria.get("Loss 허용 한도", "8")) / 100
    shortage_tolerance = float(criteria.get("조장 부족 허용율", "5")) / 100

    # 사용 가능한 WIP 로드 (total_length_m 큰 순 — 큰 재고를 먼저 소진)
    # Cross-run policy: run_label 로 filter 하지 않음 (by design).
    # 이전 run 의 재공이 다음 run 에 매칭 가능.
    allowed = _LEVEL3_STATUSES if emergency_mode else _LEVEL2_STATUSES
    wip_query = db.query(WipInventory).filter(WipInventory.status.in_(allowed))
    # 증분 업데이트 시 동결 배치에 매칭된 WIP는 풀에서 제외
    if exclude_wip_ids:
        wip_query = wip_query.filter(WipInventory.wip_id.notin_(exclude_wip_ids))
    wip_items = wip_query.order_by(
        WipInventory.cross_section.desc(), WipInventory.total_length_m.desc()
    ).all()
    if not wip_items:
        return result

    # 이번 run의 수주 로드 (외주 포함 — 재공 활용 가능 수주는 외주 여부와 무관)
    orders = (
        db.query(SalesOrder)
        .filter(
            SalesOrder.run_label == run_label,
        )
        .all()
    )

    # 수주 정렬: 고객 우선순위 → 납기 → order_id (재현성)
    from datetime import date as _date

    orders.sort(
        key=lambda o: (
            o.customer_priority or 99,
            o.due_date or _date.max,
            o.order_id or "",
        )
    )

    # ── 같은 규격의 WIP을 풀(pool)로 묶기 ──
    # 연선재고: (SQ, process_stage, voltage_class) — 색상 무관
    # 절연재고: (SQ, process_stage, voltage_class, core_colors, product_name) — 색상+제품 구분
    from collections import OrderedDict

    wip_pools: OrderedDict[tuple, dict] = OrderedDict()
    for wip in wip_items:
        wip_sq = float(wip.cross_section) if wip.cross_section else None
        wip_total = float(wip.total_length_m or 0)
        wip_drum = float(wip.length_m or 0)
        if not wip_sq or wip_total <= 0 or wip_drum <= 0:
            continue
        stage = wip.process_stage or ""
        if "절연" in stage:
            # 절연재고: 색상+제품별 분리
            key = (
                wip_sq,
                stage,
                wip.voltage_class or "",
                wip.core_colors or "",
                wip.product_name or "",
            )
        else:
            # 연선재고: SQ만으로 풀링
            key = (wip_sq, stage, wip.voltage_class or "", "", "")
        if key not in wip_pools:
            wip_pools[key] = {"wips": [], "pool_total": 0.0, "max_drum": 0.0}
        wip_pools[key]["wips"].append(wip)
        wip_pools[key]["pool_total"] += wip_total
        wip_pools[key]["max_drum"] = max(wip_pools[key]["max_drum"], wip_drum)

    for pool_key, pool in wip_pools.items():
        pool_sq, pool_stage, pool_volt = pool_key[0], pool_key[1], pool_key[2]
        pool_wips: list = pool["wips"]
        pool_total: float = pool["pool_total"]
        max_drum: float = pool["max_drum"]

        # ── 후보 수주 필터링 (풀 단위) ──────────────────────────
        candidates: list[SalesOrder] = []
        for order in orders:
            if order.use_wip:
                continue

            order_sq = _extract_sq(order.spec_raw)
            if order_sq is None or abs(order_sq - pool_sq) > 0.01:
                continue

            if "절연" in pool_stage:
                # 절연재고: product_group 호환성 + 색상 매칭
                if not _product_group_matches(
                    pool_wips[0].product_name, order.product_group
                ):
                    continue
                # 색상이 있는 풀이면 수주 core_colors에 해당 색상 포함 여부 확인
                pool_color = pool_key[3] if len(pool_key) > 3 else ""
                if pool_color:
                    order_colors = order.core_colors or ""
                    if pool_color not in order_colors:
                        continue

            if pool_volt and order.voltage:
                order_volt = (
                    "저압"
                    if "0.6" in (order.voltage or "") or "1kV" in (order.voltage or "")
                    else "고압"
                )
                if pool_volt != order_volt:
                    continue

            order_drum_length = float(order.drum_length_m or order.ordered_qty_m or 0)
            if order_drum_length > 0:
                if max_drum < order_drum_length * (1 - loss_limit):
                    continue

            order_qty = float(order.ordered_qty_m or 0)
            if order_qty <= 0:
                continue

            candidates.append(order)

        if not candidates:
            continue

        # ── 풀 합산 총량으로 최적 조합 탐색 ──────────────────────────────
        matched_orders = _find_best_combo(candidates, pool_total, shortage_tolerance)

        if not matched_orders:
            continue

        # ── 매칭된 수주를 WIP 드럼에 순차 배분 ──
        # 큰 드럼부터 채우기 (잔여량 최소화)
        pool_wips_sorted = sorted(
            pool_wips, key=lambda w: float(w.total_length_m or 0), reverse=True
        )
        # 수주를 환산수량 내림차순으로 정렬 (큰 수주부터 큰 드럼에 배정)
        matched_orders.sort(
            key=lambda o: float(o.ordered_qty_m or 0) * max(int(o.core_count or 1), 1),
            reverse=True,
        )

        wip_remaining = {
            w.wip_id: float(w.total_length_m or 0) for w in pool_wips_sorted
        }

        for order in matched_orders:
            conv_qty = float(order.ordered_qty_m or 0) * max(
                int(order.core_count or 1), 1
            )
            # 잔여량이 충분한 첫 번째 드럼에 배정
            assigned_wip = None
            for w in pool_wips_sorted:
                if wip_remaining[w.wip_id] >= conv_qty:
                    assigned_wip = w
                    break
            # 충분한 드럼이 없으면 가장 잔여량 큰 드럼에 배정
            if assigned_wip is None:
                assigned_wip = max(
                    pool_wips_sorted, key=lambda w: wip_remaining[w.wip_id]
                )

            wip_remaining[assigned_wip.wip_id] -= conv_qty

            order.use_wip = True
            order.wip_type = pool_stage
            order.actual_length_m = float(order.ordered_qty_m or 0)
            order.wip_id = assigned_wip.wip_id

            result["matched"] += 1
            result["details"].append(
                {
                    "order_id": order.order_id,
                    "order_line": order.order_line,
                    "wip_id": assigned_wip.wip_id,
                    "wip_process": pool_stage,
                    "wip_sq": pool_sq,
                    "wip_total_m": pool_total,
                    "order_qty_m": float(order.ordered_qty_m or 0),
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
                            f"WIP풀 {pool_sq}SQ {pool_stage} "
                            f"총{pool_total}m → 수주 {order.order_id}:{order.order_line} "
                            f"({float(order.ordered_qty_m or 0)}m, 환산{conv_qty}m)"
                        ),
                    }
                ],
                reason=(
                    f"재공 매칭: {pool_stage} {pool_sq}SQ 풀{pool_total}m → "
                    f"{order.order_id}:{order.order_line}"
                ),
            )

        # ── WIP 상태 갱신 ──
        for w in pool_wips_sorted:
            used = float(w.total_length_m or 0) - wip_remaining[w.wip_id]
            if used > 0:
                w.status = "사용완료"
                # 대표 수주 ID 기록
                first_matched = next(
                    (o for o in matched_orders if o.wip_id == w.wip_id), None
                )
                if first_matched:
                    w.matched_order_id = (
                        f"{first_matched.order_id}:{first_matched.order_line}"
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
    # 다심 케이블(4C 등)은 환산수량(수량×코어수) 기준 — WIP는 연선(심선) 기준 재고
    qtys = [
        float(o.ordered_qty_m or 0) * max(int(o.core_count or 1), 1) for o in candidates
    ]

    # WIP 총량은 고정 — tolerance는 개별 드럼 길이 비교(호출측)에서 이미 적용됨
    # 조합 합계가 WIP 총량을 초과할 수 없음
    effective_cap = wip_total

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

        # 후검증: 이산화 오차로 실제 합계가 WIP 총량을 초과하면 가장 작은 수주부터 제거
        def _conv(o: SalesOrder) -> float:
            return float(o.ordered_qty_m or 0) * max(int(o.core_count or 1), 1)

        actual_total = sum(_conv(o) for o in selected)
        if actual_total > wip_total and selected:
            selected.sort(key=_conv)
            while actual_total > wip_total and selected:
                removed = selected.pop(0)
                actual_total -= _conv(removed)

        return selected


def _product_group_matches(
    wip_product_name: str | None, order_product_group: str | None
) -> bool:
    """WIP 제품명과 수주 제품군이 호환되는지 판별.

    wip_product_name 이 없거나 order_product_group 이 없으면 필터 없이 통과.
    """
    if not wip_product_name or not order_product_group:
        return True

    wpn = wip_product_name.strip()
    opg = order_product_group.strip()

    if wpn == "TFR-CV(WB)":
        return (
            opg == "TFR-CV" or opg.startswith("TFR-CV-WB") or opg.startswith("TFR-CV(")
        )
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
