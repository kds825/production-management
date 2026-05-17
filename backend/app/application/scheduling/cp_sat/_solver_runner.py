"""cp_sat_schedule() 의 §6 + §7 — 모델 weights/frozen prep + lex/weighted solve.

원본: ``orchestrator.py:390-617`` 본문 그대로. Phase 2 Task 2.9 (B-3.4) 추출.

본 모듈은 두 개의 use-case helper 를 제공한다:

1. ``prepare_model_inputs`` — §6 의 frozen_tasks_snapshot 빌드 + ConstraintSpec
   기반 weights 계산. orchestrator 가 build_model 을 호출하기 위한 입력 준비.
   DB 접근 필요 (ScheduleTask join + load_active_constraints) → boundary crosser.

2. ``run_solver`` — §7-pre/7-a/7-b. lex 시도 → 성공이면 그 결과, 실패면
   weighted-sum 폴백. CpSolver 파라미터 설정 + solve() wall-time 측정.

Why 두 함수로 분리:
    §6 의 build_model 호출 자체는 orchestrator 가 수행 (closure 로 lex
    INFEASIBLE 폴백 시 재호출 가능해야 함 — cp_model.IntAffine 이
    deepcopy 불가능). prepare_model_inputs 는 weights/frozen 만 생성.
    run_solver 는 build_model 결과(BuiltModel) + rebuild_fn 을 받아
    실제 solve 만 담당.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from app.application._shared.calendar_ops import _datetime_to_wmin
from app.application.scheduling.cp_sat.constraint_loader import (
    ConstraintSpec,
    load_active_constraints,
)
from app.application.scheduling.cp_sat.helpers import (
    _EDD_MIXED_PASTDUE_WEIGHT,
    _EDD_PAIR_WEIGHT,
    _IDLE_WEIGHT,
    _MAX_HORIZON_MIN,
    _PAST_SEVERITY_K,
    _SLACK_WEIGHT_BASE,
    _SOLVER_TIME_LIMIT_SEC,
    _TARDINESS_WEIGHT,
    _resolve_num_workers,
    compute_horizon,  # noqa: F401 — Phase 6 step 4; formatter 가 unused 로 오인 방지
)
from app.application.scheduling.cp_sat.model_builder import BuiltModel, ModelWeights
from app.application.scheduling.cp_sat.objective import compose_objective
from app.domain.constants import (
    _CHAIN_WEIGHT,
    _DUE_HARD_WEIGHT,
    _TRANSITION_WEIGHT,
    _WORK_MIN_PER_DAY,
)
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


def _spec_weight_factory(specs_by_id: dict):
    """`_spec_weight(cid, fallback)` 클로저를 만들어 반환 — Week 5A.4 wiring.

    Reads `ConstraintSpec.params["weight"]` and **scales by `priority / 50.0`**
    so the Admin-UI priority slider is causally wired to the solver objective.
    `priority=50` (DB default for all W-* rows) → factor 1.0 → 기존 11개
    fixture hash 무회귀 보장 (parity-preserving by construction).
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


@dataclass
class _ModelInputs:
    """§6 prep 결과 묶음 — orchestrator 가 build_model 호출에 사용."""

    weights: ModelWeights
    frozen_tasks_snapshot: dict[str, dict[str, Any]] | None


def prepare_model_inputs(
    *,
    db: Session,
    run_label: str,
    base_date: datetime,
    frozen_group_keys: set[str] | None,
    group_meta: dict[str, Any] | None = None,
) -> _ModelInputs:
    """§6 의 frozen_tasks_snapshot + ConstraintSpec weights 빌드.

    원본: orchestrator.py:394-456 본문 그대로.

    Phase 6 step 4: ``group_meta`` 를 받으면 ``compute_horizon`` 으로 동적
    horizon 산정 후 ``ModelWeights.MAX_HORIZON_MIN`` 에 반영. 미전달 시 기존
    상수 ``_MAX_HORIZON_MIN`` 유지 (기존 caller 호환).
    """
    # ── 6. CP-SAT 모델 구성 (1/2): frozen_tasks_snapshot 빌드 ──────────────
    # Task 2A.2 (Rev 3): §6 블록은 model_builder.build_model 로 이전됐다.
    # DB 접근이 필요한 frozen_group_keys 는 여기서 스냅샷 dict 로 변환하여
    # pure 함수에 주입한다 (services/solver/ 경계 불변식).
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
                "end_wmin": _datetime_to_wmin(_tk.end_datetime, base_date),
                "equipment_code": _tk.equipment_code,
            }
            for _bg, _tk in frozen_task_by_gk.items()
        }

    # Week 5 Task 5A.3: 가중치 source 가 Python 상수 → DB-driven (ConstraintSpec
    # params_json["weight"]) 으로 단계적 이전 중. 누락/disable 행은 fallback 으로
    # 기존 상수 유지 → parity 보존. 하나씩 옮기며 sub-commit 단위로 검증.
    _specs_by_id: dict[str, ConstraintSpec] = {
        s.constraint_id: s for s in load_active_constraints(db)
    }

    # Week 5A.4: priority 슬라이더 wiring. factory 가 weight × (priority/50)
    # 스케일 적용. 모든 W-* row 의 priority=50 (DB default) → factor 1.0 →
    # 기존 11/27 fixture hash 무회귀.
    _spec_weight = _spec_weight_factory(_specs_by_id)

    # _TARDINESS_WEIGHT 는 dict — 각 urgency tier 를 별도 W-* row 로 매핑 후 재구성.
    _tardiness_weight_db = {
        "critical": _spec_weight("W-TCRIT", _TARDINESS_WEIGHT["critical"]),
        "urgent": _spec_weight("W-TURG", _TARDINESS_WEIGHT["urgent"]),
        "normal": _spec_weight("W-TNORM", _TARDINESS_WEIGHT["normal"]),
    }

    # Phase 6 step 4: 동적 horizon. group_meta 가 None 이면 기존 상수 유지.
    _horizon = (
        compute_horizon(group_meta, frozen_tasks_snapshot)
        if group_meta is not None
        else _MAX_HORIZON_MIN
    )

    weights = ModelWeights(
        DUE_HARD_WEIGHT=_spec_weight("W-DHARD", _DUE_HARD_WEIGHT),
        TARDINESS_WEIGHT=_tardiness_weight_db,
        CHAIN_WEIGHT=_spec_weight("W-CHAIN", _CHAIN_WEIGHT),
        IDLE_WEIGHT=_spec_weight("W-IDLE", _IDLE_WEIGHT),
        SLACK_WEIGHT_BASE=_spec_weight("W-SLACK", _SLACK_WEIGHT_BASE),
        PAST_SEVERITY_K=_spec_weight("W-PSEV", _PAST_SEVERITY_K),
        EDD_PAIR_WEIGHT=_spec_weight("W-EDDP", _EDD_PAIR_WEIGHT),
        EDD_MIXED_PASTDUE_WEIGHT=_spec_weight("W-EDDM", _EDD_MIXED_PASTDUE_WEIGHT),
        TRANSITION_WEIGHT=_spec_weight("W-TRANS", _TRANSITION_WEIGHT),
        MAX_HORIZON_MIN=_horizon,
        WORK_MIN_PER_DAY=_WORK_MIN_PER_DAY,
    )

    return _ModelInputs(weights=weights, frozen_tasks_snapshot=frozen_tasks_snapshot)


