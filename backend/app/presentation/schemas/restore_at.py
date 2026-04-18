"""POST /api/pipeline/batch-group/{bg}/restore-at 요청/응답 스키마.

계약: Task 1.1의 RestoreAtPlanResult / TaskPosition 데이터클래스와 호환.
응답은 cascade-preview-v2 shape와도 호환 (pushes/pulls/unresolved/request_id).
"""

from datetime import datetime

from pydantic import BaseModel, Field


class RestoreAtRequest(BaseModel):
    anchor_equipment_code: str = Field(..., min_length=1)
    # naive KST 가정 — tz-aware 주입은 서비스 레이어에서 reject 가능
    anchor_start: datetime


class TaskPositionSchema(BaseModel):
    task_id: int
    batch_id: int
    process_name: str
    new_equipment_code: str
    new_start: datetime
    new_end: datetime
    is_anchor: bool


class RestoreAtResponse(BaseModel):
    batch_group: str
    task_positions: list[TaskPositionSchema]
    pushes: list[dict] = []
    pulls: list[dict] = []
    unresolved: list[dict] = []
    request_id: str
    can_auto_resolve: bool
    iter_count: int
    truncated: bool
