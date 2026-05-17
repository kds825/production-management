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
from app.application.scheduling.cp_sat.lex_min_time import solve_lex_min_time
from app.application.scheduling.cp_sat.model_builder import build_model

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

import logging
import time as _time  # noqa: F401  formatter 의 unused-import sweep 회피 (Phase A/B/C timing instrumentation)
from dataclasses import dataclass
from typing import Literal

from ortools.sat.python import cp_model

from app.application.scheduling.cp_sat.model_builder import BuiltModel

logger = logging.getLogger(__name__)

LexStatus = Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE_A", "INFEASIBLE_B", "UNKNOWN"]

# Phase C (W-* soft objective 최소화) status. ``"SKIPPED"`` 는 caller 가 weights/
# group_meta 를 전달하지 않은 경우 — phase C 자체를 시도하지 않음 (기존 2-phase
# 와 동일 동작). ``"FAILED_FALLBACK_TO_B"`` 는 Phase C 가 INFEASIBLE/UNKNOWN 으로
# 떨어져 Phase B 해로 복원한 케이스 (이론상 매우 드묾).
LexPhaseCStatus = Literal["SKIPPED", "OPTIMAL", "FEASIBLE", "FAILED_FALLBACK_TO_B"]


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
        Phase B (또는 Phase C) 의 solver 인스턴스. caller 가 ``solver.Value(var)``
        로 해를 읽음. status 가 INFEASIBLE_* 이면 None.
    phase_c_status :
        Phase C (W-* soft objective 최소화) 의 결과. ``"SKIPPED"`` 면 caller 가
        weights/group_meta 를 전달하지 않아 Phase C 자체 미실행 (UI 슬라이더 보존
        의무가 없는 호출자 — 예: parity harness). ``"OPTIMAL"``/``"FEASIBLE"`` 은
        Phase C 가 성공해 solver 의 해가 lex C-minimal 임을 의미.
        ``"FAILED_FALLBACK_TO_B"`` 는 Phase C 가 INFEASIBLE/UNKNOWN 으로 떨어져
        Phase B 해로 복원한 경우.
    """

    status: LexStatus
    t_star: int
    makespan_min: int
    all_due_met: bool
    solver: cp_model.CpSolver | None
    phase_c_status: LexPhaseCStatus = "SKIPPED"


def solve_lex_min_time(
    built: BuiltModel,
    *,
    time_limit_phase_a_sec: int = 20,
    time_limit_phase_b_sec: int = 20,
    time_limit_phase_c_sec: int = 5,
    num_workers: int = 8,
    random_seed: int = 1,
    weights: ModelWeights | None = None,
    group_meta: dict[str, Any] | None = None,
    tardiness_hard: bool = False,
) -> LexResult:
    """납기 충족 우선, 그 안에서 makespan 최소화, 그 안에서 W-* soft term 최소화.

    ``built.model`` 은 mutate 됨 — Phase B 가 ``max_tard ≤ T*`` constraint,
    Phase C (옵션) 가 ``makespan ≤ M*`` constraint 추가. 같은 BuiltModel 을
    재사용하려면 caller 가 deep-copy 후 호출.

    Phase C 는 caller 가 ``weights`` 와 ``group_meta`` 를 함께 전달할 때만 실행
    된다 (UI 의 W-* priority 슬라이더 → ConstraintSpec.weight × priority/50
    의미를 lex 경로에서도 보존). 둘 다 ``None`` 이면 기존 2-phase 동작과 동일
    하며 parity hash 가 흔들리지 않는다.
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
    _ta = _time.perf_counter()
    status_a = solver.solve(model)
    _phase_a_wall = _time.perf_counter() - _ta
    logger.info(
        "lex Phase A: status=%s t_star=%s wall=%.3fs limit=%ds workers=%d",
        solver.status_name(status_a),
        solver.value(max_tard)
        if status_a in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        else "n/a",
        _phase_a_wall,
        time_limit_phase_a_sec,
        num_workers,
    )

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
    _tb = _time.perf_counter()
    status_b = solver.solve(model)
    _phase_b_wall = _time.perf_counter() - _tb
    logger.info(
        "lex Phase B: status=%s makespan=%s wall=%.3fs limit=%ds",
        solver.status_name(status_b),
        solver.value(makespan)
        if status_b in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        else "n/a",
        _phase_b_wall,
        time_limit_phase_b_sec,
    )

    if status_b not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Phase B 실패 — INFEASIBLE_B 는 이론상 불가능 (Phase A 결과가 valid),
        # UNKNOWN 은 time-limit hit. 어느 쪽이든 caller 가 weighted-sum 으로
        # 폴백하도록 INFEASIBLE_B / UNKNOWN 으로 분기.
        if status_b == cp_model.INFEASIBLE:
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

    m_star = solver.value(makespan)
    base_status: LexStatus = "OPTIMAL" if status_b == cp_model.OPTIMAL else "FEASIBLE"

    # ── Phase C (옵션): max_tard ≤ T*, makespan ≤ M* 하에서 W-* soft term 최소화 ──
    # caller 가 weights/group_meta 를 전달한 경우에만 실행. ``compose_objective``
    # 는 idle/transition/sheath_end/slack/edd_pair/edd_mixed_pastdue/tardiness
    # term 을 W-* 가중치로 합성해 ``model.minimize(_obj)`` 로 새 objective 를
    # set 한다 (이전 Phase B 의 minimize(makespan) 은 덮어쓰여진다). Phase B 의
    # makespan ≤ M* 은 hard constraint 로 박혀 있으므로 lex 우선순위 보존.
    if weights is not None and group_meta is not None:
        from app.application.scheduling.cp_sat.objective import compose_objective

        model.add(makespan <= m_star)
        compose_objective(
            built,
            weights=weights,
            group_meta=group_meta,
            tardiness_hard=tardiness_hard,
        )
        solver.parameters.max_time_in_seconds = float(time_limit_phase_c_sec)
        _tc = _time.perf_counter()
        status_c = solver.solve(model)
        _phase_c_wall = _time.perf_counter() - _tc
        logger.info(
            "lex Phase C: status=%s wall=%.3fs limit=%ds",
            solver.status_name(status_c),
            _phase_c_wall,
            time_limit_phase_c_sec,
        )
        if status_c in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return LexResult(
                status=(
                    "OPTIMAL"
                    if base_status == "OPTIMAL" and status_c == cp_model.OPTIMAL
                    else "FEASIBLE"
                ),
                t_star=t_star,
                makespan_min=m_star,
                all_due_met=t_star == 0,
                solver=solver,
                phase_c_status=(
                    "OPTIMAL" if status_c == cp_model.OPTIMAL else "FEASIBLE"
                ),
            )
        # Phase C 가 INFEASIBLE/UNKNOWN — 이론상 makespan ≤ M* + 모든 hard 제약
        # 하에 Phase B 해가 valid 이므로 INFEASIBLE 은 솔버 numerical edge.
        # 안전 복구: minimize(makespan) 으로 objective 복원 + 재솔브. solver 가
        # makespan == M* 인 해를 즉시 찾는다 (hard 로 박혀 있으므로 거의 0 cost).
        model.minimize(makespan)
        solver.parameters.max_time_in_seconds = float(time_limit_phase_b_sec)
        status_recover = solver.solve(model)
        if status_recover in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return LexResult(
                status="FEASIBLE",
                t_star=t_star,
                makespan_min=solver.value(makespan),
                all_due_met=t_star == 0,
                solver=solver,
                phase_c_status="FAILED_FALLBACK_TO_B",
            )
        # 복구도 실패 — 매우 이상한 상황. caller 가 weighted-sum 폴백 트리거.
        return LexResult(
            status="INFEASIBLE_B",
            t_star=t_star,
            makespan_min=0,
            all_due_met=t_star == 0,
            solver=None,
        )

    # Phase C skip — 기존 2-phase 결과 그대로.
    return LexResult(
        status=base_status,
        t_star=t_star,
        makespan_min=m_star,
        all_due_met=t_star == 0,
        solver=solver,
    )


