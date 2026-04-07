from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ProcessType:
    """Known process types — reference constants only, not enforced as an enum.
    새 공정 타입이 추가되더라도 코드 변경 없이 str로 수용된다."""

    DRAWING = "drawing"
    STRANDING = "stranding"
    HV_INSULATION = "hv_insulation"
    LV_INSULATION = "lv_insulation"
    TAPING = "taping"
    CABLING = "cabling"
    LV_JACKETING = "lv_jacketing"
    HV_JACKETING = "hv_jacketing"
    NEUTRAL_WIRE = "neutral_wire"


class TaskPriority(str, Enum):
    NORMAL = "normal"
    URGENT = "urgent"
    CRITICAL = "critical"


class TaskStatus(str, Enum):
    PLANNED = "planned"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    WIP_COMPLETE = "wip_complete"
    COMPLETED = "completed"
    DELAYED = "delayed"


@dataclass
class Equipment:
    id: str
    name: str
    process_type: str  # any string — ProcessType 상수는 참조용
    capabilities: list[str]
    capacity_tons_per_month: float
    max_diameter_mm: Optional[float] = None
    status: str = "available"

    def supports_spec(self, spec: str) -> bool:
        """설비가 해당 규격을 생산할 수 있는지 확인"""
        return spec in self.capabilities


@dataclass
class ProcessStep:
    order: int
    process_type: str  # any string — ProcessType 상수는 참조용
    equipment_ids: list[str]
    is_optional: bool = False


@dataclass
class ProcessRoute:
    id: str
    voltage: str
    core_count_range: str
    steps: list[ProcessStep]
    description: str


@dataclass
class Order:
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
    packaging: str = "목드럼"
    is_scheduled: bool = False
    priority: TaskPriority = TaskPriority.NORMAL


@dataclass
class ScheduleTask:
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
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PLANNED
    delivery_date: Optional[datetime] = None
    process_step: Optional[int] = None
    predecessors: list[str] = field(default_factory=list)
    notes: str = ""
    changeover_min: int = 0

    @property
    def duration_hours(self) -> float:
        """작업 소요 시간 (시간 단위)"""
        return (self.end - self.start).total_seconds() / 3600

    def overlaps(self, other: "ScheduleTask") -> bool:
        """두 작업의 시간 겹침 여부 확인"""
        return self.start < other.end and other.start < self.end
