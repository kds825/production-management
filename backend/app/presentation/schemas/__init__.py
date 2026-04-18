"""
Pydantic v2 스키마 — API 요청/응답 직렬화
도메인 엔티티와 분리하여 표현 계층 책임만 담당.

패키지화(2026-04-18): 기존 단일 `schemas.py` 모듈을 디렉터리 패키지로 변환.
- 기존 `from app.presentation.schemas import X` 호출 호환 유지를 위해 이 파일에
  base 스키마들을 그대로 둠.
- cascade 전용 스키마는 `schemas/cascade.py` 서브모듈로 분리 (Task 11).
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
    range_min: Optional[float] = None  # 최소 SQ (mm²)
    range_max: Optional[float] = None  # 최대 SQ (mm²)
    material_limit: Optional[str] = None  # CU, AL, ALL
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
    setup_time_min: int = 0  # 규격교체 시간 (SpeedMaster 기준)
    color_change_min: int = 0  # 색상교체 시간 (시스 공정)
    duration_hours: float  # 계산 프로퍼티를 직렬화
    customer: Optional[str] = None  # 거래처명 — production_batch.customer_name
    batch_group: Optional[str] = None  # 배치 그룹 식별자 — 간트 블록 1개 단위
    material: Optional[str] = None  # 도체 재질 — CU, AL
    batch_id: Optional[int] = (
        None  # production_batch.batch_id — 상태 변경 API 호출에 필요
    )
    created_at: Optional[datetime] = None  # schedule_task.created_at — 생성 시각
    sq_mm2: Optional[float] = None  # 도체 단면적 (mm²) — SQ별 색상 구분용
    lot_count: Optional[int] = None  # 헤더 배치 drum_count — 간트 틀 수 표시용
    # 시스(SH-*) 배치 블록에서 같은 batch_group 에 묶인 수주들의 SQ 규격 목록.
    # 예: ['50SQ', '100SQ']. 비시스 task 는 None — 프론트가 존재 여부로 분기.
    spec_list: Optional[list[str]] = None
    # production_batch.wip_matched_id (FK → wip_inventory.wip_id).
    # 프론트 ContextMenu(Task 5.2) "미배정으로 이동" disabled 판정용 — WIP 매칭된
    # 배치는 재고로 대체된 공정이라 해제 불가. None 이면 일반 생산 배치.
    wip_matched_id: Optional[int] = None


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