@dataclass
class AdaptedSolveResult:
    """orchestrator §7 가 두 path (weighted-sum / lex) 에서 동일하게 소비하는
    결과 shape. weighted-sum 은 ``cp_model.CpSolver().solve(model)`` 직접
    호출로 (solver, status, wall_s) 를 만들고, lex 는 본 모듈의
    ``_adapt_lex_to_solve_result`` 로 만든다.

    필드 의미:
        solver      — ``CpSolver`` 인스턴스 (``solver.value(var)`` 호출 가능).
        status      — ``cp_model`` 상수 (OPTIMAL / FEASIBLE).
        wall_s      — 솔브 elapsed seconds (계측용).
        built       — ``BuiltModel``. lex path 는 deepcopy 본을 가리킴 (vars
                       가 ``solver`` 와 같은 protobuf 식별자).
        lex_t_star / lex_makespan_min / lex_all_due_met — lex 결과 메트릭.
                       weighted-sum path 는 모두 ``None``.
        mode        — ``"lex_min_time"`` / ``"weighted_sum"`` /
                       ``"weighted_sum_fallback_from_lex"``.
    """

    solver: cp_model.CpSolver
    status: int
    wall_s: float
    built: BuiltModel
    lex_t_star: int | None = None
    lex_makespan_min: int | None = None
    lex_all_due_met: bool | None = None
    mode: str = "weighted_sum"


def _adapt_lex_to_solve_result(
    lex_res: LexResult,
    lex_built: BuiltModel,
    wall_s: float,
) -> AdaptedSolveResult:
    """``LexResult`` 를 orchestrator 가 소비하는 ``AdaptedSolveResult`` 로 변환.

    호출 전제: ``lex_res.status ∈ {"OPTIMAL", "FEASIBLE"}``. INFEASIBLE_A/B/
    UNKNOWN 은 caller 가 미리 거른 후 weighted-sum 폴백을 처리한다.

    같은 ``ScheduleTask`` insert 경로 사용 — ``lex_built`` 의 vars 와
    ``lex_res.solver`` 가 동일한 protobuf 식별자를 가리키므로 §8 캘린더
    그리디는 ``solver.value(start_vars[gk])`` 로 읽을 수 있다.
    """
    if lex_res.status not in ("OPTIMAL", "FEASIBLE"):
        raise ValueError(
            f"_adapt_lex_to_solve_result expects OPTIMAL/FEASIBLE, "
            f"got {lex_res.status!r} (caller must filter INFEASIBLE_* / UNKNOWN "
            f"and trigger weighted-sum fallback explicitly)"
        )
    if lex_res.solver is None:
        raise ValueError(
            "lex_res.solver must be present on OPTIMAL/FEASIBLE — None signals "
            "internal inconsistency in solve_lex_min_time"
        )
    return AdaptedSolveResult(
        solver=lex_res.solver,
        status=cp_model.OPTIMAL if lex_res.status == "OPTIMAL" else cp_model.FEASIBLE,
        wall_s=wall_s,
        built=lex_built,
        lex_t_star=lex_res.t_star,
        lex_makespan_min=lex_res.makespan_min,
        lex_all_due_met=lex_res.all_due_met,
        mode="lex_min_time",
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
