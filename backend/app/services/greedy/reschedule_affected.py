"""긴급 수주 증분 반영 + 전체 재스케줄 진입점.

기존 위치: app.services.schedule_optimizer (Week 3 Task 3A.2 이전 분리됨).
원래 dotted path 는 schedule_optimizer 모듈에서 re-export 로 유지된다 (D7-C).

이 모듈에 포함된 심볼:
  공개:
    - reschedule_affected_groups : 영향받은 batch_group 만 부분 재스케줄
    - reschedule                 : 전체 run 재스케줄 (frozen 보존)
  비공개:
    - _ALWAYS_FROZEN_STATUSES    : frozen 상태 집합 (in_progress/completed/wip_complete)
    - _reset_non_frozen_for_retry: CP-SAT 재시도 전 non-frozen 상태 리셋
    - _reschedule_affected_groups_cpsat : CP-SAT 전역 재최적화 (P9-B 3-level fallback)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.calendar_engine import calculate_end_datetime
from app.services.greedy.auto_schedule import _run_optimization_once
from app.application._shared.group_ops import (
    _extract_core_main_sq,
    _is_core_group,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_ALWAYS_FROZEN_STATUSES: frozenset[str] = frozenset(
    {"in_progress", "completed", "wip_complete"}
)


def _reset_non_frozen_for_retry(
    run_label: str,
    frozen_batch_ids: set[int],
    db: "Session",
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
    db: "Session",
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
      부분 최적화 mode 가 아직 없음. 향후 '부분 최적화' 옵션이 추가되면
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
    from app.application._shared.calendar_ops import _datetime_to_wmin

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
    db: "Session",
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
    run_label: str, db: "Session", *, base_date: datetime | None = None
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

    from app.application._shared.calendar_ops import _datetime_to_wmin

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
    from app.application._shared.db_ops import _delete_task_safely

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
    # monkeypatch 호환 — 테스트가 `schedule_optimizer.auto_schedule = ...` 패치 시
    # 본 호출이 패치를 따르도록 모듈 lookup.
    from app.services import schedule_optimizer as _so

    result = _so.auto_schedule(
        run_label,
        db,
        base_date=base_date,
        warm_start_hints=warm_start_hints or None,
    )
    result["cleared_tasks"] = cleared_count
    return result
