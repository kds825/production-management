"""On-time 그룹 slack-weighted completion soft penalty.

ConstraintConfig 매핑: 1-1 tardiness 의 soft 분기 (납기 임박 우선 정렬).
원래 ``model_builder.build_model`` §6-g-slack 블록.

핵심:
  - on-time 그룹 (due_wmin > 0, < MAX_HORIZON) 각각에 ``w × end`` 항을 추가.
  - w = SLACK_WEIGHT_BASE // due_wmin → 임박할수록 가중치 큼.
  - past-due 는 tardiness_vars × TARDINESS_WEIGHT 가 별도 처리 → 중복 방지.
  - no-due (due_wmin == MAX_HORIZON) 은 skip.

Why 진단용 slack_terms_meta:
  trace_writer / decision_aggregator 가 ``(gk, weight, end_var)`` 튜플로
  기여도를 사후 분해할 수 있어야 한다.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model

# S1 #2: 임박 그룹 (≤ 5 working day) 의 slack weight step boost.
# 5 일 × 14 시간 × 60 분 = 4200 working minutes.
_SLACK_URGENT_THRESHOLD_WMIN = 5 * 14 * 60
_SLACK_URGENT_MULTIPLIER = 10


def collect_slack_terms(
    *,
    group_meta: dict[str, Any],
    end_vars: dict[str, cp_model.IntVar],
    slack_weight_base: int,
    max_horizon_min: int,
) -> tuple[list[Any], list[tuple[str, int, cp_model.IntVar]]]:
    """§6-g-slack. Returns ``(slack_terms, slack_terms_meta)``.

    S1 #2: 임박 (≤ threshold) 그룹은 step boost — greedy 의 slack_step
    tiebreak 와 동일 정신. 임박 외에는 기존 continuous weight 유지.
    """
    slack_terms: list[Any] = []
    slack_terms_meta: list[tuple[str, int, cp_model.IntVar]] = []
    for gk, meta in group_meta.items():
        due = meta["due_wmin"]
        if due == max_horizon_min:
            continue
        if due < 0:
            continue
        slack_min = max(1, int(due))
        if slack_min <= _SLACK_URGENT_THRESHOLD_WMIN:
            w = max(1, slack_weight_base * _SLACK_URGENT_MULTIPLIER)
        else:
            w = max(1, slack_weight_base // slack_min)
        slack_terms.append(w * end_vars[gk])
        slack_terms_meta.append((gk, w, end_vars[gk]))
    return slack_terms, slack_terms_meta