@dataclass
class _SolveResult:
    """§7 run_solver 의 결과 — orchestrator 가 §8 캘린더 그리디 입력으로 사용."""

    built: BuiltModel
    solver: cp_model.CpSolver
    status: int
    solve_wall_s: float
    num_workers: int


def run_solver(
    *,
    built: BuiltModel,
    rebuild_fn: Callable[[], BuiltModel],
    weights: ModelWeights,
    group_meta: dict,
    min_time_mode: bool,
    tardiness_hard: bool,
    sheath_color_hard: bool,
    time_limit_sec: int | None,
    num_search_workers: int | None,
    random_seed: int,
    result: dict[str, Any],
) -> _SolveResult:
    """§7-pre/7-a/7-b: lex 시도 → 폴백 → weighted-sum solve.

    원본: orchestrator.py:478-602 본문 그대로.

    ``result`` in-place mutate: solver_mode / lex_t_star / lex_makespan_min /
    lex_all_due_met / warm_start_applied / warm_start_skipped / warnings.
    """
    # ── 7-pre. 솔버 공통 파라미터 (lex / weighted 두 path 공유) ───────────
    # time_limit_sec override — 증분 경로는 10s, 전역 재최적화는 60s 등 호출자
    # 시나리오에 따라 조정. None/<=0 이면 기본값 유지 (하위호환).
    _time_limit = (
        int(time_limit_sec)
        if (time_limit_sec and int(time_limit_sec) > 0)
        else _SOLVER_TIME_LIMIT_SEC
    )
    # 워커 수: 환경변수 기반 해상도. 운영 기본 8, CI/테스트는 1로 강제 (결정론).
    # Task 1.1 (Rev 3): num_search_workers kwarg 가 주어지면 env 해상도를
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
    solver: cp_model.CpSolver | None = None
    status: int = 0
    _solve_wall_s: float = 0.0
    _built = built

    if min_time_mode:
        from app.application.scheduling.cp_sat.lex_min_time import (
            _adapt_lex_to_solve_result,
            solve_lex_min_time,
        )

        # lex 가 _built.model 을 mutate 한다 (max_tard 변수 + Phase B 의
        # max_tard ≤ T* hard constraint). cp_model 내부 IntAffine 객체가
        # picklable 하지 않아 deepcopy 불가능 → INFEASIBLE 폴백 시 model
        # 을 rebuild_fn() 로 재구성. 일반 lex 성공 경로는 추가 비용 0.
        _lex_t0 = time.perf_counter()
        # Phase 6 step 3: weights/group_meta/tardiness_hard 를 전달해 lex Phase C
        # 가 compose_objective (W-* slider 반영) 를 실행하도록 한다. 둘 다
        # None 으로 두면 Phase C skip → 기존 2-phase 동작 (parity 보존).
        _lex_res = solve_lex_min_time(
            _built,
            time_limit_phase_a_sec=_time_limit,
            time_limit_phase_b_sec=_time_limit,
            num_workers=_num_workers,
            random_seed=int(random_seed),
            weights=weights,
            group_meta=group_meta,
            tardiness_hard=tardiness_hard,
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
            _built = rebuild_fn()
            result["solver_mode"] = "weighted_sum_fallback_from_lex"
    else:
        result["solver_mode"] = "weighted_sum"

    # weighted-sum 경로: lex 미사용 또는 lex INFEASIBLE 폴백 시.
    if not _used_lex:
        compose_objective(
            _built,
            weights=weights,
            group_meta=group_meta,
            tardiness_hard=tardiness_hard,
        )

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
        status = solver.solve(_built.model)
        _solve_wall_s = time.perf_counter() - _solve_t0

    return _SolveResult(
        built=_built,
        solver=solver,  # type: ignore[arg-type]
        status=status,
        solve_wall_s=_solve_wall_s,
        num_workers=_num_workers,
    )
