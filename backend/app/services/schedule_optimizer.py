"""자동 스케줄링 엔진 — 납기역산 + 그리디 배치

── Task #7 조사 결과 ─────────────────────────────────────────────────
1. batch_seq 정렬:
   - 61연선의 7연선 코어 배치(batch_seq=0)가 본 배치(batch_seq=1)보다 먼저
     스케줄링되도록 batches query에서 batch_seq.asc()를 포함한다 (line 78).
   - 이는 predecessor_map을 통해 코어→본 배치 선행관계를 올바르게 구성하기 위함이다.

2. 공정 간 선행관계:
   - _PREDECESSOR_PROCESS dict가 연선→절연→시스 파이프라인을 강제한다.
   - process_end_by_sq로 같은 SQ의 앞 공정 종료 시각을 추적하여 후공정 시작을 지연시킨다.
   - A100/A120 시스 배치는 저압절연 첫 번째 드럼 출력 후 시작 (파이프라인 겹침).

3. 제한사항:
   - 프론트엔드 간트에서의 수동 블록 이동 시에는 이 파이프라인 제약이 재적용되지 않는다.
   - scheduleStore.ts의 moveTask는 같은 order_id의 후공정만 연동하며,
     cross-equipment cascade는 미구현 상태이다.
"""

import os
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.domain.constants import (
    PROCESS_ORDER,
    PREDECESSOR_PROCESS,  # re-export until Week 9 (D7-C)
    _DEFAULT_WELDING_MIN,  # re-export until Week 9 (D7-C)
    _WIP_SKIP_PROCESSES,  # re-export until Week 9 (D7-C)
)
from app.services.calendar_engine import (
    calculate_end_datetime,
    calculate_start_datetime,
)
from app.services.audit_logger import log_decision
from app.services.constraint_params import ConstraintParams, resolve_color_change_min
from app.services.jit_scheduling import apply_jit_delay
from app.exceptions import SchedulerOverlapError


def _should_apply_jit() -> bool:
    """SCHEDULER_JIT env var 읽어 JIT post-processing on/off 결정.

    "1" / "true" / "yes" (case-insensitive) 이면 활성. 그 외 (unset, "0") 비활성.
    """
    v = os.environ.get("SCHEDULER_JIT", "").strip().lower()
    return v in ("1", "true", "yes", "on")


# _WIP_SKIP_PROCESSES, _DEFAULT_WELDING_MIN 은 app.domain.constants 로 이동
# (Week 3 Task 3A.1). 위 import 블록에서 re-export 되어 기존 path 유지.

# 시스 재질 → 설비 라우팅 규칙 (10-3)
# 값은 equipment_code prefix 또는 특수 라우팅 키
_SHEATH_ROUTING = {
    "HFPO": "HFPO",  # HFPO 전용 라우팅 (설비 선택 시 HFPO 계열만)
    "PVC": "PVC",  # 표준 PVC 라우팅
    "LLDPE": "A150",  # LLDPE → A150 설비 고정
}

# PREDECESSOR_PROCESS 는 app.domain.constants 로 이동 (Week 3 Task 3A.1).
# 위 import 블록에서 re-export 되어 기존 path 유지 (D7-C).


def _is_core_group(group_key: str) -> bool:
    """CORE 또는 AL-CORE 그룹 키인지 판별 (CU/AL 공통)."""
    return group_key.startswith("CORE-") or group_key.startswith("AL-CORE-")


def _st_sq(group_key: str) -> int:
    """ST-{sq}-... 그룹 키에서 SQ 정수를 추출한다. 실패 시 0."""
    try:
        return int(group_key.split("-")[1])
    except (IndexError, ValueError):
        return 0


def _group_earliest_due(batches: list) -> date:
    """배치 목록에서 가장 이른 납기일을 반환한다. 납기 없으면 date.max."""
    dates = [b.due_date for b in batches if b.due_date is not None]
    return min(dates) if dates else date.max


def _is_sheath_group(group_key: str, batches: list) -> bool:
    """시스 공정 그룹 판별 — batch_group prefix + 대표 배치 공정명 폴백.

    create_batches 가 할당한 A120_*/A100_* 외에도, 수동 시드로 batch_group
    이 비어 있는 시스 배치도 체인 정렬 대상에 포함시킨다.
    """
    if group_key.startswith("A120_") or group_key.startswith("A100_"):
        return True
    if batches and batches[0].process_name in ("저압시스", "고압시스"):
        return True
    return False


def _sheath_group_color_rank(batches: list) -> int:
    """대표 배치의 sheath_color 로부터 색상 순위를 반환 (A'' 접근안).

    순위는 batch_grouping._SHEATH_COLOR_RANK 를 참조 (단일 소스 유지).
    순환 import 회피를 위해 함수 내부에서 지연 로드한다.
    """
    # 지연 import: batch_grouping 모듈의 비공개 상수를 참조하되 top-level
    # 순환 의존성과 린터의 "unused import 제거" 부작용을 동시에 방지한다.
    from app.services.batch_grouping import _SHEATH_COLOR_RANK

    if not batches:
        return 99
    color = (batches[0].sheath_color or "").strip() or "기타"
    return _SHEATH_COLOR_RANK.get(color, 99)


def _sheath_group_due_week_int(batches: list) -> int:
    """대표 배치의 납기 반-주차(H1/H2) 버킷.

    batch_grouping 의 H1(월~수)/H2(목~일) 분할과 정합시키기 위해 주차 × 2
    해상도로 인코딩한다. 같은 색상 안에서 H1 이 H2 보다 먼저 스케줄링되고,
    다른 색상 간에는 earliest_due 가 3차 tiebreaker 로 동작한다.
    """
    due = _group_earliest_due(batches)
    if due == date.max:
        return 9999999
    yr, wk, wday = due.isocalendar()
    half = 0 if wday <= 3 else 1  # H1 → 0, H2 → 1
    return (yr * 100 + wk) * 2 + half


def _extract_core_main_sq(group_key: str) -> int | None:
    """CORE/AL-CORE 그룹 키에서 main SQ를 추출한다.

    "CORE-633-35kV" → 633, "AL-CORE-633-35kV" → 633
    """
    try:
        parts = group_key.split("-")
        if group_key.startswith("AL-CORE-"):
            return int(parts[2])  # AL-CORE-{sq}-...
        if group_key.startswith("CORE-"):
            return int(parts[1])  # CORE-{sq}-...
    except (IndexError, ValueError):
        pass
    return None


def auto_schedule(
    run_label: str, db: Session, *, use_cpsat: bool = False, **kwargs
) -> dict:
    """겹침 재시도 2회 포함 스케줄 생성 래퍼 (greedy / CP-SAT 공용).

    동작:
      1. 전략 선택
         - ``use_cpsat=False`` (기본): ``_run_optimization_once`` 만 실행 (greedy)
         - ``use_cpsat=True``        : ``cp_sat_schedule`` 먼저 시도, infeasible
           /타임아웃 시 같은 retry 사이클 안에서 greedy 폴백
      2. ``constraint_checker.validate_all`` 로 겹침 검증
      3. 겹침 있으면 최대 2회 재시도
         - CP-SAT 경로는 시도 번호를 ``random_seed`` 로 전달해 결정론적 동일 해가
           반복되지 않도록 변동 (Fix P0-4B)
         - 재시도 전 ``_purge_run_tasks`` 로 기존 태스크/감사 로그 정리
      4. 2회 재시도 후에도 겹침이면 ``SchedulerOverlapError`` 발생 (DB rollback)

    왜 단일 public entry 로 통합했는가:
      기존에는 plan_pipeline 이 ``cp_sat_schedule`` 을 직접 호출하여
      retry+validate 루프를 완전히 우회했음 (Review B CRITICAL). 같은 실패 모드를
      두 경로가 공유하도록 여기서 묶어 안전망을 단일 지점에 집약한다.

    왜 지역 import인가: constraint_checker / cp_sat_optimizer 는 monkeypatch
    시나리오에서 실시간 lookup 이 필요하므로 함수 내부에서 import 하여
    패치된 바인딩을 그대로 사용한다.
    """
    from app.services import constraint_checker

    # ── Phase 2 개선: warm_start_hints 자동 생성 ──────────────────────────
    # 왜 자동 생성:
    #   cp_sat_optimizer.py:1185~1186 주석에 따르면 warm_start_hints 주입은
    #   ERP 재업로드/증분 재최적화에서 2.5~5× speedup 을 준다. 기존 구현은
    #   `_reschedule_affected_groups_cpsat` 같은 특수 경로에서만 힌트를
    #   명시 전달했고, 일반 `auto_schedule` 재실행 경로에서는 힌트 없이
    #   처음부터 탐색했다. 결과적으로 **동일 run 을 반복 자동배열할 때
    #   매번 cold-start** 가 되어 시간 낭비.
    #
    # 전략:
    #   1) use_cpsat=True 이고 caller 가 warm_start_hints 를 명시 전달하지
    #      않았다면 기존 ScheduleTask 에서 (batch_group → start_wmin +
    #      equipment_code) 맵을 자동 생성해 kwargs 에 주입.
    #   2) 기존 태스크가 없으면 (최초 실행) 빈 dict → solver 에 영향 없음.
    #   3) base_date 는 cp_sat_schedule 내부 폴백 로직과 동일하게
    #      `resolve_base_date` 로 미리 확정해, 힌트의 wmin 축과 solver 의
    #      wmin 축이 일치하도록 보장.
    #   4) add_hint 는 silent-fail 이라 batch_group 이 신규 모델에 없어도
    #      안전 (cp_sat_optimizer 주석 참조).
    if use_cpsat and "warm_start_hints" not in kwargs:
        from app.services.cp_sat_optimizer import _datetime_to_wmin, resolve_base_date
        from app.infrastructure.models.production_batch import ProductionBatch

        _hint_base = resolve_base_date(run_label, kwargs.get("base_date"))

        existing = (
            db.query(ScheduleTask)
            .filter(
                ScheduleTask.run_label == run_label,
                ScheduleTask.equipment_code.isnot(None),
                ScheduleTask.start_datetime.isnot(None),
            )
            .all()
        )
        if existing:
            _batch_groups = {
                b.batch_id: b.batch_group
                for b in db.query(ProductionBatch.batch_id, ProductionBatch.batch_group)
                .filter(ProductionBatch.run_label == run_label)
                .all()
            }
            _hints: dict[str, dict] = {}
            for t in existing:
                bg = _batch_groups.get(t.batch_id)
                if not bg:
                    continue
                # 같은 그룹 여러 배치 → 첫 등장만 사용 (긴급수주 경로와 동일 규칙)
                if bg in _hints:
                    continue
                _hints[bg] = {
                    "start_wmin": _datetime_to_wmin(t.start_datetime, _hint_base),
                    "equipment_code": t.equipment_code,
                }
            if _hints:
                kwargs["warm_start_hints"] = _hints
                # base_date 도 함께 주입해 solver 축 일관성 보장
                kwargs.setdefault("base_date", _hint_base)

    MAX_RETRIES = 2
    result: dict = {}
    violations: list[dict] = []

    for attempt in range(MAX_RETRIES + 1):
        if use_cpsat:
            # CP-SAT 우선 시도. random_seed 를 시도 번호로 변동 → 동일 해 반복 방지.
            # P9-B: 3-level fallback (tardiness_hard=True → sheath_color_hard 완화 →
            # tardiness_hard 완화) 을 greedy 폴백 전에 적용.
            from app.services.cp_sat_optimizer import cp_sat_schedule

            # P4-3: attempt==0 에만 warm_start_hints 유지, 1+ 에서는 drop.
            # 왜: 같은 힌트로 재시도하면 비슷한 해로 수렴 → overlap 이 재발할 위험
            # (random_seed 변동만으로는 탐색 공간을 충분히 다르게 못 만듦). 힌트도
            # 함께 drop 해야 retry 가 의미를 갖는다. kwargs 원본 보존을 위해 copy.
            _attempt_kwargs = dict(kwargs)
            if attempt > 0 and "warm_start_hints" in _attempt_kwargs:
                _attempt_kwargs["warm_start_hints"] = None

            # Level 1: 납기/색상 모두 엄격
            result = cp_sat_schedule(
                run_label, db, random_seed=attempt, **_attempt_kwargs
            )
            l1_status = result.get("solver_status")

            # Level 2: 색상만 완화
            if l1_status == "INFEASIBLE":
                _purge_run_tasks(db, run_label)
                result = cp_sat_schedule(
                    run_label,
                    db,
                    random_seed=attempt,
                    sheath_color_hard=False,
                    tardiness_hard=True,
                    **{
                        k: v
                        for k, v in _attempt_kwargs.items()
                        if k not in ("sheath_color_hard", "tardiness_hard")
                    },
                )
                result.setdefault("warnings", []).append(
                    "납기 hard 유지 + 색상 hard 완화(Level 2) 로 재시도"
                )
            l2_status = result.get("solver_status")

            # Level 3: 둘 다 완화
            if l2_status == "INFEASIBLE":
                _purge_run_tasks(db, run_label)
                result = cp_sat_schedule(
                    run_label,
                    db,
                    random_seed=attempt,
                    sheath_color_hard=False,
                    tardiness_hard=False,
                    **{
                        k: v
                        for k, v in _attempt_kwargs.items()
                        if k not in ("sheath_color_hard", "tardiness_hard")
                    },
                )
                result.setdefault("warnings", []).append(
                    "납기+색상 모두 완화(Level 3) 로 재시도 — 납기 초과 가능성 있음"
                )

            # greedy 최종 폴백 — greedy 는 힌트를 모르므로 kwargs 에서 제거.
            if result.get("solver_status") not in ("OPTIMAL", "FEASIBLE"):
                result.setdefault("warnings", []).append(
                    "CP-SAT 3-level 모두 미해결 — 그리디 폴백으로 전환합니다"
                )
                _purge_run_tasks(db, run_label)
                _greedy_kwargs = {
                    k: v
                    for k, v in kwargs.items()
                    if k
                    not in (
                        "warm_start_hints",
                        "time_limit_sec",
                        "frozen_group_keys",
                        "sheath_color_hard",
                        "tardiness_hard",
                    )
                }
                result = _run_optimization_once(run_label, db, **_greedy_kwargs)
        else:
            # greedy 경로도 CP-SAT 전용 kwargs 가 흘러들어오지 않도록 필터.
            _greedy_kwargs = {
                k: v
                for k, v in kwargs.items()
                if k
                not in (
                    "warm_start_hints",
                    "time_limit_sec",
                    "frozen_group_keys",
                    "sheath_color_hard",
                    "tardiness_hard",
                )
            }
            result = _run_optimization_once(run_label, db, **_greedy_kwargs)

        # 재시도 판단 경량 검증 — overlap 만. 최종 전체 검증은 run_stage2 에서 1회.
        # 왜: 재시도 루프는 "겹침이면 다시 돌린다" 만 필요. 전체 28개 체커를
        # retry 마다 돌리는 기존 방식은 공통 로드 4쿼리 + 체커 loop 가 반복되어
        # 원격 Supabase 왕복이 누적. 이 분기에서는 overlap 만 확인하고, violation
        # 리스트는 호출자(run_stage2) 가 최종 시점에 한 번만 계산한다.
        violations = constraint_checker.validate_overlap_only(run_label, db)

        if not constraint_checker.has_overlap(violations):
            result["overlap_alert"] = False
            # JIT post-processing — opt-in via SCHEDULER_JIT env var.
            # 성공한 schedule 에만 적용 (겹침 재시도 회피). JIT 자체가 overlap
            # 을 만들면 안 되므로 shift 후 재검증 — 실패 시 경고만 남기고 shift
            # 결과를 그대로 둔다 (invariant 위반은 도메인 오류로 후속 round 조사).
            if _should_apply_jit():
                run_tasks = (
                    db.query(ScheduleTask)
                    .filter(ScheduleTask.run_label == run_label)
                    .all()
                )
                shifts = apply_jit_delay(run_tasks, db)
                db.flush()
                if shifts:
                    # JIT post-shift 후에도 overlap 만 확인 (전체 검증은 run_stage2 최종).
                    post_overlap = constraint_checker.validate_overlap_only(
                        run_label, db
                    )
                    result["jit_shifts_applied"] = shifts
                    if post_overlap:
                        result.setdefault("warnings", []).append(
                            f"JIT post-shift 후 overlap {len(post_overlap)}건 발생 — 로직 재검토 필요"
                        )
            # 납기 초과 리포트 — solver 결과와 무관하게 DB 기준으로 집계.
            # 운영자/UI 가 "납기 초과 N" 배지/리스트로 활용. past-due 포함.
            try:
                from app.services.tardiness_metrics import count_tardiness

                result["tardiness_report"] = count_tardiness(run_label, db)
            except Exception as _e:  # noqa: BLE001
                # 메트릭 실패는 스케줄 결과 자체를 막지 않도록 best-effort.
                result.setdefault("warnings", []).append(
                    f"tardiness_report 집계 실패 (무시): {_e}"
                )

            # 납기 초과 발견 시 boost retry — tardy 배치의 customer_priority 를
            # 일시적으로 critical 로 상향해 CP-SAT 재실행, 개선되면 채택.
            # SAVEPOINT 로 감싸 악화/동일 시 원본 상태로 완전 복원.
            _retry_depth = int(kwargs.pop("_tardiness_retry_depth", 0))
            _tr = result.get("tardiness_report") or {}
            if use_cpsat and _retry_depth == 0 and _tr.get("total_tardy_count", 0) > 0:
                result = _tardiness_boost_retry(
                    original_result=result,
                    original_tardiness=_tr,
                    run_label=run_label,
                    db=db,
                    use_cpsat=use_cpsat,
                    retry_depth=_retry_depth,
                    **kwargs,
                )
            return result

        overlap_hits = violations

        # 재시도 전 audit 기록 + 현재 run 의 기존 태스크 정리
        # audit 실패는 스케줄링 실패로 연결하지 않는다 (best-effort 로깅).
        try:
            log_decision(
                db,
                run_label=run_label,
                stage="stage2",
                action_type="overlap_detected_retry",
                reason=(
                    f"겹침 감지 — 재시도 {attempt + 1}/{MAX_RETRIES + 1}회차, "
                    f"위반 {len(overlap_hits)}건"
                ),
                constraints_applied=overlap_hits,
            )
        except Exception:
            pass
        _purge_run_tasks(db, run_label)

    # 모든 재시도 후에도 겹침 지속 → 고위 경보 + DB 롤백 + 예외
    try:
        log_decision(
            db,
            run_label=run_label,
            stage="stage2",
            action_type="overlap_persist_alert",
            reason=(f"겹침 재시도 {MAX_RETRIES + 1}회 모두 실패 — 스케줄 저장 거부"),
            constraints_applied=[
                v for v in violations if v.get("constraint_id") == "overlap"
            ],
        )
    except Exception:
        pass

    db.rollback()
    raise SchedulerOverlapError(
        "스케줄 겹침이 재시도 후에도 지속됩니다.",
        run_label=run_label,
        violations=[v for v in violations if v.get("constraint_id") == "overlap"],
        attempts=MAX_RETRIES + 1,
    )


