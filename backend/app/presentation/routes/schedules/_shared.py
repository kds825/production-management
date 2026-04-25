"""schedules 라우트 서브패키지에서 공유하는 헬퍼/스키마/스토어.

Week 7 Task 7A.1 — schedules.py (1,702 LOC) 를 sub-package 로 분리하면서
복수 submodule 이 참조하는 항목만 이 모듈에 모은다.

포함 대상:
- 인메모리 버전 스토어 (`_versions`) + 버전 Pydantic 스키마
  (list/detail 두 submodule 이 동일 리스트를 사용해야 함)
- `_db_task_to_response` / `_to_response`
  (list_tasks, update_task, create_task 가 공유)
- `_parse_task_id`
  (cascade_preview_legacy + bulk_update_tasks_legacy 공통)
"""

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from app.domain.entities import ScheduleTask, TaskPriority, TaskStatus
from app.infrastructure.models.production_batch import (
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_task import (
    ScheduleTask as ScheduleTaskModel,
)
from app.presentation.schemas import ScheduleTaskResponse
from app.application.ingest import format_spec_display


# ---------------------------------------------------------------------------
# 인메모리 버전 스토어 — PoC 단계용 단순 리스트
# list.py / detail.py 양쪽이 같은 리스트를 보도록 _shared 에 유일한 인스턴스를 둔다.
# ---------------------------------------------------------------------------
_versions: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# 버전 관련 Pydantic 스키마
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
# Task 응답 변환 헬퍼
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
    color_change_min: int = 0,
    lot_count: int | None = None,
    spec_list: list[str] | None = None,
) -> ScheduleTaskResponse:
    """DB schedule_task + production_batch 레코드를 프론트엔드 응답 형태로 변환.

    group_volume_m: batch_group 전체 합산 길이 (None이면 단일 배치 길이 사용)
    group_order_count: 그룹 내 개별 행 수
    spec_list: 시스(SH-*) 블록에서 같은 batch_group 에 묶인 SQ 목록
               (예: ['50SQ','100SQ']). 비시스 task 는 None.
    """
    # customer_priority(int) → TaskPriority
    cp = batch.customer_priority or 99
    if cp <= 3:
        priority = TaskPriority.CRITICAL
    elif cp <= 7:
        priority = TaskPriority.URGENT
    else:
        priority = TaskPriority.NORMAL

    # spec: 고압이면 "1C x 4/0AWG" / "1C x 500KCMIL", 일반이면 "1C x 633SQ"
    core_count = batch.core_count or 1
    sq_mm2 = float(batch.sq_mm2 or 0)
    spec = format_spec_display(getattr(batch, "spec_raw", None), core_count, sq_mm2)

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
        process_name=batch.process_name,
        sales_order_line=batch.sales_order_line,
        predecessors=[f"TASK-{task.predecessor_task_id}"]
        if task.predecessor_task_id
        else [],
        notes=batch.remarks or "",
        changeover_min=int(task.setup_time_min or 0),
        setup_time_min=int(task.setup_time_min or 0),
        color_change_min=color_change_min,
        duration_hours=duration_hours,
        customer=batch.customer_name or "",
        batch_group=task.batch_group or "",
        material=batch.conductor_material or None,
        batch_id=batch.batch_id,
        created_at=task.created_at,
        sq_mm2=sq_mm2 if sq_mm2 else None,
        lot_count=lot_count,
        spec_list=spec_list,
        # WIP 매칭 FK를 그대로 노출 — 프론트 ContextMenu "미배정으로 이동"
        # disabled 판정에 사용 (Task 5.2). None 이면 일반 생산 배치.
        wip_matched_id=batch.wip_matched_id,
    )


# ---------------------------------------------------------------------------
# 공통 task_id 파서
# ---------------------------------------------------------------------------


def _parse_task_id(raw_id: str) -> int:
    """'TASK-123' → 123. 숫자만 들어온 경우도 허용."""
    prefix = "TASK-"
    suffix = raw_id[len(prefix) :] if raw_id.startswith(prefix) else raw_id
    if not suffix.isdigit():
        raise HTTPException(
            status_code=400,
            detail=f"task_id 형식이 올바르지 않습니다: '{raw_id}'",
        )
    return int(suffix)
