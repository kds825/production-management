"""JIT (Just-In-Time) 후방 shift — 납기 여유 활용 post-processing.

목적: PDF 1안 의 T6B0 4/8~4/12 gap 같은 "의도적 여백" 재현. greedy forward
packing 이 끝난 schedule 에 대해 납기 여유가 큰 task 를 설비 내 gap 과
후공정 제약 범위에서 뒤로 미룸.

설계: docs/specs/2026-04-18-jit-scheduling-design.md.

auto_schedule() 내부에서 env var ``SCHEDULER_JIT=1`` 일 때 호출된다. 파이프라인
체인 (연선→절연→시스) 은 fixed-point iteration 으로 전파 — 한 pass 에서
downstream task 가 shift 되면 upstream 의 successor cap 이 확장되어 다음
pass 에서 추가 shift 가능. shift 0 pass 에 도달하면 종료.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time

from sqlalchemy.orm import Session

from app.infrastructure.models import ProductionBatch, ScheduleTask
from app.services.calendar_engine import calculate_start_datetime

_MAX_JIT_ITERATIONS = 5


def apply_jit_delay(
    tasks: list[ScheduleTask],
    db: Session | None,
    *,
    min_slack_days: int = 3,
    max_iterations: int = _MAX_JIT_ITERATIONS,
) -> int:
    """납기 여유 큰 task 를 설비 내 gap 범위에서 뒤로 shift.

    불변식 (invariants):
        - 설비 내 task 순서 보존 (tasks[i].end <= tasks[i+1].start)
        - 납기 준수 (shifted end <= 납기일 23:59)
        - Pipeline dependency 유지 (successor task.start >= shifted end)
        - Wallclock duration 불변 (calculate_start_datetime 으로 break 반영)

    Args:
        tasks: scheduler 가 생성한 ScheduleTask 리스트 (mutated in-place).
        db: calendar_engine 접근용 session (None 이면 pure timedelta 역산).
        min_slack_days: 납기 여유 이 일수 이상일 때만 shift 고려.

    Returns:
        실제 shift 가 적용된 task 개수.
    """
    if not tasks:
        return 0

    # batch_id → ProductionBatch lookup (due_date, sales_order 참조용)
    batch_ids = {t.batch_id for t in tasks if t.batch_id}
    if db is not None and batch_ids:
        pb_rows = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_id.in_(batch_ids))
            .all()
        )
        pb_by_id = {pb.batch_id: pb for pb in pb_rows}
    else:
        pb_by_id = {}

    # Fixed-point iteration — downstream shift 이후 upstream cap 확장 재평가.
    total = 0
    for _ in range(max_iterations):
        pass_shifts = _backward_pass(tasks, db, pb_by_id, min_slack_days)
        total += pass_shifts
        if pass_shifts == 0:
            break
    return total


def _backward_pass(
    tasks: list[ScheduleTask],
    db: Session | None,
    pb_by_id: dict,
    min_slack_days: int,
) -> int:
    """한 번의 우→좌 backward shift pass — 설비별 독립.

    호출마다 successors_by_key 를 tasks 의 현재 start_datetime 기준으로 재계산
    → 이전 pass 에서 shift 된 downstream 반영.
    """
    # Successor lookup — 같은 (sales_order_id, sales_order_line) 의 다른 task
    successors_by_key: dict[tuple, list[ScheduleTask]] = defaultdict(list)
    for t in tasks:
        pb = pb_by_id.get(t.batch_id)
        if pb and pb.sales_order_id:
            key = (pb.sales_order_id, pb.sales_order_line)
            successors_by_key[key].append(t)

    # 설비별 task 수집 + 정렬
    by_eq: dict[str, list[ScheduleTask]] = defaultdict(list)
    for t in tasks:
        by_eq[t.equipment_code].append(t)

    shifts_applied = 0
    for eq, eq_tasks in by_eq.items():
        eq_tasks.sort(key=lambda x: x.start_datetime)

        # 우→좌 pass
        for i in reversed(range(len(eq_tasks))):
            t = eq_tasks[i]
            pb = pb_by_id.get(t.batch_id)
            if pb is None or pb.due_date is None:
                continue

            # 1. 납기 여유 확인
            slack_days = (pb.due_date - t.end_datetime.date()).days
            if slack_days < min_slack_days:
                continue

            due_end = datetime.combine(pb.due_date, time(23, 59))

            # 2. 설비 내 다음 task 의 start (upper bound) + 이전 task 의 end (lower bound)
            next_start = (
                eq_tasks[i + 1].start_datetime
                if i + 1 < len(eq_tasks)
                else datetime.max
            )
            prev_end = eq_tasks[i - 1].end_datetime if i > 0 else datetime.min

            # 3. Successor (후공정) 제약 — 같은 수주 line 의 다른 task 중
            #    start 가 현재 t.end 이상인 것 = 후공정.
            # >= 사용 이유: 연속 공정 간 adjacent (prev.end == succ.start) 관계도
            # 반드시 후공정으로 인식되어야 shift 가 후공정을 침범하지 않음.
            succ_cap = datetime.max
            key = (pb.sales_order_id, pb.sales_order_line)
            for succ in successors_by_key.get(key, []):
                if succ.task_id == t.task_id:
                    continue
                if succ.start_datetime >= t.end_datetime:
                    if succ.start_datetime < succ_cap:
                        succ_cap = succ.start_datetime

            upper = min(next_start, succ_cap, due_end)
            if upper <= t.end_datetime:
                continue  # 현재 end 가 이미 upper — shift 여유 없음

            # 4. Wallclock duration 유지하며 new_start 역산
            wall_min = (t.end_datetime - t.start_datetime).total_seconds() / 60
            if wall_min <= 0:
                continue

            new_end = upper
            if db is not None:
                new_start = calculate_start_datetime(new_end, wall_min, db, eq)
            else:
                # Pure timedelta fallback (test 용)
                from datetime import timedelta

                new_start = new_end - timedelta(minutes=wall_min)

            if new_start <= t.start_datetime:
                continue  # gain 없음

            # Defensive sanity: calculate_start_datetime 의 edge case (working hour 창
            # 경계, break 구간 처리 등) 에서 드물게 new_start 가 new_end 이후로 튀는
            # 현상 관측 — 이 경우 task 의 start/end 역전이 발생해 overlap 생성.
            if new_start >= new_end:
                continue

            # Defensive lower-bound: wall_min 은 working_min 의 상한이라
            # calculate_start_datetime 이 backward 로 overshoot 할 수 있음.
            # 그 결과 new_start < prev_end 이면 설비 내 overlap 을 만들 수 있으므로 skip.
            if new_start < prev_end:
                continue

            # 5. Apply shift
            t.start_datetime = new_start
            t.end_datetime = new_end
            shifts_applied += 1

    return shifts_applied
