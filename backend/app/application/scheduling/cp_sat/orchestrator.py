"""CP-SAT 기반 스케줄 최적화 엔진

── 설계 방침 ───────────────────────────────────────────────────────────────
CP-SAT 담당: 모든 배치 그룹의 처리 순서 결정 + 납기 초과 최소화
  - 목적함수: customer_priority 가중 납기 초과 근무일 합산 최소화
  - Hard 제약: 설비 충돌 없음, 공정 선후관계(연선→절연→시스), CORE 선행

실제 배치(캘린더): CP-SAT가 결정한 순서대로 그리디 캘린더 엔진 수행
  - 멀티설비 그룹 → _schedule_multi_equipment
  - 단일설비 그룹 → _find_available_slot + calculate_end_datetime
  - 실제 시작/종료는 항상 08~22시 근무 캘린더 기준

CP-SAT 시간 단위: 근무 분(working minute), 하루 = 840분(14h×60)
  - 캘린더와 직접 1:1 대응은 불가하지만 근무일 단위로 근사하여
    납기 제약의 방향성(어떤 그룹을 먼저 처리할지)을 올바르게 결정

폴백: CP-SAT 실패(INFEASIBLE / 타임아웃) 시 기존 그리디로 자동 전환
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from app.domain.constants import (
    PREDECESSOR_PROCESS,
    PROCESS_ORDER,
    _CHAIN_WEIGHT,  # re-export until Week 9 (D7-C)
    _DUE_HARD_WEIGHT,  # re-export until Week 9 (D7-C)
    _TRANSITION_WEIGHT,  # re-export until Week 9 (D7-C)
    _WORK_MIN_PER_DAY,  # re-export until Week 9 (D7-C)
)
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.application._shared.audit_logger import log_decision
from app.infrastructure.calendar_engine import (
    calculate_end_datetime,
)
from app.domain.constraint_rules import resolve_color_change_min

# Week 3 Task 3A.2 wiring (sub-commit D):
#   greedy / scheduling_shared 이 분리되면서 cp_sat → schedule_optimizer 의
#   top-level import 가 모두 사라진다. domain.constants / scheduling_shared /
#   greedy.slot_finder 직접 참조로 순환 의존성을 제거한다.
from app.application.scheduling.greedy.slot_finder import _find_available_slot
from app.application._shared.group_ops import (
    _extract_core_main_sq,
    _get_stranding_setup_min,
    _is_core_group,
    _is_sheath_group,
    _schedule_multi_equipment,
    _st_sq,
)
from app.application._shared.slot_filters import (
    align_start_to_predecessor_end,
)
from app.application.scheduling.cp_sat import SolverInput
from app.application.scheduling.cp_sat.constraint_loader import (
    ConstraintSpec,
    load_active_constraints,
)
from app.application.scheduling.cp_sat.model_builder import (
    BuiltModel,
    ModelWeights,
    build_model,
)
from app.application.scheduling.cp_sat.objective import compose_objective
from app.application.scheduling.cp_sat.snapshot import write_snapshot
from app.application.scheduling.cp_sat._load_inputs import load_solver_inputs
from app.application.scheduling.cp_sat._trace_writer import write_solver_trace

# Phase 3 step 2: 가중치 상수 + 워커/우선순위/duration helpers + group meta
# builder 를 helpers.py 에 단일 source 로 이동. orchestrator 는 import 만.
from app.application.scheduling.cp_sat.helpers import (
    _EDD_MIXED_PASTDUE_WEIGHT,
    _EDD_PAIR_WEIGHT,
    _IDLE_WEIGHT,
    _MAX_HORIZON_MIN,
    _PAST_SEVERITY_K,
    _SLACK_WEIGHT_BASE,
    _SOLVER_TIME_LIMIT_SEC,
    _TARDINESS_WEIGHT,
    _build_group_meta,  # noqa: F401  # used at §4-5 inline (Phase 3 step 2)
    _build_snapshot_weights,
    _priority_label,
    _resolve_num_workers,
)

# Logger for non-fatal trace-write failures: observability must not kill
# solver correctness (see Task 2A.3 wiring note near `return result`).
_logger = logging.getLogger(__name__)

# _TARDINESS_WEIGHT / _IDLE_WEIGHT / _SLACK_WEIGHT_BASE / _PAST_SEVERITY_K /
# _EDD_PAIR_WEIGHT / _EDD_MIXED_PASTDUE_WEIGHT / _MAX_HORIZON_MIN / _SOLVER_TIME_LIMIT_SEC
# 상수, _resolve_num_workers / _build_snapshot_weights / _priority_label /
# _compute_group_duration helpers, _build_group_meta 는 모두 helpers.py 로 이동
# (Phase 3 step 2). 위 import 블록에서 가져온다.
# 가중치 설계 의도는 helpers.py 의 상수 docstring 참조.


def _spec_weight_factory(specs_by_id: dict):
    """`_spec_weight(cid, fallback)` 클로저를 만들어 반환 — Week 5A.4 wiring.

    Reads `ConstraintSpec.params["weight"]` and **scales by `priority / 50.0`**
    so the Admin-UI priority slider is causally wired to the solver objective.
    `priority=50` (DB default for all W-* rows) → factor 1.0 → 기존 11개
    fixture hash 무회귀 보장 (parity-preserving by construction).

    | priority | factor | 효과                          |
    |----------|--------|-------------------------------|
    | 0        | 0.0    | effective off (term 무력화)    |
    | 50       | 1.0    | baseline (기존 동작)           |
    | 100      | 2.0    | weight 2배 (강하게 우선)       |

    Falls back to the hardcoded constant when:
      - spec 자체가 없음 (W-* row 미시드 환경)
      - params["weight"] 가 숫자가 아님 (스키마 손상)
      - priority 가 None (이론상 불가 — column NOT NULL DEFAULT 50)

    Why 모듈-수준 factory: closure 가 cp_sat_schedule 안에 있으면 단위 테스트
    하기 어려움. specs_by_id 만 분리해서 받으면 factory 자체가 pure → unit
    test 가능. (test_priority_slider_objective.py)
    """

    def _spec_weight(cid: str, fallback: int) -> int:
        spec = specs_by_id.get(cid)
        if spec is None:
            return fallback
        w = spec.params.get("weight")
        if not isinstance(w, (int, float)):
            return fallback
        # priority=0 은 명시적 "term off" 의도 → falsy 단축평가 금지.
        # None 만 fallback (column NOT NULL DEFAULT 50 이라 이론상 불가).
        priority = getattr(spec, "priority", None)
        if priority is None:
            priority = 50
        # weight × (priority/50) — int round (CP-SAT 는 정수 계수만 안전)
        return int(round(w * (priority / 50.0)))

    return _spec_weight


# _work_days_between, _working_minutes_between, _due_work_min 은
# app.application._shared.calendar_ops 로 이동 (Week 3 Task 3A.1, Phase 1 step 3 재배치).
# 아래 import 가 모듈 namespace 에 re-export 하여 기존 path 가 유지된다 (D7-C).
# F401 silences "unused" — 외부 (테스트/다른 모듈) 가 cp_sat_optimizer 경유로
# 이 심볼들을 import 하므로 ruff 가 제거하면 안 됨.
from app.application._shared.calendar_ops import (  # noqa: E402, F401
    _due_work_min,  # re-export until Week 9 (D7-C)
    _work_days_between,  # re-export until Week 9 (D7-C)
    _working_minutes_between,  # re-export until Week 9 (D7-C)
)


# _compute_group_duration_map, _is_multi_equip_group 은
# app.application._shared.group_ops 로 이동 (Week 3 Task 3A.1, Phase 1 step 3 재배치).
# 아래 import 가 모듈 namespace 에 re-export 한다 (D7-C invariant).
from app.application._shared.group_ops import (  # noqa: E402, F401
    _compute_group_duration_map,  # re-export until Week 9 (D7-C)
    _is_multi_equip_group,  # re-export until Week 9 (D7-C)
)


# Week 9 SRP cleanup: 선점 스케줄링 로직은 `app.application.scheduling.cp_sat.preemption`.
# `_delete_task_safely` re-export 는 D7-C 호환 path 유지용 (외부 import 가
# 사라진 Week 9 막바지에 제거 예정).
from app.application._shared.db_ops import (  # noqa: E402, F401
    _delete_task_safely,  # re-export until Week 9 (D7-C)
)
from app.application.scheduling.cp_sat.preemption import (  # noqa: E402
    try_preempt_for_urgent,  # called inside cp_sat_schedule (line ~1330)
)


# ── 메인 함수 ─────────────────────────────────────────────────────────────


# resolve_base_date, _datetime_to_wmin 은 app.application._shared.calendar_ops
# 로 이동 (Week 3 Task 3A.1, Phase 1 step 3 재배치). 아래 import 가 모듈 namespace 에
# re-export 하여 기존 path (app.application.scheduling.cp_sat.orchestrator.resolve_base_date 등) 가 유지된다 (D7-C).
# F401 silences "unused" — schedule_optimizer / 테스트가 cp_sat_optimizer 경유로
# resolve_base_date 를 import 하므로 ruff 가 제거하면 안 됨.
from app.application._shared.calendar_ops import (  # noqa: E402, F401
    _datetime_to_wmin,  # re-export until Week 9 (D7-C)
    resolve_base_date,  # re-export until Week 9 (D7-C)
)


def cp_sat_schedule(
    run_label: str,
    db: Session,
    *,
    base_date: datetime | None = None,
    random_seed: int = 0,
    frozen_group_keys: set[str] | None = None,
    sheath_color_hard: bool = True,
    tardiness_hard: bool = True,
    time_limit_sec: int | None = None,
    warm_start_hints: dict[str, dict] | None = None,
    # ── Task 1.1 (Rev 3 리팩터): 파러티 하니스용 결정론 훅 ──────────────
    # 세 파라미터 모두 **기본값이 None** 이어서, 기존 호출자 8 곳은 한 줄도
    # 고칠 필요가 없다. Task 1.4 (parity harness) 와 Task 1.5 (CI gate) 에서
    # 이 훅들을 사용해 pre/post 리팩터 bit-exact 비교를 수행한다.
    solver_input_override: SolverInput | None = None,
    num_search_workers: int | None = None,
    run_id_override: str | None = None,
    # ── Phase 3 step 3 (target.md §3 row 5): lex_min_time wiring ──────────
    # 납기 lexicographic 우선 (Phase A: max_tardiness 최소 → Phase B: makespan
    # 최소). True 시 weighted-sum objective 대신 lex 솔버 호출. INFEASIBLE_A/B
    # / UNKNOWN 발생 시 자동으로 weighted-sum 폴백 (model 은 deepcopy 로 보호).
    min_time_mode: bool = False,
) -> dict:
    """
    CP-SAT 기반 자동 배치.

    CP-SAT → 전체 그룹의 처리 순서 결정
    캘린더 그리디 → 그 순서대로 실제 시작/종료 시각 계산 및 DB 저장

    Args:
        random_seed: CP-SAT 솔버의 random_seed. retry wrapper 가 시도 번호를
            전달해 결정론적 동일 해가 반복되는 것을 방지한다 (기본 0).
        frozen_group_keys: 재최적화 시 고정할 batch_group 집합. 각 그룹의
            start/end/equipment 는 기존 DB ScheduleTask 값으로 박힌다. 긴급수주
            추가 후 전역 재최적화에서 "이미 진행중/완료/base_date 이전 scheduled"
            배치가 움직이지 않도록 보장. None 또는 빈 set 이면 기존 동작 유지.
        sheath_color_hard: 시스(저압/고압) 색상 클러스터 내 인접 그룹을 hard
            constraint 로 강제할지 여부 (기본 True — 긴급수주 반영 시 "블록
            배치에서 색상 우선" 사용자 결정사항). True 일 때:
              1) 같은 (설비 카테고리, 주차, 색상) 클러스터로 묶인 그룹들의
                 인접 쌍에 대해 gk_b.start ≥ gk_a.end + color_changeover_min
                 을 model.add() 로 강제.
              2) 동일 인접 쌍은 같은 설비 선택을 강제 (cluster 의 의미가
                 "같은 설비에서 연속" 이므로).
            False 면 기존 soft penalty(chain_terms)만 유지되는 기존 동작.
            호출측(auto_schedule)이 전달하지 않으면 True 가 적용된다.
        tardiness_hard: 납기 초과를 hard constraint 로 강제할지 여부 (기본 True —
            P9-B "Tardiness A 엄격" 사용자 결정). True 일 때:
              - `model.add(e <= due_wmin)` 로 납기 직접 강제. 단 1분도 초과 불가.
              - tardiness 목적함수 항이 제거되므로 objective = idle + CHAIN*chain_diff.
              - INFEASIBLE 시 호출부(_reschedule_affected_groups_cpsat)가
                (tardiness_hard=False, sheath_color_hard=False) 등으로 단계적 완화.
            False 면 기존 weight-based soft(tardiness * _TARDINESS_WEIGHT) 동작 유지.
            단, `_CHAIN_WEIGHT=120` 은 두 모드 모두 공통 적용 (색상 교체가 tardiness
            와 동일 분 단위로 경쟁 가능하도록).
        time_limit_sec: 솔버 wall-time 상한(초). None 이면 `_SOLVER_TIME_LIMIT_SEC` (30)
            기본. 증분 경로는 10 으로 낮추어 UX 체감 개선 권장. 전역 재최적화는
            60 까지 허용 가능. 값은 `max(1, int(v))` 로 clamp.
        warm_start_hints: 자유 변수에 주입할 웜스타트 힌트 dict.
            형식: `{batch_group: {"start_wmin": int, "equipment_code": str}}`.
            - `add_hint()` 는 hard constraint 가 아닌 "탐색 시작점" — 더 나은 해가
              있으면 솔버가 자유롭게 이동한다 (품질은 목적함수로 결정).
            - `frozen_group_keys` 와 배타 — 이미 hard-pinned 된 그룹의 힌트는
              무시(redundant). start_vars 에 없는 그룹도 skip (stale key 방어).
            - start_wmin 이 horizon 범위를 벗어나면 skip. 설비 코드가 eligible
              에 없으면 시간만 주입. `add_hint()` 는 silent-fail 이므로 힌트가
              현 제약에 맞지 않아도 솔버는 죽지 않고 전역 탐색으로 대체.
            - 효과: ERP 재업로드·증분 시나리오에서 이전 해의 대부분 feasibility 를
              유지한 채 변경 부분만 재탐색 → 실측 2.5~5× speedup 기대.
            - `result["warm_start_applied"]` / `warm_start_skipped` 카운터로 관측.
        min_time_mode: True 면 lexicographic 솔버 (Phase A: max_tardiness 최소
            → Phase B: makespan 최소) 사용. weighted-sum tardiness term 대신
            "납기 우선, 그 안에서 최소시간" 도메인 언어 1:1 매핑.
              - lex 가 OPTIMAL/FEASIBLE 이면 그 결과를 §8 캘린더 그리디 입력으로 사용.
              - INFEASIBLE_A/B/UNKNOWN 이면 자동으로 weighted-sum 폴백 (warning 기록).
              - 모델은 lex 시도 전에 deepcopy 되어 폴백 시 원본 무손상.
              - `result["solver_mode"]` ∈ {"lex_min_time", "weighted_sum",
                "weighted_sum_fallback_from_lex"} 로 어떤 path 가 사용됐는지 기록.

    Returns:
        {"total_tasks", "violations", "warnings", "solver_status", "objective_value",
         "solver_wall_time_s", "solver_n_groups", "solver_num_workers",
         "warm_start_applied", "warm_start_skipped", "solver_mode"} (+ "lex_t_star",
        "lex_makespan_min", "lex_all_due_met" when min_time_mode 사용시)
    """
    result: dict[str, Any] = {
        "total_tasks": 0,
        "violations": [],
        "warnings": [],
        "solver_status": "UNKNOWN",
        "objective_value": 0,
        # 웜스타트 주입 결과 관측 카운터 — warm_start_hints 미사용 시 0/0.
        "warm_start_applied": 0,
        "warm_start_skipped": 0,
    }

    # Task 2A.3 (Rev 3): started_at is captured at the TOP of the function
    # (before the DB-load branch) so the solver_run row's started_at
    # accurately reflects total wall-clock cost — not just the CP-SAT
    # solve step. The B8 speculative `result["run_id"] = run_id_override`
    # stub that lived here (Task 1.1) is superseded by the real trace
    # write at the final `return result`: the run_id is generated from
    # either `run_id_override` (parity harness determinism, but mapped
    # to a UUID-shaped string to fit SolverRun.run_id VARCHAR(36)) or
    # a fresh uuid.uuid4().
    _started_at = datetime.now(timezone.utc)
    # PK contract: SolverRun.run_id is VARCHAR(36). Callers may pass a
    # longer `run_id_override` (e.g., parity harness uses run_label =
    # "20260421_parity_10_all_vs_none_constraints" = 42 chars) — we
    # deterministically collapse it into a UUID5 so the same override
    # yields the same PK every invocation (parity hash stability) but
    # fits the column width. UUID-shaped overrides pass through.
    if run_id_override is None:
        _run_id = str(uuid.uuid4())
    elif len(run_id_override) <= 36:
        _run_id = run_id_override
    else:
        # Namespace is a fixed DNS UUID — the choice doesn't matter,
        # only that it stays constant across invocations.
        _run_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, run_id_override))

    # Task 2A.4 (spec §10a): set run_id into the contextvar so every
    # log emitted during the solve — including downstream helpers
    # like calendar_engine and trace_writer — carries the same
    # [run_id=<uuid>] prefix automatically. Inline import keeps the
    # dependency co-located with the call site (matches the existing
    # trace_writer inline-import pattern near `return result`) and
    # survives the auto-formatter's unused-import sweep.
    #
    # Reset semantics: for HTTP callers, the outer RunIdMiddleware
    # resets the contextvar in its own try/finally using its own
    # token — so even if this function's early-return paths skip
    # their own reset, the middleware's outer reset restores the
    # contextvar to its pre-request state. For non-HTTP callers
    # (parity harness, direct test invocation) the contextvar is
    # scoped to the current context, not to the process, so it
    # does not leak across contexts. We still call _reset at the
    # final return path for defense-in-depth on the happy path.
    from app.infrastructure.logging import (
        set_run_id as _set_run_id,
    )

    _ctx_token = _set_run_id(_run_id)

    # ── 1-3. DB 로드 또는 override rebind ────────────────────────────────
    # Phase 2 Task 2.6 (B-3.1): 본 블록은 ``_load_inputs.load_solver_inputs``
    # 로 추출됐다. parity 보장을 위해 본문 변경 0, 단순 함수 호출 위임.
    load_out = load_solver_inputs(
        run_label,
        db,
        base_date=base_date,
        solver_input_override=solver_input_override,
    )
    if load_out.early_return is not None:
        # "배치 없음" 조기 반환 — 원본의 두 분기 메시지/wip_skipped 전파를
        # 그대로 재현. DB-load 분기는 wip_skipped 카운트를 result 에 싣지
        # 않은 채 반환, override 분기는 wip_skipped 비제로면 result 에 싣고
        # 반환 (load_inputs 가 분기별로 다른 dict 를 만들어 둔 차이를 보존).
        for w in load_out.early_return.get("warnings", []):
            result["warnings"].append(w)
        if "wip_skipped" in load_out.early_return:
            result["wip_skipped"] = load_out.early_return["wip_skipped"]
        return result

    base_date = load_out.base_date
    batches = load_out.batches
    equipment_by_process = load_out.equipment_by_process
    speed_map = load_out.speed_map
    color_setup_map = load_out.color_setup_map
    constraint_params = load_out.constraint_params
    welding_min = load_out.welding_min
    sq_to_wire_d = load_out.sq_to_wire_d
    if load_out.wip_skipped:
        result["wip_skipped"] = load_out.wip_skipped

    # ── 4-5. 그루핑 + 그룹별 메타 계산 ─────────────────────────────────────
    # Phase 3 step 2: §4 (그루핑) + §5 (group_meta) 는 helpers._build_group_meta
    # 로 이동. behaviour 1:1 보존 — eligible 필터, cpsat_dur_by_eq, due_wmin /
    # severity / weight 산식 모두 동일. warnings 는 in-place append.
    batch_groups, group_meta = _build_group_meta(
        batches,
        equipment_by_process,
        speed_map,
        base_date=base_date,
        warnings_out=result["warnings"],
    )

    if not group_meta:
        result["warnings"].append("스케줄링 가능한 배치 그룹 없음")
        return result

    # ── 6. CP-SAT 모델 구성 ───────────────────────────────────────────────
    # Task 2A.2 (Rev 3): §6 블록은 `app.application.scheduling.cp_sat.model_builder.build_model`
    # 로 이전됐다. DB 접근이 필요한 `frozen_group_keys` 는 여기서 스냅샷 dict 로
    # 변환하여 pure 함수에 주입한다 (services/solver/ 경계 불변식).
    frozen_tasks_snapshot: dict[str, dict[str, Any]] | None = None
    if frozen_group_keys:
        # run_label 범위 ScheduleTask 를 한번에 로드 (N+1 쿼리 방지)
        frozen_batches_q = (
            db.query(ProductionBatch, ScheduleTask)
            .join(ScheduleTask, ScheduleTask.batch_id == ProductionBatch.batch_id)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.batch_group.in_(list(frozen_group_keys)),
                ScheduleTask.run_label == run_label,
                ScheduleTask.start_datetime.isnot(None),
                ScheduleTask.end_datetime.isnot(None),
                ScheduleTask.equipment_code.isnot(None),
            )
            .all()
        )
        # batch_group → 대표 ScheduleTask (첫번째 매치). 여러 batch 가 한 group 에
        # 속해도 group-level start/end/equip 은 대표값으로 고정.
        frozen_task_by_gk: dict[str, ScheduleTask] = {}
        for _pb, _tk in frozen_batches_q:
            _bg = _pb.batch_group
            if _bg and _bg not in frozen_task_by_gk:
                frozen_task_by_gk[_bg] = _tk
        frozen_tasks_snapshot = {
            _bg: {
                "start_wmin": _datetime_to_wmin(_tk.start_datetime, base_date),
                "equipment_code": _tk.equipment_code,
            }
            for _bg, _tk in frozen_task_by_gk.items()
        }

    # Week 5 Task 5A.3: 가중치 source 가 Python 상수 → DB-driven (`ConstraintSpec`
    # `params_json["weight"]`) 으로 단계적 이전 중. 누락/disable 행은 fallback
    # 으로 기존 상수 유지 → parity 보존. 하나씩 옮기며 sub-commit 단위로 검증.
    _specs_by_id: dict[str, ConstraintSpec] = {
        s.constraint_id: s for s in load_active_constraints(db)
    }

    # Week 5A.4: priority 슬라이더 wiring. factory 가 weight × (priority/50)
    # 스케일 적용. 모든 W-* row 의 priority=50 (DB default) → factor 1.0 →
    # 기존 11/27 fixture hash 무회귀. 자세한 행위는 _spec_weight_factory docstring.
    _spec_weight = _spec_weight_factory(_specs_by_id)

    # _TARDINESS_WEIGHT 는 dict — 각 urgency tier 를 별도 W-* row 로 매핑 후 재구성.
    _tardiness_weight_db = {
        "critical": _spec_weight("W-TCRIT", _TARDINESS_WEIGHT["critical"]),
        "urgent": _spec_weight("W-TURG", _TARDINESS_WEIGHT["urgent"]),
        "normal": _spec_weight("W-TNORM", _TARDINESS_WEIGHT["normal"]),
    }

    _weights = ModelWeights(
        DUE_HARD_WEIGHT=_spec_weight("W-DHARD", _DUE_HARD_WEIGHT),
        TARDINESS_WEIGHT=_tardiness_weight_db,
        CHAIN_WEIGHT=_spec_weight("W-CHAIN", _CHAIN_WEIGHT),
        IDLE_WEIGHT=_spec_weight("W-IDLE", _IDLE_WEIGHT),
        SLACK_WEIGHT_BASE=_spec_weight("W-SLACK", _SLACK_WEIGHT_BASE),
        PAST_SEVERITY_K=_spec_weight("W-PSEV", _PAST_SEVERITY_K),
        EDD_PAIR_WEIGHT=_spec_weight("W-EDDP", _EDD_PAIR_WEIGHT),
        EDD_MIXED_PASTDUE_WEIGHT=_spec_weight("W-EDDM", _EDD_MIXED_PASTDUE_WEIGHT),
        TRANSITION_WEIGHT=_spec_weight("W-TRANS", _TRANSITION_WEIGHT),
        MAX_HORIZON_MIN=_MAX_HORIZON_MIN,
        WORK_MIN_PER_DAY=_WORK_MIN_PER_DAY,
    )

    # build_model 호출을 closure 로 wrap — lex INFEASIBLE 폴백 시 model 재구성
    # 가능 (cp_model 내부 IntAffine 등이 picklable 하지 않아 deepcopy 불가능).
    # 일반 경로 (min_time_mode=False) 는 1회만 호출.
    def _build_solver_model() -> BuiltModel:
        return build_model(
            group_meta=group_meta,
            equipment_by_process=equipment_by_process,
            weights=_weights,
            constraint_params=constraint_params,
            random_seed=random_seed,
            frozen_group_keys=frozen_group_keys,
            frozen_tasks_snapshot=frozen_tasks_snapshot,
            sheath_color_hard=sheath_color_hard,
            tardiness_hard=tardiness_hard,
            warm_start_hints=warm_start_hints,
            base_date=base_date,
            warnings_out=result["warnings"],
        )

    _built: BuiltModel = _build_solver_model()
    # ── 7-pre. 솔버 공통 파라미터 (lex / weighted 두 path 공유) ───────────
    # time_limit_sec override — 증분 경로는 10s, 전역 재최적화는 60s 등 호출자
    # 시나리오에 따라 조정. None/<=0 이면 기본값 유지 (하위호환).
    _time_limit = (
        int(time_limit_sec)
        if (time_limit_sec and int(time_limit_sec) > 0)
        else _SOLVER_TIME_LIMIT_SEC
    )
    # 워커 수: 환경변수 기반 해상도. 운영 기본 8, CI/테스트는 1로 강제 (결정론).
    # Task 1.1 (Rev 3): `num_search_workers` kwarg 가 주어지면 env 해상도를
    # override — parity harness 가 워커 수를 1 로 고정해 비결정성을 제거.
    if num_search_workers is not None and int(num_search_workers) > 0:
        _num_workers = max(1, int(num_search_workers))
    else:
        _num_workers = _resolve_num_workers()

    # ── 7-a. min_time_mode: lex 솔버 시도 (Phase 3 step 3) ────────────────
    # Lex 가 OPTIMAL/FEASIBLE 이면 그 솔버를 그대로 §8 캘린더 그리디에 전달.
    # INFEASIBLE_A/B/UNKNOWN 이면 weighted-sum 폴백 (원본 _built 유지를 위해
    # lex 시도 전 deepcopy). compose_objective 는 weighted-sum path 에서만
    # 호출되도록 분기 안으로 이동.
    _used_lex = False
    if min_time_mode:
        from app.application.scheduling.cp_sat.lex_min_time import (
            _adapt_lex_to_solve_result,
            solve_lex_min_time,
        )

        # lex 가 _built.model 을 mutate 한다 (max_tard 변수 + Phase B 의
        # max_tard ≤ T* hard constraint). cp_model 내부 IntAffine 객체가
        # picklable 하지 않아 deepcopy 불가능 → INFEASIBLE 폴백 시 model
        # 을 _build_solver_model() 로 재구성. 일반 lex 성공 경로는 추가
        # 비용 0 (1회 build).
        _lex_t0 = time.perf_counter()
        _lex_res = solve_lex_min_time(
            _built,
            time_limit_phase_a_sec=_time_limit,
            time_limit_phase_b_sec=_time_limit,
            num_workers=_num_workers,
            random_seed=int(random_seed),
        )
        _lex_wall_s = time.perf_counter() - _lex_t0
        if _lex_res.status in ("OPTIMAL", "FEASIBLE"):
            # adapter (Phase 3 step 4) 가 LexResult → AdaptedSolveResult 변환.
            # 같은 ScheduleTask insert 경로 사용 — adapted.built 의 vars 와
            # adapted.solver 가 동일 protobuf 식별자.
            adapted = _adapt_lex_to_solve_result(_lex_res, _built, _lex_wall_s)
            _used_lex = True
            _built = adapted.built
            solver = adapted.solver
            status = adapted.status
            _solve_wall_s = adapted.wall_s
            result["solver_mode"] = adapted.mode
            result["lex_t_star"] = adapted.lex_t_star
            result["lex_makespan_min"] = adapted.lex_makespan_min
            result["lex_all_due_met"] = adapted.lex_all_due_met
        else:
            # INFEASIBLE_A/B/UNKNOWN — weighted-sum 폴백 (모델 재구성).
            # _built 는 lex 시도 중 mutate 되어 사용 불가 → 새로 build.
            result["warnings"].append(
                f"lex_min_time {_lex_res.status} → weighted-sum 폴백 (모델 재구성)"
            )
            _built = _build_solver_model()
            result["solver_mode"] = "weighted_sum_fallback_from_lex"
    else:
        result["solver_mode"] = "weighted_sum"

    # weighted-sum 경로: lex 미사용 또는 lex INFEASIBLE 폴백 시.
    if not _used_lex:
        compose_objective(
            _built,
            weights=_weights,
            group_meta=group_meta,
            tardiness_hard=tardiness_hard,
        )

    # §6 의 로컬 변수를 `cp_sat_schedule` 후속 코드(§7~§9 + 스냅샷 writer)가 쓸
    # 수 있도록 unpack. 이름은 기존 코드와 1:1 호환되도록 유지 (파러티 보존).
    # _built 는 lex 성공 시 _lex_built 로 rebind 된 상태 — vars 는 lex.solver
    # 가 인지하는 것과 동일.
    model = _built.model
    groups = _built.groups
    start_vars = _built.start_vars
    end_vars = _built.end_vars
    equip_vars = _built.equip_vars
    tardiness_vars = _built.tardiness_vars
    idle_terms = _built.idle_terms
    transition_terms = _built.transition_terms
    _sheath_end_terms = _built.sheath_end_terms
    _slack_terms_meta = _built.slack_terms_meta
    _edd_pair_terms = _built.edd_pair_terms
    _edd_mixed_pastdue_terms = _built.edd_mixed_pastdue_terms
    result["warm_start_applied"] = _built.warm_start_applied
    result["warm_start_skipped"] = _built.warm_start_skipped

    # ── 7-b. weighted-sum 솔버 실행 (lex 미사용 또는 폴백 path) ────────────
    if not _used_lex:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = _time_limit
        solver.parameters.num_search_workers = _num_workers
        solver.parameters.log_search_progress = False
        # 재시도 시 다른 탐색 경로를 시도하도록 seed 변동 (Fix P0-4B)
        solver.parameters.random_seed = int(random_seed)

        # ── Phase 1 개선: 수렴 가속 파라미터 ──────────────────────────────
        # 왜 이 세 파라미터를 추가하는가:
        #   (1) linearization_level=2 — 정수 스케줄링 문제에서 LP 이완 정확도를
        #       상승시켜 분기한정(branch-and-bound) 가지치기 효율을 높임. 최적성은
        #       유지되고 수렴만 빨라진다 (OR-Tools 기본 1 → 2).
        #   (2) cp_model_probing_level=2 — constraint propagation 을 강하게 돌려
        #       INFEASIBLE 을 조기에 탐지. Level 2/3 완화 모드 전환을 앞당겨
        #       재시도 누적 시간을 단축.
        #   (3) relative_gap_limit — Level 1 (hard 납기 + hard 색상) 에서만 적용.
        #       이 모드는 FEASIBLE 이면 납기/색상 제약이 100% 만족되므로, 소프트
        #       목적함수(idle + chain_diff) 를 2% 이내로 근사해도 운영상 동등.
        #       완화 모드(Level 2/3) 에서는 품질이 중요하므로 gap 미적용.
        solver.parameters.linearization_level = 2
        solver.parameters.cp_model_probing_level = 2
        if tardiness_hard and sheath_color_hard:
            solver.parameters.relative_gap_limit = 0.02

        # 계측: solve() wall-time. 성능 개선 판정의 baseline 데이터 소스.
        _solve_t0 = time.perf_counter()
        status = solver.solve(model)
        _solve_wall_s = time.perf_counter() - _solve_t0

    status_name = solver.status_name(status)
    result["solver_status"] = status_name
    result["solver_wall_time_s"] = round(_solve_wall_s, 3)
    result["solver_n_groups"] = len(groups)
    result["solver_num_workers"] = _num_workers

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["warnings"].append(
            f"CP-SAT 솔버 실패 ({status_name}) — 그리디 폴백으로 전환합니다"
        )
        return result

    result["objective_value"] = int(solver.objective_value)

    # ── 진단 스냅샷: solver 입력(group_meta) + 출력(start/end/equip/tardiness) +
    # 주요 objective 항의 실제 기여값을 JSON 한 벌로 저장. 원인 분석 시
    # "어떤 그룹이 왜 그 위치에 갔는가" 를 사후에 재현할 수 있도록.
    write_snapshot(
        run_label=run_label,
        base_date=base_date,
        group_meta=group_meta,
        frozen_group_keys=frozen_group_keys,
        solver=solver,
        solver_status_name=status_name,
        start_vars=start_vars,
        end_vars=end_vars,
        equip_vars=equip_vars,
        tardiness_vars=tardiness_vars,
        edd_pair_vars=_edd_pair_terms,
        slack_terms_meta=_slack_terms_meta,
        weights=_build_snapshot_weights(),
        work_min_per_day=_WORK_MIN_PER_DAY,
        max_horizon_min=_MAX_HORIZON_MIN,
        idle_terms=idle_terms,
        transition_terms=transition_terms,
        sheath_end_terms=_sheath_end_terms,
        edd_mixed_pastdue_vars=_edd_mixed_pastdue_terms,
    )

    # ── 8. CP-SAT 순서대로 캘린더 그리디로 실제 배치 ─────────────────────
    #
    # CP-SAT는 "어떤 순서로, 어떤 설비에" 처리할지만 결정한다.
    # 실제 시작/종료 시각은 항상 캘린더 인식 엔진(_find_available_slot +
    # calculate_end_datetime)으로 계산하므로 겹침이 발생하지 않는다.
    #
    # 처리 순서: CP-SAT start_vars 값 오름차순
    #   → 납기 빠른 그룹이 앞에 오도록 솔버가 결정한 순서
    # 처리 순서: 공정 선후관계 → 납기일 오름차순(EDD) → 고객 우선순위
    # ── 소선경 클러스터별 최초 납기 계산 (ST- 연선 그룹 연속 배치용) ─────────
    # schedule_optimizer의 wire_d_earliest와 동일한 로직
    wire_d_earliest: dict[float, date] = {}
    for gk in groups:
        if gk.startswith("ST-"):
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            if wd > 0:
                ed = group_meta[gk]["earliest_due"]
                if ed and (wd not in wire_d_earliest or ed < wire_d_earliest[wd]):
                    wire_d_earliest[wd] = ed

    # 처리 순서 결정:
    #   CORE/AL-CORE: 공정순 최우선 (ST- 선행)
    #   ST- 연선: 공정순 → 소선경 클러스터 최초납기 → 소선경값 → 그룹 EDD
    #     (같은 소선경 그룹을 연속 배치 → 선재교체 비용 최소화)
    #   시스(저압/고압): 공정순 → 주 버킷(H1/H2) → 색상 → 실제 EDD
    #     Why: 시스는 **납기 최우선, 그 다음 색상 우선**. 납기 주차 bucket
    #     안에서만 색상을 묶어 교체 비용 최소화. 주차가 다르면 납기 순서 유지.
    #     (색상을 1차로 두면 회 W15 그룹이 갈 W18 뒤로 밀려 11일+ 지연 발생)
    #     solver 가 tardiness 최소화로 주차 윈도우를 보장하므로, post-solve
    #     ordering 은 (주차, 색상) 로 안전하게 정렬 가능.
    #     schedule_optimizer._group_sort_key 의 시스 분기와 동일 규칙.
    #   그 외 공정(절연 등): 공정순 → EDD → 고객 우선순위
    #       절연(proc=2)이 시스(proc=4)보다 항상 먼저 스케줄링 → 파이프라인 데이터 등록 보장
    #
    # 모든 분기가 동일 길이 7-tuple 을 반환한다 (color_rank / due_wk 필드에
    # 비시스 분기는 중립값을 채움). solver.value(start_vars) 는 색상 키가
    # 이미 클러스터링을 확정하므로 제거.
    _COLOR_RANK_PAD = 99  # 비시스: 색상 키 무효
    _DUE_WK_PAD = 999999  # 비시스: 주 버킷 무효

    # ── 시스 색상 묶음 기반 정렬 ─────────────────────────────────────────────
    # 1차 키: 묶음의 pred_ready_wmin (선행공정 first-drum 완료 시각, working-min).
    #        solver start_vars 값으로 선행공정 그룹의 대략적 완료 시점을 계산해
    #        "설비가 빨리 사용 가능한 묶음" 부터 처리 → 설비 idle 최소화.
    # 2차 키: latest_due (같은 pred_ready 이면 납기 순)
    # 3차 키: color_rank (같은 납기면 색상 체인 유도)
    from app.domain.sheath_cluster import (
        build_sheath_clusters,
        cluster_sort_key,
    )

    # 각 그룹의 pred_ready_wmin 계산 (솔버 결과 기반 근사)
    for _gk, _meta in group_meta.items():
        _rep = _meta["rep"]
        _pred_proc = PREDECESSOR_PROCESS.get(_rep.process_name)
        _meta["pred_ready_wmin"] = 0
        if not _pred_proc:
            continue
        _pred_sq = _meta["sq"]
        _pred_candidates = [
            g
            for g, m in group_meta.items()
            if m["rep"].process_name == _pred_proc and m["sq"] == _pred_sq
        ]
        if not _pred_candidates:
            continue
        _best: int | None = None
        for _pg in _pred_candidates:
            _pg_start = solver.value(start_vars[_pg])
            _pg_dur = group_meta[_pg]["cpsat_dur"]
            _pg_drums = max(int(group_meta[_pg]["rep"].drum_count or 1), 1)
            _pg_first_drum = _pg_start + max(1, _pg_dur // _pg_drums)
            if _best is None or _pg_first_drum < _best:
                _best = _pg_first_drum
        _meta["pred_ready_wmin"] = _best or 0

    _sheath_clusters = build_sheath_clusters(group_meta)
    _sorted_clusters = sorted(
        _sheath_clusters, key=lambda c: cluster_sort_key(c, group_meta)
    )
    _cluster_rank: dict[str, tuple[int, int]] = {}
    for ci, _cluster in enumerate(_sorted_clusters):
        for gi, _gk in enumerate(_cluster.group_keys):
            _cluster_rank[_gk] = (ci, gi)

    def _solved_order_key(gk: str):
        meta = group_meta[gk]
        rep = meta["rep"]
        proc_level = PROCESS_ORDER.get(rep.process_name, 50)
        earliest = meta["earliest_due"] or date.max
        cust_prio = rep.customer_priority or 99

        # CORE/AL-CORE: ST- 선행 공정이므로 반드시 먼저 실행 (date.min으로 최우선)
        if _is_core_group(gk):
            return (
                proc_level,
                date.min,  # ST- 그룹보다 항상 앞에 오도록
                -1.0,
                _COLOR_RANK_PAD,
                _DUE_WK_PAD,
                earliest,
                cust_prio,
            )
        # ST- 연선: 소선경 클러스터 연속 배치
        if gk.startswith("ST-") and rep.process_name == "연선":
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            cluster_due = wire_d_earliest.get(wd, date.max)
            return (
                proc_level,
                cluster_due,  # 소선경 클러스터 최초 납기
                wd,  # 소선경값 (같은 클러스터 내 연속 배치)
                _COLOR_RANK_PAD,
                _DUE_WK_PAD,
                earliest,
                cust_prio,
            )
        # 시스: CP-SAT 의 start_var 를 primary 로 사용 (solver 의 tardiness/idle
        # 최소화 결정을 존중) → 빈 설비 idle 을 achievable future 클러스터로 채우게.
        # Why: cluster_rank (latest_due primary) 는 overdue 클러스터를 앞에 두어
        # achievable 클러스터의 idle-gap 활용 기회를 놓친다. 예: W17H1 흑 25 의
        # pred 가 4/9 ready 지만 SH-A120 의 4/7~4/10 gap 대신 W16H2 흑 뒤(4/21)
        # 에 배치되어 납기 초과. solver 는 chain_terms 로 color chain 도 선호하므로
        # start_var 순서는 (a) 공정 선후, (b) tardiness 최소, (c) color chain 을
        # 모두 반영한 결정이다.
        # Tiebreak: cluster_rank 로 같은 시각에 여러 그룹이 올 때 색상 체인 유지.
        if _is_sheath_group(gk, meta["batches"]):
            rank = _cluster_rank.get(gk, (10**9, 10**9))
            start_val = int(solver.value(start_vars[gk]))
            return (
                proc_level,
                date.max,
                0.0,
                start_val,
                rank[0],
                rank[1],
                earliest,
                cust_prio,
            )
        # 절연 등 기타 공정: EDD 순
        return (
            proc_level,
            earliest,
            0.0,
            _COLOR_RANK_PAD,
            _DUE_WK_PAD,
            earliest,
            cust_prio,
        )

    solved_order = sorted(groups, key=_solved_order_key)

    # CP-SAT가 선택한 설비
    cpsat_eq: dict[str, str] = {
        gk: next(ec for ec, bv in equip_vars[gk].items() if solver.value(bv) == 1)
        for gk in groups
    }

    # 상태 추적 딕셔너리
    predecessor_map: dict[tuple, int] = {}
    tasks_created: list[ScheduleTask] = []
    last_batch_on_equip: dict[str, ProductionBatch] = {}
    sq_to_equip: dict[tuple[str, int], str] = {}
    process_end_by_sq: dict[tuple[str, int], datetime] = {}
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}
    core_first_drum_by_main_sq: dict[int, datetime] = {}

    # ── 기존 scheduled 태스크를 timeline에 pre-load ───────────────────────
    # 긴급수주 추가 후 재스케줄링 시 이미 확정된 블록과의 겹침을 방지한다.
    timeline: dict[str, list] = {}
    existing_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
            ScheduleTask.equipment_code.isnot(None),
            ScheduleTask.start_datetime.isnot(None),
            ScheduleTask.end_datetime.isnot(None),
        )
        .all()
    )
    for et in existing_tasks:
        timeline.setdefault(et.equipment_code, []).append(
            (et.start_datetime, et.end_datetime)
        )
    first_insul_output: datetime | None = None
    preempted_remainder: list[ProductionBatch] = []  # 선점 분할된 잔여 배치

    # 시스 묶음 기반 append 정책용 — group_key → cluster_id / 설비별 직전 cluster
    _gk_to_cluster_id: dict[str, str] = {}
    for _c in _sorted_clusters:
        for _gk_iter in _c.group_keys:
            _gk_to_cluster_id[_gk_iter] = _c.cluster_id
    _prev_cluster_on_eq: dict[str, str] = {}

    for gk in solved_order:
        meta = group_meta[gk]
        rep = meta["rep"]
        gb = meta["batches"]
        chosen_eq_code = cpsat_eq[gk]
        sq_int = meta["sq"]
        sq_key = (rep.process_name, sq_int)

        # 이 그룹이 멀티설비 분배 대상인지 판단
        # (sq_to_equip은 이미 배치된 SQ→설비 매핑을 반영하므로 순서 의존적으로 정확함)
        is_multi, total_drums = _is_multi_equip_group(
            gk, gb, meta["eligible"], sq_to_equip
        )

        if is_multi:
            # ── 멀티설비: _schedule_multi_equipment에 위임 ────────────────
            header_batch = next((b for b in gb if b.batch_seq == -1), None)
            split_ok = _schedule_multi_equipment(
                group_key=gk,
                group_batches=gb,
                eligible=meta["eligible"],
                total_drums=total_drums,
                header_batch=header_batch,
                base_date=base_date,
                run_label=run_label,
                db=db,
                speed_map=speed_map,
                timeline=timeline,
                last_batch_on_equip=last_batch_on_equip,
                sq_to_equip=sq_to_equip,
                predecessor_map=predecessor_map,
                process_end_by_sq=process_end_by_sq,
                process_first_output_by_sq=process_first_output_by_sq,
                core_first_drum_by_main_sq=core_first_drum_by_main_sq,
                tasks_created=tasks_created,
                result=result,
                welding_min=welding_min,
                sq_to_wire_d=sq_to_wire_d,
            )
            if split_ok:
                result["total_tasks"] += 1
                continue
            # split 실패 시 단일설비로 폴백 (아래 로직 계속)

        # ── 단일설비 배치 ─────────────────────────────────────────────────
        # 연선 셋업 3-tier / 그 외 동일SQ 스킵
        actual_setup = meta["setup_min"]
        prev_batch = last_batch_on_equip.get(chosen_eq_code)
        if prev_batch and rep.process_name == "연선":
            compound_min = float(
                speed_map.get((chosen_eq_code, float(rep.sq_mm2 or 0)), None)
                and speed_map[
                    (chosen_eq_code, float(rep.sq_mm2 or 0))
                ].setup_compound_min
                or 0
            )
            actual_setup = _get_stranding_setup_min(
                float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                float(rep.sq_mm2) if rep.sq_mm2 else None,
                sq_to_wire_d,
                spec_min=meta["setup_min"],
                compound_min=compound_min,
            )
        elif prev_batch and prev_batch.sq_mm2 and rep.sq_mm2:
            if float(prev_batch.sq_mm2) == float(rep.sq_mm2):
                actual_setup = 0.0

        # 색상 교체 — 메모리 dict lookup (사전에 color_setup_map 으로 일괄 로드).
        # 기존에는 매번 `db.query(SpeedMaster).filter(...).first()` 라 배치 수 × 색상
        # 교체마다 원격 Supabase 왕복이 발생. 대형 런(수백 배치, 수십 색상 교체)
        # 에서 수 초~수십 초의 누적 지연 원인이었다.
        color_change_min = 0.0
        if prev_batch and rep.process_name in ("저압시스", "고압시스", "HFCO시스"):
            pc = (prev_batch.sheath_color or "").strip()
            cc = (rep.sheath_color or "").strip()
            if pc and cc and pc != cc:
                sm_c_val = color_setup_map.get(chosen_eq_code)
                color_change_min = resolve_color_change_min(
                    sm_color_min=sm_c_val,
                    params=constraint_params,
                )

        total_dur = (
            meta["work_dur"] + actual_setup + meta["drum_wind"] + color_change_min
        )

        # 공정 선후관계 earliest 계산
        earliest = base_date
        pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
        if pred_proc:
            all_sqs = {int(b.sq_mm2 or 0) for b in gb}
            if len(all_sqs) > 1:
                valid = [
                    t
                    for sq_i in all_sqs
                    if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                    and t < datetime.max
                ]
                if valid:
                    earliest = max(earliest, min(valid))
            else:
                pf = process_first_output_by_sq.get((pred_proc, next(iter(all_sqs))))
                if pf and pf > earliest:
                    earliest = pf
            if rep.process_name == "고압시스":
                earliest += timedelta(hours=20)

        if gk.startswith(("A100_", "A120_")):
            if first_insul_output and first_insul_output > earliest:
                earliest = first_insul_output

        if gk.startswith("ST-") and rep.process_name == "연선":
            try:
                main_sq = int(gk.split("-")[1])
            except (IndexError, ValueError):
                main_sq = sq_int
            cf = core_first_drum_by_main_sq.get(main_sq)
            if cf and cf > earliest:
                earliest = cf

        _is_st = gk.startswith("ST-") and rep.process_name == "연선"
        _skip_ind = (
            rep.process_name
            in ("저압절연", "고압절연", "저압시스", "고압시스", "연합", "T/P")
            or _is_st
        )
        if not _skip_ind:
            for b in gb:
                pt = predecessor_map.get((b.sales_order_id, b.sales_order_line))
                if pt:
                    ptask = next((t for t in tasks_created if t.task_id == pt), None)
                    if ptask and ptask.end_datetime > earliest:
                        earliest = ptask.end_datetime

        # ── 시스 묶음 기반 append 정책 ──────────────────────────────────────────
        # 묶음 내부(같은 cluster_id): 무조건 append → 색상 체인 유지
        # 묶음 경계(다른 cluster_id): 조건부 append → 납기 초과 예상이면 earliest
        #   그대로 두고 _find_available_slot 이 빈 공간 사용 (납기 보호)
        # 색상 교체 비용(수십 분)은 납기 위반(일 단위) 대비 작으므로 경계에서는
        # 납기 우선. 묶음 내부는 교체 없음이 자명하므로 무조건 append.
        if rep.process_name in ("저압시스", "고압시스"):
            current_cluster_id = _gk_to_cluster_id.get(gk)
            eq_slots = timeline.get(chosen_eq_code, [])
            prev_cluster = _prev_cluster_on_eq.get(chosen_eq_code)
            if eq_slots and current_cluster_id:
                last_end = max(slot[1] for slot in eq_slots)
                append_earliest = max(earliest, last_end)
                if prev_cluster == current_cluster_id:
                    earliest = append_earliest
                else:
                    append_end = calculate_end_datetime(
                        append_earliest, total_dur, db, chosen_eq_code
                    )
                    due = meta["earliest_due"]
                    if not due or append_end.date() <= due:
                        earliest = append_earliest

        # ── 시스 weekend-aware: 긴 batch 가 주말에 걸치면 다음 업무일 시작 ─────
        # 실측 버그: 금요일 20:00 시작 갈 400SQ 가 주말 60h idle 후 월요일 11:00
        # 종료 → 뒤따르는 8 개 묶음 모두 +1~+4 일 late. 긴 batch(>4h)가 금요일
        # 오후 늦게 시작해 주말에 걸치는 것으로 예측되면 earliest 를 다음 월요일
        # 08:00 으로 밀어 주말 걸침 회피. 설비 idle 약간 증가하나 뒤따르는 묶음
        # 전체 지연 회피로 순이득.
        if rep.process_name in ("저압시스", "고압시스"):
            # 임시 end 계산: 현재 earliest 에 append 시 언제 끝나는가
            _tentative_end = calculate_end_datetime(
                earliest, total_dur, db, chosen_eq_code
            )
            _cross_weekend = False
            _d = earliest.date()
            while _d < _tentative_end.date():
                if _d.weekday() >= 5:  # 토/일
                    _cross_weekend = True
                    break
                _d += timedelta(days=1)
            if _cross_weekend and total_dur > 240:
                # 긴 batch 가 주말 걸침 → 다음 월요일 08:00 으로 이동
                _e = earliest
                while _e.weekday() >= 5:
                    _e = _e.replace(
                        hour=8, minute=0, second=0, microsecond=0
                    ) + timedelta(days=1)
                if _e.weekday() == 4 and _e.hour >= 17:  # 금 17시 이후면 월요일로
                    _e = _e.replace(
                        hour=8, minute=0, second=0, microsecond=0
                    ) + timedelta(days=(7 - _e.weekday()) % 7 or 3)
                # Due 초과 우려시만 적용 (납기 지킬 수 있는 그룹만 회피)
                _new_end = calculate_end_datetime(_e, total_dur, db, chosen_eq_code)
                due = meta["earliest_due"]
                if not due or _new_end.date() <= due:
                    earliest = _e

        # ── 긴급/중요 배치: 납기 위반 예상 시 선점 분할 시도 ────────────────────
        if (
            _priority_label(rep.customer_priority) in ("urgent", "critical")
            and meta["earliest_due"]
        ):
            slots_sim = timeline.get(chosen_eq_code, [])
            sim_start = _find_available_slot(
                earliest, total_dur, slots_sim, db, chosen_eq_code
            )
            sim_end = calculate_end_datetime(sim_start, total_dur, db, chosen_eq_code)
            if sim_end.date() > meta["earliest_due"] and sim_start > earliest:
                # 납기 초과 + earliest보다 늦게 시작 → 선점 가능 여부 시도
                rem_list = try_preempt_for_urgent(
                    earliest=earliest,
                    chosen_eq_code=chosen_eq_code,
                    run_label=run_label,
                    timeline=timeline,
                    db=db,
                    urgent_priority=int(rep.customer_priority or 7),
                    predecessor_map=predecessor_map,
                )
                if rem_list:
                    preempted_remainder.extend(rem_list)
                    result["warnings"].append(
                        f"선점분할: 배치그룹 {gk} 납기 {meta['earliest_due']} 맞추기 위해 "
                        f"{rem_list[0].batch_group} 잔여 {rem_list[0].drum_count}드럼 후처리 예약"
                    )

        # 캘린더 인식 슬롯 탐색 — 겹침 완전 방지
        slots = timeline.get(chosen_eq_code, [])
        best_start = _find_available_slot(
            earliest, total_dur, slots, db, chosen_eq_code
        )
        end_dt = calculate_end_datetime(best_start, total_dur, db, chosen_eq_code)

        # ── 파이프라인 유휴 최소 역산 — start 지연 방식 ──────────────────────
        # T_succ_end = T_pred_end + 후공정 1드럼 소요, 블록 폭(total_dur)은 고정.
        # lot_count: 헤더 있으면 그 drum_count, 없으면 gb 합산.
        _header_p = next((b for b in gb if b.batch_seq == -1), None)
        if _header_p is not None:
            _lot_count_p = max(int(_header_p.drum_count or 1), 1)
        else:
            _lot_count_p = max(sum(int(b.drum_count or 1) for b in gb), 1)
        _per_drum_p = meta["work_dur"] / _lot_count_p if _lot_count_p > 0 else 0.0
        best_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in gb},
            process_end_by_sq=process_end_by_sq,
            current_start=best_start,
            current_end=end_dt,
            duration_min=total_dur,
            tail_offset_min=_per_drum_p,
            slots=slots,
            db=db,
            equipment_code=chosen_eq_code,
        )

        # 정각 올림
        if end_dt.minute > 0 or end_dt.second > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # 체인 하이라이트 — 대표 order 의 상류 task id 를 predecessor 로 기록
        rep_pred_task_id = predecessor_map.get(
            (rep.sales_order_id, rep.sales_order_line)
        )

        task = ScheduleTask(
            batch_id=rep.batch_id,
            equipment_code=chosen_eq_code,
            start_datetime=best_start,
            end_datetime=end_dt,
            setup_time_min=actual_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=gk,
            predecessor_task_id=rep_pred_task_id,
        )
        db.add(task)
        db.flush()

        # timeline 갱신 (이후 그룹이 이 슬롯을 피할 수 있도록)
        timeline.setdefault(chosen_eq_code, []).append((best_start, end_dt))

        # 파이프라인 첫 드럼 출력 시각 갱신
        header_batch = next((b for b in gb if b.batch_seq == -1), None)
        if header_batch:
            lot_count = max(int(header_batch.drum_count or 1), 1)
        else:
            # CORE 그룹 포함, 헤더 없는 그룹 모두 drum_count 합산 (len(gb) 아님)
            lot_count = max(sum(int(b.drum_count or 1) for b in gb), 1)
        first_drum_min = actual_setup + (meta["work_dur"] / lot_count)
        first_output_dt = calculate_end_datetime(
            best_start, first_drum_min, db, chosen_eq_code
        )

        proc_sq_key = (rep.process_name, sq_int)
        if not _is_core_group(gk):
            if (
                proc_sq_key not in process_first_output_by_sq
                or first_output_dt < process_first_output_by_sq[proc_sq_key]
            ):
                process_first_output_by_sq[proc_sq_key] = first_output_dt
        else:
            msq = _extract_core_main_sq(gk)
            if msq and (
                msq not in core_first_drum_by_main_sq
                or first_output_dt < core_first_drum_by_main_sq[msq]
            ):
                core_first_drum_by_main_sq[msq] = first_output_dt

        if rep.process_name == "저압절연":
            if first_insul_output is None or first_output_dt < first_insul_output:
                first_insul_output = first_output_dt

        if (
            proc_sq_key not in process_end_by_sq
            or end_dt > process_end_by_sq[proc_sq_key]
        ):
            process_end_by_sq[proc_sq_key] = end_dt

        for b in gb:
            predecessor_map[(b.sales_order_id, b.sales_order_line)] = task.task_id
            b.equipment_code = chosen_eq_code
            b.status = "scheduled"

        if rep.process_name == "연선" and not _is_core_group(gk):
            sq_to_equip[sq_key] = chosen_eq_code

        last_batch_on_equip[chosen_eq_code] = gb[-1]
        tasks_created.append(task)

        # 시스 묶음 기반 append 정책 — 현재 그룹의 cluster_id 로 갱신
        if rep.process_name in ("저압시스", "고압시스"):
            _cid = _gk_to_cluster_id.get(gk)
            if _cid:
                _prev_cluster_on_eq[chosen_eq_code] = _cid

        # 납기 위반 기록 — hard constraint 위반이므로 error 격상
        if meta["earliest_due"] and end_dt.date() > meta["earliest_due"]:
            late_days = (end_dt.date() - meta["earliest_due"]).days
            result["violations"].append(
                {
                    "batch_id": rep.batch_id,
                    "task_id": task.task_id,
                    "type": "delivery",
                    "severity": "error",
                    "detail": f"납기 {meta['earliest_due']} 초과 → 완료 {end_dt.date()} (+{late_days}일)",
                }
            )

        log_decision(
            db=db,
            run_label=run_label,
            stage="stage2",
            action_type="auto_assign",
            batch_id=rep.batch_id,
            task_id=task.task_id,
            reason=f"CP-SAT 순서 → {chosen_eq_code} @ {best_start:%Y-%m-%d %H:%M}",
        )
        result["total_tasks"] += 1

    # ── 9. 선점 잔여 배치 후속 배치 ───────────────────────────────────────────
    # 선점 분할로 생성된 잔여 배치들을 같은 설비에서 순서대로 스케줄링한다.
    # (이미 긴급 배치 슬롯이 timeline에 등록되어 있으므로 겹치지 않는다.)
    for rem_b in preempted_remainder:
        eq_code = rem_b.equipment_code
        if not eq_code:
            continue
        # 밀어낸 단드럼 배치는 setup_time을 유지; 분할 잔여는 setup=0 (이미 설정됨)
        rem_setup = float(rem_b.setup_time_min or 0)
        work_dur = float(rem_b.estimated_duration_min or 0)
        total_rem_dur = work_dur + rem_setup
        slots_rem = timeline.get(eq_code, [])
        rem_start = _find_available_slot(
            base_date, total_rem_dur, slots_rem, db, eq_code
        )
        rem_end = calculate_end_datetime(rem_start, total_rem_dur, db, eq_code)
        if rem_end.minute > 0 or rem_end.second > 0:
            rem_end = rem_end.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # 체인 하이라이트 — 잔여 배치도 같은 (order, line) 의 predecessor 계보 유지
        rem_pred_task_id = predecessor_map.get(
            (rem_b.sales_order_id, rem_b.sales_order_line)
        )

        rem_task = ScheduleTask(
            batch_id=rem_b.batch_id,
            equipment_code=eq_code,
            start_datetime=rem_start,
            end_datetime=rem_end,
            setup_time_min=rem_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=rem_b.batch_group,
            predecessor_task_id=rem_pred_task_id,
        )
        db.add(rem_task)
        db.flush()

        timeline.setdefault(eq_code, []).append((rem_start, rem_end))
        rem_b.status = "scheduled"
        rem_b.equipment_code = eq_code
        result["total_tasks"] += 1

    # ── Task 2A.3: write one solver_run + N solver_decision rows ──────────
    # Phase 2 Task 2.7 (B-3.2): 본 trace write 블록은 ``_trace_writer.write_solver_trace``
    # 로 추출. 본문은 1:1 보존 — try/except observability 가드 그대로.
    # Why here (final return path) and not at the early-exit paths:
    #   - Early returns (no batches, no groups, solver INFEASIBLE) represent
    #     degenerate states where an `assignments`-shaped trace would be
    #     empty/meaningless. Week 4 may want to start tracing those too;
    #     for now we trace only the "real" solve path that actually
    #     produced ScheduleTask rows.
    write_solver_trace(
        db=db,
        run_id=_run_id,
        run_label=run_label,
        started_at=_started_at,
        base_date=base_date,
        batches=batches,
        solver_input_override=solver_input_override,
        solver=solver,
        solver_status=status,
        built=_built,
        result=result,
        random_seed=int(random_seed),
        time_limit_sec=time_limit_sec,
        sheath_color_hard=sheath_color_hard,
        tardiness_hard=tardiness_hard,
    )

    # Task 2A.4 (spec §10a): reset the contextvar on the happy path.
    # Early-return sites are covered by the outer RunIdMiddleware's
    # own reset (HTTP path) or by pytest's per-test context (test
    # path); see the comment near the _set_run_id call above.
    from app.infrastructure.logging import reset_run_id as _reset_run_id

    _reset_run_id(_ctx_token)
    return result