def _tardiness_boost_retry(
    *,
    original_result: dict,
    original_tardiness: dict,
    run_label: str,
    db: Session,
    use_cpsat: bool,
    retry_depth: int,
    **kwargs,
) -> dict:
    """납기 초과 건의 customer_priority 를 일시 boost 해 재스케줄, 개선되면 채택.

    Why:
      CP-SAT 는 weight × tardiness 를 minimize 하지만 time_limit 내 최적해를
      못 찾거나 equal-weight 동점 해 중 suboptimal 을 선택할 수 있음. Tardy
      그룹의 weight 를 한 단계 상향(_TARDINESS_WEIGHT["critical"] = 1e7/min,
      normal 1e5 대비 100×) 후 재실행하면 solver 가 해당 그룹을 더 공격적으로
      앞당기려 시도 → 총 tardiness 감소 가능.

    Safety:
      - SAVEPOINT 내부에서 customer_priority UPDATE + _purge_run_tasks +
        auto_schedule 재호출. 악화/동일/예외 시 rollback 으로 원본 상태 복원.
      - `_tardiness_retry_depth` kwarg 로 1회만 수행 (무한 재귀 방지).
      - 비교 기준: (tardy_count, tardy_minutes) 사전식 비교 — 두 지표 모두
        악화 또는 동일이면 reject.

    Args:
        original_result: 직전 auto_schedule 결과 (rejection 시 반환용).
        original_tardiness: tardiness_report dict (worst_tasks 포함).
        run_label, db: 재스케줄 대상.
        use_cpsat: True 여야 의미 있음 (호출부에서 이미 체크).
        retry_depth: 현재 0 이면 +1 로 재시도 허용, 아니면 skip.
        **kwargs: 상위 auto_schedule 인자 passthrough.

    Returns:
        채택 시: retry_result + {"retry_adopted": True,
                                   "retry_original_tardiness": {count, minutes}}
        거부 시: original_result + {"retry_adopted": False,
                                     "retry_rejected_reason": str}
        오류 시: original_result + {"retry_adopted": False, "retry_error": str}
    """
    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask

    original_count = int(original_tardiness.get("total_tardy_count", 0))
    original_min = int(original_tardiness.get("total_tardy_minutes", 0))
    worst = original_tardiness.get("worst_tasks") or []
    tardy_task_ids = [t["task_id"] for t in worst if t.get("task_id") is not None]
    if not tardy_task_ids:
        original_result["retry_adopted"] = False
        original_result["retry_rejected_reason"] = "worst_tasks 가 비어 boost 대상 없음"
        return original_result

    # Tardy task 의 batch_id 조회 (boost 대상)
    tardy_batch_ids = [
        row[0]
        for row in db.query(ScheduleTask.batch_id)
        .filter(ScheduleTask.task_id.in_(tardy_task_ids))
        .all()
    ]
    if not tardy_batch_ids:
        original_result["retry_adopted"] = False
        original_result["retry_rejected_reason"] = "tardy batch_id 조회 결과 비어 있음"
        return original_result

    sp = db.begin_nested()
    try:
        # Boost: customer_priority = 1 (critical). NULL 은 유지하지 않고 1 로.
        db.query(ProductionBatch).filter(
            ProductionBatch.batch_id.in_(tardy_batch_ids)
        ).update({ProductionBatch.customer_priority: 1}, synchronize_session=False)
        # 기존 태스크 purge — retry 가 처음부터 배치
        _purge_run_tasks(db, run_label)

        retry_result = auto_schedule(
            run_label=run_label,
            db=db,
            use_cpsat=use_cpsat,
            _tardiness_retry_depth=retry_depth + 1,
            **kwargs,
        )
        retry_tr = retry_result.get("tardiness_report") or {}
        retry_count = int(retry_tr.get("total_tardy_count", 0))
        retry_min = int(retry_tr.get("total_tardy_minutes", 0))

        # 엄격한 개선: (count, minutes) 사전식 비교
        if (retry_count, retry_min) < (original_count, original_min):
            sp.commit()
            retry_result["retry_adopted"] = True
            retry_result["retry_original_tardiness"] = {
                "count": original_count,
                "minutes": original_min,
            }
            return retry_result
        # 동일 또는 악화 → rollback
        sp.rollback()
        original_result["retry_adopted"] = False
        original_result["retry_rejected_reason"] = (
            f"retry ({retry_count}, {retry_min}) ≥ original "
            f"({original_count}, {original_min})"
        )
        return original_result
    except Exception as e:  # noqa: BLE001
        # 예외 시 원본 상태로 복원. retry 실패가 스케줄 자체를 막지 않음.
        sp.rollback()
        original_result["retry_adopted"] = False
        original_result["retry_error"] = str(e)
        original_result.setdefault("warnings", []).append(
            f"tardiness boost retry 실패 (원본 유지): {e}"
        )
        return original_result


def _purge_run_tasks(db: Session, run_label: str) -> None:
    """재시도 전 해당 run 의 감사로그 + ScheduleTask 삭제 + 배치 상태 리셋.

    왜 AuditLog 를 먼저 지우는가:
      ``audit_log.task_id`` 는 ``schedule_task.task_id`` 에 FK 참조(ON DELETE
      CASCADE 없음). 첫 시도에서 ``schedule_placed`` 액션으로 남긴 감사 로그가
      ScheduleTask 를 참조하므로, ScheduleTask 먼저 삭제 시 Postgres 에서
      FK violation 발생 → AuditLog 삭제를 가장 먼저 수행.

    왜 배치 상태 리셋이 필요한가:
      ``_run_optimization_once`` 는 placement 시점에 ``ProductionBatch.status`` 를
      ``"scheduled"`` 로 변경하고, 다음 호출에서는 ``status == "planned"`` 인 배치만
      다시 로드한다. 상태 리셋을 하지 않으면 재시도 시 0건 배치만 발견되어
      빈 스케줄이 반환되고 겹침 검증도 통과(0 태스크 → 겹침 없음)해
      ``overlap_alert=False`` 로 조용히 성공 처리되는 심각한 integrity 버그 발생.

    동작:
      1. 해당 run 의 AuditLog 삭제 (FK 참조 제거)
      2. 해당 run 의 ScheduleTask 삭제
      3. ``status == "scheduled"`` 인 ProductionBatch 를 ``"planned"`` 로 복원하고
         ``equipment_code`` 도 해제 (재배정 허용)
    """
    from app.infrastructure.models.audit_log import AuditLog
    from app.infrastructure.models.production_batch import ProductionBatch

    # 0. 세션에 아직 flush 되지 않은 INSERT (schedule_placed audit, overlap_detected_retry 등)
    #    를 먼저 DB 에 밀어넣는다. autoflush=False 세션이므로, 이 단계 없이 bulk DELETE 를
    #    실행한 뒤 db.flush() 시점에 pending INSERT 가 뒤늦게 수행되면 "삭제된 task_id 를
    #    참조하는 audit row 를 insert" 하려다 FK violation 이 발생한다.
    db.flush()

    # 1. Frozen 배치 식별 — 물리 시작/완료된 배치의 ScheduleTask 는 보존해야
    # Gantt 에 해당 시간 슬롯이 계속 표시되고, 재최적화 시 overlap 회피 정보도 보존된다.
    frozen_batch_ids = {
        row[0]
        for row in db.query(ProductionBatch.batch_id)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status.in_(["in_progress", "completed", "wip_complete"]),
        )
        .all()
    }

    # 2. AuditLog 먼저 — FK 무결성 (audit_log.task_id → schedule_task.task_id).
    #    Frozen task 의 audit 은 삭제되지만, preserved task 자체는 남으므로
    #    FK 참조가 끊어져도 무방 (AuditLog → ScheduleTask 방향이라 역방향 영향 없음).
    db.query(AuditLog).filter(AuditLog.run_label == run_label).delete(
        synchronize_session=False
    )
    # 3. ScheduleTask — frozen 배치 제외하고 삭제
    task_delete_q = db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label)
    if frozen_batch_ids:
        task_delete_q = task_delete_q.filter(
            ~ScheduleTask.batch_id.in_(frozen_batch_ids)
        )
    task_delete_q.delete(synchronize_session=False)
    # 4. 배치 상태 리셋 — scheduled → planned (frozen 은 건드리지 않음)
    db.query(ProductionBatch).filter(
        ProductionBatch.run_label == run_label,
        ProductionBatch.status == "scheduled",
    ).update(
        {"status": "planned", "equipment_code": None},
        synchronize_session=False,
    )
    db.flush()


