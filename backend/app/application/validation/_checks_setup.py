"""셋업/색상 체커 — color_group / setup_time.

constraint_checker.py 분할 (Task 1.5, B-4.1).
"""


def _check_color_group(tasks, batches, equipment, config) -> list[dict]:
    """설비 색상그룹 제한 확인 (A120: 흑/청만)"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        eq = equipment.get(t.equipment_code)
        if batch and eq and eq.color_group == "흑/청":
            color = (batch.sheath_color or "").strip()
            if color and color not in (
                "흑",
                "청",
                "흑색",
                "청색",
                "BLACK",
                "BLUE",
                "BK",
                "BL",
            ):
                violations.append(
                    {
                        "constraint_id": "3-3",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"A120 설비에 {color} 색상 배정 (흑/청만 가능)",
                    }
                )
    return violations


def _check_setup_time(tasks, batches, equipment, config) -> list[dict]:
    """규격교체 시간이 연속 작업 간에 반영되었는지 확인"""
    violations = []
    setup_params = config.params_json or {}  # noqa: F841 — reserved for future param lookup

    by_equip = {}
    for t in tasks:
        by_equip.setdefault(t.equipment_code, []).append(t)

    for eq_code, eq_tasks in by_equip.items():
        sorted_tasks = sorted(eq_tasks, key=lambda x: x.start_datetime)
        for i in range(len(sorted_tasks) - 1):
            curr = sorted_tasks[i]
            next_task = sorted_tasks[i + 1]
            curr_batch = batches.get(curr.batch_id)
            next_batch = batches.get(next_task.batch_id)

            if not curr_batch or not next_batch:
                continue

            # SQ 변경 발생 시 규격교체 준비시간 확보 여부 확인
            if curr_batch.sq_mm2 != next_batch.sq_mm2:
                gap_min = (
                    next_task.start_datetime - curr.end_datetime
                ).total_seconds() / 60
                # Why:
                # schedule_optimizer 는 setup 을 task.setup_time_min 내부에 저장하고
                # eq_total_duration 에 포함(line 1309). single-equipment 경로는 next 에,
                # multi-equipment 경로는 curr 에 setup 을 기록하는 차이가 있음.
                # 따라서 "어느 쪽에든 setup 시간이 기록됐는가" 를 gap + curr + next 합으로
                # 판정해야 false positive (gap만 보는 과거 로직) 가 발생하지 않는다.
                curr_setup = float(curr.setup_time_min or 0)
                next_setup = float(next_task.setup_time_min or 0)
                required_setup = max(curr_setup, next_setup)
                effective_setup = gap_min + curr_setup + next_setup

                if required_setup > 0 and effective_setup < required_setup * 0.5:
                    violations.append(
                        {
                            "constraint_id": "4-1",
                            "task_id": next_task.task_id,
                            "severity": "warning",
                            "detail": (
                                f"설비 {eq_code}: SQ {curr_batch.sq_mm2}→{next_batch.sq_mm2}"
                                f" 교체, 확보된 setup {effective_setup:.0f}분"
                                f" (gap={gap_min:.0f} + curr.setup={curr_setup:.0f}"
                                f" + next.setup={next_setup:.0f}, 필요: {required_setup:.0f}분)"
                            ),
                        }
                    )
    return violations
