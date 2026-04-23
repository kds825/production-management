"""CP-SAT model construction — pure function extracted from `cp_sat_schedule`.

Task 2A.2 (Production Handoff Refactor, Week 2): `cp_sat_schedule` 의 §6
"CP-SAT 모델 구성" 블록을 값-객체 기반 pure 함수 `build_model` 로 이전한다.

## Scope (Task 2A.2)

이 모듈은 다음 작업을 담당한다:

1. **Decision variable 생성** — `start_vars`, `end_vars`, `equip_vars`,
   `tardiness_vars`, `dur_vars` 을 `SolverInput.group_meta` 로부터 구성.
2. **Hard constraint 포스팅** —
   - frozen_group_keys 고정 (사전 쿼리된 스냅샷 사용)
   - no-overlap (설비별 optional interval)
   - 공정 선후관계 (연선→절연→시스)
   - CORE → ST 선행
   - sheath_color_hard (True 일 때)
3. **Soft constraint 항 생성** —
   - past-due tardiness (tardiness_hard 모드에서도 past 그룹은 soft)
   - 시스 색상 sequence-dependent setup (conditional gap)
   - 시스 end-sum tiebreak
   - on-time slack-weighted end
   - EDD pair penalty (normal + mixed past-due)
   - 연선 transition (SQ 전이 비용)
4. **Objective term 수집** — 위 soft 항의 IntVar 를 리스트로 모아
   `objective.compose_objective` 가 model.minimize(...) 로 합성하도록 전달.

## Boundary invariant (Spec §7)

이 모듈은 `app.infrastructure` 를 **직접 import 하지 않는다**. DB 접근이
필요한 `frozen_group_keys` 로직은 caller (`cp_sat_schedule`) 가 미리
스냅샷 dict (`frozen_tasks_snapshot`) 으로 변환해 주입한다.

사용 가능한 import:
  - `ortools.sat.python.cp_model`
  - `app.domain.constants` (읽기 전용)
  - `app.services.solver.*`
  - `app.services.schedule_optimizer` 의 pure helper (`_is_core_group`,
    `_extract_core_main_sq`, `_st_sq`) — 이 helper 들은 schedule_optimizer
    가 infrastructure 를 import 하지만 symbol 자체는 pure function.
  - `app.services.constraint_params`, `app.services.sheath_cluster`
  - stdlib

## Week 5 migration note

`_TARDINESS_WEIGHT`, `_CHAIN_WEIGHT`, `_IDLE_WEIGHT`, `_SLACK_WEIGHT_BASE`,
`_EDD_PAIR_WEIGHT`, `_EDD_MIXED_PASTDUE_WEIGHT`, `_TRANSITION_WEIGHT`,
`_MAX_HORIZON_MIN` 등의 constant 는 아직 `cp_sat_optimizer.py` 에 상수로
존재한다 (Week 5 에서 `ConstraintSpec.weight` 로 마이그레이션). Week 2
에서는 `ModelWeights` dataclass 에 담아 caller 가 넘겨준다 — parity 보존.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ortools.sat.python import cp_model

from app.services.constraint_params import ConstraintParams
from app.services.solver.input_builder import SolverInput


@dataclass(frozen=True)
class ModelWeights:
    """`build_model` 이 penalty / slack / EDD 항에 사용하는 가중치 번들.

    Week 2 에서는 `cp_sat_optimizer.py` 모듈 상수를 그대로 전달해 parity 를
    보존한다. Week 5 에서 `ConstraintSpec.weight` 기반으로 교체된다.
    """

    # Penalty / priority
    DUE_HARD_WEIGHT: int
    TARDINESS_WEIGHT: dict[str, int]
    CHAIN_WEIGHT: int
    IDLE_WEIGHT: int
    SLACK_WEIGHT_BASE: int
    PAST_SEVERITY_K: int
    EDD_PAIR_WEIGHT: int
    EDD_MIXED_PASTDUE_WEIGHT: int
    TRANSITION_WEIGHT: int

    # Horizon / time axis
    MAX_HORIZON_MIN: int
    WORK_MIN_PER_DAY: int


@dataclass
class BuiltModel:
    """`build_model` 반환 값 — solver 실행과 스냅샷 기록에 필요한 모든 변수.

    `cp_sat_schedule` 은 이 객체를 받아 `objective.compose_objective` 로
    model.minimize(...) 를 호출하고, solver.solve(model) 후 `_write_solver_snapshot`
    에 각 필드를 전달한다.
    """

    model: cp_model.CpModel
    groups: list[str]
    all_eq_codes: list[str]

    # Decision variables
    start_vars: dict[str, cp_model.IntVar]
    end_vars: dict[str, cp_model.IntVar]
    equip_vars: dict[str, dict[str, cp_model.IntVar]]
    tardiness_vars: dict[str, cp_model.IntVar]
    dur_vars: dict[str, Any]
    itv_vars: dict[tuple[str, str], Any]

    # Objective-term IntVar lists (for snapshot + compose_objective)
    idle_terms: list[Any]
    transition_terms: list[Any]
    sheath_end_terms: list[Any]
    slack_terms: list[Any]
    slack_terms_meta: list[tuple[str, int, Any]]
    edd_pair_terms: list[Any]
    edd_mixed_pastdue_terms: list[Any]

    # Warm-start result (caller records into result["warm_start_applied/skipped"])
    warm_start_applied: int
    warm_start_skipped: int

    # Task 2A.2 plan 요구사항: trace_writer 용 dict (Week 2-3 trace_writer 에서 소비)
    penalty_vars: dict[str, cp_model.IntVar] = field(default_factory=dict)
    hard_literals: dict[str, cp_model.IntVar] = field(default_factory=dict)


def build_model(
    solver_input: SolverInput,
    *,
    weights: ModelWeights,
    random_seed: int,
    frozen_group_keys: set[str] | None = None,
    frozen_tasks_snapshot: dict[str, dict[str, Any]] | None = None,
    sheath_color_hard: bool = True,
    tardiness_hard: bool = True,
    warm_start_hints: dict[str, dict] | None = None,
    base_date: datetime,
    constraint_params: ConstraintParams,
    warnings_out: list[str] | None = None,
) -> BuiltModel:
    """CP-SAT 모델을 구성해 `BuiltModel` 로 반환.

    이 함수는 **pure** 다 — DB 접근, 파일 I/O, 로깅 없음. `frozen_tasks_snapshot`
    은 caller 가 DB 에서 미리 로드한 `{batch_group: {"start_wmin": int,
    "equipment_code": str}}` dict 이어야 한다.

    Args:
        solver_input: `build_solver_input` 결과물. `group_meta`, `groups`,
            `equipment_by_process`, `sq_to_wire_d` 를 사용.
        weights: `ModelWeights` 번들 — 가중치/horizon 상수.
        random_seed: solver 에 전달될 값 (모델 구성엔 쓰이지 않지만 결정론
            보존을 위해 시그니처로 명시).
        frozen_group_keys: 재최적화 고정 대상 batch_group 집합.
        frozen_tasks_snapshot: frozen_group_keys 각 그룹의 `{start_wmin,
            equipment_code}`. None 이면 frozen 처리 skip.
        sheath_color_hard: True 면 §6-f-hard 시스 색상 체인 hard constraint 포스팅.
        tardiness_hard: True 면 납기 hard constraint, False 면 soft.
        warm_start_hints: `{batch_group: {start_wmin, equipment_code}}` 형식.
        base_date: horizon 기준 datetime (현재는 logging only 로 쓰이지 않음).
        constraint_params: ConstraintConfig 4-2 색상 교체 시간 조회용.
        warnings_out: caller result["warnings"] 에 append 될 메시지 수집용.
            None 이면 warning 을 버린다 (snapshot 재현만 할 때 사용).

    Returns:
        BuiltModel — solver.solve(model) 에 넘기고 compose_objective 가 소비.
    """
    raise NotImplementedError("Task 2A.2 sub-commit 2 에서 구현")