def _run_optimization_once(
    run_label: str, db: Session, *, base_date: datetime | None = None
) -> dict:
    """
    run_label의 production_batch를 간트 차트에 자동 배치.

    Args:
        base_date: 스케줄 시작 기준일시. None이면 KST 당일 08:00.
    Returns: {"total_tasks": int, "violations": list, "warnings": list}
    """
    result = {"total_tasks": 0, "violations": [], "warnings": []}

    # Load all batches for this run, excluding outsourced and already-scheduled
    # 공정 순서를 포함하여 정렬 — 같은 수주의 연선이 절연보다 먼저 스케줄링되어야
    # predecessor_map이 올바르게 동작함
    # PROCESS_ORDER: 공정 순서 상수 (domain.constants에서 공유)
    batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "planned",
        )
        .order_by(
            ProductionBatch.due_date.asc(),
            ProductionBatch.customer_priority.asc(),
            ProductionBatch.batch_seq.asc(),
        )
        .all()
    )
    # Python 레벨 재정렬: batch_seq는 라우팅 내 공정 순서이지만,
    # batch_group 스케줄링: 공정 순서 최우선 (연선→절연→시스 파이프라인)
    # 같은 공정 내에서 납기→우선순위→SQ 순으로 정렬 (실제 공장 스케줄링 기준)
    batches.sort(
        key=lambda b: (
            PROCESS_ORDER.get(b.process_name, 50),
            b.batch_seq or 0,  # 61연선 코어(seq=0)가 메인(seq=1)보다 먼저
            b.due_date or date.max,
            b.customer_priority or 99,
            -(float(b.sq_mm2 or 0)),
        )
    )

    if not batches:
        result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
        return result

    # ── WIP 공정 스킵: 재고로 대체 가능한 공정은 간트에 미배치 ───────────────
    from app.infrastructure.models.wip_inventory import WipInventory

    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id is not None}
    wip_stage_map: dict[int, str] = {}
    if wip_ids:
        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

    schedulable: list[ProductionBatch] = []
    wip_skipped = 0
    for batch in batches:
        if batch.wip_matched_id and batch.wip_matched_id in wip_stage_map:
            wip_stage = wip_stage_map[batch.wip_matched_id]
            skip_set = _WIP_SKIP_PROCESSES.get(wip_stage, set())
            if batch.process_name in skip_set:
                batch.status = "wip_complete"
                wip_skipped += 1
                continue
        schedulable.append(batch)

    batches = schedulable
    if wip_skipped:
        result["wip_skipped"] = wip_skipped

    # ── 기준일시 설정 — 계획 생성일(run_label) 08:00 ─────────────────────
    # run_label 형식: "YYYYMMDD_HHMMSS" — 앞 8자리를 날짜로 파싱한다.
    # 파싱 실패 시 KST 당일 08:00으로 폴백.
    if base_date is None:
        try:
            date_part = run_label.split("_")[0]  # "20260406"
            base_date = datetime(
                int(date_part[:4]),
                int(date_part[4:6]),
                int(date_part[6:8]),
                8,
                0,
                0,
            )
        except Exception:
            from zoneinfo import ZoneInfo

            kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
            base_date = kst_now.replace(
                hour=8, minute=0, second=0, microsecond=0
            ).replace(tzinfo=None)

    # Load equipment into memory
    equipment_list = db.query(EquipmentMaster).all()
    equipment_by_process = {}
    for eq in equipment_list:
        equipment_by_process.setdefault(eq.process_name, []).append(eq)

    # Load speed master into memory: (equipment_code, sq_mm2) → SpeedMaster row
    speed_records = db.query(SpeedMaster).all()
    speed_map: dict[tuple, SpeedMaster] = {}
    for sr in speed_records:
        speed_map[(sr.equipment_code, float(sr.cross_section or 0))] = sr

    # ConstraintConfig 프리페치 (4-2 색상교체 fallback 등에서 재사용)
    constraint_params = ConstraintParams.load(db)

    # 용접 시간 (4-4): ConstraintParams 통합 경로로 조회 (하위 호환 default 유지)
    welding_min = constraint_params.get(
        "4-4", "welding_min", default=_DEFAULT_WELDING_MIN
    )

    # Load existing tasks (to check overlaps)
    existing_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
        )
        .all()
    )

    # Build equipment timeline: equipment_code → list of (start, end) occupied slots
    timeline = {}
    for t in existing_tasks:
        timeline.setdefault(t.equipment_code, []).append(
            (t.start_datetime, t.end_datetime)
        )

    # ── 부분 재스케줄용: 기존 tasks에서 파이프라인 상태 사전 초기화 ────────────
    # 전체 재스케줄(full reschedule)에서는 existing_tasks가 비어 있으므로 no-op.
    # 부분 재스케줄 시 비영향 그룹 tasks가 existing_tasks로 전달되므로
    # 이를 바탕으로 process_end_by_sq / process_first_output_by_sq 등을 미리 채운다.
    _seed_pipeline_process_end: dict[tuple[str, int], datetime] = {}
    _seed_pipeline_first_output: dict[tuple[str, int], datetime] = {}
    _seed_first_insul_output: datetime | None = None
    _seed_core_first_drum: dict[int, datetime] = {}

    if existing_tasks:
        _seed_batch_ids = {t.batch_id for t in existing_tasks if t.batch_id is not None}
        _seed_batches = (
            (
                db.query(ProductionBatch)
                .filter(ProductionBatch.batch_id.in_(_seed_batch_ids))
                .all()
            )
            if _seed_batch_ids
            else []
        )
        _seed_batch_map = {b.batch_id: b for b in _seed_batches}

        for t in existing_tasks:
            if t.start_datetime is None or t.end_datetime is None:
                continue
            b = _seed_batch_map.get(t.batch_id)
            if b is None:
                continue
            proc = b.process_name or ""
            sq_int = int(b.sq_mm2 or 0)
            proc_sq = (proc, sq_int)

            # process_end_by_sq: 해당 (공정, SQ)의 최대 종료 시각
            if (
                proc_sq not in _seed_pipeline_process_end
                or t.end_datetime > _seed_pipeline_process_end[proc_sq]
            ):
                _seed_pipeline_process_end[proc_sq] = t.end_datetime

            # process_first_output_by_sq: 첫 번째 드럼 출력 시각
            if not _is_core_group(b.batch_group or ""):
                _setup_min = float(t.setup_time_min or 0)
                _dur = float(b.estimated_duration_min or 0)
                _lot_count = max(int(b.drum_count or 1), 1)
                _first_drum_min = _setup_min + (
                    _dur / _lot_count if _lot_count else _dur
                )
                _first_out = calculate_end_datetime(
                    t.start_datetime, _first_drum_min, db, t.equipment_code
                )
                if (
                    proc_sq not in _seed_pipeline_first_output
                    or _first_out < _seed_pipeline_first_output[proc_sq]
                ):
                    _seed_pipeline_first_output[proc_sq] = _first_out
                if proc in ("저압절연", "고압절연"):
                    if (
                        _seed_first_insul_output is None
                        or _first_out < _seed_first_insul_output
                    ):
                        _seed_first_insul_output = _first_out
            else:
                # CORE 그룹 → core_first_drum_by_main_sq 채우기
                _setup_min = float(t.setup_time_min or 0)
                _dur = float(b.estimated_duration_min or 0)
                _lot_count = max(int(b.drum_count or 1), 1)
                _first_drum_min = _setup_min + (
                    _dur / _lot_count if _lot_count else _dur
                )
                _first_out = calculate_end_datetime(
                    t.start_datetime, _first_drum_min, db, t.equipment_code
                )
                _msq = _extract_core_main_sq(b.batch_group or "")
                if _msq is not None:
                    if (
                        _msq not in _seed_core_first_drum
                        or _first_out < _seed_core_first_drum[_msq]
                    ):
                        _seed_core_first_drum[_msq] = _first_out

    # Track predecessor tasks by (sales_order_id, sales_order_line)
    predecessor_map = {}  # (order_id, order_line) → last task_id for this order

    # 용접 시간 추적 (4-4): equipment_code → last placed batch (sq_mm2, sales_order_id)
    last_batch_on_equip: dict[str, ProductionBatch] = {}

    # ── 규칙 2: 19연선 이상(70SQ+)은 같은 SQ→같은 설비 고정 ────────────────
    # 이미 배정된 SQ→설비 매핑을 추적하여 동일 SQ는 같은 설비에 배치
    sq_to_equip: dict[tuple[str, int], str] = {}  # (process_name, sq) → equipment_code

    tasks_created = []

    # 공정 간 선행관계 추적 — SQ 단위로 앞 공정의 종료 시각 기록
    # 연선_120SQ 종료 → 저압절연_120SQ 시작 가능
    # 저압절연_120SQ 종료 → A100_120SQ / A120_120SQ 시작 가능
    process_end_by_sq: dict[tuple[str, int], datetime] = dict(
        _seed_pipeline_process_end
    )
    # key: (공정명, SQ) → value: 해당 공정+SQ 그룹의 종료 시각

    # 파이프라인 겹침용: 앞 공정에서 첫 번째 드럼이 출력되는 시각
    # 연선에서 1틀이 나오면 절연 시작 가능, 절연 1틀 나오면 시스 시작 가능
    # = task.start_datetime + setup_min + (group_run_duration / drum_count)
    process_first_output_by_sq: dict[tuple[str, int], datetime] = dict(
        _seed_pipeline_first_output
    )

    # 저압절연 전체 중 가장 이른 첫 번째 드럼 출력 시각 — A100/A120 시스 그룹 시작 기준
    first_insul_output: datetime | None = _seed_first_insul_output

    # 61연선 코어(T6B0/AL6BO) 첫 드럼 출력 시각 — pipeline overlap 기준
    # "CORE-300-..." 첫 드럼 완료 후 "ST-300-..." 시작 가능
    core_first_drum_by_main_sq: dict[int, datetime] = dict(_seed_core_first_drum)

    # ── batch_group 단위로 그루핑 ────────────────────────────────────────────
    from collections import OrderedDict

    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        key = batch.batch_group or f"_single_{batch.batch_id}"
        batch_groups.setdefault(key, []).append(batch)

    # ── drum_lot_master에서 SQ별 소선경(wire_diameter) 로드 ──────────────────
    # ST- 연선 그룹 정렬 시 동일 소선경 그룹이 연속 배치되도록 하기 위해 사용
    sq_to_wire_d: dict[int, float] = {
        int(d.cross_section): float(d.wire_diameter)
        for d in db.query(DrumLotMaster).all()
        if d.wire_diameter is not None
    }

    # 소선경 클러스터별 최초 납기: wire_diameter → min(due_date of all ST- groups in cluster)
    # 가장 급한 소선경 클러스터를 먼저 처리하기 위해 사용
    wire_d_earliest: dict[float, date] = {}
    for gk, gb in batch_groups.items():
        if gk.startswith("ST-"):
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            if wd > 0:
                ed = _group_earliest_due(gb)
                if wd not in wire_d_earliest or ed < wire_d_earliest[wd]:
                    wire_d_earliest[wd] = ed

    # ── 시스 색상 묶음 lookup — CP-SAT 와 동일 규칙 ─────────────────────────
    # 그리디 경로의 batch_groups 는 {gk: [batches...]} 형식이라 묶음 빌더가 기대하는
    # {gk: {"batches": [...], "earliest_due": ..., "cpsat_dur": ..., "pred_ready": ...}}
    # 형식으로 wrapping 한 뒤 전달한다.
    from app.services.sheath_cluster import (
        build_sheath_clusters,
        cluster_sort_key,
    )

    _gm_for_cluster = {
        gk: {
            "batches": gb,
            "earliest_due": _group_earliest_due(gb),
            "cpsat_dur": int(sum(float(b.estimated_duration_min or 0) for b in gb)),
            "pred_ready": None,
        }
        for gk, gb in batch_groups.items()
    }
    _sheath_clusters_g = build_sheath_clusters(_gm_for_cluster)
    _sorted_clusters_g = sorted(
        _sheath_clusters_g, key=lambda c: cluster_sort_key(c, _gm_for_cluster)
    )
    _cluster_rank_g: dict[str, tuple[int, int]] = {}
    for _ci, _cluster in enumerate(_sorted_clusters_g):
        for _gi, _gk_c in enumerate(_cluster.group_keys):
            _cluster_rank_g[_gk_c] = (_ci, _gi)

    # 설비별 마지막 처리 시스 묶음 ID 추적 — 묶음 내부/경계 append 정책에 사용.
    # auto_schedule 호출 당 초기화 (모듈 레벨 상태 공유 방지).
    _gk_to_cluster_id_g: dict[str, str] = {}
    for _c in _sorted_clusters_g:
        for _gk_c in _c.group_keys:
            _gk_to_cluster_id_g[_gk_c] = _c.cluster_id
    _prev_cluster_on_eq_g: dict[str, str] = {}  # equipment_code → last cluster_id

    # ── 그룹 처리 순서 결정 ──────────────────────────────────────────────────
    # 우선순위:
    #   0 = CORE/AL-CORE: 선행 공정이므로 반드시 먼저 스케줄링
    #   1 = ST- 연선 그룹: 소선경(wire_diameter) 클러스터 단위로 연속 배치
    #       클러스터 내 정렬: 클러스터 최초납기 → 소선경 → 그룹 최초납기
    #   2 = 그 외 공정(절연·시스 등): 공정 순서(PROCESS_ORDER) 최우선 → EDD
    #       파이프라인 보장: 절연(2)이 시스(4)보다 항상 먼저 스케줄링되어야
    #       process_first_output_by_sq에 절연 데이터가 등록된 후 시스가 참조 가능.
    #
    # 시스 그룹 전용 체인 정렬:
    #   - 1차: 납기 주 버킷(H1/H2) — 납기 최우선
    #   - 2차: 색상 순위 (_SHEATH_COLOR_RANK: 흑→갈→회→청…) — 같은 주차 내 묶기
    #   - 3차: 실제 납기일 (같은 주+색상 내 stable EDD)
    #   → 납기를 지키면서 같은 주차 내에서만 색상을 묶어 교체 비용 최소화.
    #     색상을 1차로 두면 멀리 있는 주차의 같은 색상 그룹이 먼저 끌려와
    #     급한 납기(다른 색상)가 뒤로 밀리는 현상이 발생해 사용자 룰과 상충.
    #   Tradeoff: 같은 주차 내에 여러 색상이 있으면 그만큼 교체가 발생한다.
    #     하지만 시스 설비는 주당 그룹 수가 제한적(≤ ~4건)이라 주차별 교체는
    #     최대 2~3회 수준으로 수렴. 납기 준수 이득이 더 크다.
    def _group_sort_key(kv):
        gk, gb = kv
        tier = 0 if _is_core_group(gk) else (1 if gk.startswith("ST-") else 2)
        proc_order = PROCESS_ORDER.get(gb[0].process_name, 50) if gb else 50
        earliest_due = _group_earliest_due(gb)
        cust_prio = gb[0].customer_priority or 99 if gb else 99

        # 시스 체인: 묶음 단위 정렬 — CP-SAT _solved_order_key 와 동일 규칙
        # _cluster_rank_g 는 _group_sort_key 정의 전에 build_sheath_clusters 로 구성된
        # lookup 테이블로, (cluster_idx, position_in_cluster) 를 제공한다.
        if _is_sheath_group(gk, gb):
            rank = _cluster_rank_g.get(gk, (10**9, 10**9))
            return (
                tier,
                date.max,  # ST- 클러스터 납기 (비해당)
                0.0,  # ST- 소선경 (비해당)
                proc_order,
                rank[0],  # 1차: 묶음 순위 (납기 임박 묶음 먼저)
                rank[1],  # 2차: 묶음 내 순서
                earliest_due,  # 3차: 실제 EDD (tiebreak)
                cust_prio,
            )

        # 비시스 기존 정렬 (호환성 유지)
        return (
            tier,
            wire_d_earliest.get(sq_to_wire_d.get(_st_sq(gk), 0.0), date.max)
            if gk.startswith("ST-")
            else date.max,
            sq_to_wire_d.get(_st_sq(gk), 0.0) if gk.startswith("ST-") else 0.0,
            proc_order,
            earliest_due,
            # 시스 정렬키와 길이를 맞추기 위한 padding (비교 시 영향 없도록 동일 상수)
            0,
            date.max,
            cust_prio,
        )

    ordered_group_items = sorted(batch_groups.items(), key=_group_sort_key)

    for group_key, group_batches in ordered_group_items:
        rep = group_batches[0]  # 대표 배치 (설비 선정용)

        # 10-3: 시스 재질 라우팅
        candidate_equip = equipment_by_process.get(rep.process_name, [])
        if rep.process_name in ("고압시스", "저압시스"):
            candidate_equip = _filter_by_sheath_routing(rep, candidate_equip)

        # 저압시스 A100/A120 색상별 설비 강제 라우팅
        # A120 배치(흑/청) → SH-A120 전용, A100 배치(갈/회/녹황) → SH-A100 전용
        if group_key.startswith("A120_"):
            candidate_equip = [
                e for e in candidate_equip if e.equipment_code == "SH-A120"
            ]
        elif group_key.startswith("A100_"):
            candidate_equip = [
                e for e in candidate_equip if e.equipment_code == "SH-A100"
            ]

        eligible = _find_eligible_equipment(rep, candidate_equip)

        sq = int(rep.sq_mm2 or 0)
        sq_key = (rep.process_name, sq)
        is_stranding = rep.process_name == "연선"

        # ── 61연선 설비 선호도 좁히기 (T6BO / 54BO) ──────────────────────
        # 매칭 설비 없으면 eligible 전체 유지 → 스케줄링 skip 방지
        if is_stranding:
            eligible = _narrow_by_stranding(rep, eligible)

        # ── 규칙 2: 같은 SQ → 같은 설비 (연선 공정만, 70SQ+) ─────────────
        # CORE-/AL-CORE- 그룹 제외: 61연선에서 CORE와 ST는 서로 다른 설비를 타야 함
        if (
            is_stranding
            and sq >= 70
            and sq_key in sq_to_equip
            and not _is_core_group(group_key)
        ):
            preferred_eq = sq_to_equip[sq_key]
            pref_match = [e for e in eligible if e.equipment_code == preferred_eq]
            if pref_match:
                eligible = pref_match

        # ── T/P 공정 preferred 설비: TP-2 (not alphabetical TP-1) ────────────
        # Why: _find_speed.equipment_map["T/P"] = ["TP-2"] 이므로 duration 계산과
        # 실제 배정 설비를 일관되게 유지한다. PDF 1안도 T/P#2 만 사용.
        # 만약 TP-2 가 eligible 에서 제외 (color/range 필터 등) 되면 fallback.
        if rep.process_name == "T/P":
            tp2_match = [e for e in eligible if e.equipment_code == "TP-2"]
            if tp2_match:
                eligible = tp2_match

        # ── 규칙 3: 소선경 그루핑 ────────────────────────────────────────────
        # drum_lot_master.wire_diameter 기준 — 동일 소선경 SQ는 같은 설비 선호
        if is_stranding and sq_key not in sq_to_equip and not _is_core_group(group_key):
            wire_d = sq_to_wire_d.get(sq, 0.0)
            if wire_d > 0:
                same_wd_equips = set()
                for (proc, s), eq_code in sq_to_equip.items():
                    if proc == "연선" and sq_to_wire_d.get(s, -1.0) == wire_d:
                        same_wd_equips.add(eq_code)
                if same_wd_equips:
                    wd_match = [
                        e for e in eligible if e.equipment_code in same_wd_equips
                    ]
                    if wd_match:
                        eligible = wd_match

        if not eligible:
            candidate_codes = [e.equipment_code for e in candidate_equip]
            result["warnings"].append(
                f"배치그룹 {group_key}: 공정 '{rep.process_name}' SQ={rep.sq_mm2} "
                f"재질={rep.conductor_material} — 적합한 설비 없음 "
                f"(후보설비={candidate_codes})"
            )
            # 미스케줄된 공정을 max 시간으로 등록 → 후행 공정이 이 공정 없이 시작하는 것을 방지
            sq_int = int(rep.sq_mm2 or 0)
            process_end_by_sq[(rep.process_name, sq_int)] = datetime.max
            process_first_output_by_sq[(rep.process_name, sq_int)] = datetime.max
            continue

        # ── 멀티설비 분배: 드럼 수 >= 2 이고 적격 설비 >= 2 일 때
        #    드럼을 설비 수로 균등 분할하여 병렬 배치 ─────────────────────────
        #    대상: 연선(CORE 제외) + 고압절연(CV#1/CV#2 분배)
        header_batch_chk = next((b for b in group_batches if b.batch_seq == -1), None)
        if header_batch_chk:
            total_drums = int(header_batch_chk.drum_count or 0)
        else:
            # 고압절연 등 헤더 없는 공정: 그룹 내 배치 drum_count 합산
            total_drums = sum(int(b.drum_count or 0) for b in group_batches)
        is_high_insul = rep.process_name == "고압절연"
        is_high_sheath = rep.process_name == "고압시스"
        multi_eligible = (
            (
                is_stranding
                and not _is_core_group(group_key)
                and sq_key not in sq_to_equip
            )
            or is_high_insul
            or is_high_sheath
        )
        if multi_eligible and total_drums >= 2 and len(eligible) >= 2:
            split_ok = _schedule_multi_equipment(
                group_key=group_key,
                group_batches=group_batches,
                eligible=eligible,
                total_drums=total_drums,
                header_batch=header_batch_chk,
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
                continue

        # ── 그룹 전체 duration 계산 ──────────────────────────────────────────
        # batch_seq=-1 헤더 배치가 있으면 그 estimated_duration_min을 직접 사용.
        # (연선 그룹: 실제 작업량 work_qty_g / 선속 — 수주 건수와 무관)
        # 헤더 없으면 기존 방식으로 각 배치 duration 합산.
        rep_speed = float(rep.line_speed_mpm or 0)
        if rep_speed <= 0:
            # SpeedMaster에서 해당 설비+SQ 조합의 line_speed 조회
            for eq in eligible:
                sm = speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
                if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
                    rep_speed = float(sm.line_speed_mpm)
                    break
        line_speed = rep_speed if rep_speed > 0 else 10
        header_batch = next((b for b in group_batches if b.batch_seq == -1), None)
        if header_batch is not None:
            hd = float(header_batch.estimated_duration_min or 0)
            if hd <= 0:
                total = float(header_batch.total_length_m or 0)
                ls = float(header_batch.line_speed_mpm or 0) or line_speed
                hd = total / ls if ls > 0 else 60
            group_duration = hd
        else:
            group_duration = 0.0
            for b in group_batches:
                d = float(b.estimated_duration_min or 0)
                if d <= 0:
                    total = float(b.total_length_m or 0) + float(b.extra_length_m or 0)
                    ls = float(b.line_speed_mpm or 0) or line_speed
                    d = total / ls if ls > 0 else 60
                group_duration += d

        setup_min = float(rep.setup_time_min or 0)
        drum_winding_min = _get_drum_winding_min(
            eligible[0].equipment_code, rep.sq_mm2, speed_map
        )
        total_duration = group_duration + setup_min + drum_winding_min

        # ── 설비 선택 (최적 슬롯 탐색) ───────────────────────────────────────
        best_eq = None
        best_start = None
        best_total_duration = total_duration

        for eq in eligible:
            eq_code = eq.equipment_code
            slots = timeline.get(eq_code, [])

            eq_total_duration = total_duration

            # 4-1: 연선 셋업 3-tier (동일SQ=0 / 동일소선경=선재교체 / 다른소선경=규격교체)
            prev_batch = last_batch_on_equip.get(eq_code)
            if prev_batch is not None and rep.process_name == "연선":
                compound_min = float(
                    speed_map.get((eq_code, float(rep.sq_mm2 or 0)), None)
                    and speed_map[(eq_code, float(rep.sq_mm2 or 0))].setup_compound_min
                    or 0
                )
                actual_setup = _get_stranding_setup_min(
                    float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                    float(rep.sq_mm2) if rep.sq_mm2 else None,
                    sq_to_wire_d,
                    spec_min=setup_min,
                    compound_min=compound_min,
                )
            else:
                actual_setup = setup_min
                if prev_batch is not None:
                    same_sq = (
                        prev_batch.sq_mm2 is not None
                        and rep.sq_mm2 is not None
                        and float(prev_batch.sq_mm2) == float(rep.sq_mm2)
                    )
                    if same_sq:
                        actual_setup = 0.0
            eq_total_duration = eq_total_duration - setup_min + actual_setup

            # 4-2: 색상교체 시간 — 그룹 간 변경 시 (SpeedMaster 조회)
            color_change_min = 0.0
            if prev_batch is not None and rep.process_name in (
                "저압시스",
                "고압시스",
                "HFCO시스",
            ):
                prev_color = (prev_batch.sheath_color or "").strip()
                curr_color = (rep.sheath_color or "").strip()
                if prev_color and curr_color and prev_color != curr_color:
                    # SpeedMaster에서 해당 설비의 색상교체 시간 조회
                    sm_color = (
                        db.query(SpeedMaster.setup_color_min)
                        .filter(SpeedMaster.equipment_code == eq.equipment_code)
                        .first()
                    )
                    sm_color_val = sm_color[0] if sm_color else None
                    color_change_min = resolve_color_change_min(
                        sm_color_min=sm_color_val,
                        params=constraint_params,
                    )
            eq_total_duration += color_change_min

            # ── 선행공정(predecessor) — 공정 순서에 따라 앞 공정 종료 후 시작
            earliest = base_date
            sq_int = int(rep.sq_mm2 or 0)

            # 파이프라인 겹침: 앞 공정에서 첫 번째 드럼이 나오면 후공정 시작 가능
            # 연선 1틀 완료 → 절연 시작 / 절연 1틀 완료 → 시스 시작
            pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
            if pred_proc:
                all_sqs = {int(b.sq_mm2 or 0) for b in group_batches}
                if len(all_sqs) > 1:
                    # 색상 기준 혼합 SQ 그룹(시스): 어느 SQ든 첫 드럼이 나오면 시작 가능
                    # → 그룹 내 SQ 중 가장 이른 첫 출력 시각을 선행 제약으로 사용
                    valid_firsts = [
                        t
                        for sq_i in all_sqs
                        if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                        and t < datetime.max
                    ]
                    if valid_firsts:
                        pred_min = min(valid_firsts)
                        if pred_min > earliest:
                            earliest = pred_min
                else:
                    # 단일 SQ 그룹: 해당 SQ의 선행 제약만 확인
                    sq_i = next(iter(all_sqs))
                    pred_first = process_first_output_by_sq.get((pred_proc, sq_i))
                    if pred_first and pred_first > earliest:
                        earliest = pred_first
                if rep.process_name == "고압시스":
                    earliest += timedelta(hours=20)

            # 시스 배치(A100/A120): 저압절연 첫 번째 드럼 출력 후 시작
            if group_key.startswith("A100_") or group_key.startswith("A120_"):
                if first_insul_output and first_insul_output > earliest:
                    earliest = first_insul_output

            # 61연선 ST- 그룹: 동일 SQ의 CORE/AL-CORE 첫 드럼 출력 후 시작 (overlap)
            # 예: "ST-633-..." 그룹 → core_first_drum_by_main_sq[633] 이후 시작
            if group_key.startswith("ST-") and rep.process_name == "연선":
                try:
                    main_sq = int(group_key.split("-")[1])
                except (IndexError, ValueError):
                    main_sq = sq_int
                core_first = core_first_drum_by_main_sq.get(main_sq)
                if core_first and core_first > earliest:
                    earliest = core_first

            # 개별 수주 레벨 predecessor — 절연/시스/연합/T/P는 first-drum overlap만 사용
            # ST-* 연선 그룹: CORE first-drum overlap 사용 → 개별 predecessor 스킵
            # 개별 predecessor end_datetime을 쓰면 전체 완료를 기다리게 되어 overlap 무효화
            _is_st_group = group_key.startswith("ST-") and rep.process_name == "연선"
            _skip_individual = (
                rep.process_name
                in (
                    "저압절연",
                    "고압절연",
                    "저압시스",
                    "고압시스",
                    "연합",
                    "T/P",
                )
                or _is_st_group
            )
            if not _skip_individual:
                for b in group_batches:
                    pred_key = (b.sales_order_id, b.sales_order_line)
                    pred_tid = predecessor_map.get(pred_key)
                    if pred_tid:
                        pred_task = next(
                            (t for t in tasks_created if t.task_id == pred_tid),
                            None,
                        )
                        if pred_task and pred_task.end_datetime > earliest:
                            earliest = pred_task.end_datetime

            # ── 시스 묶음 기반 append 정책 (CP-SAT 와 동일) ─────────────────
            # 묶음 내부: 무조건 append (색상 체인 유지)
            # 묶음 경계: 조건부 append (납기 초과 예상이면 earliest 유지 →
            # _find_available_slot 이 빈 공간 사용 → 납기 보호)
            eq_earliest = earliest
            if rep.process_name in ("저압시스", "고압시스"):
                current_cluster_id = _gk_to_cluster_id_g.get(group_key)
                prev_cluster = _prev_cluster_on_eq_g.get(eq_code)
                if slots and current_cluster_id:
                    last_end = max(s[1] for s in slots)
                    append_earliest = max(eq_earliest, last_end)
                    if prev_cluster == current_cluster_id:
                        eq_earliest = append_earliest
                    else:
                        append_end = calculate_end_datetime(
                            append_earliest, eq_total_duration, db, eq_code
                        )
                        due = _group_earliest_due(group_batches)
                        if not due or append_end.date() <= due:
                            eq_earliest = append_earliest

            slot_start = _find_available_slot(
                eq_earliest, eq_total_duration, slots, db, eq.equipment_code
            )

            if best_start is None or slot_start < best_start:
                best_eq = eq
                best_start = slot_start
                best_total_duration = eq_total_duration

        if best_eq is None or best_start is None:
            result["warnings"].append(f"배치그룹 {group_key}: 가용 슬롯 없음")
            continue

        end_dt = calculate_end_datetime(
            best_start, best_total_duration, db, best_eq.equipment_code
        )

        # ── 파이프라인 유휴 최소 역산 공식 ────────────────────────────────────
        # 물리: 후공정은 선행 마지막 드럼이 나와야 자기 마지막 드럼을 처리 가능.
        # T_succ_end = T_pred_end + D_succ_per_drum
        # T_succ_start = T_succ_end - D_succ_total (블록 폭 유지)
        _per_drum_min = (
            group_duration / max(int(total_drums or 1), 1)
            if group_duration > 0
            else 0.0
        )
        best_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
            process_end_by_sq=process_end_by_sq,
            current_start=best_start,
            current_end=end_dt,
            duration_min=best_total_duration,
            tail_offset_min=_per_drum_min,
            slots=timeline.get(best_eq.equipment_code, []),
            db=db,
            equipment_code=best_eq.equipment_code,
        )

        # ── 시간 올림 — 간트 블록은 정각 단위로 표시 ────────────────────────
        if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # ── 그룹당 1 schedule_task 생성 ──────────────────────────────────────
        # 체인 하이라이트 — 본 그룹의 상류 task id 를 predecessor 로 고정.
        # 같은 group 내 복수 order 가 있어도 대표 order 의 predecessor 로 일관 처리.
        rep_pred_task_id = predecessor_map.get(
            (rep.sales_order_id, rep.sales_order_line)
        )

        task = ScheduleTask(
            batch_id=rep.batch_id,  # 대표 배치 ID
            equipment_code=best_eq.equipment_code,
            start_datetime=best_start,
            end_datetime=end_dt,
            setup_time_min=setup_min,
            status="scheduled",
            run_label=run_label,
            batch_group=group_key,
            predecessor_task_id=rep_pred_task_id,
        )
        db.add(task)
        db.flush()

        timeline.setdefault(best_eq.equipment_code, []).append((best_start, end_dt))

        # 공정+SQ별 종료 시각 갱신 (후공정 선행관계 추적)
        sq_int = int(rep.sq_mm2 or 0)
        proc_sq_key = (rep.process_name, sq_int)
        if (
            proc_sq_key not in process_end_by_sq
            or end_dt > process_end_by_sq[proc_sq_key]
        ):
            process_end_by_sq[proc_sq_key] = end_dt

        # ── 파이프라인 겹침: 첫 번째 드럼 출력 시각 계산 ────────────────────
        # 연선 ST-: 헤더 배치(seq=-1)의 drum_count = 실제 틀 수
        # CORE-/AL-CORE-: drum_count 합산 — 드럼 하나씩 완료될 때마다 ST 시작 가능
        #   (AL6BO에서 한 드럼 완료 → 54BO 즉시 시작하는 파이프라인)
        # 절연/시스 등: 헤더 없으므로 그룹 내 배치 수 = 순차 처리 단위 수
        if header_batch is not None:
            lot_count = max(int(header_batch.drum_count or 1), 1)
        else:
            # CORE 그룹 포함, 절연/시스 등 헤더 없는 그룹 모두 drum_count 합산
            lot_count = max(sum(int(b.drum_count or 1) for b in group_batches), 1)
        first_drum_min = setup_min + (group_duration / lot_count)
        first_output_dt = calculate_end_datetime(
            best_start, first_drum_min, db, best_eq.equipment_code
        )
        # CORE-/AL-CORE- 그룹 제외: 절연은 ST(54BO) 첫 드럼 기준으로 시작해야 함
        # (CORE 첫 드럼은 너무 이르므로 후행 공정 선행 제약으로 부적합)
        if not _is_core_group(group_key) and (
            proc_sq_key not in process_first_output_by_sq
            or first_output_dt < process_first_output_by_sq[proc_sq_key]
        ):
            process_first_output_by_sq[proc_sq_key] = first_output_dt

        # 61연선 CORE-/AL-CORE- 그룹 첫 드럼 출력 시각 기록 — pipeline overlap
        # CU: "CORE-{main_sq}-...", AL: "AL-CORE-{main_sq}-..." 패턴
        if _is_core_group(group_key):
            main_sq = _extract_core_main_sq(group_key)
            if main_sq is not None:
                if (
                    main_sq not in core_first_drum_by_main_sq
                    or first_output_dt < core_first_drum_by_main_sq[main_sq]
                ):
                    core_first_drum_by_main_sq[main_sq] = first_output_dt

        # 저압절연 첫 번째 드럼 출력 시각 — A100/A120 시스 그룹 시작 기준
        if rep.process_name == "저압절연":
            if first_insul_output is None or first_output_dt < first_insul_output:
                first_insul_output = first_output_dt

        # 그룹 내 모든 배치의 predecessor + status 갱신
        for b in group_batches:
            pred_key = (b.sales_order_id, b.sales_order_line)
            predecessor_map[pred_key] = task.task_id
            b.equipment_code = best_eq.equipment_code
            b.status = "scheduled"

        # 규칙 2: SQ→설비 매핑 기록 (CORE/AL-CORE 그룹 제외 — 코어는 ST설비 고정 대상 아님)
        if rep.process_name == "연선" and not _is_core_group(group_key):
            sq_to_equip[sq_key] = best_eq.equipment_code

        # 용접 시간 추적 (4-4): 설비별 마지막 배치 갱신 (그룹의 마지막 배치)
        last_batch_on_equip[best_eq.equipment_code] = group_batches[-1]

        # 시스 묶음 기반 append 정책 — 현재 그룹의 cluster_id 로 갱신
        if rep.process_name in ("저압시스", "고압시스"):
            _cid_g = _gk_to_cluster_id_g.get(group_key)
            if _cid_g:
                _prev_cluster_on_eq_g[best_eq.equipment_code] = _cid_g

        tasks_created.append(task)

        # Check delivery date violation — 그룹 내 가장 빠른 납기 기준
        # 납기는 사용자 요구 상 하드 제약 → severity=error 로 상향
        # (validate_all 이 이를 보고 재시도/알림을 유발하도록)
        earliest_due = min(
            (b.due_date for b in group_batches if b.due_date), default=None
        )
        if earliest_due and end_dt.date() > earliest_due:
            violation = {
                "batch_id": rep.batch_id,
                "task_id": task.task_id,
                "type": "delivery",
                "severity": "error",
                "detail": f"납기 {earliest_due} 초과 → 완료 예정 {end_dt.date()}",
            }
            result["violations"].append(violation)
            result.setdefault("warnings", []).append(
                f"납기 위반 예상: 배치그룹 {group_key} end={end_dt.date()} > due={earliest_due}"
            )

        # Audit log
        log_decision(
            db=db,
            run_label=run_label,
            stage="stage2",
            batch_id=rep.batch_id,
            task_id=task.task_id,
            action_type="schedule_placed",
            constraints_applied=[
                {
                    "id": "1-1",
                    "name": "거래처 우선순위",
                    "result": "pass",
                    "detail": f"priority={rep.customer_priority}",
                },
                {
                    "id": "4-3",
                    "name": "드럼 권취 시간",
                    "result": "pass",
                    "detail": f"drum_winding={drum_winding_min:.0f}분 추가",
                },
                {
                    "id": "4-4",
                    "name": "용접 시간",
                    "result": "pass",
                    "detail": f"welding={welding_min:.0f}분 (스플라이스 로트 시 적용)",
                },
                {
                    "id": "4-5",
                    "name": "테이핑 속도 제한",
                    "result": "pass",
                    "detail": f"process={rep.process_name}, speed={line_speed:.1f}mpm",
                },
                {
                    "id": "5-1",
                    "name": "SQ 기준 설비 배정",
                    "result": "pass",
                    "detail": f"{best_eq.equipment_name} (range {best_eq.range_min}~{best_eq.range_max})",
                },
                {
                    "id": "10-2",
                    "name": "CU/AL 재질 분리",
                    "result": "pass",
                    "detail": f"material={rep.conductor_material}, equip_limit={best_eq.material_limit}",
                },
                {
                    "id": "10-3",
                    "name": "시스 재질 라우팅",
                    "result": "pass",
                    "detail": f"sheath_type={_get_sheath_type(rep)}, equip={best_eq.equipment_code}",
                },
            ],
            reason=(
                f"설비 {best_eq.equipment_name}에 배치: "
                f"SQ={batch.sq_mm2}, 납기={batch.due_date}, 소요={best_total_duration:.0f}분"
            ),
        )

        result["total_tasks"] += 1

    return result


