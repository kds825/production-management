"""하드 제약 체커 — overlap / precedence / sq_range.

constraint_checker.py 분할 (Task 1.5, B-4.1).
원본 import path 보존 — constraint_checker 가 본 모듈에서 re-export.
"""


def _check_overlap(tasks: list) -> list[dict]:
    """동일 설비에서 시간 겹침 확인"""
    violations = []
    by_equip = {}
    for t in tasks:
        by_equip.setdefault(t.equipment_code, []).append(t)

    for eq_code, eq_tasks in by_equip.items():
        sorted_tasks = sorted(eq_tasks, key=lambda x: x.start_datetime)
        for i in range(len(sorted_tasks) - 1):
            if sorted_tasks[i].end_datetime > sorted_tasks[i + 1].start_datetime:
                violations.append(
                    {
                        "constraint_id": "overlap",
                        "task_id": sorted_tasks[i + 1].task_id,
                        "severity": "error",
                        "detail": f"설비 {eq_code}: 작업 {sorted_tasks[i].task_id}과 시간 겹침",
                    }
                )
    return violations


def _check_precedence(tasks, batches, equipment, config) -> list[dict]:
    """선행공정 완료 전 후속공정 시작 확인"""
    violations = []
    task_map = {t.task_id: t for t in tasks}
    for t in tasks:
        if t.predecessor_task_id:
            pred = task_map.get(t.predecessor_task_id)
            if pred and t.start_datetime < pred.end_datetime:
                violations.append(
                    {
                        "constraint_id": "9-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"선행작업 {pred.task_id} 완료 전 시작",
                    }
                )
    return violations


def _check_sq_range(tasks, batches, equipment, config) -> list[dict]:
    """설비 SQ 범위 확인"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        eq = equipment.get(t.equipment_code)
        if batch and eq and batch.sq_mm2:
            sq = float(batch.sq_mm2)
            if eq.range_min and sq < float(eq.range_min):
                violations.append(
                    {
                        "constraint_id": "5-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"SQ {sq} < 설비 최소 {eq.range_min}",
                    }
                )
            if eq.range_max and sq > float(eq.range_max):
                violations.append(
                    {
                        "constraint_id": "5-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"SQ {sq} > 설비 최대 {eq.range_max}",
                    }
                )
    return violations
