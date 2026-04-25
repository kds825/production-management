"""규칙 기반 리스크 감지 — LLM 보완/폴백용 pure 룰.

Why 분리: `application/decisions/summarize_run.py` 가 LLM 성공/실패와 무관하게
항상 본 룰셋으로 리스크를 감지해 최종 highlights 에 병합한다. 본 모듈은 DB
조회 / I/O 의존이 0 (입력 = ProductionBatch 시퀀스, 출력 = 카운트 + 한국어
하이라이트). LLM 코드 경로와 분리되어 단위 테스트가 결정론적이다.

직전 위치: `services/llm_explainer.py::_detect_rule_based_risks` (Phase 2
step 1 에서 분리, 동작 동일).

리스크 카테고리 (4 종):
  1. 납기 임박: due_date - today ≤ 3일
  2. 설비 과부하: 특정 설비 배치 수 > 평균 × 2
  3. SQ 교체 빈도: 동일 설비 연속 배치의 SQ 변동률 > 50%
  4. 재공 미매칭: WIP 매칭 가능 batch 중 미활용 비율 > 30%
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as _date
from typing import Iterable


def _detect_rule_based_risks(batches: Iterable) -> tuple[int, list[str]]:
    """규칙 기반 리스크 감지 — LLM 보완/폴백용.

    Returns (riskCount, risk_highlights).
    """
    risk_count = 0
    risk_highlights: list[str] = []
    today = _date.today()
    batches = list(batches)

    # --- 1) 납기 임박: due_date - today <= 3일 ---
    urgent_count = 0
    for b in batches:
        if b.due_date:
            dd = (
                b.due_date
                if isinstance(b.due_date, _date)
                else _date.fromisoformat(str(b.due_date))
            )
            if (dd - today).days <= 3:
                urgent_count += 1
    if urgent_count > 0:
        risk_count += 1
        risk_highlights.append(f"긴급 납기: {urgent_count}건의 배치가 3일 이내 납기")

    # --- 2) 설비 과부하: 특정 설비 배치 수 > 평균의 2배 ---
    equip_counts: dict[str, int] = defaultdict(int)
    for b in batches:
        code = b.equipment_code or "미배정"
        equip_counts[code] += 1
    if equip_counts:
        avg = sum(equip_counts.values()) / len(equip_counts)
        for code, cnt in equip_counts.items():
            if avg > 0 and cnt > avg * 2:
                risk_count += 1
                risk_highlights.append(
                    f"병목 설비: {code}에 배치 집중 ({cnt}건, 평균 {avg:.0f}건)"
                )

    # --- 3) SQ 교체 빈도: 동일 설비에서 연속 배치의 SQ가 다른 비율 > 50% ---
    equip_batches: dict[str, list] = defaultdict(list)
    for b in batches:
        if b.equipment_code:
            equip_batches[b.equipment_code].append(b)
    for code, eq_batches in equip_batches.items():
        if len(eq_batches) < 2:
            continue
        changes = 0
        for i in range(1, len(eq_batches)):
            if eq_batches[i].sq_mm2 != eq_batches[i - 1].sq_mm2:
                changes += 1
        rate = changes / (len(eq_batches) - 1) * 100
        if rate > 50:
            risk_count += 1
            risk_highlights.append(f"교체 손실: {code}에서 SQ 교체율 {rate:.0f}%")

    # --- 4) 재공 미매칭: WIP 매칭 가능하지만 미활용 비율 > 30% ---
    wip_total = sum(
        1 for b in batches if b.wip_matched_id is not None or b.wip_matched_id == 0
    )
    wip_unused = sum(1 for b in batches if b.wip_matched_id is None)
    # wip_total은 매칭된 건수, 전체에서 미매칭 비율 계산
    total = len(batches)
    if total > 0 and wip_total > 0:
        unused_ratio = wip_unused / total
        if unused_ratio > 0.3:
            risk_count += 1
            risk_highlights.append(
                f"재공 활용도: 매칭 가능 {total}건 중 {wip_unused}건 미활용"
            )

    return risk_count, risk_highlights