# ── 멀티설비 분배 ────────────────────────────────────────────────────────────
def _schedule_multi_equipment(
    *,
    group_key: str,
    group_batches: list,
    eligible: list,
    total_drums: int,
    header_batch,
    base_date: datetime,
    run_label: str,
    db: "Session",
    speed_map: dict,
    timeline: dict,
    last_batch_on_equip: dict,
    sq_to_equip: dict,
    predecessor_map: dict,
    process_end_by_sq: dict,
    process_first_output_by_sq: dict,
    core_first_drum_by_main_sq: dict,
    tasks_created: list,
    result: dict,
    welding_min: float,
    sq_to_wire_d: dict | None = None,
) -> bool:
    """연선/고압절연 그룹의 드럼을 eligible 설비에 균등 분배하여 병렬 스케줄링.

    드럼 수를 설비 수로 나눠 각 설비에 proportional duration의 task를 생성한다.
    process_end_by_sq / process_first_output_by_sq는 가장 이른 완료 기준으로 갱신.

    - 연선: header_batch(seq=-1)의 duration 사용
    - 고압절연: header 없음 → 그룹 내 배치 duration 합산

    Returns:
        True면 분배 성공 (호출측에서 continue), False면 단일설비 경로로 폴백.
    """

    rep = group_batches[0]
    sq = int(rep.sq_mm2 or 0)
    sq_key = (rep.process_name, sq)

    # 그룹 전체 duration 계산
    # line_speed fallback: rep에 없으면 speed_map에서 설비별 기본값 조회
    rep_speed = float(rep.line_speed_mpm or 0)
    if rep_speed <= 0:
        # SpeedMaster에서 해당 설비+SQ 조합의 line_speed 조회
        for eq in eligible:
            sm = speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
            if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
                rep_speed = float(sm.line_speed_mpm)
                break
    line_speed = rep_speed if rep_speed > 0 else 10  # 최종 fallback 10 mpm

    if header_batch is not None:
        # 연선: header batch(seq=-1)의 estimated_duration_min 직접 사용
        hd = float(header_batch.estimated_duration_min or 0)
        if hd <= 0:
            total_len = float(header_batch.total_length_m or 0)
            ls = float(header_batch.line_speed_mpm or 0) or line_speed
            hd = total_len / ls if ls > 0 else 60
        group_duration = hd
    else:
        # 고압절연 등 헤더 없는 공정: 각 배치 duration 합산
        group_duration = 0.0
        for b in group_batches:
            d = float(b.estimated_duration_min or 0)
            if d <= 0:
                total_len = float(b.total_length_m or 0) + float(b.extra_length_m or 0)
                ls = float(b.line_speed_mpm or 0) or line_speed
                d = total_len / ls if ls > 0 else 60
            group_duration += d
        if group_duration <= 0:
            return False  # duration 계산 불가 시 단일설비 폴백

    setup_min = float(rep.setup_time_min or 0)

    # 드럼을 설비 수로 균등 분할
    num_eq = len(eligible)
    drums_per_eq = []
    base_drums = total_drums // num_eq
    remainder = total_drums % num_eq
    for i in range(num_eq):
        drums_per_eq.append(base_drums + (1 if i < remainder else 0))

    # 선행공정 earliest 계산 — 단일설비 경로와 동일하되,
    # 혼합 SQ 그룹(고압시스_흑_적 등)은 모든 SQ의 predecessor를 확인
    earliest = base_date
    sq_int = sq

    pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
    if pred_proc:
        # 혼합 SQ 그룹: 그룹 내 모든 SQ의 predecessor 중 가장 이른 first output
        all_sqs = {int(b.sq_mm2 or 0) for b in group_batches}
        if len(all_sqs) > 1:
            valid_firsts = [
                t
                for sq_i in all_sqs
                if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                and t < datetime.max
            ]
            if valid_firsts:
                pred_min = min(valid_firsts)
                if pred_min > earliest:
                    earliest = pred_min
        else:
            pred_first = process_first_output_by_sq.get((pred_proc, sq_int))
            if pred_first and pred_first > earliest:
                earliest = pred_first

        # 고압시스: 절연 경화 대기 시간 20h (단일설비 경로와 동일)
        if rep.process_name == "고압시스":
            earliest += timedelta(hours=20)

    # 61연선 ST- 그룹: CORE 첫 드럼 출력 후 시작 (pipeline overlap)
    if group_key.startswith("ST-") and rep.process_name == "연선":
        try:
            main_sq = int(group_key.split("-")[1])
        except (IndexError, ValueError):
            main_sq = sq_int
        core_first = core_first_drum_by_main_sq.get(main_sq)
        if core_first and core_first > earliest:
            earliest = core_first

    # 개별 수주 predecessor 확인
    # 절연/시스/연합/T/P는 first-drum overlap만 사용 (단일설비 경로와 동일)
    # ST-* 연선 그룹: CORE first-drum overlap 사용 → 개별 predecessor 스킵
    is_st_group = group_key.startswith("ST-") and rep.process_name == "연선"
    skip_individual_pred = (
        rep.process_name
        in (
            "저압절연",
            "고압절연",
            "저압시스",
            "고압시스",
            "연합",
            "T/P",
        )
        or is_st_group
    )
    if not skip_individual_pred:
        for b in group_batches:
            pred_key = (b.sales_order_id, b.sales_order_line)
            pred_tid = predecessor_map.get(pred_key)
            if pred_tid:
                pred_task = next(
                    (t for t in tasks_created if t.task_id == pred_tid), None
                )
                if pred_task and pred_task.end_datetime > earliest:
                    earliest = pred_task.end_datetime

    # ── earliest 확정 후: 설비 우선순위 정렬 + 수주 납기 우선 배정 ───────────
    # 각 설비의 예상 최초 가용 시각 계산 (earliest 반영)
    one_drum_dur = (group_duration / max(total_drums, 1)) + setup_min
    machine_est_starts = []
    for eq in eligible:
        slots = timeline.get(eq.equipment_code, [])
        est_start = _find_available_slot(
            earliest, one_drum_dur, slots, db, eq.equipment_code
        )
        machine_est_starts.append((est_start, eq))
    # 가장 빨리 시작 가능한 설비 순으로 정렬
    machine_est_starts.sort(key=lambda x: x[0])
    sorted_eligible = [eq for _, eq in machine_est_starts]

    # 개별 수주(seq >= 1)를 납기 오름차순으로 정렬 → 급한 수주를 빠른 설비에 배정
    order_batches_sorted = sorted(
        [b for b in group_batches if (b.batch_seq or 0) >= 1],
        key=lambda b: (b.due_date or date.max, b.customer_priority or 99),
    )
    machine_order_assignments: list[list] = [[] for _ in range(num_eq)]
    order_cursor = 0
    for i in range(num_eq):
        drums_left = drums_per_eq[i]
        while drums_left > 0 and order_cursor < len(order_batches_sorted):
            b = order_batches_sorted[order_cursor]
            machine_order_assignments[i].append(b)
            drums_left -= int(b.drum_count or 1)
            order_cursor += 1
    # 미처리 수주는 마지막 설비에 추가
    if order_cursor < len(order_batches_sorted):
        machine_order_assignments[-1].extend(order_batches_sorted[order_cursor:])

    # 설비별 서브배치의 납기: 해당 설비에 배정된 수주 중 가장 이른 납기
    sub_due_dates: list[date | None] = [
        min((b.due_date for b in orders if b.due_date), default=None)
        for orders in machine_order_assignments
    ]

    # 각 설비에 분배 task 생성 (납기 우선 배정된 sorted_eligible 순서)
    split_tasks = []
    split_end_dts = []
    split_first_outputs = []
    split_sub_dues: list[date | None] = []

    for i, eq in enumerate(sorted_eligible):
        eq_drums = drums_per_eq[i]
        if eq_drums <= 0:
            continue

        eq_code = eq.equipment_code
        # proportional duration (배정된 드럼 수 기반)
        eq_duration = group_duration * (eq_drums / total_drums)

        drum_winding_min = _get_drum_winding_min(eq_code, rep.sq_mm2, speed_map)

        # 4-1: 연선 셋업 3-tier (동일SQ=0 / 동일소선경=선재교체 / 다른소선경=규격교체)
        prev_batch = last_batch_on_equip.get(eq_code)
        if prev_batch is not None and rep.process_name == "연선" and sq_to_wire_d:
            compound_min = float(
                speed_map.get((eq_code, float(rep.sq_mm2 or 0)), None)
                and speed_map[(eq_code, float(rep.sq_mm2 or 0))].setup_compound_min
                or 0
            )
            actual_setup = _get_stranding_setup_min(
                float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                float(rep.sq_mm2) if rep.sq_mm2 else None,
                sq_to_wire_d,
                spec_min=setup_min,
                compound_min=compound_min,
            )
        else:
            actual_setup = setup_min
            if prev_batch is not None:
                same_sq = (
                    prev_batch.sq_mm2 is not None
                    and rep.sq_mm2 is not None
                    and float(prev_batch.sq_mm2) == float(rep.sq_mm2)
                )
                if same_sq:
                    actual_setup = 0.0

        eq_total_duration = eq_duration + actual_setup + drum_winding_min

        slots = timeline.get(eq_code, [])
        slot_start = _find_available_slot(
            earliest, eq_total_duration, slots, db, eq_code
        )
        end_dt = calculate_end_datetime(slot_start, eq_total_duration, db, eq_code)

        # ── 파이프라인 유휴 최소 역산 — 서브태스크별 독립 적용 ────────────────
        # duration(= eq_total_duration)은 드럼 수 비례이므로 서브태스크마다 다름.
        # 각 서브태스크가 선행공정 종료 + 후공정 1드럼 소요 이상에서 끝나도록 개별 정렬.
        _per_drum_min = eq_duration / max(int(eq_drums or 1), 1)
        slot_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
            process_end_by_sq=process_end_by_sq,
            current_start=slot_start,
            current_end=end_dt,
            duration_min=eq_total_duration,
            tail_offset_min=_per_drum_min,
            slots=slots,
            db=db,
            equipment_code=eq_code,
        )

        # 시간 올림 — 간트 블록은 정각 단위
        if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # 체인 하이라이트 — 분할 배치에서도 동일 원칙.
        # 모든 split 서브태스크는 같은 predecessor FK 를 가짐 (상류 group 의 대표 task id).
        rep_pred_task_id = predecessor_map.get(
            (rep.sales_order_id, rep.sales_order_line)
        )

        task = ScheduleTask(
            batch_id=rep.batch_id,
            equipment_code=eq_code,
            start_datetime=slot_start,
            end_datetime=end_dt,
            setup_time_min=actual_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=group_key,
            predecessor_task_id=rep_pred_task_id,
        )
        db.add(task)
        db.flush()

        timeline.setdefault(eq_code, []).append((slot_start, end_dt))
        last_batch_on_equip[eq_code] = group_batches[-1]
        split_tasks.append(task)
        split_end_dts.append(end_dt)
        split_sub_dues.append(sub_due_dates[i] if i < len(sub_due_dates) else None)

        # 첫 번째 드럼 출력 시각
        first_drum_min = actual_setup + (eq_duration / eq_drums)
        first_output_dt = calculate_end_datetime(
            slot_start, first_drum_min, db, eq_code
        )
        split_first_outputs.append(first_output_dt)

        tasks_created.append(task)
        result["total_tasks"] += 1

    if not split_tasks:
        return False

    # 공정+SQ별 종료/첫출력 시각 — 가장 늦은 종료, 가장 이른 첫출력
    proc_sq_key = (rep.process_name, sq_int)
    latest_end = max(split_end_dts)
    earliest_first = min(split_first_outputs)

    if (
        proc_sq_key not in process_end_by_sq
        or latest_end > process_end_by_sq[proc_sq_key]
    ):
        process_end_by_sq[proc_sq_key] = latest_end

    if (
        proc_sq_key not in process_first_output_by_sq
        or earliest_first < process_first_output_by_sq[proc_sq_key]
    ):
        process_first_output_by_sq[proc_sq_key] = earliest_first

    # 그룹 내 배치 status + equipment 갱신 (첫 번째 설비를 대표로)
    for b in group_batches:
        pred_key = (b.sales_order_id, b.sales_order_line)
        predecessor_map[pred_key] = split_tasks[0].task_id
        b.equipment_code = split_tasks[0].equipment_code
        b.status = "scheduled"

    # 규칙 2 매핑은 기록하지 않음 — 분배된 그룹은 여러 설비를 사용하므로

    # ── 납기 위반 체크: 서브배치별 독립 검사 ─────────────────────────────────
    # 각 설비 서브배치는 자신에게 배정된 수주의 가장 이른 납기를 기준으로 위반 여부 판정
    for j, (task_j, end_j, due_j) in enumerate(
        zip(split_tasks, split_end_dts, split_sub_dues)
    ):
        if due_j and end_j.date() > due_j:
            late_days = (end_j.date() - due_j).days
            result["violations"].append(
                {
                    "batch_id": rep.batch_id,
                    "task_id": task_j.task_id,
                    "type": "delivery",
                    "severity": "warning",
                    "detail": (
                        f"[분할배치 {j + 1}/{len(split_tasks)}] 납기 {due_j} 초과 "
                        f"→ 완료 {end_j.date()} (+{late_days}일)"
                    ),
                }
            )

    return True


