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
import uuid
from datetime import datetime, timezone
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask

# Week 3 Task 3A.2 wiring (sub-commit D):
#   greedy / scheduling_shared 이 분리되면서 cp_sat → schedule_optimizer 의
#   top-level import 가 모두 사라진다. domain.constants / scheduling_shared /
#   greedy.slot_finder 직접 참조로 순환 의존성을 제거한다.
from app.application.scheduling.cp_sat import SolverInput
from app.application.scheduling.cp_sat.model_builder import (
    BuiltModel,
    build_model,
)
from app.application.scheduling.cp_sat._load_inputs import load_solver_inputs
from app.application.scheduling.cp_sat._trace_writer import write_solver_trace
from app.application.scheduling.cp_sat._preemption_runner import (
    schedule_preempted_remainders,
)
from app.application.scheduling.cp_sat._solver_runner import (
    prepare_model_inputs,
    run_solver,
)
from app.application.scheduling.cp_sat._diagnostic_snapshot import (
    write_diagnostic_snapshot,
)
from app.application.scheduling.cp_sat._calendar_apply import (
    apply_calendar_greedy,
    apply_sheath_color_sort,
    preload_existing_timeline,
    resolve_first_due_by_strand_cluster,
)

# Phase 3 step 2: 가중치 상수 + 워커/우선순위/duration helpers + group meta
# builder 를 helpers.py 에 단일 source 로 이동. orchestrator 는 import 만.
from app.application.scheduling.cp_sat.helpers import (
    _build_group_meta,  # noqa: F401  # used at §4-5 inline (Phase 3 step 2)
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


# Phase 2 Task 2.9 (B-3.4): `_spec_weight_factory` 본 정의는 `_solver_runner.py`
# 로 이동. 단위 테스트(test_priority_slider_objective.py) 가 import path
# `app.application.scheduling.cp_sat.orchestrator._spec_weight_factory` 를 사용
# 하므로 본 모듈에서 re-export 유지 (D7-C 호환).
from app.application.scheduling.cp_sat._solver_runner import (  # noqa: E402, F401
    _spec_weight_factory,
)


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
    # ── Phase 6 step 3 (2026-05): lex_min_time 을 default 로 승격 ──────────
    # 납기 lexicographic 우선 (Phase A: max_tardiness 최소 → Phase B: makespan
    # 최소 → Phase C: W-* soft term 최소). True 시 lex 3-phase 호출, False 시
    # 기존 weighted-sum 단일 호출 (opt-in 잔존: 비교·롤백·dual-run 용).
    # INFEASIBLE_A/B/UNKNOWN 발생 시 자동으로 weighted-sum 폴백 (model 은
    # deepcopy 가 아니라 rebuild_fn 으로 재구성).
    min_time_mode: bool = True,
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

    # Phase 6 step 12 (2026-05): 17s 의 lex 밖 12s 분해 instrumentation.
    # 각 후처리 단계의 wall-time 을 logger.info 로 노출해 design 단계의
    # hotspot 식별 근거 확보. quality 영향 0 (측정 only).
    import logging as _log
    import time as _stage_time

    _stage_logger = _log.getLogger(__name__)
    _stage_t = _stage_time.perf_counter()

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
    _stage_logger.info(
        "cp_sat stage[load_solver_inputs]: wall=%.3fs batches=%d",
        _stage_time.perf_counter() - _stage_t,
        len(load_out.batches) if load_out.batches else 0,
    )
    _stage_t = _stage_time.perf_counter()

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
    _stage_logger.info(
        "cp_sat stage[build_group_meta]: wall=%.3fs n_groups=%d",
        _stage_time.perf_counter() - _stage_t,
        len(group_meta),
    )
    _stage_t = _stage_time.perf_counter()

    # ── 6. CP-SAT 모델 구성 ───────────────────────────────────────────────
    # Phase 2 Task 2.9 (B-3.4): §6 의 frozen_tasks_snapshot 빌드 + ConstraintSpec
    # 가중치 계산은 ``_solver_runner.prepare_model_inputs`` 로 추출.
    _model_inputs = prepare_model_inputs(
        db=db,
        run_label=run_label,
        base_date=base_date,
        frozen_group_keys=frozen_group_keys,
        group_meta=group_meta,
    )
    _weights = _model_inputs.weights
    frozen_tasks_snapshot = _model_inputs.frozen_tasks_snapshot

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
    _stage_logger.info(
        "cp_sat stage[prepare_inputs+build_model]: wall=%.3fs",
        _stage_time.perf_counter() - _stage_t,
    )
    _stage_t = _stage_time.perf_counter()

    # ── 7. lex 시도 → 폴백 → weighted-sum 솔버 실행 ────────────────────────
    # Phase 2 Task 2.9 (B-3.4): §7-pre/7-a/7-b 본문은 ``_solver_runner.run_solver``
    # 로 추출. closure ``_build_solver_model`` 을 rebuild_fn 으로 주입하여
    # lex INFEASIBLE 폴백 시 모델을 재구성할 수 있게 한다 (cp_model.IntAffine
    # 가 deepcopy 불가능한 invariant 보존).
    _solve_res = run_solver(
        built=_built,
        rebuild_fn=_build_solver_model,
        weights=_weights,
        group_meta=group_meta,
        min_time_mode=min_time_mode,
        tardiness_hard=tardiness_hard,
        sheath_color_hard=sheath_color_hard,
        time_limit_sec=time_limit_sec,
        num_search_workers=num_search_workers,
        random_seed=int(random_seed),
        result=result,
    )
    _built = _solve_res.built
    solver = _solve_res.solver
    status = _solve_res.status
    _solve_wall_s = _solve_res.solve_wall_s
    _num_workers = _solve_res.num_workers

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
    _stage_logger.info(
        "cp_sat stage[run_solver_total]: wall=%.3fs status=%s",
        _stage_time.perf_counter() - _stage_t,
        status_name,
    )
    _stage_t = _stage_time.perf_counter()

    # Phase 2 Task 2.10a: 진단 스냅샷 호출은 ``_diagnostic_snapshot`` 으로
    # 추출. BuiltModel 을 그대로 전달하여 vars/terms unpacking 을 helper 에 위임.
    write_diagnostic_snapshot(
        run_label=run_label,
        base_date=base_date,
        group_meta=group_meta,
        frozen_group_keys=frozen_group_keys,
        solver=solver,
        status_name=status_name,
        built=_built,
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
    # Phase 2 Task 2.10b: ``_calendar_apply.resolve_first_due_by_strand_cluster``
    # 로 추출. schedule_optimizer 의 wire_d_earliest 와 동일한 로직.
    wire_d_earliest = resolve_first_due_by_strand_cluster(
        groups=groups,
        group_meta=group_meta,
        sq_to_wire_d=sq_to_wire_d,
    )

    # Phase 2 Task 2.10c: 시스 색상 묶음 정렬 + solved_order + cpsat_eq +
    # gk_to_cluster_id 빌드는 ``_calendar_apply.apply_sheath_color_sort`` 로
    # 추출. group_meta["pred_ready_wmin"] 은 in-place 갱신 (원본 동작 보존).
    _sort_res = apply_sheath_color_sort(
        groups=groups,
        group_meta=group_meta,
        solver=solver,
        start_vars=start_vars,
        equip_vars=equip_vars,
        wire_d_earliest=wire_d_earliest,
        sq_to_wire_d=sq_to_wire_d,
    )
    solved_order = _sort_res.solved_order
    cpsat_eq = _sort_res.cpsat_eq
    _sorted_clusters = _sort_res.sorted_clusters
    _gk_to_cluster_id = _sort_res.gk_to_cluster_id

    # 상태 추적 딕셔너리
    predecessor_map: dict[tuple, int] = {}
    tasks_created: list[ScheduleTask] = []
    last_batch_on_equip: dict[str, ProductionBatch] = {}
    sq_to_equip: dict[tuple[str, int], str] = {}
    process_end_by_sq: dict[tuple[str, int], datetime] = {}
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}
    core_first_drum_by_main_sq: dict[int, datetime] = {}

    # Phase 2 Task 2.10d: 기존 scheduled 태스크의 timeline pre-load 는
    # ``_calendar_apply.preload_existing_timeline`` 으로 추출.
    timeline = preload_existing_timeline(db=db, run_label=run_label)
    preempted_remainder: list[ProductionBatch] = []  # 선점 분할된 잔여 배치

    # Phase 6 step 14 (2026-05): deferred bulk update buffer.
    # Why: apply_calendar_greedy 의 매 batch placement 마다 ProductionBatch
    # state mutation 이 explicit db.flush() 와 함께 81회 SQL UPDATE round-trip
    # (21s) 으로 emit 되던 것을, buffer 에 누적 → 끝에서 1회 bulk_update_
    # mappings 으로 commit (~1-3s). 81 → 1 round-trip.
    from app.application._shared.audit_logger import AuditLogBuffer
    from app.application.scheduling.cp_sat._calendar_apply import BatchStateBuffer

    state_buffer = BatchStateBuffer()
    # Phase 6 Task 5 (2026-05-17): audit_log INSERT 도 buffer 로 묶음.
    # log_decision 매 호출이 db.add(AuditLog(...)) 후 explicit db.flush() 와
    # 같이 emit → ~수 초. bulk_insert_mappings 1회로 압축.
    audit_buffer = AuditLogBuffer()

    # Phase 2 Task 2.10e: §8 main loop 본체는 ``_calendar_apply.apply_calendar_greedy``
    # 로 추출. 모든 상태 dict (timeline / predecessor_map / tasks_created /
    # last_batch_on_equip / sq_to_equip / process_end_by_sq /
    # process_first_output_by_sq / core_first_drum_by_main_sq /
    # preempted_remainder / result) 가 in-place mutate.
    apply_calendar_greedy(
        solved_order=solved_order,
        group_meta=group_meta,
        cpsat_eq=cpsat_eq,
        gk_to_cluster_id=_gk_to_cluster_id,
        timeline=timeline,
        base_date=base_date,
        run_label=run_label,
        db=db,
        speed_map=speed_map,
        color_setup_map=color_setup_map,
        constraint_params=constraint_params,
        welding_min=welding_min,
        sq_to_wire_d=sq_to_wire_d,
        predecessor_map=predecessor_map,
        tasks_created=tasks_created,
        last_batch_on_equip=last_batch_on_equip,
        sq_to_equip=sq_to_equip,
        process_end_by_sq=process_end_by_sq,
        process_first_output_by_sq=process_first_output_by_sq,
        core_first_drum_by_main_sq=core_first_drum_by_main_sq,
        preempted_remainder=preempted_remainder,
        result=result,
        state_buffer=state_buffer,
        audit_buffer=audit_buffer,
    )
    _stage_logger.info(
        "cp_sat stage[apply_calendar_greedy]: wall=%.3fs n_tasks_so_far=%d preempted=%d",
        _stage_time.perf_counter() - _stage_t,
        result.get("total_tasks", 0),
        len(preempted_remainder),
    )
    _stage_t = _stage_time.perf_counter()

    # ── 9. 선점 잔여 배치 후속 배치 ───────────────────────────────────────────
    # Phase 2 Task 2.8 (B-3.3): 본 블록은 ``_preemption_runner.schedule_preempted_remainders``
    # 로 추출. timeline / preempted_remainder / predecessor_map / result
    # 모두 in-place mutate (원본 동작 그대로).
    schedule_preempted_remainders(
        preempted_remainder=preempted_remainder,
        timeline=timeline,
        base_date=base_date,
        run_label=run_label,
        db=db,
        predecessor_map=predecessor_map,
        result=result,
        state_buffer=state_buffer,
    )
    _stage_logger.info(
        "cp_sat stage[schedule_preempted_remainders]: wall=%.3fs",
        _stage_time.perf_counter() - _stage_t,
    )
    _stage_t = _stage_time.perf_counter()

    # Phase 6 step 14: buffer 누적된 ProductionBatch state mutation 을 1회
    # bulk_update_mappings 으로 commit. 81 SQL round-trip → 1.
    n_state = state_buffer.flush(db)
    _stage_logger.info(
        "cp_sat stage[state_buffer.flush]: wall=%.3fs n=%d",
        _stage_time.perf_counter() - _stage_t,
        n_state,
    )
    _stage_t = _stage_time.perf_counter()

    # Phase 6 Task 5: AuditLog INSERT buffer flush — bulk_insert_mappings 1회.
    n_audit = audit_buffer.flush(db)
    _stage_logger.info(
        "cp_sat stage[audit_buffer.flush]: wall=%.3fs n=%d",
        _stage_time.perf_counter() - _stage_t,
        n_audit,
    )
    _stage_t = _stage_time.perf_counter()

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
