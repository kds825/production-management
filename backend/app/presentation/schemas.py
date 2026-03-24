"""
Pydantic v2 스키마 — API 요청/응답 직렬화
도메인 엔티티와 분리하여 표현 계층 책임만 담당
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.domain.entities import TaskPriority, TaskStatus


# ---------------------------------------------------------------------------
# 설비 (Equipment)
# ---------------------------------------------------------------------------


class EquipmentResponse(BaseModel):
    id: str
    name: str
    process_type: str  # open string — 새 공정 타입도 그대로 직렬화
    capabilities: list[str]
    capacity_tons_per_month: float
    max_diameter_mm: Optional[float] = None
    status: str


# ---------------------------------------------------------------------------
# 수주 (Order)
# ---------------------------------------------------------------------------


class OrderResponse(BaseModel):
    id: str
    voltage: str
    product_group: str
    spec: str
    core_count: int
    core_color: str
    sheath_color: str
    customer: str
    delivery_date: datetime
    length_m: float
    quantity: int
    total_length_m: float
    cu_weight_kg: Optional[float] = None
    al_weight_kg: Optional[float] = None
    packaging: str
    is_scheduled: bool
    priority: TaskPriority


# ---------------------------------------------------------------------------
# 스케줄 작업 (ScheduleTask)
# ---------------------------------------------------------------------------


class ScheduleTaskResponse(BaseModel):
    id: str
    order_id: str
    equipment_id: str
    product: str
    spec: str
    core_count: int
    color: str
    start: datetime
    end: datetime
    volume_m: float
    line_speed_m_per_min: float
    priority: TaskPriority
    status: TaskStatus
    delivery_date: Optional[datetime] = None
    process_step: Optional[int] = None
    predecessors: list[str]
    notes: str
    changeover_min: int
    duration_hours: float  # 계산 프로퍼티를 직렬화


class ScheduleTaskCreate(BaseModel):
    order_id: str
    equipment_id: str
    product: str
    spec: str
    core_count: int
    color: str
    start: datetime
    end: datetime
    volume_m: float
    line_speed_m_per_min: float
    priority: TaskPriority = TaskPriority.NORMAL
    delivery_date: Optional[datetime] = None
    process_step: Optional[int] = None
    predecessors: list[str] = Field(default_factory=list)
    notes: str = ""
    changeover_min: int = 0


class ScheduleTaskUpdate(BaseModel):
    equipment_id: Optional[str] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    volume_m: Optional[float] = None
    line_speed_m_per_min: Optional[float] = None
    priority: Optional[TaskPriority] = None
    status: Optional[TaskStatus] = None
    delivery_date: Optional[datetime] = None
    process_step: Optional[int] = None
    predecessors: Optional[list[str]] = None
    notes: Optional[str] = None
    changeover_min: Optional[int] = None


# ---------------------------------------------------------------------------
# 공정 경로 (ProcessRoute)
# ---------------------------------------------------------------------------


class ProcessStepResponse(BaseModel):
    order: int
    process_type: str  # open string — 새 공정 타입도 그대로 직렬화
    equipment_ids: list[str]
    is_optional: bool


class ProcessRouteResponse(BaseModel):
    id: str
    voltage: str
    core_count_range: str
    steps: list[ProcessStepResponse]
    description: str


# ---------------------------------------------------------------------------
# 선속도 (LineSpeed)
# ---------------------------------------------------------------------------


class LineSpeedResponse(BaseModel):
    spec: str
    speeds: dict[str, float]  # 공정 유형 → 속도(m/min)


# ---------------------------------------------------------------------------
# 제약 조건 검증 (Constraint Validation)
# ---------------------------------------------------------------------------


class ConstraintValidateRequest(BaseModel):
    task_id: str
    # 특정 task_id가 없으면 전체 스케줄을 검증
    validate_all: bool = False


class ConstraintViolationResponse(BaseModel):
    type: str
    severity: str
    message: str
    task_id: str
    related_task_id: Optional[str] = None


class ConstraintValidateResponse(BaseModel):
    task_id: Optional[str]
    violations: list[ConstraintViolationResponse]
    is_valid: bool
    error_count: int
    warning_count: int


# ---------------------------------------------------------------------------
# 스케줄 이력 (ScheduleHistory) — 미래 확장용 플레이스홀더
# ---------------------------------------------------------------------------


class ScheduleHistoryResponse(BaseModel):
    id: str
    snapshot_at: datetime
    description: str
    task_count: int