def _find_eligible_equipment(
    batch: ProductionBatch, equipment: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """배치에 적합한 설비 필터링 (재질, SQ범위, 색상그룹)"""
    eligible = []
    for eq in equipment:
        # ── Material filter ────────────────────────────────────────────────
        if eq.material_limit and eq.material_limit != "ALL":
            if (
                batch.conductor_material
                and batch.conductor_material != eq.material_limit
            ):
                continue

        # ── Range filter ───────────────────────────────────────────────────
        if eq.range_unit == "mm":
            pass
        elif eq.range_unit == "Ø":
            # 연합 설비: SQ 기준으로 직접 분류 (小4BO: ~25SQ, 4BO: 35SQ~)
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_min and sq < float(eq.range_min):
                continue
            if sq and eq.range_max and sq > float(eq.range_max):
                continue
        else:
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_min and sq < float(eq.range_min):
                continue
            if sq and eq.range_max and sq > float(eq.range_max):
                continue

        # ── Color group filter (저압시스) ──────────────────────────────────
        if eq.color_group:
            color = (batch.sheath_color or "").strip()
            if eq.color_group == "흑/청":
                if color not in (
                    "흑",
                    "청",
                    "흑색",
                    "청색",
                    "BLACK",
                    "BLUE",
                    "BK",
                    "BL",
                    "",
                ):
                    continue

        eligible.append(eq)

    return eligible


def _narrow_by_stranding(
    batch: ProductionBatch, eligible: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """61연선 케이스 설비 선호도 좁히기 (fallback 있음).

    stranding_method 컬럼 또는 equipment_code 명칭으로 선호 설비를 추립니다.
    매칭 설비가 없으면 eligible 전체를 그대로 반환하여 스케줄링 skip을 방지합니다.
    """
    batch_st = (batch.stranding_type or "").strip()
    sq_val = float(batch.sq_mm2 or 0)

    if batch_st == "7연선코어":
        mat = (batch.conductor_material or "").upper()
        if mat == "AL":
            # AL 7연선코어 → AL6BO 선호 (material_limit="AL", stranding_method="7연선")
            preferred = [
                e
                for e in eligible
                if "AL6B" in (e.equipment_code or "").upper()
                or (
                    (e.stranding_method or "").strip() == "7연선"
                    and (e.material_limit or "").upper() == "AL"
                )
            ]
            return preferred if preferred else eligible
        # CU 7연선코어 → T6BO 선호
        preferred = [
            e
            for e in eligible
            if (e.stranding_method or "").strip() == "7연선"
            or "T6B" in (e.equipment_code or "").upper()
        ]
        return preferred if preferred else eligible

    if sq_val >= 300:
        # 54BO 선호: stranding_method="61연선" 또는 코드에 "54BO" 포함
        preferred = [
            e
            for e in eligible
            if (e.stranding_method or "").strip() == "61연선"
            or "54BO" in (e.equipment_code or "").upper()
        ]
        return preferred if preferred else eligible

    # 일반 연선: 7연선/61연선 전용 설비는 제외 (stranding_method 기준, 없으면 무시)
    excluded = [
        e for e in eligible if (e.stranding_method or "").strip() in ("7연선", "61연선")
    ]
    if len(excluded) < len(eligible):
        return [e for e in eligible if e not in excluded]
    return eligible


def align_start_to_predecessor_end(
    *,
    process_name: str,
    pred_proc: str | None,
    group_sqs: set[int],
    process_end_by_sq: dict[tuple[str, int], datetime],
    current_start: datetime,
    current_end: datetime,
    duration_min: float,
    tail_offset_min: float = 0.0,
    slots: list,
    db: "Session",
    equipment_code: str,
) -> tuple[datetime, datetime]:
    """후공정 종료가 선행공정 종료 + 후공정 1드럼 소요시간 이상이 되도록 시작을 지연한다.

    물리적 의미: 후공정은 선행공정 마지막 드럼이 나온 뒤에야 자기 마지막 드럼을 처리.
    → T_succ_end = T_pred_end + tail_offset_min (후공정 1드럼 wall-clock duration)

    불변식:
      - aligned_end >= T_target (T_target = calculate_end_datetime(pred_end, tail_offset_min))
      - aligned_end - aligned_start == duration_min (블록 폭 유지, 캘린더 보정 오차 허용)

    tail_offset_min 은 호출자가 `후공정 group_duration / drum_count` 로 산정한 per-drum 소요.
    시스 공정(저압시스/고압시스)은 pred_proc 외에 "연합" 종료도 함께 고려.
    pred_proc 가 None 이거나 process_end_by_sq 에 기록이 없으면 입력 그대로 반환.

    반환: (aligned_start, aligned_end)
    """
    pipeline_procs: list[str] = []
    if pred_proc:
        pipeline_procs.append(pred_proc)
    if process_name in ("저압시스", "고압시스"):
        pipeline_procs.append("연합")

    if not pipeline_procs:
        return current_start, current_end

    pred_end_latest: datetime | None = None
    for pp in pipeline_procs:
        for sq_i in group_sqs:
            pe = process_end_by_sq.get((pp, sq_i))
            if pe and pe < datetime.max:
                if pred_end_latest is None or pe > pred_end_latest:
                    pred_end_latest = pe

    if pred_end_latest is None:
        return current_start, current_end

    aligned_start = current_start
    aligned_end = current_end

    # Phase 1: aligned_end 를 pred_end 에 역산 정렬
    reverse_start = calculate_start_datetime(
        pred_end_latest, duration_min, db, equipment_code
    )
    if reverse_start > current_start:
        aligned_start = _find_available_slot(
            reverse_start, duration_min, slots, db, equipment_code
        )
        aligned_end = calculate_end_datetime(
            aligned_start, duration_min, db, equipment_code
        )
    if aligned_end < pred_end_latest:
        aligned_end = pred_end_latest

    # Phase 2: tail_offset_min 만큼 wall-clock 으로 shift (aligned_end > pred_end 보장)
    # 블록 폭(duration_min 기반 wall-clock)은 Phase 1 결과 그대로 유지.
    if tail_offset_min > 0 and aligned_start > current_start:
        shifted_start = aligned_start + timedelta(minutes=tail_offset_min)
        shifted_start = _find_available_slot(
            shifted_start, duration_min, slots, db, equipment_code
        )
        shifted_end = calculate_end_datetime(
            shifted_start, duration_min, db, equipment_code
        )
        aligned_start = shifted_start
        aligned_end = shifted_end

    return aligned_start, aligned_end


def _find_available_slot(
    earliest: datetime,
    duration_min: float,
    occupied_slots: list,
    db=None,
    equipment_code: str | None = None,
) -> datetime:
    """설비에서 가용한 첫 번째 슬롯 찾기.

    근무시간(08~22시) 기반 캘린더를 사용하여 실제 종료 시각을 계산한다.
    단순 timedelta 덧셈은 야간/주말을 무시하여 슬롯 겹침을 유발할 수 있다.
    """
    candidate = earliest
    sorted_slots = sorted(occupied_slots, key=lambda s: s[0])

    for slot_start, slot_end in sorted_slots:
        # 캘린더 기반 종료 시각으로 슬롯 겹침 판단
        if db is not None:
            candidate_end = calculate_end_datetime(
                candidate, duration_min, db, equipment_code
            )
        else:
            candidate_end = candidate + timedelta(minutes=duration_min)
        # 올림 일관성: auto_schedule line 922-925 는 end_dt 를 다음 정각으로 올림
        # 하여 timeline 에 등록한다. 여기서도 같은 규칙을 적용해야 slot_start 비교가
        # 실제 점유 시간과 일치 (Phase C Task 3 root cause — SH-A100 task 28942×28966
        # 19분 overlap 원인).
        if (
            candidate_end.minute > 0
            or candidate_end.second > 0
            or candidate_end.microsecond > 0
        ):
            candidate_end = candidate_end.replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)
        if candidate_end <= slot_start:
            # Fits before this slot
            return candidate
        if candidate < slot_end:
            candidate = slot_end  # Push after this slot

    return candidate


def _get_drum_winding_min(
    equipment_code: str,
    sq_mm2,
    speed_map: dict,
) -> float:
    """
    4-3: 드럼 권취 시간 반환.
    SpeedMaster.setup_start_min을 (equipment_code, sq_mm2) 키로 조회.
    매칭되는 레코드가 없으면 0 반환.
    """
    if sq_mm2 is None:
        return 0.0
    sq_key = float(sq_mm2)
    record = speed_map.get((equipment_code, sq_key))
    if record is None:
        # SQ 정확 매칭 실패 시 가장 가까운 SQ 레코드 탐색
        candidates = [
            (abs(k[1] - sq_key), v)
            for k, v in speed_map.items()
            if k[0] == equipment_code
        ]
        if candidates:
            record = min(candidates, key=lambda x: x[0])[1]
    if record is None:
        return 0.0
    return float(record.setup_start_min or 0)


def _get_stranding_setup_min(
    prev_sq: float | None,
    curr_sq: float | None,
    sq_to_wire_d: dict,
    spec_min: float,
    compound_min: float,
) -> float:
    """연선 공정 셋업 시간 결정 (3-tier).

    동일 SQ          → 0분 (교체 없음)
    다른 SQ, 동일 소선경 → compound_min (선재교체만, 기본 120분)
    다른 SQ, 다른 소선경 → spec_min     (규격교체만, 기본 240분)
    """
    if prev_sq is None or curr_sq is None:
        return spec_min
    if prev_sq == curr_sq:
        return 0.0
    prev_wd = sq_to_wire_d.get(int(prev_sq), None)
    curr_wd = sq_to_wire_d.get(int(curr_sq), None)
    if prev_wd and curr_wd and prev_wd == curr_wd:
        return compound_min  # 동일 소선경: 선재교체만
    return spec_min  # 다른 소선경: 규격교체만


def _get_tp_line_speed(
    equipment_code: str,
    sq_mm2,
    speed_map: dict,
) -> float | None:
    """
    4-5: T/P 설비 전용 라인 속도 반환.
    SpeedMaster에서 해당 설비 + SQ 조합의 line_speed_mpm을 읽는다.
    매칭 레코드 없으면 None 반환 (호출자가 기본값 사용).
    """
    if sq_mm2 is None:
        return None
    sq_key = float(sq_mm2)
    record = speed_map.get((equipment_code, sq_key))
    if record is None:
        # 근사 SQ 탐색
        candidates = [
            (abs(k[1] - sq_key), v)
            for k, v in speed_map.items()
            if k[0] == equipment_code
        ]
        if candidates:
            record = min(candidates, key=lambda x: x[0])[1]
    if record is None:
        return None
    speed = record.line_speed_mpm
    return float(speed) if speed else None


def _get_sheath_type(batch: ProductionBatch) -> str:
    """
    10-3: 배치에서 시스 재질 추출.
    ProductionBatch에 별도 sheath_type 컬럼이 없으므로 remarks 필드를
    우선 확인하고, 없으면 product_group에서 추론한다.
    인식 가능한 값: HFPO, LLDPE, PVC (기본값)
    """
    # remarks 또는 product_group에 재질 명시된 경우
    for field_val in (batch.remarks or "", batch.product_group or ""):
        upper = field_val.upper()
        if "HFPO" in upper:
            return "HFPO"
        if "LLDPE" in upper:
            return "LLDPE"
        if "PVC" in upper:
            return "PVC"
    return "PVC"  # 기본값


def _filter_by_sheath_routing(
    batch: ProductionBatch,
    equipment: list[EquipmentMaster],
) -> list[EquipmentMaster]:
    """
    10-3: 시스 재질에 따른 설비 라우팅 필터.
    - HFPO → equipment_code 또는 equipment_name에 'HFPO' 포함 설비만
    - LLDPE → equipment_code == 'A150' 설비만
    - PVC   → 표준 라우팅 (필터 없음)
    """
    sheath_type = _get_sheath_type(batch)
    routing_key = _SHEATH_ROUTING.get(sheath_type, "PVC")

    if routing_key == "PVC":
        # 표준 라우팅 — 제약 없음
        return equipment

    if routing_key == "HFPO":
        # HFPO 전용 설비 필터
        filtered = [
            eq
            for eq in equipment
            if "HFPO" in (eq.equipment_code or "").upper()
            or "HFPO" in (eq.equipment_name or "").upper()
        ]
        # HFPO 전용 설비가 없으면 전체 허용 (fallback — 경고는 checker에서 처리)
        return filtered if filtered else equipment

    if routing_key == "A150":
        # LLDPE → A150 고정
        filtered = [eq for eq in equipment if eq.equipment_code == "A150"]
        return filtered if filtered else equipment

    return equipment


_ALWAYS_FROZEN_STATUSES: frozenset[str] = frozenset(
    {"in_progress", "completed", "wip_complete"}
)


def _reset_non_frozen_for_retry(
    run_label: str,
    frozen_batch_ids: set[int],
    db: Session,
) -> None:
    """frozen 이 아닌 ScheduleTask / AuditLog / batch status 를 재시도 전 원위치.

    CP-SAT 재시도 (색상 soft, greedy 폴백) 전에 non-frozen 영역을 완전히 비워야
    한다. 그렇지 않으면 이전 solve 의 부분 삽입물이 남아 중복/오버랩을 일으킨다.

    Side effects:
      - AuditLog: non-frozen task_id 에 걸린 로그 일괄 삭제.
      - ScheduleTask: non-frozen task 삭제.
      - ProductionBatch: non-frozen 이면서 status=='scheduled' 인 배치를 'planned'
        + equipment_code=None 으로 리셋 (CP-SAT 는 planned 만 재스케줄 대상).
    DB commit 은 호출자 책임 — 여기서는 flush 까지만.
    """
    from app.infrastructure.models.audit_log import AuditLog

    non_frozen_task_ids = [
        t.task_id
        for t in db.query(ScheduleTask)
        .filter(ScheduleTask.run_label == run_label)
        .all()
        if t.batch_id not in frozen_batch_ids
    ]
    if non_frozen_task_ids:
        db.query(AuditLog).filter(AuditLog.task_id.in_(non_frozen_task_ids)).delete(
            synchronize_session=False
        )
        db.query(ScheduleTask).filter(
            ScheduleTask.task_id.in_(non_frozen_task_ids)
        ).delete(synchronize_session=False)

    reset_ids = [
        b.batch_id
        for b in db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "scheduled",
        )
        .all()
        if b.batch_id not in frozen_batch_ids
    ]
    if reset_ids:
        db.query(ProductionBatch).filter(
            ProductionBatch.batch_id.in_(reset_ids)
        ).update(
            {"status": "planned", "equipment_code": None},
            synchronize_session=False,
        )
    db.flush()


