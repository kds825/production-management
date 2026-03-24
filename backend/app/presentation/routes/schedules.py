import copy
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.domain.entities import ScheduleTask, TaskStatus
from app.infrastructure.memory_store import store
from app.presentation.schemas import (
    ScheduleTaskCreate,
    ScheduleTaskResponse,
    ScheduleTaskUpdate,
)

router = APIRouter(prefix="/schedules", tags=["스케줄"])

# ---------------------------------------------------------------------------
# 인메모리 버전 스토어 — PoC 단계용 단순 리스트
# ---------------------------------------------------------------------------
_versions: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Pydantic 스키마 (버전 관련)
# ---------------------------------------------------------------------------


class VersionSaveRequest(BaseModel):
    label: str = ""
    tasks: list[dict[str, Any]]
    created_at: datetime | None = None


class VersionSummaryResponse(BaseModel):
    id: str
    label: str
    created_at: datetime
    task_count: int


class VersionDetailResponse(BaseModel):
    id: str
    label: str
    created_at: datetime
    tasks: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _to_response(task: ScheduleTask) -> ScheduleTaskResponse:
    return ScheduleTaskResponse(
        id=task.id,
        order_id=task.order_id,
        equipment_id=task.equipment_id,
        product=task.product,
        spec=task.spec,
        core_count=task.core_count,
        color=task.color,
        start=task.start,
        end=task.end,
        volume_m=task.volume_m,
        line_speed_m_per_min=task.line_speed_m_per_min,
        priority=task.priority,
        status=task.status,
        delivery_date=task.delivery_date,
        process_step=task.process_step,
        predecessors=task.predecessors,
        notes=task.notes,
        changeover_min=task.changeover_min,
        duration_hours=task.duration_hours,
    )


# ---------------------------------------------------------------------------
# 작업(Task) CRUD 엔드포인트
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=list[ScheduleTaskResponse])
def list_tasks() -> list[ScheduleTaskResponse]:
    """전체 스케줄 작업 목록 조회 (시작 시간 오름차순)"""
    return [_to_response(t) for t in store.list_tasks()]


@router.post("/tasks", response_model=ScheduleTaskResponse, status_code=201)
def create_task(body: ScheduleTaskCreate) -> ScheduleTaskResponse:
    """새 스케줄 작업 등록"""
    # 설비 존재 확인
    equipment = store.get_equipment(body.equipment_id)
    if equipment is None:
        raise HTTPException(
            status_code=404,
            detail=f"설비 '{body.equipment_id}'를 찾을 수 없습니다.",
        )

    # 수주 존재 확인 및 스케줄 완료 플래그 처리
    order = store.get_order(body.order_id)
    if order is None:
        raise HTTPException(
            status_code=404,
            detail=f"수주 '{body.order_id}'를 찾을 수 없습니다.",
        )

    task = ScheduleTask(
        id=f"TASK-{uuid.uuid4().hex[:8].upper()}",
        order_id=body.order_id,
        equipment_id=body.equipment_id,
        product=body.product,
        spec=body.spec,
        core_count=body.core_count,
        color=body.color,
        start=body.start,
        end=body.end,
        volume_m=body.volume_m,
        line_speed_m_per_min=body.line_speed_m_per_min,
        priority=body.priority,
        status=TaskStatus.PLANNED,
        delivery_date=body.delivery_date,
        process_step=body.process_step,
        predecessors=body.predecessors,
        notes=body.notes,
        changeover_min=body.changeover_min,
    )

    created = store.create_task(task)
    store.mark_order_scheduled(body.order_id)
    return _to_response(created)


@router.put("/tasks/{task_id}", response_model=ScheduleTaskResponse)
def update_task(task_id: str, body: ScheduleTaskUpdate) -> ScheduleTaskResponse:
    """스케줄 작업 수정 (부분 업데이트)"""
    # 존재 여부 확인
    existing = store.get_task(task_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"작업 '{task_id}'를 찾을 수 없습니다.",
        )

    # 설비 변경 시 유효성 확인
    if body.equipment_id is not None:
        if store.get_equipment(body.equipment_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"설비 '{body.equipment_id}'를 찾을 수 없습니다.",
            )

    updates = body.model_dump(exclude_none=True)
    updated = store.update_task(task_id, updates)
    return _to_response(updated)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str) -> None:
    """스케줄 작업 삭제"""
    if not store.delete_task(task_id):
        raise HTTPException(
            status_code=404,
            detail=f"작업 '{task_id}'를 찾을 수 없습니다.",
        )


# ---------------------------------------------------------------------------
# 버전(Version) 엔드포인트 — 스케줄 스냅샷 저장/조회
# ---------------------------------------------------------------------------


@router.post("/versions", response_model=VersionSummaryResponse, status_code=201)
def save_version(body: VersionSaveRequest) -> VersionSummaryResponse:
    """현재 스케줄을 새 버전으로 저장 (타임스탬프 자동 생성)"""
    now = body.created_at or datetime.now()
    version_id = f"VER-{uuid.uuid4().hex[:8].upper()}"
    label = body.label or f"버전 {now.strftime('%Y-%m-%d %H:%M')}"

    version: dict[str, Any] = {
        "id": version_id,
        "label": label,
        "created_at": now,
        # 깊은 복사로 스냅샷 보존
        "tasks": copy.deepcopy(body.tasks),
    }
    _versions.append(version)

    return VersionSummaryResponse(
        id=version_id,
        label=label,
        created_at=now,
        task_count=len(body.tasks),
    )


@router.get("/versions", response_model=list[VersionSummaryResponse])
def list_versions() -> list[VersionSummaryResponse]:
    """저장된 모든 버전 목록 조회 (최신 순)"""
    return [
        VersionSummaryResponse(
            id=v["id"],
            label=v["label"],
            created_at=v["created_at"],
            task_count=len(v["tasks"]),
        )
        for v in reversed(_versions)
    ]


@router.get("/versions/{version_id}", response_model=VersionDetailResponse)
def get_version(version_id: str) -> VersionDetailResponse:
    """특정 버전의 전체 작업 목록 조회"""
    version = next((v for v in _versions if v["id"] == version_id), None)
    if version is None:
        raise HTTPException(
            status_code=404,
            detail=f"버전 '{version_id}'를 찾을 수 없습니다.",
        )
    return VersionDetailResponse(
        id=version["id"],
        label=version["label"],
        created_at=version["created_at"],
        tasks=version["tasks"],
    )
