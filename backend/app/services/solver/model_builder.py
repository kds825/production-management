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
  - `app.application._shared.constraint_params`, `app.domain.sheath_cluster`,
    `app.domain.constraint_rules`
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

from app.application._shared.constraint_params import ConstraintParams
from app.services.solver.constraints.global_.decision_vars import (  # noqa: F401  # used at §6-b
    DecisionVars,
    add_decision_vars,
)
from app.services.solver.constraints.global_.frozen_pins import (  # noqa: F401  # used at §6-b-2
    apply_frozen_pins,
)
from app.services.solver.constraints.global_.idle_terms import (  # noqa: F401  # used at §6-f
    collect_idle_terms,
)
from app.services.solver.constraints.global_.no_overlap import (  # noqa: F401  # used at §6-c
    add_equipment_no_overlap,
)
from app.services.solver.constraints.global_.predecessor import (  # noqa: F401  # used at §6-d/§6-e
    add_core_st_precedence,
    add_predecessor_precedence,
    compute_proc_groups_by_sq,
)
from app.services.solver.constraints.global_.slack_terms import (  # noqa: F401  # used at §6-g-slack
    collect_slack_terms,
)
from app.services.solver.constraints.process.edd_pair import (  # noqa: F401  # used at §6-h
    collect_edd_pair_terms,
)
from app.services.solver.constraints.process.sheath_color_hard import (  # noqa: F401  # used at §6-f-hard
    add_sheath_color_hard_chain,
)
from app.services.solver.constraints.process.sheath_color_sequence import (  # noqa: F401  # used at §6-g+§6-g-tiebreak
    add_sheath_color_sequence_and_tiebreak,
)
from app.services.solver.constraints.process.transition import (  # noqa: F401  # used at Round-2-transition
    collect_transition_terms,
)
from app.services.solver.constraints.global_.warm_start import (  # noqa: F401  # used at §6-b2
    apply_warm_start_hints,
)


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
    *,
    group_meta: dict[str, Any],
    equipment_by_process: dict[str, list[Any]],
    weights: ModelWeights,
    constraint_params: ConstraintParams,
    random_seed: int,
    frozen_group_keys: set[str] | None = None,
    frozen_tasks_snapshot: dict[str, dict[str, Any]] | None = None,
    sheath_color_hard: bool = True,
    tardiness_hard: bool = True,
    warm_start_hints: dict[str, dict] | None = None,
    base_date: datetime | None = None,
    warnings_out: list[str] | None = None,
) -> BuiltModel:
    """CP-SAT 모델을 구성해 `BuiltModel` 로 반환.

    이 함수는 **pure** 다 — DB 접근, 파일 I/O, 로깅 없음. `frozen_tasks_snapshot`
    은 caller 가 DB 에서 미리 로드한 `{batch_group: {"start_wmin": int,
    "equipment_code": str}}` dict 이어야 한다.

    Args:
        group_meta: `cp_sat_schedule` §5 에서 만들어진 그룹별 메타 dict.
            키 형식은 기존과 동일 (cpsat_dur, earliest_due, due_wmin, eligible,
            rep, weight, sq, per_eq_dur_enabled, cpsat_dur_by_eq, batches 등).
        equipment_by_process: `{process_name: [EquipmentMaster, ...]}`.
            `SolverInput.equipment_by_process` 와 동일. 타입은 ORM 클래스를
            직접 참조하지 않기 위해 `list[Any]` 로 잡는다 (경계 불변식).
        weights: `ModelWeights` 번들 — 가중치/horizon 상수.
        constraint_params: ConstraintConfig 4-2 색상 교체 시간 조회용.
        random_seed: solver 에 전달될 값 (모델 구성엔 쓰이지 않지만 결정론
            보존을 위해 시그니처로 명시).
        frozen_group_keys: 재최적화 고정 대상 batch_group 집합.
        frozen_tasks_snapshot: frozen_group_keys 각 그룹의 `{start_wmin,
            equipment_code}`. None 이면 frozen 처리 skip.
        sheath_color_hard: True 면 §6-f-hard 시스 색상 체인 hard constraint 포스팅.
        tardiness_hard: True 면 납기 hard constraint, False 면 soft.
        warm_start_hints: `{batch_group: {start_wmin, equipment_code}}` 형식.
        base_date: horizon 기준 datetime (현재는 logging only 로 쓰이지 않음).
        warnings_out: caller result["warnings"] 에 append 될 메시지 수집용.
            None 이면 warning 을 버린다 (snapshot 재현만 할 때 사용).

    Returns:
        BuiltModel — solver.solve(model) 에 넘기고 compose_objective 가 소비.
    """
    # Parity 보존을 위해 `cp_sat_schedule` §6 의 로컬 alias 를 그대로 사용.
    _MAX_HORIZON_MIN = weights.MAX_HORIZON_MIN

    _warnings: list[str] = warnings_out if warnings_out is not None else []

    # ── 6. CP-SAT 모델 구성 ───────────────────────────────────────────────
    model = cp_model.CpModel()
    groups = list(group_meta.keys())

    # 6-a. 설비 유니버스
    all_eq_codes = sorted(
        {e.equipment_code for eqs in equipment_by_process.values() for e in eqs}
    )

    # 6-b. 결정변수: start / end / equip_bool / tardiness / dur.
    # Phase 1 추출: solver/constraints/global_/decision_vars.py
    _dv = add_decision_vars(
        model=model,
        group_meta=group_meta,
        max_horizon_min=_MAX_HORIZON_MIN,
        tardiness_hard=tardiness_hard,
        warnings=_warnings,
    )
    start_vars = _dv.start_vars
    end_vars = _dv.end_vars
    equip_vars = _dv.equip_vars
    tardiness_vars = _dv.tardiness_vars
    dur_vars = _dv.dur_vars

    # 6-b-2. Frozen groups — 긴급수주 재최적화 시 진행 배치 고정.
    # Phase 1 추출: solver/constraints/global_/frozen_pins.py
    apply_frozen_pins(
        model=model,
        group_meta=group_meta,
        start_vars=start_vars,
        equip_vars=equip_vars,
        frozen_group_keys=frozen_group_keys,
        frozen_tasks_snapshot=frozen_tasks_snapshot,
        max_horizon_min=_MAX_HORIZON_MIN,
        warnings=_warnings,
    )

    # 6-b2. 웜스타트 힌트 주입 (자유 변수 대상).
    # Phase 1 추출: solver/constraints/global_/warm_start.py
    _warm_start_applied, _warm_start_skipped = apply_warm_start_hints(
        model=model,
        group_meta=group_meta,
        start_vars=start_vars,
        equip_vars=equip_vars,
        warm_start_hints=warm_start_hints,
        frozen_group_keys=frozen_group_keys,
        max_horizon_min=_MAX_HORIZON_MIN,
    )

    # 6-c. 설비 충돌 방지 (no_overlap)
    # Round 2 HIGH #5: per_eq_dur_enabled 이면 interval size 는 설비별 상수 dur_i.
    # 6-c. 설비 충돌 방지 (no_overlap).
    # Phase 1 추출: solver/constraints/global_/no_overlap.py 모듈로 이동.
    itv_vars = add_equipment_no_overlap(
        model=model,
        group_meta=group_meta,
        all_eq_codes=all_eq_codes,
        start_vars=start_vars,
        end_vars=end_vars,
        equip_vars=equip_vars,
        max_horizon_min=_MAX_HORIZON_MIN,
    )

    # 6-d / 6-e. 공정 선후관계 + CORE→ST 선행.
    # Phase 1 추출: solver/constraints/global_/predecessor.py 모듈로 이동.
    # proc_groups_by_sq 는 §6-f 의 idle_terms 가 재사용하므로 caller 가 받아둠.
    proc_groups_by_sq = compute_proc_groups_by_sq(group_meta)
    add_predecessor_precedence(
        model=model,
        group_meta=group_meta,
        start_vars=start_vars,
        end_vars=end_vars,
        proc_groups_by_sq=proc_groups_by_sq,
    )
    add_core_st_precedence(
        model=model,
        group_meta=group_meta,
        start_vars=start_vars,
    )

    # 6-f. 파이프라인 유휴 시간 soft penalty.
    # Phase 1 추출: solver/constraints/global_/idle_terms.py 모듈로 이동.
    idle_terms = collect_idle_terms(
        model=model,
        group_meta=group_meta,
        end_vars=end_vars,
        proc_groups_by_sq=proc_groups_by_sq,
        max_horizon_min=_MAX_HORIZON_MIN,
    )

    # 6-f-hard. 시스 색상 체인 Hard Constraint (sheath_color_hard=True 일 때만)
    #
    # Why: 긴급수주 반영 시 사용자 결정사항 — "블록 배치에서 색상 우선을 강제".
    # 기존 6-g 의 soft penalty(chain_terms, weight=1) 는 tardiness_weight 에 밀려
    # 실질적으로 무력해지는 경우가 있었음. Hard 승격 시:
    #   1) 같은 클러스터(설비 카테고리 + 주차 버킷 + 색상) 내 정렬된 인접 쌍이
    #      반드시 같은 설비에서 선행(gk_a → gk_b) 으로 순차 배치.
    #   2) 두 작업 사이 간격 ≥ color_changeover_min (ConstraintConfig 4-2
    #      sheath_color_min, 기본 120 분). 같은 색상이므로 이론상 교체 0 분이 맞지만,
    #      클러스터 단위 연속성을 보장하기 위한 최소 gap 으로만 사용.
    #
    # 방어 로직:
    #   - build_sheath_clusters 빈 list → 제약 추가 없이 pass
    #   - frozen 그룹 pair → skip (start 이미 고정됨, 추가 제약 불필요)
    #   - 인접 쌍 중 하나라도 start_vars/equip_vars 에 없으면 skip
    #   - 클러스터 group_keys 가 1개 이하 → skip (인접 쌍 없음)
    if sheath_color_hard:
        # Phase 1 추출: solver/constraints/process/sheath_color_hard.py
        add_sheath_color_hard_chain(
            model=model,
            group_meta=group_meta,
            start_vars=start_vars,
            end_vars=end_vars,
            equip_vars=equip_vars,
            constraint_params=constraint_params,
            frozen_group_keys=frozen_group_keys,
        )

    # 6-g + 6-g-tiebreak. 시스 색상 sequence-dependent gap + makespan bias.
    # Phase 1 추출: solver/constraints/process/sheath_color_sequence.py
    sheath_end_terms = add_sheath_color_sequence_and_tiebreak(
        model=model,
        group_meta=group_meta,
        start_vars=start_vars,
        end_vars=end_vars,
        equip_vars=equip_vars,
        constraint_params=constraint_params,
    )

    # 6-g-slack. On-time 그룹 slack-weighted completion — 납기 임박 우선 정렬.
    #
    # Why: 기존 on-time 그룹은 `e ≤ due_wmin` hard constraint 만 걸리고 objective
    # 항이 없어 슬랙 차이가 무시됨 → "납기 여유 있는 그룹이 납기 임박 그룹보다
    # 앞에 배치" 현상 (KBI PoC 관찰: 300SQ 납기 4/30 이 150SQ 납기 4/17 보다
    # 먼저 같은 설비에 배치). EDD pair tie-breaker (weight=1/pair) 만으론 idle /
    # chain / transition 항에 밀려 역전 발생 가능.
    #
    # 구현: on-time 그룹 각각에 `w × end` 항 추가. w 는 슬랙에 반비례 → 임박한
    # 그룹일수록 end 를 작게 하려는 힘 강함. 연선/절연/시스 등 모든 후속 공정에
    # 일괄 적용 (요구사항: "연선 뿐만 아니라 절연 등 후속공정에도 모두").
    #
    # 필터:
    #   - no-due (`due_wmin == _MAX_HORIZON_MIN`): 납기 없는 그룹은 skip.
    #   - past-due (`due_wmin < 0`): 기존 `_TARDINESS_WEIGHT × tardiness_vars` 가
    #     담당 — 중복 부과 방지.
    #
    # 가중치 스케일:
    #   `_IDLE_WEIGHT=1` < slack_w (수~수백/min) < `_TARDINESS_WEIGHT=1e5/min`.
    #   past-due penalty 를 이기지 못해 안전, idle_terms 와 경쟁 가능.
    # Phase 1 추출: solver/constraints/global_/slack_terms.py
    slack_terms, slack_terms_meta = collect_slack_terms(
        group_meta=group_meta,
        end_vars=end_vars,
        slack_weight_base=weights.SLACK_WEIGHT_BASE,
        max_horizon_min=_MAX_HORIZON_MIN,
    )

    # 6-h. EDD 전역 penalty — "같은 공정 + 공유 설비 후보" 인 그룹 쌍에서
    # 납기 빠른 쪽이 뒤에 시작하면 `_EDD_PAIR_WEIGHT` penalty.
    #
    # Why: past-due tardiness 는 overdue 그룹만 앞으로 끌고, slack penalty 는
    # on-time 그룹 간 duration 차이가 크면 WSPT(짧은 작업 먼저) 이익에 밀림.
    # 관찰 사례: 150SQ 33000m 납기 4/17 이 300SQ 9500m 납기 4/30 보다 뒤에
    # 배치. 두 그룹 모두 on-time 이라 tardiness 0, slack w_150≈4 / w_300≈2 의
    # 2× 차이보다 WSPT duration 차이(51h/16h = 3×) 가 더 강해 역전 발생.
    # → EDD 위반 penalty 를 충분히 크게 해 duration 이익을 압도하게 만듦.
    # 대상 축소 (폭주 방지):
    #   1) 같은 공정 — 다른 공정끼리는 precedence 가 이미 순서 결정
    #   2) 공통 eligible 설비 존재 — 같은 설비에 놓일 가능성 있어야 순서가 의미
    #   3) due_wmin 이 _MAX_HORIZON_MIN (no-due) 또는 동일한 쌍은 skip
    # Phase 1 추출: solver/constraints/process/edd_pair.py
    edd_pair_terms, edd_mixed_pastdue_terms = collect_edd_pair_terms(
        model=model,
        group_meta=group_meta,
        start_vars=start_vars,
        equip_vars=equip_vars,
        max_horizon_min=_MAX_HORIZON_MIN,
    )

    # Round 2 HIGH #6: 연선 setup 3-tier soft penalty.
    # 같은 설비에 배치된 두 연선 그룹의 SQ 가 다르면 `_TRANSITION_WEIGHT` 분 비용
    # 부과 → solver 가 "같은 SQ 들을 한 설비에 모으는" 배치를 선호. adjacency
    # 단위 sequence-dependent setup 의 정확 모델링은 아니지만 (circuit constraint
    # 필요) 현재 group=single-interval 구조에서 실용적 proxy.
    #
    # 적용 범위: rep.process_name == "연선" (CORE 포함). 납기 ordinal 이 14일 이상
    # 벌어진 쌍은 skip (멀리 있으면 sequence 영향 희미). 납기 ordinal 없으면 skip.
    #
    # 각 (gk_a, gk_b) 쌍에 대해:
    #   transition_bool = AND(equip_a == equip_b, sq_a != sq_b)
    # 두 그룹이 같은 설비에 배치 AND SQ 다름 시 1, 아니면 0.
    # 경량화: SQ 같으면 penalty 0 이므로 쌍 skip. SQ 다를 때만 bool var 생성.
    # Phase 1 추출: solver/constraints/process/transition.py
    transition_terms = collect_transition_terms(
        model=model,
        group_meta=group_meta,
        equip_vars=equip_vars,
    )

    # Task 2A.2: trace_writer 용 dict. 현재 ConstraintConfig row 에 대응되는
    # 실제 penalty IntVar 가 없는 constraint class 들은 `_internal.` prefix 로
    # 구분하여 Week 4-5 에 정식 매핑이 생기면 재분류할 수 있게 한다.
    penalty_vars_dict: dict[str, cp_model.IntVar] = {}
    hard_literals_dict: dict[str, cp_model.IntVar] = {}

    return BuiltModel(
        model=model,
        groups=groups,
        all_eq_codes=all_eq_codes,
        start_vars=start_vars,
        end_vars=end_vars,
        equip_vars=equip_vars,
        tardiness_vars=tardiness_vars,
        dur_vars=dur_vars,
        itv_vars=itv_vars,
        idle_terms=idle_terms,
        transition_terms=transition_terms,
        sheath_end_terms=sheath_end_terms,
        slack_terms=slack_terms,
        slack_terms_meta=slack_terms_meta,
        edd_pair_terms=edd_pair_terms,
        edd_mixed_pastdue_terms=edd_mixed_pastdue_terms,
        warm_start_applied=_warm_start_applied,
        warm_start_skipped=_warm_start_skipped,
        penalty_vars=penalty_vars_dict,
        hard_literals=hard_literals_dict,
    )