def _reschedule_affected_groups_cpsat(
    run_label: str,
    db: Session,
    affected_group_keys: set[str],
    *,
    base_date: datetime | None = None,
) -> dict:
    """긴급수주 재최적화 — CP-SAT 전역 경로 (P4 → P9-B).

    왜 전역 재최적화인가 (사용자 결정):
      - Merged 그룹을 "start 유지 + end 연장" 트릭으로 제자리에 두지 않고,
        CP-SAT 가 자유변수로 전체를 다시 풀어 납기 초과 최우선화.
      - 진행중/완료 작업만 frozen (in_progress/completed/wip_complete) +
        base_date 이전 start_datetime 의 scheduled 도 보호 (생산 중이므로).

    동작 (P9-B — 3-level fallback):
      1. hard_frozen_keys 계산: 보호 상태 배치 + base_date 이전 scheduled 의
         batch_group 집합.
      2. 비-frozen ScheduleTask 삭제 + 비-frozen 배치 status 를 'planned' 로 리셋.
      3. Level 1: cp_sat_schedule(tardiness_hard=True, sheath_color_hard=True)
         → 납기/색상 둘 다 엄격.
      4. Level 2 (INFEASIBLE 시): tardiness_hard=True, sheath_color_hard=False
         → 색상만 완화, 납기는 여전히 엄격.
      5. Level 3 (여전히 INFEASIBLE): tardiness_hard=False, sheath_color_hard=False
         → 둘 다 soft penalty 로 강등 (weight 기반 최소화).
      6. 그래도 실패 → greedy 폴백 (_run_optimization_once).

    각 단계 결과는 result["warnings"] 에 사유와 함께 기록된다.

    affected_group_keys 는 현재 단계에서는 소비하지 않는다. 이유:
      cp_sat_schedule 자체가 "run_label 의 planned 배치 전체" 를 푸는 설계라
      부분 최적화 mode 가 아직 없음. 향후 '부분 재최적화' 옵션이 추가되면
      affected 를 필터로 사용하도록 확장 가능. 현재는 시그니처 호환 목적.
    """
    from app.services.cp_sat_optimizer import cp_sat_schedule

    result: dict = {"total_tasks": 0, "violations": [], "warnings": []}

    # ── 1. base_date 기본값 — auto_schedule / _run_optimization_once 와 동일 ──
    if base_date is None:
        try:
            dp = run_label.split("_")[0]
            base_date = datetime(int(dp[:4]), int(dp[4:6]), int(dp[6:8]), 8, 0, 0)
        except Exception:
            from zoneinfo import ZoneInfo

            kst = datetime.now(ZoneInfo("Asia/Seoul"))
            base_date = kst.replace(hour=8, minute=0, second=0, microsecond=0).replace(
                tzinfo=None
            )

    # ── 2. hard_frozen 그룹 키 계산 ──────────────────────────────────────────
    #   (a) status in _ALWAYS_FROZEN_STATUSES 인 배치의 batch_group.
    #   (b) base_date 이전에 start_datetime 이 잡힌 'scheduled' 배치의 batch_group.
    #       — 이미 생산이 시작되고 있을 수 있으므로 이동 금지.
    all_batches: list[ProductionBatch] = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).all()
    )
    frozen_batch_ids: set[int] = {
        b.batch_id for b in all_batches if b.status in _ALWAYS_FROZEN_STATUSES
    }

    hard_frozen_keys: set[str] = {
        b.batch_group
        for b in all_batches
        if b.batch_group and b.status in _ALWAYS_FROZEN_STATUSES
    }

    # (b) 기존 ScheduleTask 중 base_date 이전 시작 + scheduled → frozen 로 승격
    _batch_group_by_id: dict[int, str] = {
        b.batch_id: b.batch_group for b in all_batches if b.batch_group
    }
    _batch_status_by_id: dict[int, str] = {b.batch_id: b.status for b in all_batches}

    existing_tasks = (
        db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    )
    for t in existing_tasks:
        bg = _batch_group_by_id.get(t.batch_id)
        st = _batch_status_by_id.get(t.batch_id)
        if (
            bg
            and st == "scheduled"
            and t.start_datetime is not None
            and t.start_datetime < base_date
        ):
            hard_frozen_keys.add(bg)
            # 이 batch 도 이동 금지 → frozen_batch_ids 에 포함시켜 task 삭제/리셋 대상에서 제외
            frozen_batch_ids.add(t.batch_id)

    # ── 2.5. 웜스타트 스냅샷 캡처 (P4-4) ──────────────────────────────────────
    #   삭제 직전의 비-frozen 태스크 위치를 기록해 CP-SAT 에 힌트로 주입한다.
    #   ERP 재업로드 시나리오: 신규 스냅샷의 80~95% batch_group 이 이전과 동일
    #   → 힌트 재사용으로 feasibility warm-up 단계 생략 → 2.5~5× speedup 기대.
    #   frozen 그룹은 이미 hard-pin 되므로 힌트 redundant — 스킵.
    #   add_hint() 는 silent-fail 이라 batch_group 이 신규 모델에 없어도 안전.
    from app.services.cp_sat_optimizer import _datetime_to_wmin

    warm_start_hints: dict[str, dict] = {}
    for t in existing_tasks:
        bg = _batch_group_by_id.get(t.batch_id)
        if not bg:
            continue
        if t.batch_id in frozen_batch_ids:
            continue  # frozen 은 frozen_group_keys 로 hard-pin
        if t.start_datetime is None or t.equipment_code is None:
            continue
        # 같은 batch_group 에 대해 첫 등장만 사용 (같은 그룹 다중 batch 가능).
        if bg in warm_start_hints:
            continue
        warm_start_hints[bg] = {
            "start_wmin": _datetime_to_wmin(t.start_datetime, base_date),
            "equipment_code": t.equipment_code,
        }

    # ── 3. 비-frozen ScheduleTask 삭제 + 비-frozen 배치 status 'planned' 리셋 ─
    #   CP-SAT 는 status=='planned' 배치만 재스케줄한다. 이 리셋 없이 호출하면
    #   scheduled 그대로 남은 배치는 "pre-load timeline" 블록 역할만 하고 재배치
    #   되지 않아 전역 재최적화가 되지 않는다. frozen 은 그대로 유지 (pre-load +
    #   cp_sat_schedule 이 frozen_group_keys 로 start/equipment 를 고정).
    from app.infrastructure.models.audit_log import AuditLog

    non_frozen_task_ids = [
        t.task_id for t in existing_tasks if t.batch_id not in frozen_batch_ids
    ]
    if non_frozen_task_ids:
        db.query(AuditLog).filter(AuditLog.task_id.in_(non_frozen_task_ids)).delete(
            synchronize_session=False
        )
        db.query(ScheduleTask).filter(
            ScheduleTask.task_id.in_(non_frozen_task_ids)
        ).delete(synchronize_session=False)

    non_frozen_scheduled_batch_ids = [
        b.batch_id
        for b in all_batches
        if b.batch_id not in frozen_batch_ids and b.status == "scheduled"
    ]
    if non_frozen_scheduled_batch_ids:
        db.query(ProductionBatch).filter(
            ProductionBatch.batch_id.in_(non_frozen_scheduled_batch_ids)
        ).update(
            {"status": "planned", "equipment_code": None}, synchronize_session=False
        )
    db.flush()

    # ── 4. CP-SAT Level 1: tardiness_hard=True, sheath_color_hard=True ──────
    # P9-B: 납기/색상 모두 엄격. 가장 strict 한 설정 — 해가 있으면 무조건 납기 지킴.
    # P4-4: warm_start_hints 로 이전 스케줄 위치 주입 — Level 1 에만. L2/L3 는
    # infeasible 에서의 완화 재시도라 힌트가 같은 해로 수렴시킬 위험.
    cp_result = cp_sat_schedule(
        run_label,
        db,
        base_date=base_date,
        frozen_group_keys=hard_frozen_keys or None,
        sheath_color_hard=True,
        tardiness_hard=True,
        warm_start_hints=warm_start_hints or None,
    )
    first_status = cp_result.get("solver_status")
    first_objective = cp_result.get("objective_value")
    second_status: str | None = None
    second_objective: int | None = None
    third_status: str | None = None
    third_objective: int | None = None

    # ── 5. Level 2: tardiness_hard=True, sheath_color_hard=False (색상만 완화) ──
    # 색상 hard 가 infeasible 주범일 가능성 높음 (같은 색상 block 연속 강제가
    # 설비 충돌·납기 제약과 동시에 성립 안될 때). 납기는 여전히 엄격.
    if first_status == "INFEASIBLE":
        _reset_non_frozen_for_retry(run_label, frozen_batch_ids, db)

        cp_result = cp_sat_schedule(
            run_label,
            db,
            base_date=base_date,
            frozen_group_keys=hard_frozen_keys or None,
            sheath_color_hard=False,
            tardiness_hard=True,
        )
        cp_result.setdefault("warnings", []).append(
            "납기 hard constraint 유지한 채 색상 hard 완화 (Level 2) 로 재시도"
        )
        second_status = cp_result.get("solver_status")
        second_objective = cp_result.get("objective_value")

    # ── 6. Level 3: tardiness_hard=False, sheath_color_hard=False (둘 다 완화) ─
    # 납기 제약이 infeasible 주범인 경우(수주량이 설비 용량 초과 등). soft penalty
    # 로 강등 → weight 기반 최소 지연 해를 구함. 사용자 UX: warning 으로 납기 위반
    # 가능성 안내. 2회 실패 시점에 원인 후보 정보를 함께 기록.
    if cp_result.get("solver_status") == "INFEASIBLE":
        _reset_non_frozen_for_retry(run_label, frozen_batch_ids, db)

        cp_result = cp_sat_schedule(
            run_label,
            db,
            base_date=base_date,
            frozen_group_keys=hard_frozen_keys or None,
            sheath_color_hard=False,
            tardiness_hard=False,
        )
        cp_result.setdefault("warnings", []).append(
            "납기+색상 모두 완화(Level 3, soft weighted tardiness) 로 재시도 "
            "— 납기 초과 가능성 있음"
        )
        third_status = cp_result.get("solver_status")
        third_objective = cp_result.get("objective_value")

    # ── 7. 그래도 실패 → greedy 폴백 (최후 수단) ────────────────────────────
    if cp_result.get("solver_status") not in ("OPTIMAL", "FEASIBLE"):
        _reset_non_frozen_for_retry(run_label, frozen_batch_ids, db)

        greedy_result = _run_optimization_once(run_label, db, base_date=base_date)
        result["total_tasks"] = greedy_result.get("total_tasks", 0)
        result["violations"] = greedy_result.get("violations", [])
        result["warnings"].extend(cp_result.get("warnings", []))
        result["warnings"].extend(greedy_result.get("warnings", []))

        # 3차까지 INFEASIBLE: 구조적 문제(frozen 충돌, horizon 초과 등).
        if third_status == "INFEASIBLE":
            result["warnings"].append(
                "INFEASIBLE 3회 연속 — 원인 후보: frozen 제약 충돌, "
                "horizon 내 할당 불가, 설비 처리량 초과 등 "
                "(납기+색상 모두 완화해도 해결되지 않음)"
            )
        elif second_status == "INFEASIBLE" and third_status is None:
            # 2차 INFEASIBLE 후 3차 돌리지 않은 경로(방어적 — 이론상 도달 불가)
            result["warnings"].append(
                "INFEASIBLE 2회 연속 — Level 3 로 미진입 (알 수 없는 오류)"
            )
        result["warnings"].append(
            "CP-SAT 재최적화 실패 → greedy 폴백으로 전환 | "
            f"solver_status(1차={first_status}, 2차={second_status}, 3차={third_status}) | "
            f"objective(1차={first_objective}, 2차={second_objective}, 3차={third_objective})"
        )
        return result

    # ── 7. 성공 — CP-SAT 결과 그대로 전달 (shape 유지) ───────────────────────
    result["total_tasks"] = cp_result.get("total_tasks", 0)
    result["violations"] = cp_result.get("violations", [])
    result["warnings"].extend(cp_result.get("warnings", []))
    return result


