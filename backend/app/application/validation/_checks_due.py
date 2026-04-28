"""납기 / 우선순위 체커 — delivery / due_type / priority_order.

constraint_checker.py 분할 (Task 1.5, B-4.1).
"""

from datetime import timedelta


def _check_delivery(tasks, batches) -> list[dict]:
    """납기 초과 확인 — 사용자 요구 '납기는 반드시 지켜져야함' → severity=error"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        if batch and batch.due_date and t.end_datetime.date() > batch.due_date:
            violations.append(
                {
                    "constraint_id": "1-1",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    # 납기는 하드 제약 — 위반 시 error 로 격상하여 재시도/알림 트리거
                    "severity": "error",
                    "detail": f"납기 {batch.due_date} 초과 (완료 예정: {t.end_datetime.date()})",
                }
            )
    return violations


def _check_priority_order(tasks, batches, equipment, config) -> list[dict]:
    """같은 설비에서 우선순위 낮은 주문이 높은 주문보다 먼저 배치되었는지"""
    violations = []
    by_equip = {}
    for t in tasks:
        by_equip.setdefault(t.equipment_code, []).append(t)

    for eq_code, eq_tasks in by_equip.items():
        sorted_tasks = sorted(eq_tasks, key=lambda x: x.start_datetime)
        for i in range(len(sorted_tasks) - 1):
            b1 = batches.get(sorted_tasks[i].batch_id)
            b2 = batches.get(sorted_tasks[i + 1].batch_id)
            if b1 and b2:
                if (b1.customer_priority or 99) > (b2.customer_priority or 99):
                    if b1.due_date and b2.due_date and b1.due_date > b2.due_date:
                        # Lower priority task is scheduled first AND has later due date
                        violations.append(
                            {
                                "constraint_id": "1-1",
                                "task_id": sorted_tasks[i].task_id,
                                "severity": "warning",
                                "detail": (
                                    f"우선순위 역전: {b1.customer_name}(P{b1.customer_priority})"
                                    f" before {b2.customer_name}(P{b2.customer_priority})"
                                ),
                            }
                        )
    return violations


def _check_due_type(tasks, batches, equipment, config) -> list[dict]:
    """도착기준 고객은 운송일 1일 차감하여 실질 납기 체크"""

    violations = []
    transport_days = 1  # default
    if config.params_json:
        transport_days = config.params_json.get("transport_days", 1)

    for t in tasks:
        batch = batches.get(t.batch_id)
        if not batch or not batch.due_date:
            continue
        # Check if customer is 도착기준 type
        # For now, check customer_name against known 도착기준 customers
        if batch.customer_name and "아이마켓" in batch.customer_name:
            effective_due = batch.due_date - timedelta(days=transport_days)
            if t.end_datetime.date() > effective_due:
                violations.append(
                    {
                        "constraint_id": "1-2",
                        "task_id": t.task_id,
                        "severity": "warning",
                        "detail": (
                            f"도착기준 고객 {batch.customer_name}: 실질납기 {effective_due}"
                            f" 초과 (완료: {t.end_datetime.date()})"
                        ),
                    }
                )
    return violations
