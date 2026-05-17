"""cp_sat_schedule() 의 §9 선점 잔여 배치 후속 처리 — Phase 2 Task 2.8 추출.

원본: ``orchestrator.py:1189-1232`` 본문 그대로. 선점 분할로 생성된 잔여
배치(preempted_remainder)들을 같은 설비에서 순서대로 스케줄링한다.

Why module name `_preemption_runner.py`:
    같은 디렉토리에 이미 존재하는 ``preemption.py`` (긴급 배치 분할 로직
    `try_preempt_for_urgent`) 와 충돌하지 않도록 underscore prefix +
    `_runner` suffix. 본 모듈은 분할된 잔여 배치를 후속 스케줄링하는 use-case.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.application.scheduling.greedy.slot_finder import _find_available_slot
from app.infrastructure.calendar_engine import calculate_end_datetime
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


def schedule_preempted_remainders(
    *,
    preempted_remainder: list[ProductionBatch],
    timeline: dict[str, list],
    base_date: datetime,
    run_label: str,
    db: Session,
    predecessor_map: dict[tuple, int],
    result: dict[str, Any],
) -> None:
    """원본: orchestrator.py:1189-1232 본문 그대로.

    timeline / preempted_remainder / predecessor_map / result 를 in-place
    mutate. preempted_remainder 의 status 와 equipment_code 도 갱신.
    """
    # ── 9. 선점 잔여 배치 후속 배치 ───────────────────────────────────────────
    # 선점 분할로 생성된 잔여 배치들을 같은 설비에서 순서대로 스케줄링한다.
    # (이미 긴급 배치 슬롯이 timeline에 등록되어 있으므로 겹치지 않는다.)
    # Phase 6 step 6 (F-2): 같은 설비에 여러 잔여 배치가 쌓이는 패턴 → cache hit 잦음.
    _cal_end_cache: dict[tuple[str, datetime, int], datetime] = {}
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
            base_date,
            total_rem_dur,
            slots_rem,
            db,
            eq_code,
            calendar_end_cache=_cal_end_cache,
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
