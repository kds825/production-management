"""Greedy calendar-aware slot assignment loop — `_run_optimization_once`.

Extracted from ``app.application.scheduling.greedy.auto_schedule`` (Week 9 SRP cleanup).

This module owns ONE responsibility: given a set of `planned`
`ProductionBatch` rows for a run, walk them in priority order and
allocate calendar-aware slots on the eligible equipment, writing
`ScheduleTask` rows + audit logs as it goes.

Why a separate module: ``auto_schedule.py`` holds the **retry harness**
(CP-SAT-or-greedy + overlap detection + 3-level fallback +
tardiness-boost). Mixing that retry logic with the 900-line greedy core
inflated the file to 1500+ lines and obscured the responsibility split.

Why a single 900-line function still: the greedy pass shares dozens of
local-state caches (timeline, predecessor_map, sq_to_equip,
process_first_output_by_sq, ...). A correct decomposition into smaller
functions requires designing a state-bag dataclass and threading it
through; that's a separate engineering task tracked in the post-pilot
backlog. The extraction here gives file-level visibility while
preserving exact behaviour.

Why monkeypatching still works: the public re-export shell
``app.services.schedule_optimizer`` re-exports
``_run_optimization_once`` from this module, so existing tests that
patch ``schedule_optimizer._run_optimization_once`` see the new
location transparently.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.calendar_engine import calculate_end_datetime
from app.application.scheduling.greedy.loaders.base_date import (  # noqa: F401  # used in setup
    resolve_base_date,
)
from app.application.scheduling.greedy.loaders.master_data import (  # noqa: F401  # used in setup
    load_master_data,
)
from app.application.scheduling.greedy.loaders.planned_batches import (  # noqa: F401  # used in setup
    load_planned_batches,
)
from app.application.scheduling.greedy.loaders.wip_filter import (  # noqa: F401  # used in setup
    filter_wip_skippable,
)
from app.application._shared.group_ops import (
    _extract_core_main_sq,
    _is_core_group,
)
from app.application.scheduling.greedy.scheduler_state import SchedulerState
from app.application.scheduling.greedy._group_and_sort import (
    _group_and_sort,
)
from app.application.scheduling.greedy._assign_group import _assign_group


def _group_earliest_due(batches: list) -> date:
    """Earliest non-null due date in a batch list, or ``date.max``.

    Co-located here (not imported from auto_schedule) so this module
    has zero dependency on the retry harness — eliminating the cycle
    that would otherwise occur when ``auto_schedule.py`` imports back
    from this module via the schedule_optimizer shell.
    """
    dates = [b.due_date for b in batches if b.due_date is not None]
    return min(dates) if dates else date.max


def _seed_state_from_existing(
    existing_tasks: list[ScheduleTask], db: Session
) -> tuple[
    dict[tuple[str, int], datetime],
    dict[tuple[str, int], datetime],
    datetime | None,
    dict[int, datetime],
]:
    """Partial-rerun pipeline state seed — existing_tasks 로부터 4 개 dict/scalar 사전 채움.

    부분 재스케줄(partial reschedule) 호출 시 비영향 그룹의 task 가
    `existing_tasks` 로 전달됨. 본 함수는 그 task 들로부터 후행 그룹의
    선행 제약을 사전에 채워 반환한다:

    - process_end_by_sq seed: (process_name, sq) → 최대 end_datetime
    - process_first_output_by_sq seed: (process_name, sq) → 가장 이른 첫 드럼 출력
    - first_insul_output seed: 저압/고압절연 중 가장 이른 첫 드럼 출력
    - core_first_drum_by_main_sq seed: CORE/AL-CORE main_sq → 첫 드럼 출력

    전체 재스케줄 시 `existing_tasks=[]` → 모두 빈 dict / None 반환 (no-op).
    """
    process_end: dict[tuple[str, int], datetime] = {}
    first_output: dict[tuple[str, int], datetime] = {}
    insul_first: datetime | None = None
    core_first: dict[int, datetime] = {}

    if not existing_tasks:
        return process_end, first_output, insul_first, core_first

    seed_batch_ids = {t.batch_id for t in existing_tasks if t.batch_id is not None}
    seed_batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id.in_(seed_batch_ids))
        .all()
        if seed_batch_ids
        else []
    )
    seed_batch_map = {b.batch_id: b for b in seed_batches}

    for t in existing_tasks:
        if t.start_datetime is None or t.end_datetime is None:
            continue
        b = seed_batch_map.get(t.batch_id)
        if b is None:
            continue
        proc = b.process_name or ""
        sq_int = int(b.sq_mm2 or 0)
        proc_sq = (proc, sq_int)

        if proc_sq not in process_end or t.end_datetime > process_end[proc_sq]:
            process_end[proc_sq] = t.end_datetime

        setup_min = float(t.setup_time_min or 0)
        dur = float(b.estimated_duration_min or 0)
        lot_count = max(int(b.drum_count or 1), 1)
        first_drum_min = setup_min + (dur / lot_count if lot_count else dur)
        first_out = calculate_end_datetime(
            t.start_datetime, first_drum_min, db, t.equipment_code
        )

        if not _is_core_group(b.batch_group or ""):
            if proc_sq not in first_output or first_out < first_output[proc_sq]:
                first_output[proc_sq] = first_out
            if proc in ("저압절연", "고압절연"):
                if insul_first is None or first_out < insul_first:
                    insul_first = first_out
        else:
            msq = _extract_core_main_sq(b.batch_group or "")
            if msq is not None:
                if msq not in core_first or first_out < core_first[msq]:
                    core_first[msq] = first_out

    return process_end, first_output, insul_first, core_first


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

    # Phase 2 추출: greedy/loaders/planned_batches.py
    batches = load_planned_batches(run_label, db)

    if not batches:
        result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
        return result

    # Phase 2 추출: greedy/loaders/wip_filter.py
    batches, wip_skipped = filter_wip_skippable(batches, db)
    if wip_skipped:
        result["wip_skipped"] = wip_skipped

    # Phase 2 추출: greedy/loaders/base_date.py
    base_date = resolve_base_date(run_label, base_date)

    # Phase 2 추출: greedy/loaders/master_data.py
    _md = load_master_data(db)

    # Phase 4: SchedulerState — per-call mutable + master state container.
    # 모든 in-progress dict (timeline / predecessor_map / sq_to_equip / ...) 와
    # master data (equipment_by_process / speed_map / constraint_params / welding_min) 를
    # 한 객체로 묶어 (a) 함께 진화하는 의도 명시 (b) retry 사이 cross-contamination
    # 회피. 매 호출마다 새로 생성 (default_factory 격리). isolation invariant 는
    # tests/test_scheduler_state_isolation.py 가 freeze.
    state = SchedulerState(
        equipment_by_process=_md.equipment_by_process,
        speed_map=_md.speed_map,
        constraint_params=_md.constraint_params,
        welding_min=_md.welding_min,
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
    for t in existing_tasks:
        state.timeline.setdefault(t.equipment_code, []).append(
            (t.start_datetime, t.end_datetime)
        )

    # ── 부분 재스케줄용: 기존 tasks 로부터 파이프라인 state seed ──────────────
    # 전체 재스케줄에서는 existing_tasks=[] 이므로 no-op.
    # 부분 재스케줄 시 비영향 그룹 task 가 후행 그룹의 선행 제약을 채운다.
    (
        _seed_pe,
        _seed_fo,
        _seed_fi,
        _seed_cf,
    ) = _seed_state_from_existing(existing_tasks, db)
    state.process_end_by_sq.update(_seed_pe)
    state.process_first_output_by_sq.update(_seed_fo)
    state.first_insul_output = _seed_fi
    state.core_first_drum_by_main_sq.update(_seed_cf)

    # ── 그룹핑 + 정렬 (Phase 4 step 2c 추출) ───────────────────────────────
    # batch_group 단위 그루핑 + ST 소선경 cluster + 시스 색상 묶음 lookup +
    # 정렬 우선순위 (CORE → ST 소선경 클러스터 → 절연/시스 EDD) 계산.
    # ctx.prev_cluster_on_eq 는 inner loop 에서 시스 묶음 boundary 추적용.
    ordered_group_items, group_ctx = _group_and_sort(batches, db)

    # NOTE: audit log reason (per-task) 이 마지막 batches 원소의 sq_mm2 / due_date
    # 를 출력하기 위해 outer loop 변수 누설 (`for batch in batches:` 종료 후
    # batches[-1]) 에 의존했던 기존 동치 행동을 명시적으로 보존. step 2c 추출 후
    # outer loop 가 helper 로 이동했으므로 같은 값을 명시 binding.
    batch = batches[-1] if batches else None  # noqa: F841  # used in audit log reason

    for group_key, group_batches in ordered_group_items:
        _assign_group(
            group_key=group_key,
            group_batches=group_batches,
            state=state,
            group_ctx=group_ctx,
            base_date=base_date,
            run_label=run_label,
            db=db,
            result=result,
            last_batch_for_audit=batch,
        )

    return result


# ── 보조 헬퍼 — 모듈 외부에서 호출되거나 audit-log 에 직접 사용 ────────────


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
