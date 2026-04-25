"""Lexicographic min-time solver — 납기 우선 + 그 안에서 makespan 최소.

사용자 요구 #4: "납기를 반드시 준수하면서 최소시간(makespan) 해를 구하는
lexicographic 최적화".

## 설계 동기

기존 ``compose_objective`` 는 weighted-sum:
  obj = w_tard × Σ tardiness + w_idle × Σ idle + w_edd × Σ edd_pair + ...

가중치 비율에 따라 trade-off 가 달라지고, 운영자가 "납기 충족 vs 최소 시간"
을 수치 비율로 표현하기 어렵다. Lexicographic 은 "납기 먼저, 그 다음 시간"
이라는 도메인 언어와 1:1 대응한다.

## 알고리즘

```
Phase A — minimize max_tardiness:
  max_tard = max(tardiness_vars[g] for g in groups)
  → 최적값 T*
  Case T* = 0  → 모든 납기 충족 가능 ★
  Case T* > 0  → 일부 납기 초과 불가피, T* 가 최소 가능 max-tardy

Phase B — minimize makespan within T*:
  add constraint max_tard ≤ T*    # Phase A 결과를 hard 로 고정
  makespan = max(end_vars[g] for g in groups)
  → 같은 납기 만족도 안에서 가장 짧은 일정
```

## 사용

```python
from app.services.solver.lex_min_time import solve_lex_min_time
from app.services.solver.model_builder import build_model

built = build_model(...)
result = solve_lex_min_time(built)
if result.status in ("OPTIMAL", "FEASIBLE"):
    # solver.Value(start_vars[gk]) 등으로 해 추출
    print(f"T* = {result.t_star} min, makespan = {result.makespan_min} min")
```

## cp_sat_schedule 와의 wiring (별도 작업)

본 모듈은 stand-alone. ``cp_sat_schedule`` 의 weighted-sum 경로는 보존
(parity tests 가 그 결과를 동결). 사용자가 ``min_time_mode=True`` 를 명시할
때만 본 경로를 호출하도록 opt-in flag 도입은 별도 phase 에서 처리.

## 한계

- model 객체를 mutate 한다 (Phase B 의 ``max_tard ≤ T*`` constraint 추가).
  같은 ``BuiltModel`` 을 재사용하려면 caller 가 ``copy.deepcopy`` 가 필요.
- ``add_max_equality`` 는 input 이 비어있으면 동작하지 않으므로 빈 리스트
  방어 필요.
- Phase B 의 time limit 은 Phase A 와 독립 — A 가 빨리 끝나도 B 는 별도 수
  계산 시간을 받는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ortools.sat.python import cp_model

from app.services.solver.model_builder import BuiltModel

LexStatus = Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE_A", "INFEASIBLE_B", "UNKNOWN"]


@dataclass
class LexResult:
    """``solve_lex_min_time`` 반환 값.

    Attributes
    ----------
    status :
        ``"OPTIMAL"``  : Phase B 가 OPTIMAL 까지 도달
        ``"FEASIBLE"`` : Phase B 가 time-limit 내 FEASIBLE 만 (sub-optimal)
        ``"INFEASIBLE_A"`` : Phase A (납기 최소화) 가 해 없음 → 모델 자체 INFEASIBLE
        ``"INFEASIBLE_B"`` : Phase B 가 max_tard ≤ T* 추가 후 깨짐 (이론상 불가
            능 — Phase A solution 자체가 valid B-feasible 이므로 fallback 검증용)
        ``"UNKNOWN"`` : solver 가 시간 내에 어떤 결정도 못 내림
    t_star :
        Phase A 의 최적값 ``min(max_tardiness)``. 0 이면 모든 납기 충족.
        ``status="INFEASIBLE_A"`` 또는 tardiness_vars 가 비었을 때 0.
    makespan_min :
        Phase B 의 최적값. status 가 OPTIMAL/FEASIBLE 일 때만 의미 있음.
    all_due_met :
        ``t_star == 0`` 의 alias (가독성).
    solver :
        Phase B 의 solver 인스턴스. caller 가 ``solver.Value(var)`` 로 해를 읽음.
        status 가 INFEASIBLE_* 이면 None.
    """

    status: LexStatus
    t_star: int
    makespan_min: int
    all_due_met: bool
    solver: cp_model.CpSolver | None


def solve_lex_min_time(
    built: BuiltModel,
    *,
    time_limit_phase_a_sec: int = 20,
    time_limit_phase_b_sec: int = 20,
    num_workers: int = 8,
    random_seed: int = 1,
) -> LexResult:
    """납기 충족 우선, 그 안에서 makespan 최소화.

    ``built.model`` 은 mutate 됨 — Phase B 가 ``max_tard ≤ T*`` constraint 추가.
    같은 BuiltModel 을 재사용하려면 caller 가 deep-copy 후 호출.
    """
    model = built.model

    # ── Phase A: minimize max_tardiness ──────────────────────────────────
    # tardiness_vars 가 비어있으면 max_tard = 0 으로 간주 (모든 그룹이 납기 없거나
    # tardiness_hard=True 에서 due_wmin ≥ 0 인 hard constraint 만으로 처리).
    horizon = _infer_horizon(built)
    max_tard = model.new_int_var(0, 2 * horizon, "lex_max_tardiness")
    if built.tardiness_vars:
        model.add_max_equality(max_tard, list(built.tardiness_vars.values()))
    else:
        model.add(max_tard == 0)

    model.minimize(max_tard)
    # Phase A 와 Phase B 는 같은 solver 인스턴스 재사용 — 새 solver 를 매번
    # 생성하면 Phase A 의 search state (best bound, learned clauses) 를 잃어
    # Phase B 가 INFEASIBLE 로 잘못 보고하는 케이스 관찰 (2026-04 PoC).
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_phase_a_sec)
    solver.parameters.num_search_workers = num_workers
    solver.parameters.random_seed = random_seed
    status_a = solver.solve(model)

    if status_a not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return LexResult(
            status="INFEASIBLE_A",
            t_star=0,
            makespan_min=0,
            all_due_met=False,
            solver=None,
        )
    t_star = solver.value(max_tard)

    # ── Phase B: fix max_tard ≤ T*, minimize makespan ─────────────────────
    model.add(max_tard <= t_star)

    if not built.end_vars:
        # 그룹 0 — degenerate case. Phase A solution 그대로 반환.
        return LexResult(
            status="OPTIMAL" if status_a == cp_model.OPTIMAL else "FEASIBLE",
            t_star=t_star,
            makespan_min=0,
            all_due_met=t_star == 0,
            solver=solver,
        )

    makespan = model.new_int_var(0, 2 * horizon, "lex_makespan")
    model.add_max_equality(makespan, list(built.end_vars.values()))

    model.minimize(makespan)
    solver.parameters.max_time_in_seconds = float(time_limit_phase_b_sec)
    status_b = solver.solve(model)

    if status_b == cp_model.OPTIMAL:
        return LexResult(
            status="OPTIMAL",
            t_star=t_star,
            makespan_min=solver.value(makespan),
            all_due_met=t_star == 0,
            solver=solver,
        )
    if status_b == cp_model.FEASIBLE:
        return LexResult(
            status="FEASIBLE",
            t_star=t_star,
            makespan_min=solver.value(makespan),
            all_due_met=t_star == 0,
            solver=solver,
        )
    if status_b == cp_model.INFEASIBLE:
        # 이론상 불가능 (Phase A 결과가 valid B-feasible) — solver 버그 또는
        # max_tard ≤ T* 추가 시 numerical edge. fallback 으로 INFEASIBLE_B 보고.
        return LexResult(
            status="INFEASIBLE_B",
            t_star=t_star,
            makespan_min=0,
            all_due_met=t_star == 0,
            solver=None,
        )
    return LexResult(
        status="UNKNOWN",
        t_star=t_star,
        makespan_min=0,
        all_due_met=t_star == 0,
        solver=None,
    )


def _infer_horizon(built: BuiltModel) -> int:
    """end_vars 의 최대 upper-bound 를 horizon 으로 추정.

    Why: max_tard / makespan IntVar 의 domain 을 어디서 받아오는가? BuiltModel
    이 ModelWeights.MAX_HORIZON_MIN 을 직접 노출하지 않으므로 end_vars 첫
    원소의 domain 으로 안전 추정. fallback 90일 × 1440분 = 129600.

    Note: ``proto.domain`` 은 protobuf RepeatedScalarFieldContainer — negative
    indexing 시 0 반환 (silent bug). 반드시 ``list()`` 후 indexing.
    """
    for v in built.end_vars.values():
        domain_list = list(v.proto.domain)
        if domain_list:
            # domain 은 [lb, ub] 페어의 flat list. 마지막 element 가 최대 ub.
            return int(domain_list[-1])
    return 90 * 1440
