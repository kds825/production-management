from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.domain.constraints import validate_task
from app.infrastructure.database import get_db
from app.infrastructure.memory_store import store
from app.infrastructure.models.constraint_config import ConstraintConfig
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


# ── 제약조건 마스터 CRUD (DB 기반) ───────────────────────────────────────────


@router.get("", summary="제약조건 목록 조회")
def list_constraints(db: Session = Depends(get_db)):
    rows = db.query(ConstraintConfig).order_by(ConstraintConfig.priority).all()
    return {
        "constraints": [
            {
                "constraint_id": r.constraint_id,
                "constraint_name": r.constraint_name,
                "category": r.category,
                "is_enabled": r.is_enabled,
                "priority": r.priority,
                "impact_level": r.impact_level,
                "params_json": r.params_json,
                "applicable_processes": r.applicable_processes,
                "implementation_type": r.implementation_type,
                "notes": r.notes,
            }
            for r in rows
        ],
        "total": len(rows),
        "enabled": sum(1 for r in rows if r.is_enabled),
    }


@router.patch("/{constraint_id}", summary="제약조건 수정 (on/off, 파라미터)")
def update_constraint(constraint_id: str, body: dict, db: Session = Depends(get_db)):
    row = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == constraint_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"제약조건 '{constraint_id}' 없음")
    if "is_enabled" in body:
        row.is_enabled = body["is_enabled"]
    if "params_json" in body:
        row.params_json = body["params_json"]
    if "priority" in body:
        row.priority = body["priority"]
    db.commit()
    return {"constraint_id": constraint_id, "updated": True}