def reschedule_affected_groups(
    run_label: str,
    db: Session,
    affected_group_keys: set[str],
    *,
    base_date: datetime | None = None,
    use_cpsat: bool = False,
) -> dict:
    """긴급 수주 증분 반영 후 영향 받은 batch_group 만 부분 재스케줄링.

    Args:
        use_cpsat: False (기본) → 기존 greedy 경로 (부분 재스케줄).
            True → CP-SAT 전역 재최적화 경로 (P4).
                - hard_frozen: status in {in_progress, completed, wip_complete}
                  + base_date 이전 start_datetime 의 scheduled 배치의 batch_group.
                - 나머지 ScheduleTask 는 삭제하고 배치 status 를 planned 로 리셋,
                  그 뒤 cp_sat_schedule(frozen_group_keys=hard_frozen,
                  sheath_color_hard=True) 호출. INFEASIBLE 이면 sheath_color_hard
                  =False 로 1회 재시도, 그래도 실패하면 greedy 로 최종 폴백.

    Greedy (use_cpsat=False) 동작 원리
    ─────────
    1. 비영향 그룹의 기존 ScheduleTask → timeline / process_end_by_sq /
       process_first_output_by_sq를 미리 채운다.
    2. 영향 그룹의 ScheduleTask 삭제 + 배치 상태 'planned'으로 리셋.
    3. 영향 그룹만 대상으로 기존 그리디 루프를 실행. 비영향 그룹의 슬롯이
       timeline에 이미 박혀 있으므로 겹치지 않는 빈 자리를 찾는다.

    납기 기준 삽입 보장:
       ordered_group_items 정렬 시 영향·비영향 그룹이 모두 PROCESS_ORDER → EDD
       순으로 정렬된다. 비영향 그룹은 timeline 사전 등록 후 스킵되므로
       '납기가 더 이른 기존 배치는 그대로, 납기가 더 늦은 기존 배치 사이에 끼워넣기'
       효과를 timeline 레벨에서 자연스럽게 구현한다.
    """
    if use_cpsat:
        return _reschedule_affected_groups_cpsat(
            run_label, db, affected_group_keys, base_date=base_date
        )

    result: dict = {"total_tasks": 0, "violations": [], "warnings": []}

    if not affected_group_keys:
        return result

    # ── 1. 전체 기존 ScheduleTask 로드 ───────────────────────────────────────
    all_tasks = db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()

    # batch_group → task (1:1 보장: 그룹당 1 ScheduleTask)
    task_by_group: dict[str, ScheduleTask] = {}
    batch_by_id = {
        b.batch_id: b
        for b in db.query(ProductionBatch)
        .filter(ProductionBatch.run_label == run_label)
        .all()
    }
    for t in all_tasks:
        b = batch_by_id.get(t.batch_id)
        if b and b.batch_group:
            task_by_group[b.batch_group] = t

    # ── 2. 비영향 그룹: timeline + pipeline tracking 사전 채우기 ────────────
    #    frozen 배치의 ScheduleTask도 함께 포함 (frozen 그룹 ≠ affected)
    timeline: dict[str, list[tuple[datetime, datetime]]] = {}
    process_end_by_sq: dict[tuple[str, int], datetime] = {}
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}
    first_insul_output: datetime | None = None
    core_first_drum_by_main_sq: dict[int, datetime] = {}

    for gk, task in task_by_group.items():
        if gk in affected_group_keys:
            continue  # 영향 그룹은 나중에 재스케줄
        if task.start_datetime is None or task.end_datetime is None:
            continue
        # timeline 등록
        timeline.setdefault(task.equipment_code, []).append(
            (task.start_datetime, task.end_datetime)
        )
        # pipeline tracking: batch_group 대표 배치에서 process/sq 정보 추출
        b = batch_by_id.get(task.batch_id)
        if b is None:
            continue
        proc = b.process_name
        sq_int = int(b.sq_mm2 or 0)
        proc_sq = (proc, sq_int)
        # process_end_by_sq: 해당 공정+SQ 최대 종료 시각
        if (
            proc_sq not in process_end_by_sq
            or task.end_datetime > process_end_by_sq[proc_sq]
        ):
            process_end_by_sq[proc_sq] = task.end_datetime
        # process_first_output_by_sq: 해당 공정+SQ 최소 첫 드럼 출력 시각
        # 정확한 first_output_dt를 ScheduleTask에서 역산 (lot_count 이용)
        header = next(
            (
                bx
                for bx in batch_by_id.values()
                if bx.batch_group == gk and bx.batch_seq == -1
            ),
            None,
        )
        if header is not None:
            lot_count = max(int(header.drum_count or 1), 1)
        else:
            grp_batches = [bx for bx in batch_by_id.values() if bx.batch_group == gk]
            lot_count = max(sum(int(bx.drum_count or 1) for bx in grp_batches), 1)
        setup_min = float(task.setup_time_min or 0)
        dur = float(b.estimated_duration_min or 0) if b.estimated_duration_min else 0.0
        first_drum_min = setup_min + (dur / lot_count)
        from app.services.calendar_engine import calculate_end_datetime

        first_out = calculate_end_datetime(
            task.start_datetime, first_drum_min, db, task.equipment_code
        )
        if (
            proc_sq not in process_first_output_by_sq
            or first_out < process_first_output_by_sq[proc_sq]
        ):
            process_first_output_by_sq[proc_sq] = first_out
        # 절연 첫 출력 (시스 시작 기준)
        if proc in ("저압절연", "고압절연"):
            if first_insul_output is None or first_out < first_insul_output:
                first_insul_output = first_out
        # CORE 첫 드럼
        if _is_core_group(gk):
            msq = _extract_core_main_sq(gk)
            if msq and (
                msq not in core_first_drum_by_main_sq
                or first_out < core_first_drum_by_main_sq[msq]
            ):
                core_first_drum_by_main_sq[msq] = first_out

    # ── 3. 영향 그룹의 기존 ScheduleTask 삭제 + 배치 리셋 ─────────────────
    from app.infrastructure.models.audit_log import AuditLog

    affected_task_ids = [
        t.task_id for gk, t in task_by_group.items() if gk in affected_group_keys
    ]
    if affected_task_ids:
        db.query(AuditLog).filter(AuditLog.task_id.in_(affected_task_ids)).delete(
            synchronize_session=False
        )
        db.query(ScheduleTask).filter(
            ScheduleTask.task_id.in_(affected_task_ids)
        ).delete(synchronize_session=False)

    # 영향 그룹 배치 → 'planned' 리셋
    affected_batch_ids = [
        b.batch_id
        for b in batch_by_id.values()
        if b.batch_group in affected_group_keys and b.status == "scheduled"
    ]
    if affected_batch_ids:
        db.query(ProductionBatch).filter(
            ProductionBatch.batch_id.in_(affected_batch_ids)
        ).update(
            {"status": "planned", "equipment_code": None}, synchronize_session=False
        )
    db.flush()

    # ── 4. 영향 그룹 배치 로드 → 부분 그리디 실행 ──────────────────────────
    # 영향 그룹에 속한 'planned' 배치만 다시 로드 (flush 후)
    affected_batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_group.in_(affected_group_keys),
            ProductionBatch.status == "planned",
        )
        .all()
    )
    affected_batches.sort(
        key=lambda b: (
            PROCESS_ORDER.get(b.process_name, 50),
            b.batch_seq or 0,
            b.due_date or date.max,
            b.customer_priority or 99,
            -(float(b.sq_mm2 or 0)),
        )
    )

    if not affected_batches:
        result["warnings"].append(
            "재스케줄 대상 배치 없음 (모두 frozen 또는 이미 scheduled)"
        )
        return result

    # ── 5. _run_optimization_once 와 동일한 그리디 루프 — 영향 그룹만 ───────
    # 전체 재스케줄 대신 영향 그룹만 처리. timeline은 2단계에서 사전 채워진 상태.
    # affected_batches를 기존 _run_optimization_once 인자 형식으로 전달하는
    # 내부 함수 대신, _run_optimization_once를 직접 호출하되
    # 비영향 그룹 배치는 이미 'scheduled' 상태이므로 쿼리에서 자동 제외된다.
    #
    # 단, timeline은 비영향 그룹 슬롯으로 사전 채워야 하므로
    # _run_optimization_once 내부의 'existing_tasks' 로드가 이미 이를 포함한다.
    # → 별도 timeline 주입 없이 바로 호출 가능.
    partial_result = _run_optimization_once(run_label, db, base_date=base_date)
    result["total_tasks"] = partial_result.get("total_tasks", 0)
    result["violations"] = partial_result.get("violations", [])
    result["warnings"].extend(partial_result.get("warnings", []))
    return result


