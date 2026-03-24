from dataclasses import dataclass
from typing import Optional

from .entities import ScheduleTask, Equipment, ProcessRoute


@dataclass
class ConstraintViolation:
    type: str
    severity: str  # "error" or "warning"
    message: str
    task_id: str
    related_task_id: Optional[str] = None


def check_overlap(
    task: ScheduleTask, all_tasks: list[ScheduleTask]
) -> list[ConstraintViolation]:
    """동일 설비에서 동시간대 작업 겹침 검사"""
    violations = []
    for other in all_tasks:
        if (
            other.equipment_id == task.equipment_id
            and other.id != task.id
            and task.overlaps(other)
        ):
            violations.append(
                ConstraintViolation(
                    type="overlap",
                    severity="error",
                    message=(
                        f"'{task.product} {task.spec}'이(가) "
                        f"'{other.product} {other.spec}'과(와) 시간이 겹칩니다."
                    ),
                    task_id=task.id,
                    related_task_id=other.id,
                )
            )
    return violations


def check_equipment_capability(
    task: ScheduleTask, equipment: Equipment
) -> list[ConstraintViolation]:
    """설비 생산 가능 규격 검사"""
    if not equipment.supports_spec(task.spec):
        return [
            ConstraintViolation(
                type="equipment_capability",
                severity="error",
                message=(
                    f"설비 '{equipment.name}'에서 규격 '{task.spec}'을(를) "
                    f"생산할 수 없습니다."
                ),
                task_id=task.id,
            )
        ]
    return []


def check_delivery_date(task: ScheduleTask) -> list[ConstraintViolation]:
    """납기일 초과 여부 검사"""
    if task.delivery_date and task.end > task.delivery_date:
        return [
            ConstraintViolation(
                type="delivery",
                severity="warning",
                message=(
                    f"납기({task.delivery_date.strftime('%Y-%m-%d')}) 초과 예상: "
                    f"완료 예정 {task.end.strftime('%Y-%m-%d %H:%M')}"
                ),
                task_id=task.id,
            )
        ]
    return []


def check_precedence(
    task: ScheduleTask, all_tasks: list[ScheduleTask]
) -> list[ConstraintViolation]:
    """선행 공정 완료 여부 검사"""
    violations = []
    task_map = {t.id: t for t in all_tasks}
    for pred_id in task.predecessors:
        pred = task_map.get(pred_id)
        if pred and pred.end > task.start:
            violations.append(
                ConstraintViolation(
                    type="precedence",
                    severity="error",
                    message=(
                        f"선행 공정 '{pred.product} {pred.spec}'이(가) "
                        f"아직 완료되지 않았습니다."
                    ),
                    task_id=task.id,
                    related_task_id=pred_id,
                )
            )
    return violations


def check_process_route(
    task: ScheduleTask,
    equipment: Equipment,
    routes: list[ProcessRoute],
) -> list[ConstraintViolation]:
    """공정 경로 유효성 검사 - 해당 공정 단계에 올바른 설비가 배정되었는지 확인"""
    violations = []
    if task.process_step is not None:
        for route in routes:
            for step in route.steps:
                if step.order == task.process_step:
                    if (
                        equipment.id not in step.equipment_ids
                        and step.process_type != equipment.process_type
                    ):
                        violations.append(
                            ConstraintViolation(
                                type="process_route",
                                severity="error",
                                message=(
                                    f"공정 경로 위반: '{equipment.name}'은(는) "
                                    f"이 공정 단계에 사용할 수 없습니다."
                                ),
                                task_id=task.id,
                            )
                        )
    return violations


def validate_task(
    task: ScheduleTask,
    equipment: Equipment,
    all_tasks: list[ScheduleTask],
    routes: list[ProcessRoute] | None = None,
) -> list[ConstraintViolation]:
    """모든 제약 조건을 통합 검증"""
    violations: list[ConstraintViolation] = []
    violations.extend(check_overlap(task, all_tasks))
    violations.extend(check_equipment_capability(task, equipment))
    violations.extend(check_delivery_date(task))
    violations.extend(check_precedence(task, all_tasks))
    if routes:
        violations.extend(check_process_route(task, equipment, routes))
    return violations
