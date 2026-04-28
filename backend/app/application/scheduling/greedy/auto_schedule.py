"""auto_schedule + greedy 핵심 루프 + 재시도/JIT 헬퍼.

기존 위치: app.services.schedule_optimizer (Week 3 Task 3A.2 이전 분리됨).
원래 dotted path 는 schedule_optimizer 모듈에서 re-export 로 유지된다 (D7-C).

이 모듈에 포함된 심볼:
  공개:
    - auto_schedule          : Stage2 진입점 (greedy + CP-SAT 통합 retry 래퍼)
  비공개 (모니터링/재시도/내부 구현):
    - _SHEATH_ROUTING        : 시스 재질 → 설비 라우팅 규칙
    - _should_apply_jit      : SCHEDULER_JIT env 분기
    - _group_earliest_due    : 그룹 내 가장 이른 납기
    - _sheath_group_color_rank, _sheath_group_due_week_int  : 시스 그룹 정렬 보조
    - _purge_run_tasks       : 재시도 전 run-task 정리
    - _tardiness_boost_retry : 납기 boost 재시도 정책
    - _run_optimization_once : 그리디 한 사이클 (캘린더 기반 슬롯 배치)
    - _get_tp_line_speed     : T/P 설비 전용 라인 속도 헬퍼
    - _get_sheath_type       : 시스 재질 분류 헬퍼

설계 메모 — monkeypatch 호환:
  기존 테스트 (`test_overlap_retry`) 는 ``schedule_optimizer._run_optimization_once``
  를 monkeypatch 한 뒤 ``schedule_optimizer.auto_schedule(...)`` 를 호출한다.
  ``auto_schedule`` 가 모듈 로컬 binding 을 직접 호출하면 patched 버전이
  적용되지 않으므로, 호출 직전에 ``app.services.schedule_optimizer`` 모듈에서
  attribute lookup 을 한다 (re-export 셸이 동일 객체로 라우팅).
"""

from __future__ import annotations

import os
from datetime import date

from sqlalchemy.orm import Session

from app.exceptions import SchedulerOverlapError
from app.infrastructure.models.equipment_master import EquipmentMaster  # noqa: F401
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.application._shared.audit_logger import log_decision
from app.application.scheduling.greedy.jit_scheduling import apply_jit_delay


# ── 환경 분기 / 라우팅 상수 ─────────────────────────────────────────────────


def _should_apply_jit() -> bool:
    """SCHEDULER_JIT env var 읽어 JIT post-processing on/off 결정.

    "1" / "true" / "yes" (case-insensitive) 이면 활성. 그 외 (unset, "0") 비활성.
    """
    v = os.environ.get("SCHEDULER_JIT", "").strip().lower()
    return v in ("1", "true", "yes", "on")


# 시스 재질 → 설비 라우팅 규칙 (10-3)
# 값은 equipment_code prefix 또는 특수 라우팅 키.
# slot_filters._filter_by_sheath_routing 가 schedule_optimizer 셸 경유로 lookup.
_SHEATH_ROUTING = {
    "HFPO": "HFPO",  # HFPO 전용 라우팅 (설비 선택 시 HFPO 계열만)
    "PVC": "PVC",  # 표준 PVC 라우팅
    "LLDPE": "A150",  # LLDPE → A150 설비 고정
}


# ── 그룹 메타 헬퍼 ───────────────────────────────────────────────────────────


def _group_earliest_due(batches: list) -> date:
    """배치 목록에서 가장 이른 납기일을 반환한다. 납기 없으면 date.max."""
    dates = [b.due_date for b in batches if b.due_date is not None]
    return min(dates) if dates else date.max


def _sheath_group_color_rank(batches: list) -> int:
    """대표 배치의 sheath_color 로부터 색상 순위를 반환 (A'' 접근안).

    순위는 domain.batch_sheath_keys._SHEATH_COLOR_RANK 를 참조 (단일 소스 유지).
    """
    from app.domain.batch_sheath_keys import _SHEATH_COLOR_RANK

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


# ── 공개 API : auto_schedule ────────────────────────────────────────────────


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
    from app.application.validation import constraint_checker

    # monkeypatch 호환 lookup — 테스트가 schedule_optimizer 셸의 attribute 를
    # 패치해도 본 호출이 패치 결과를 따라가도록 동적 lookup 한다.
    import sys as _sys

    # 패키지 __init__.py 가 `from .auto_schedule import auto_schedule` 로 같은
    # 이름의 함수를 export 해 `from app.application.scheduling.greedy import
    # auto_schedule` 이 함수로 resolve 된다 — 모듈 자체를 참조하려면
    # sys.modules[__name__] (현재 submodule) 을 직접 lookup.
    _so = _sys.modules[__name__]

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
        from app.application._shared.calendar_ops import (
            _datetime_to_wmin,
            resolve_base_date,
        )

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
            from app.application.scheduling.cp_sat.orchestrator import cp_sat_schedule

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
                _so._purge_run_tasks(db, run_label)
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
                _so._purge_run_tasks(db, run_label)
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
                _so._purge_run_tasks(db, run_label)
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
                result = _so._run_optimization_once(run_label, db, **_greedy_kwargs)
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
            result = _so._run_optimization_once(run_label, db, **_greedy_kwargs)

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
            if _so._should_apply_jit():
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
                from app.domain.tardiness import count_tardiness

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
                result = _so._tardiness_boost_retry(
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
        _so._purge_run_tasks(db, run_label)

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

    # monkeypatch 호환 — 기존 테스트 (test_tardiness_boost_retry) 가
    # `schedule_optimizer.auto_schedule = _faulty_auto` 로 mock 한 뒤
    # 본 함수의 재호출이 mock 을 따르도록 모듈 lookup 으로 해소한다.
    import sys as _sys

    # 패키지 __init__.py 가 `from .auto_schedule import auto_schedule` 로 같은
    # 이름의 함수를 export 해 `from app.application.scheduling.greedy import
    # auto_schedule` 이 함수로 resolve 된다 — 모듈 자체를 참조하려면
    # sys.modules[__name__] (현재 submodule) 을 직접 lookup.
    _so = _sys.modules[__name__]

    sp = db.begin_nested()
    try:
        # Boost: customer_priority = 1 (critical). NULL 은 유지하지 않고 1 로.
        db.query(ProductionBatch).filter(
            ProductionBatch.batch_id.in_(tardy_batch_ids)
        ).update({ProductionBatch.customer_priority: 1}, synchronize_session=False)
        # 기존 태스크 purge — retry 가 처음부터 배치
        _so._purge_run_tasks(db, run_label)

        retry_result = _so.auto_schedule(
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


# ── Greedy core (Week 9 SRP cleanup) ────────────────────────────────────────
# `_run_optimization_once` 와 그 helpers (`_get_tp_line_speed`,
# `_get_sheath_type`) 는 `app.application.scheduling.greedy.optimization_loop` 으로 이동.
# 본 모듈은 retry harness (auto_schedule + _purge_run_tasks +
# _tardiness_boost_retry) 만 담당한다.
# 기존 dotted path (schedule_optimizer.* re-export 셸) 호환을 위해 여기서도
# 노출 — 테스트가 schedule_optimizer 모듈에 패치할 수 있도록 한다.
from app.application.scheduling.greedy.optimization_loop import (  # noqa: F401, E402  순환회피 + re-export
    _get_sheath_type,
    _get_tp_line_speed,
    _run_optimization_once,
)
