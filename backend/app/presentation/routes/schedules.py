import copy
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.entities import ScheduleTask, TaskPriority, TaskStatus
from app.infrastructure.database import get_db
from app.infrastructure.memory_store import store
from app.infrastructure.models.production_batch import (
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_task import (
    ScheduleTask as ScheduleTaskModel,
)
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
        customer=getattr(task, "customer", None),
    )


def _db_task_to_response(
    task: ScheduleTaskModel,
    batch: ProductionBatchModel,
    group_volume_m: float | None = None,
    group_order_count: int = 1,
) -> ScheduleTaskResponse:
    """DB schedule_task + production_batch 레코드를 프론트엔드 응답 형태로 변환.

    group_volume_m: batch_group 전체 합산 길이 (None이면 단일 배치 길이 사용)
    group_order_count: 그룹 내 개별 행 수
    """
    # customer_priority(int) → TaskPriority
    cp = batch.customer_priority or 99
    if cp <= 3:
        priority = TaskPriority.CRITICAL
    elif cp <= 7:
        priority = TaskPriority.URGENT
    else:
        priority = TaskPriority.NORMAL

    # spec: "1C x 633SQ" 형태
    core_count = batch.core_count or 1
    sq_mm2 = batch.sq_mm2 or 0
    spec = f"{core_count}C x {int(sq_mm2)}SQ"

    # color: sheath_color 우선, 없으면 core_colors
    color = batch.sheath_color or batch.core_colors or ""

    # status: DB 값을 TaskStatus enum으로 안전하게 파싱 (알 수 없는 값은 PLANNED)
    try:
        status = TaskStatus(task.status)
    except (ValueError, KeyError):
        status = TaskStatus.PLANNED

    # duration_hours: start~end 차이 (ScheduleTask 도메인 프로퍼티와 동일 계산)
    duration_hours = (task.end_datetime - task.start_datetime).total_seconds() / 3600

    return ScheduleTaskResponse(
        id=f"TASK-{task.task_id}",
        order_id=batch.sales_order_id or "",
        equipment_id=task.equipment_code,
        product=batch.product_group or "",
        spec=spec,
        core_count=core_count,
        color=color,
        start=task.start_datetime,
        end=task.end_datetime,
        volume_m=group_volume_m
        if group_volume_m is not None
        else float(batch.total_length_m or 0),
        line_speed_m_per_min=float(batch.line_speed_mpm or 0),
        priority=priority,
        status=status,
        delivery_date=datetime(
            batch.due_date.year,
            batch.due_date.month,
            batch.due_date.day,
        )
        if batch.due_date
        else None,
        process_step=batch.batch_seq,
        predecessors=[f"TASK-{task.predecessor_task_id}"]
        if task.predecessor_task_id
        else [],
        notes=batch.remarks or "",
        changeover_min=int(task.setup_time_min or 0),
        duration_hours=duration_hours,
        customer=batch.customer_name or "",
        batch_group=task.batch_group or "",
    )


# ---------------------------------------------------------------------------
# 작업(Task) CRUD 엔드포인트
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=list[ScheduleTaskResponse])
def list_tasks(
    date_from: str | None = Query(None, description="시작일 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="종료일 YYYY-MM-DD"),
    process_type: str | None = Query(
        None, description="공정 필터 (연선,저압절연,저압시스 등)"
    ),
    equipment_id: str | None = Query(None, description="설비 필터"),
    voltage: str | None = Query(None, description="전압 필터 (저압/고압)"),
    db: Session = Depends(get_db),
) -> list[ScheduleTaskResponse]:
    """스케줄 작업 목록 조회 (시작 시간 오름차순).

    Stage 2 auto-scheduling 결과를 PostgreSQL에서 읽어 반환한다.
    date_from/date_to/process_type/equipment_id/voltage 쿼리 파라미터로
    Gantt 뷰에 필요한 구간만 필터링하여 전송량을 줄인다.
    DB에 schedule_task 레코드가 없을 경우 인메모리 store로 폴백하여
    개발 초기 샘플 데이터도 계속 볼 수 있다.
    """
    q = db.query(ScheduleTaskModel, ProductionBatchModel).join(
        ProductionBatchModel,
        ScheduleTaskModel.batch_id == ProductionBatchModel.batch_id,
    )

    # WIP 완료 배치는 간트에 미표시 — 재고로 대체된 공정이므로 스케줄 불필요
    q = q.filter(ProductionBatchModel.status != "wip_complete")

    # 날짜 범위 필터 — 태스크가 윈도우와 겹치는 것만 포함
    if date_from:
        q = q.filter(
            ScheduleTaskModel.start_datetime >= datetime.fromisoformat(date_from)
        )
    if date_to:
        q = q.filter(ScheduleTaskModel.end_datetime <= datetime.fromisoformat(date_to))

    # 공정명 필터
    if process_type:
        q = q.filter(ProductionBatchModel.process_name == process_type)

    # 설비 코드 필터
    if equipment_id:
        q = q.filter(ScheduleTaskModel.equipment_code == equipment_id)

    # 전압 필터 — 저압: 0.6kV 계열, 고압: 22.9kV / 35kV 계열
    if voltage == "저압":
        q = q.filter(ProductionBatchModel.voltage.contains("0.6"))
    elif voltage == "고압":
        q = q.filter(
            ProductionBatchModel.voltage.contains("22.9")
            | ProductionBatchModel.voltage.contains("35")
        )

    db_tasks = q.order_by(ScheduleTaskModel.start_datetime).all()

    # batch_group별 합산 volume 계산
    group_volumes: dict[str, float] = {}
    group_counts: dict[str, int] = {}
    for task_row, batch_row in db_tasks:
        bg = task_row.batch_group
        if bg:
            all_in_group = (
                db.query(func.sum(ProductionBatchModel.total_length_m))
                .filter(ProductionBatchModel.batch_group == bg)
                .scalar()
            )
            group_volumes[bg] = float(all_in_group or 0)
            cnt = (
                db.query(func.count(ProductionBatchModel.batch_id))
                .filter(ProductionBatchModel.batch_group == bg)
                .scalar()
            )
            group_counts[bg] = int(cnt or 1)

    return [
        _db_task_to_response(
            task,
            batch,
            group_volume_m=group_volumes.get(task.batch_group),
            group_order_count=group_counts.get(task.batch_group, 1),
        )
        for task, batch in db_tasks
    ]


@router.post("/tasks", response_model=ScheduleTaskResponse, status_code=201)
def create_task(body: ScheduleTaskCreate) -> ScheduleTaskResponse:
    """새 스케줄 작업 등록 (D&D 수동 배치용 — 인메모리 store 사용)"""
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
    """스케줄 작업 수정 (부분 업데이트 — D&D용, 인메모리 store 사용)"""
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
