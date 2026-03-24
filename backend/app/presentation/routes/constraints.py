from fastapi import APIRouter, HTTPException

from app.domain.constraints import validate_task
from app.infrastructure.memory_store import store
from app.presentation.schemas import (
    ConstraintValidateRequest,
    ConstraintValidateResponse,
    ConstraintViolationResponse,
)

router = APIRouter(prefix="/constraints", tags=["제약 조건"])


@router.post("/validate", response_model=ConstraintValidateResponse)
def validate_constraints(body: ConstraintValidateRequest) -> ConstraintValidateResponse:
    """
    단일 작업 또는 전체 스케줄에 대한 제약 조건 검증
    - validate_all=True: 모든 작업 통합 검증
    - validate_all=False: task_id 단건 검증
    """
    all_tasks = store.list_tasks()
    routes = store.list_routes()
    violations_list: list[ConstraintViolationResponse] = []

    if body.validate_all:
        # 전체 스케줄 검증 — 각 작업을 순회하며 제약 위반 수집
        for task in all_tasks:
            equipment = store.get_equipment(task.equipment_id)
            if equipment is None:
                continue
            raw_violations = validate_task(task, equipment, all_tasks, routes)
            for v in raw_violations:
                violations_list.append(
                    ConstraintViolationResponse(
                        type=v.type,
                        severity=v.severity,
                        message=v.message,
                        task_id=v.task_id,
                        related_task_id=v.related_task_id,
                    )
                )
        task_id_result = None
    else:
        # 단건 작업 검증
        task = store.get_task(body.task_id)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail=f"작업 '{body.task_id}'를 찾을 수 없습니다.",
            )
        equipment = store.get_equipment(task.equipment_id)
        if equipment is None:
            raise HTTPException(
                status_code=404,
                detail=f"설비 '{task.equipment_id}'를 찾을 수 없습니다.",
            )
        raw_violations = validate_task(task, equipment, all_tasks, routes)
        for v in raw_violations:
            violations_list.append(
                ConstraintViolationResponse(
                    type=v.type,
                    severity=v.severity,
                    message=v.message,
                    task_id=v.task_id,
                    related_task_id=v.related_task_id,
                )
            )
        task_id_result = body.task_id

    error_count = sum(1 for v in violations_list if v.severity == "error")
    warning_count = sum(1 for v in violations_list if v.severity == "warning")

    return ConstraintValidateResponse(
        task_id=task_id_result,
        violations=violations_list,
        is_valid=error_count == 0,
        error_count=error_count,
        warning_count=warning_count,
    )