def reschedule(
    run_label: str, db: Session, *, base_date: datetime | None = None
) -> dict:
    """
    1-3: 긴급 변경 대응 — 기존 스케줄을 초기화하고 재스케줄링 수행.

    frozen 배치(in_progress/completed)의 schedule_tasks는 보존하고,
    planned/scheduled 배치의 schedule_tasks만 삭제 후 auto_schedule을 재실행한다.
    Returns: auto_schedule과 동일한 결과 dict + "cleared_tasks" 수

    P4-5 — ERP 재업로드 경로 웜스타트:
        삭제 직전에 비-frozen ScheduleTask 의 위치 스냅샷을 캡처해
        auto_schedule 로 전파한다. 재업로드 주기가 주 3~4회이고 기존 배치와
        80~95% 겹치는 실사용 패턴에서 CP-SAT feasibility warm-up 을 생략
        해 2.5~5× speedup 기대.
    """
    # run_stage1_update()와 동일하게 status != "planned"인 배치를 보호
    # wip_complete: WIP 소진 완료 배치 — 재스케줄 시에도 반드시 보존
    # in_progress/completed: 사용자 수동 설정 — 반드시 보존
    # scheduled: 기존 스케줄 결과 — 재스케줄 대상이므로 planned으로 초기화
    _ALWAYS_FROZEN = {"in_progress", "completed", "wip_complete"}

    # frozen 배치의 batch_id 수집 — 해당 schedule_tasks는 삭제하지 않음
    all_batches = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).all()
    )
    frozen_batch_ids = {b.batch_id for b in all_batches if b.status in _ALWAYS_FROZEN}
    batch_group_by_id: dict[int, str] = {
        b.batch_id: b.batch_group for b in all_batches if b.batch_group
    }

    # frozen 배치에 속하지 않는 schedule_tasks만 삭제
    existing_tasks = (
        db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    )

    # ── 웜스타트 스냅샷 캡처 (P4-5) ────────────────────────────────────────
    # base_date 가 None 이면 auto_schedule 내부에서 run_label 기반 유도와 동일하게
    # 재구성해 _datetime_to_wmin 의 reference 로 쓴다 (캡처 후 auto_schedule 이
    # 다시 내부적으로 유도하지만 값은 결정론적으로 같음).
    _hint_base = base_date
    if _hint_base is None:
        try:
            dp = run_label.split("_")[0]
            _hint_base = datetime(int(dp[:4]), int(dp[4:6]), int(dp[6:8]), 8, 0, 0)
        except Exception:
            from zoneinfo import ZoneInfo

            kst = datetime.now(ZoneInfo("Asia/Seoul"))
            _hint_base = kst.replace(hour=8, minute=0, second=0, microsecond=0).replace(
                tzinfo=None
            )

    from app.services.cp_sat_optimizer import _datetime_to_wmin

    warm_start_hints: dict[str, dict] = {}
    for t in existing_tasks:
        if t.batch_id in frozen_batch_ids:
            continue
        bg = batch_group_by_id.get(t.batch_id)
        if not bg or bg in warm_start_hints:
            continue
        if t.start_datetime is None or t.equipment_code is None:
            continue
        warm_start_hints[bg] = {
            "start_wmin": _datetime_to_wmin(t.start_datetime, _hint_base),
            "equipment_code": t.equipment_code,
        }

    cleared_count = 0
    from app.services.cp_sat_optimizer import _delete_task_safely

    for task in existing_tasks:
        if task.batch_id not in frozen_batch_ids:
            _delete_task_safely(db, task)
            cleared_count += 1

    # 배치 상태 초기화 — frozen 배치는 건드리지 않음
    # scheduled → planned (재스케줄 대상), wip_complete/in_progress/completed 유지
    for batch in all_batches:
        if batch.status == "scheduled":
            batch.status = "planned"
            batch.equipment_code = None

    db.flush()  # 삭제 반영 후 재스케줄

    # 재스케줄링 실행 — auto_schedule은 status='planned' 배치만 처리하므로 frozen 배치 안전.
    # warm_start_hints 는 kwargs 로 흘러 _run_with_retry → cp_sat_schedule 까지 전파.
    result = auto_schedule(
        run_label,
        db,
        base_date=base_date,
        warm_start_hints=warm_start_hints or None,
    )
    result["cleared_tasks"] = cleared_count
    return result
