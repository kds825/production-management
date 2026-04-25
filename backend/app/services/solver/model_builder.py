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

from app.services.constraint_params import ConstraintParams, resolve_color_change_min
from app.services.schedule_optimizer import (
    PREDECESSOR_PROCESS,
)
from app.services.sheath_cluster import build_sheath_clusters
from app.services.solver.constraints.global_.predecessor import (
    add_core_st_precedence,
    add_predecessor_precedence,
    compute_proc_groups_by_sq,
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

    # 6-b. 결정변수: start / end / equip_bool / tardiness
    start_vars: dict[str, cp_model.IntVar] = {}
    end_vars: dict[str, cp_model.IntVar] = {}
    equip_vars: dict[str, dict[str, cp_model.IntVar]] = {}
    tardiness_vars: dict[str, cp_model.IntVar] = {}

    # Round 2 HIGH #5: 그룹별 "effective dur" — per_eq_dur_enabled 이면 설비 bool
    # 에 종속된 선형합으로 표현, 아니면 스칼라. end = start + dur 로 고정.
    # dur_vars[gk]: None (스칼라) 또는 IntVar.
    dur_vars: dict[str, Any] = {}
    for gk in groups:
        meta = group_meta[gk]
        dur = meta["cpsat_dur"]
        per_eq = meta.get("per_eq_dur_enabled", False)
        dur_by_eq = meta.get("cpsat_dur_by_eq") or {}

        if per_eq and dur_by_eq:
            # 설비별 dur: dur_var = sum(eq_bool_i * dur_i). equip_vars 는 exactly_one
            # 이므로 dur_var 는 정확히 한 설비의 dur 를 갖게 된다 (scalar product).
            _min_dur = min(dur_by_eq.values())
            _max_dur = max(dur_by_eq.values())
            dur_var = model.new_int_var(_min_dur, _max_dur, f"dur_{gk}")
            # s upper bound: horizon - min_dur (그래야 어떤 설비 선택에도 e ≤ horizon)
            s = model.new_int_var(0, _MAX_HORIZON_MIN - _min_dur, f"s_{gk}")
            e = model.new_int_var(_min_dur, _MAX_HORIZON_MIN, f"e_{gk}")
            model.add(e == s + dur_var)
            dur_vars[gk] = dur_var
        else:
            s = model.new_int_var(0, _MAX_HORIZON_MIN - dur, f"s_{gk}")
            e = model.new_int_var(dur, _MAX_HORIZON_MIN, f"e_{gk}")
            model.add(e == s + dur)
            dur_vars[gk] = None

        start_vars[gk] = s
        end_vars[gk] = e

        if tardiness_hard:
            # P9-B: 납기 hard constraint — end_var ≤ due_wmin 로 직접 강제.
            # due 가 있을 때만 제약 추가. (due_wmin == _MAX_HORIZON_MIN 인 no-due
            # 그룹은 제약 추가해도 무의미하게 통과하므로 skip — 모델 경량화.)
            #
            # Past-due 처리 (개정): 기존에는 due_wmin<0 이면 제약도 skip, tardiness_vars
            # 도 미생성 → solver 가 past-due 그룹을 "자유변수(어디 배치해도 obj 영향 0)"
            # 로 보고 임의 위치 선택 → EDD 순서 역전(납기 빠른 게 뒤로 밀림) 관찰됨.
            # 수정: past-due 는 hard 불가이지만 **soft tardiness 항은 생성** 해서
            # `weight × (end + |past|)` 이 objective 에 반영되게 함. 이렇게 하면
            # solver 가 past-due 그룹의 end 를 작게 하려 앞쪽에 배치 → EDD 실현.
            if meta.get("earliest_due") is not None:
                if meta["due_wmin"] < 0:
                    _warnings.append(
                        f"그룹 {gk}: 납기 {meta['earliest_due']} 이미 "
                        f"{abs(meta['due_wmin'])}min 지남 — tardiness hard 강제 skip, "
                        f"soft penalty 로 전환"
                    )
                    # Past-due: soft tardiness 생성. tard = max(0, e - due_wmin)
                    # due_wmin<0 이므로 tard = e - due_wmin = e + |past| (항상 양수)
                    tard = model.new_int_var(0, 2 * _MAX_HORIZON_MIN, f"t_past_{gk}")
                    model.add_max_equality(
                        tard, [e - meta["due_wmin"], model.new_constant(0)]
                    )
                    tardiness_vars[gk] = tard
                else:
                    model.add(e <= meta["due_wmin"])
            # placeholder — 이후 코드가 tardiness_vars[gk] 를 참조하지 않아도 안전
        else:
            # Soft 모드 (폴백용): 기존 weight-based tardiness.
            # Round 2 MED #13: due_wmin 음수 허용. `end - due_wmin` 이 음수 due 에
            # 대해 `end + |past|` 로 자연 증가 → "3일 overdue 는 1일 overdue 의
            # 3배 penalty" 실현. tard upper bound 를 2×horizon 으로 확장해
            # 음수 due_wmin 에서도 max_equality 가 안전하게 동작.
            tard = model.new_int_var(0, 2 * _MAX_HORIZON_MIN, f"t_{gk}")
            # tardiness = max(0, end - due)
            model.add_max_equality(tard, [e - meta["due_wmin"], model.new_constant(0)])
            tardiness_vars[gk] = tard

        eq_bools: dict[str, cp_model.IntVar] = {}
        for eq in meta["eligible"]:
            eq_bools[eq.equipment_code] = model.new_bool_var(
                f"eq_{gk}_{eq.equipment_code}"
            )
        equip_vars[gk] = eq_bools
        model.add_exactly_one(eq_bools.values())

        # Round 2 HIGH #5: per_eq_dur_enabled 이면 dur_var == sum(bool_i * dur_i).
        # exactly_one 이 보장되어 있으므로 선형합 = 선택된 설비의 dur.
        if dur_vars.get(gk) is not None:
            _dur_by_eq_local = meta["cpsat_dur_by_eq"]
            model.add(
                dur_vars[gk]
                == sum(
                    eq_bools[_ec] * int(_dur_by_eq_local[_ec])
                    for _ec in eq_bools.keys()
                )
            )

    # 6-b-2. Frozen groups — 기존 ScheduleTask 로 start/end/equipment 고정
    # Why: 긴급수주 재최적화 시 이미 진행 중/완료/base_date 이전 'scheduled' 배치는
    # 움직이면 안 된다 (실제 생산 중인 블록을 이동시키면 작업 중단/폐기 비용 발생).
    # 호출자가 frozen_group_keys 로 대상을 명시하면 해당 그룹의 start_var/end_var/
    # equip_var 를 DB 값으로 박아 솔버가 나머지 그룹만 자유변수로 최적화.
    # 방어적 동작: DB 에 해당 task 없으면 warning 만 기록하고 skip (stale key 대응).
    #
    # Task 2A.2 (Rev 3): DB 접근은 caller (cp_sat_schedule) 가 이미 수행해
    # `frozen_tasks_snapshot` 으로 주입했다. 여기서는 pure dict 조회만 수행해
    # services/solver/ 경계 불변식 (infrastructure import 금지) 을 준수.
    if frozen_group_keys:
        _snapshot = frozen_tasks_snapshot or {}
        for _gk in frozen_group_keys:
            if _gk not in group_meta:
                # group_meta 에 없음 — 이미 스케줄링 불가(설비 없음) 또는 WIP skip
                _warnings.append(
                    f"frozen_group_keys: '{_gk}' 은(는) group_meta 에 없어 고정 불가 (skip)"
                )
                continue
            _snap = _snapshot.get(_gk)
            if _snap is None:
                _warnings.append(
                    f"frozen_group_keys: '{_gk}' 에 해당하는 ScheduleTask 없음 (skip)"
                )
                continue

            _fixed_start_wmin = int(_snap["start_wmin"])
            _fixed_eq_code = _snap["equipment_code"]

            # 변수 domain 범위를 벗어나면 모델이 INFEASIBLE → clamp 후 warning
            _dur = group_meta[_gk]["cpsat_dur"]
            if _fixed_start_wmin > _MAX_HORIZON_MIN - _dur:
                _warnings.append(
                    f"frozen_group_keys: '{_gk}' start 가 horizon 초과 → 고정 skip"
                )
                continue

            model.add(start_vars[_gk] == _fixed_start_wmin)
            # end 는 (start + dur) 로 이미 묶여있으므로 end 고정은 start 고정과 동치.
            # 방어적으로 end 도 같이 박되 실패하지 않도록 별도 equality 불필요.
            # 단, cpsat_dur 와 실제 DB duration 이 다를 수 있어 end 를 명시 고정하면
            # INFEASIBLE 가능 → start 만 박는다.

            # 설비 고정: 해당 설비 bool=1, 나머지=0
            if _fixed_eq_code in equip_vars[_gk]:
                for _ec, _bv in equip_vars[_gk].items():
                    if _ec == _fixed_eq_code:
                        model.add(_bv == 1)
                    else:
                        model.add(_bv == 0)
            else:
                # eligible 에 없는 설비로 실행 중 → eligible 확장 없이 warning
                # (eligible 재계산은 spec 범위 밖 — 재최적화가 해당 그룹 재배치 시도)
                _warnings.append(
                    f"frozen_group_keys: '{_gk}' 의 고정 설비 '{_fixed_eq_code}' 가 "
                    f"eligible 에 없음 → 설비 고정 skip (시간만 고정)"
                )

    # ── 6-b2. 웜스타트 힌트 주입 (자유 변수 대상) ────────────────────────
    # 왜 여기: frozen_group_keys 의 hard-pin 이 먼저 적용된 후에 주입해야
    # 배타성이 자연스럽다 (pinned 그룹에 힌트 주는 건 no-op). 또한 모든
    # start_vars/equip_vars 선언이 끝난 시점이라 dict 조회가 안전.
    #
    # add_hint() 특성:
    #   - hard constraint 가 아닌 "탐색 시작점" 제안. 더 나은 해 발견 시 자유 이동.
    #   - 힌트가 현 제약과 충돌하면 silent-fail (솔버는 죽지 않고 전역 탐색으로).
    #   - 포트폴리오 워커 간 공유되어 여러 워커가 근방에서 병렬 탐색.
    #
    # ERP 재업로드·증분 시나리오에서 이전 해의 대부분 feasibility 를 유지한 채
    # 변경 부분만 재탐색 → 실측 2.5~5× speedup 기대 (PoC 데이터 측정 필요).
    _warm_start_applied = 0
    _warm_start_skipped = 0
    if warm_start_hints:
        _frozen_set = frozen_group_keys or set()
        for _gk, _snap in warm_start_hints.items():
            # 이미 hard-pin — 힌트 redundant
            if _gk in _frozen_set:
                _warm_start_skipped += 1
                continue
            # 모델에 없는 그룹 (스테일 키)
            if _gk not in start_vars:
                _warm_start_skipped += 1
                continue
            if not isinstance(_snap, dict):
                _warm_start_skipped += 1
                continue
            _hint_applied_one = False
            # 시작 시각 힌트 — horizon 범위 체크 후 주입
            _start_wmin = _snap.get("start_wmin")
            if isinstance(_start_wmin, int):
                _dur = group_meta[_gk]["cpsat_dur"]
                if 0 <= _start_wmin <= _MAX_HORIZON_MIN - _dur:
                    try:
                        model.add_hint(start_vars[_gk], _start_wmin)
                        _hint_applied_one = True
                    except Exception:
                        # add_hint 가 어떤 이유로든 실패해도 전체 optimize 를
                        # 깨뜨리면 안 됨 — silent degrade.
                        pass
            # 설비 힌트 — eligible 에 있을 때만
            _eq_code = _snap.get("equipment_code")
            if _eq_code and _eq_code in equip_vars.get(_gk, {}):
                try:
                    for _ec, _bv in equip_vars[_gk].items():
                        model.add_hint(_bv, 1 if _ec == _eq_code else 0)
                    _hint_applied_one = True
                except Exception:
                    pass
            if _hint_applied_one:
                _warm_start_applied += 1
            else:
                _warm_start_skipped += 1

    # 6-c. 설비 충돌 방지 (no_overlap)
    # Round 2 HIGH #5: per_eq_dur_enabled 이면 interval size 는 설비별 상수 dur_i.
    # 해당 설비 bool=1 일 때만 interval active 이므로 (optional_interval + bv), 각
    # 설비 interval 이 자신의 고유 dur 를 사용 → solver 가 "빠른 설비에 가면
    # overlap 덜 발생" 을 정확히 인식. end_vars[gk] 는 여전히 sum 기반 dur_var 와
    # 연동되므로 선택된 설비의 interval 만 e 와 일치 (나머지는 비활성).
    itv_vars: dict[tuple[str, str], Any] = {}
    for gk in groups:
        meta = group_meta[gk]
        dur_scalar = meta["cpsat_dur"]
        per_eq_enabled = meta.get("per_eq_dur_enabled", False)
        dur_by_eq = meta.get("cpsat_dur_by_eq") or {}
        for eq_code, bv in equip_vars[gk].items():
            # Per-equipment interval size: 활성 설비의 고유 dur.
            _itv_size = (
                int(dur_by_eq.get(eq_code, dur_scalar))
                if per_eq_enabled
                else dur_scalar
            )
            # end_vars[gk] 와 선택된 설비의 interval 만 정합 — 비활성 interval 은
            # start/size/end 값 검증 안됨 (CP-SAT optional 의미). 단 안전을 위해
            # 고정 size interval 을 위한 별도 end helper 사용:
            if per_eq_enabled:
                # optional interval 은 size=상수 일 때 자체 end IntVar 를 요구.
                # start 는 공통 start_vars[gk] 사용, end 는 helper 생성.
                _e_eq = model.new_int_var(
                    _itv_size, _MAX_HORIZON_MIN, f"e_{gk}_{eq_code}"
                )
                # bv=1 일 때만 (s + size == _e_eq AND _e_eq == end_vars[gk]) 강제.
                # bv=0 이면 interval 비활성이므로 _e_eq 값 임의 — 제약 없음.
                model.add(_e_eq == start_vars[gk] + _itv_size).only_enforce_if(bv)
                model.add(_e_eq == end_vars[gk]).only_enforce_if(bv)
                itv = model.new_optional_interval_var(
                    start_vars[gk], _itv_size, _e_eq, bv, f"itv_{gk}_{eq_code}"
                )
            else:
                itv = model.new_optional_interval_var(
                    start_vars[gk], dur_scalar, end_vars[gk], bv, f"itv_{gk}_{eq_code}"
                )
            itv_vars[(gk, eq_code)] = itv

    for eq_code in all_eq_codes:
        itvs = [
            itv_vars[(gk, eq_code)]
            for gk in groups
            if eq_code in equip_vars.get(gk, {})
        ]
        if len(itvs) >= 2:
            model.add_no_overlap(itvs)

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

    # 6-f. 목적함수: 파이프라인 유휴 최소화 + 색상 체인 최소화 (+ soft 모드에선 tardiness)
    # 유휴 = succ_end - pred_end (≥ 0, 6-d 하드 제약으로 보장). 납기 가중치(수십~수백)
    # 대비 훨씬 낮은 _IDLE_WEIGHT 로 soft 최적화 — 파이프라인이 빠른 공정일수록
    # 솔버가 start_vars 를 늦춰서 pred_end 와 succ_end 를 정렬시킨다.
    idle_terms: list = []
    for _gk in groups:
        _pred_proc = PREDECESSOR_PROCESS.get(group_meta[_gk]["rep"].process_name)
        if not _pred_proc:
            continue
        for _pred_gk in proc_groups_by_sq.get((_pred_proc, group_meta[_gk]["sq"]), []):
            _idle = model.new_int_var(0, _MAX_HORIZON_MIN, f"idle_{_pred_gk}_{_gk}")
            model.add(_idle == end_vars[_gk] - end_vars[_pred_gk])
            idle_terms.append(_idle)

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
        _clusters_hard = build_sheath_clusters(group_meta)
        # ConstraintConfig 4-2 에서 색상 교체 시간 조회. SpeedMaster 값 없이
        # fallback 경로만 써서 설비별 편차 무시 (클러스터 단위 gap 의미).
        _color_gap_min = int(
            round(
                resolve_color_change_min(
                    sm_color_min=None,
                    params=constraint_params,
                )
            )
        )
        _frozen_set = frozen_group_keys or set()

        for _cluster in _clusters_hard:
            _gks_ord = _cluster.group_keys
            if len(_gks_ord) < 2:
                continue
            for _i in range(len(_gks_ord) - 1):
                _gk_a = _gks_ord[_i]
                _gk_b = _gks_ord[_i + 1]
                # 둘 다 모델에 있는지 확인 (group_meta 에서 제외된 키 방어)
                if _gk_a not in start_vars or _gk_b not in start_vars:
                    continue
                if _gk_a not in equip_vars or _gk_b not in equip_vars:
                    continue
                # 두 그룹 모두 frozen 이면 start 이미 고정 → 추가 제약은 중복/모순 위험
                if _gk_a in _frozen_set and _gk_b in _frozen_set:
                    continue
                # Hard: gk_b 는 gk_a 종료 후 color_gap 이상 이후에 시작
                model.add(start_vars[_gk_b] >= end_vars[_gk_a] + _color_gap_min)
                # 같은 설비 강제: 두 그룹이 공통으로 eligible 한 설비 bool 을 동기화.
                # 교집합 eq 가 없는 경우(서로 다른 설비 후보) → gap 제약만 적용.
                _shared_eqs = equip_vars[_gk_a].keys() & equip_vars[_gk_b].keys()
                for _eq in _shared_eqs:
                    model.add(equip_vars[_gk_a][_eq] == equip_vars[_gk_b][_eq])

    # 6-g. 시스 색상 Sequence-Dependent Setup (정식 모델링).
    #
    # Why: 기존 chain_terms (|start_a - start_b| soft) 은 proximity 만 minimize 하여
    # 실제 "같은 설비에서 다른 색상 인접 시 +color_gap 분" 을 solver 가 인식 못 함.
    # Post-solve 캘린더 엔진만 color_change_min 을 duration 에 더해 solver 목적
    # 함수와 실제 스케줄이 괴리 (solver 는 갈→회→갈→회 와 갈갈→회회 를 동일 비용
    # 으로 착각). 결과: objective 동점 해 중 색상 흩어진 해가 빈번히 선택됨.
    #
    # 수정: 시스 공정 그룹 쌍(gk_a, gk_b) 중 색상 다르고 공통 eligible 설비 있는
    # 경우에 대해, "같은 설비 + 순서" booleans 로 conditional gap 제약을 부여한다.
    #   same_eq = OR_{ec ∈ shared}(equip_a[ec] ∧ equip_b[ec])
    #   a_before_b ∈ {0,1} (solver 자율 선택)
    #   same_eq=1 ∧ a_before_b=1  ⇒  start_b ≥ end_a + color_gap
    #   same_eq=1 ∧ a_before_b=0  ⇒  start_a ≥ end_b + color_gap
    # 효과:
    #   - Solver 가 실제 교체 시간(120 분) 을 duration 으로 반영 → tardiness
    #     vs 색상 묶음 tradeoff 를 정확히 평가.
    #   - 납기 여유 있으면 같은 색상 연속 배치를 자연 선호 (makespan/idle 감소).
    #   - 여유 없으면 색상 포기 (tardiness 피하기 위해).
    #   - AddCircuit / successor-var 기반 완전 TSP 모델 대비 단순. O(n²) 쌍에
    #     per-pair bool 2-3 개 + conditional constraint 4 개 → n≈60 기준 ~3K 변수.
    #
    # Note: 같은 색상 pair 는 gap 0 이면 no_overlap 만으로 충분하므로 skip (모델
    # 경량화). 다른 색상에만 제약 추가.
    _sheath_color_gap_min = int(
        round(
            resolve_color_change_min(
                sm_color_min=None,
                params=constraint_params,
            )
        )
    )
    _sheath_gks_all = [
        _g
        for _g, _m in group_meta.items()
        if _m["rep"].process_name in ("저압시스", "고압시스")
    ]
    for _i in range(len(_sheath_gks_all)):
        for _j in range(_i + 1, len(_sheath_gks_all)):
            _gk_a = _sheath_gks_all[_i]
            _gk_b = _sheath_gks_all[_j]
            _color_a = (group_meta[_gk_a]["rep"].sheath_color or "").strip()
            _color_b = (group_meta[_gk_b]["rep"].sheath_color or "").strip()
            # 색상 미지정/동일: 실물 교체 없음 → 제약 불필요 (no_overlap 으로 충분).
            if not _color_a or not _color_b or _color_a == _color_b:
                continue
            # 공통 eligible 설비 없으면 same_eq 가 항상 0 → 제약 항상 비활성 → skip.
            _shared_eqs_color = set(equip_vars[_gk_a].keys()) & set(
                equip_vars[_gk_b].keys()
            )
            if not _shared_eqs_color:
                continue
            # same_eq bool: 공통 설비 중 하나에서 둘 다 활성화됐는지.
            # exactly_one(equip_vars[g]) 이 각 그룹에 강제되어 있으므로 설비별
            # both_on 의 합 ≤ 1 (둘이 같은 설비에 있거나 없거나).
            _both_bools = []
            for _ec in _shared_eqs_color:
                _both = model.new_bool_var(f"sh_both_{_gk_a}_{_gk_b}_{_ec}")
                model.add_bool_and(
                    [equip_vars[_gk_a][_ec], equip_vars[_gk_b][_ec]]
                ).only_enforce_if(_both)
                model.add_bool_or(
                    [equip_vars[_gk_a][_ec].Not(), equip_vars[_gk_b][_ec].Not()]
                ).only_enforce_if(_both.Not())
                _both_bools.append(_both)
            _same_eq = model.new_bool_var(f"sh_same_eq_{_gk_a}_{_gk_b}")
            model.add(_same_eq == sum(_both_bools))
            # a_before_b: no_overlap 이 둘 중 한 방향을 강제하지만, 어느 방향
            # 인지를 솔버가 선택할 수 있도록 bool 로 bind. objective(+gap) 을
            # 고려해 solver 가 자연스럽게 최적 순서 결정.
            _a_before_b = model.new_bool_var(f"sh_order_{_gk_a}_{_gk_b}")
            model.add(
                start_vars[_gk_b] >= end_vars[_gk_a] + _sheath_color_gap_min
            ).only_enforce_if([_same_eq, _a_before_b])
            model.add(
                start_vars[_gk_a] >= end_vars[_gk_b] + _sheath_color_gap_min
            ).only_enforce_if([_same_eq, _a_before_b.Not()])

    # 6-g-tiebreak. 시스 그룹 makespan bias (색상 묶음 tie-breaker).
    #
    # Why: 위 sequence-dependent gap 제약은 실제 wall-clock 을 반영하지만 objective
    # 에 makespan 항이 없으면 "납기 여유 많고 설비 여유 많은" 경우 동일 objective
    # 의 grouped/scattered 해가 공존 → solver 가 non-deterministic 으로 scattered
    # 선택 가능 (예: 4 배치 {흑,흑,청,청} 모두 tardiness=0 feasible → 흑청흑청도 유효).
    # 작은 가중치로 시스 그룹의 end_var 합을 목적함수에 더해 "빨리 끝내는 해" 를
    # 선호시키면 자연스럽게 grouped 선택 (gap 적은 해 = makespan 작은 해).
    # weight=1 → tardiness(1e5~1e7 per min) 대비 5 orders 작아 실제 tradeoff 훼손 X,
    # objective tie 상황에서만 작동.
    sheath_end_terms = [end_vars[_g] for _g in _sheath_gks_all]

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
    slack_terms: list = []
    # 진단 스냅샷용 — (gk, weight, end_var) 로 기여도를 사후 분해할 수 있도록 보존.
    slack_terms_meta: list = []
    for _gk, _meta in group_meta.items():
        _due = _meta["due_wmin"]
        if _due == _MAX_HORIZON_MIN:
            continue
        if _due < 0:
            continue
        _slack_min = max(1, int(_due))
        _w = max(1, weights.SLACK_WEIGHT_BASE // _slack_min)
        slack_terms.append(_w * end_vars[_gk])
        slack_terms_meta.append((_gk, _w, end_vars[_gk]))

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
    edd_pair_terms: list = []
    # Hybrid C: past-due ↔ on-time 혼합 쌍 별도 리스트 (heavy weight 적용).
    edd_mixed_pastdue_terms: list = []
    _process_gks: dict[str, list[str]] = {}
    for _gk, _meta in group_meta.items():
        _process_gks.setdefault(_meta["rep"].process_name, []).append(_gk)
    for _proc, _gks_proc in _process_gks.items():
        for _i in range(len(_gks_proc)):
            for _j in range(_i + 1, len(_gks_proc)):
                _gk_a = _gks_proc[_i]
                _gk_b = _gks_proc[_j]
                _due_a = group_meta[_gk_a]["due_wmin"]
                _due_b = group_meta[_gk_b]["due_wmin"]
                # no-due 또는 동일 due 는 EDD 의미 없음
                if _due_a == _MAX_HORIZON_MIN or _due_b == _MAX_HORIZON_MIN:
                    continue
                if _due_a == _due_b:
                    continue
                # 공통 eligible 설비 없으면 경쟁 관계 아님 → skip
                if not (set(equip_vars[_gk_a].keys()) & set(equip_vars[_gk_b].keys())):
                    continue
                # 납기 빠른 쪽(earlier)이 뒤에 시작하면 wrong = 1
                if _due_a < _due_b:
                    _earlier, _later = _gk_a, _gk_b
                else:
                    _earlier, _later = _gk_b, _gk_a
                _wrong = model.new_bool_var(f"edd_wrong_{_earlier}__{_later}")
                model.add(start_vars[_earlier] > start_vars[_later]).only_enforce_if(
                    _wrong
                )
                model.add(start_vars[_earlier] <= start_vars[_later]).only_enforce_if(
                    _wrong.Not()
                )
                # 쌍 분류: "past-due ↔ on-time" 혼합이면 heavy weight.
                # earlier 는 납기 빠른 쪽 (= due_wmin 작은 쪽). past-due 는 음수, on-time
                # 은 양수. `earlier 가 past-due 이고 later 가 on-time` 이면 혼합 case.
                _earlier_due = group_meta[_earlier]["due_wmin"]
                _later_due = group_meta[_later]["due_wmin"]
                _is_mixed_pastdue = _earlier_due < 0 <= _later_due
                if _is_mixed_pastdue:
                    edd_mixed_pastdue_terms.append(_wrong)
                else:
                    edd_pair_terms.append(_wrong)

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
    transition_terms: list = []
    _stranding_gks = [
        _g for _g, _m in group_meta.items() if _m["rep"].process_name == "연선"
    ]
    for _i in range(len(_stranding_gks)):
        for _j in range(_i + 1, len(_stranding_gks)):
            _gk_a = _stranding_gks[_i]
            _gk_b = _stranding_gks[_j]
            _meta_a = group_meta[_gk_a]
            _meta_b = group_meta[_gk_b]
            # 동일 SQ → setup 0 or 30 — penalty 의미 없음 (현재 모델에서는 동급)
            if _meta_a["sq"] == _meta_b["sq"]:
                continue
            # 공통 eligible 설비 없으면 same_equip 불가 → skip
            _shared = set(equip_vars[_gk_a].keys()) & set(equip_vars[_gk_b].keys())
            if not _shared:
                continue
            # 납기 14일 초과 벌어지면 sequence 영향 미미 → skip
            _due_a = _meta_a.get("due_date_ord")
            _due_b = _meta_b.get("due_date_ord")
            if _due_a is not None and _due_b is not None and abs(_due_b - _due_a) > 14:
                continue
            # same_equip bool: 공통 설비 _s 에 대해 eq_a[s] AND eq_b[s] 가 한 번이라도
            # 참이면 1. 각 공통 설비별 AND 변수 만들고 OR 로 합산.
            _same_eq_bools: list = []
            for _ec in _shared:
                _both = model.new_bool_var(f"both_{_gk_a}_{_gk_b}_{_ec}")
                # _both = eq_a[ec] AND eq_b[ec]
                model.add_bool_and(
                    [equip_vars[_gk_a][_ec], equip_vars[_gk_b][_ec]]
                ).only_enforce_if(_both)
                model.add_bool_or(
                    [equip_vars[_gk_a][_ec].Not(), equip_vars[_gk_b][_ec].Not()]
                ).only_enforce_if(_both.Not())
                _same_eq_bools.append(_both)
            # 설비 exactly_one 이므로 _same_eq_bools 중 최대 1개만 참 → sum = OR
            _trans = model.new_bool_var(f"trans_{_gk_a}_{_gk_b}")
            model.add(_trans == sum(_same_eq_bools))
            transition_terms.append(_trans)

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
