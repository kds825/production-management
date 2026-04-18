"""Cascade Preview v2 — Pydantic 스키마.

Task 11: v2 계약 전용. Legacy v1 (`AffectedTask/CascadeConflict`) 는 routes/schedules.py
에 남아있는 `cascade-preview-legacy` 엔드포인트가 프로젝트 주요 schemas 모듈에서 직접
선언해 사용한다 (v2 와 스키마 자체가 달라 재사용 없음).

Task 13: bulk-update v2 계약 스키마도 본 모듈에 함께 둔다 — cascade preview 와 동일한
TZ/naive 규약을 공유하므로 한 곳에서 관리하는 편이 변경 impact 를 단일화.

TZ guard:
- 프로젝트 전체가 naive KST 기반. ISO 'Z' 접미사가 들어오면 pydantic 이 tz-aware datetime
  으로 파싱하므로, field_validator 에서 즉시 reject 하여 KST 전용 계약을 강제한다.
- `toISOString()` 을 쓰는 legacy 프론트 호출은 이 v2 엔드포인트와는 계약이 맞지 않으므로
  frontend 쪽에서 naive format 으로 전환이 필요 (마이그레이션 스펙에 명시).
"""

from datetime import datetime

from pydantic import BaseModel, field_validator


class CascadePreviewRequest(BaseModel):
    """v2 요청 — naive KST datetime 만 허용."""

    task_id: str
    new_start: datetime
    new_end: datetime
    new_equipment_code: str | None = None

    @field_validator("new_start", "new_end")
    @classmethod
    def _reject_tz_aware(cls, v: datetime) -> datetime:
        # 'Z' 접미사 / '+09:00' 등 tz-aware 값은 프로젝트 KST 불변식을 깨므로 즉시 차단.
        if v.tzinfo is not None:
            raise ValueError(
                "naive datetime required (KST). 'Z' suffix / offset not allowed."
            )
        return v


class PushEntry(BaseModel):
    """push/pull 공통 항목. 방향에 따라 reason 의 의미만 달라진다."""

    task_id: str
    equipment_code: str
    batch_label: str
    old_start: datetime
    old_end: datetime
    new_start: datetime
    new_end: datetime
    reason: str  # enum value (e.g. 'same_equipment_conflict') — 프론트 호환용 str


class UnresolvedEntry(BaseModel):
    task_id: str
    equipment_code: str
    batch_label: str
    reason: str  # UnresolvedReason enum value — str 로 내보내 프론트 파싱 단순화
    detail: str


class CascadePreviewResponse(BaseModel):
    """v2 응답 계약.

    - `request_id`: 감사/중복 방지용 uuid (응답 단위 고유).
    - `pushes` / `pulls` / `unresolved`: 변경 제안 및 해소 실패 목록.
    - `can_auto_resolve`: unresolved 가 0 이면 True — 프론트의 자동 적용 가드.
    - `iter_count` / `truncated`: BFS 실행 메타. truncated=True 면 HARD_TASK_LIMIT 도달.
    """

    request_id: str
    summary: str
    pushes: list[PushEntry]
    pulls: list[PushEntry]
    unresolved: list[UnresolvedEntry]
    can_auto_resolve: bool
    iter_count: int
    truncated: bool


# ---------------------------------------------------------------------------
# Task 13 — Bulk Update v2 스키마
#
# cascade-preview v2 와 동일한 naive KST 계약을 공유. preview 로부터 확정된 변경
# 배열을 받아 단일 트랜잭션으로 적용하는 엔드포인트의 입출력 타입.
# ---------------------------------------------------------------------------


from enum import Enum  # noqa: E402 — 섹션 분리 위해 여기서 import


class TaskChange(BaseModel):
    """단일 task 의 확정 변경. bulk-update v2 의 요소 타입."""

    task_id: str
    new_start: datetime
    new_end: datetime
    # None 이면 설비 유지 — 시간만 변경한 드래그 케이스.
    new_equipment_code: str | None = None

    @field_validator("new_start", "new_end")
    @classmethod
    def _reject_tz_aware(cls, v: datetime) -> datetime:
        # 같은 TZ 규약 — naive 만 허용 (cascade-preview 와 일관).
        if v.tzinfo is not None:
            raise ValueError(
                "naive datetime required (KST). 'Z' suffix / offset not allowed."
            )
        return v


class BulkUpdateRequestV2(BaseModel):
    """bulk-update v2 요청.

    `expected_cascade_request_id` 는 preview 응답의 request_id 를 선택적으로 묶어
    감사 로그에 연계하기 위한 참조값. 동시성 가드로 쓰지는 않음 (별도 concurrency
    token 이 필요하면 향후 확장).
    """

    changes: list[TaskChange]
    expected_cascade_request_id: str | None = None


class BulkUpdateErrorCode(str, Enum):
    """422 응답의 error_code 열거형.

    프론트가 dialog / toast 분기를 결정할 단일 키. 문자열 상속(str, Enum) 을 써서
    response JSON 에 value 가 그대로 직렬화되도록 함.
    """

    VALIDATION_OVERLAP_SAME_EQUIPMENT = "VALIDATION_OVERLAP_SAME_EQUIPMENT"
    VALIDATION_PREDECESSOR_VIOLATION = "VALIDATION_PREDECESSOR_VIOLATION"
    VALIDATION_DUE_DATE_VIOLATION = "VALIDATION_DUE_DATE_VIOLATION"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    CONCURRENT_UPDATE = "CONCURRENT_UPDATE"


class BulkUpdateError(BaseModel):
    """422 응답 body 에 detail 로 담기는 구조. 직접 response_model 로 쓰이지는 않고
    HTTPException(detail=...) 에 dict 로 실려 나간다 — FastAPI 기본 422 컨벤션 유지.
    """

    error_code: BulkUpdateErrorCode
    offending_task_id: str
    detail: str
    can_retry: bool


class BulkUpdateSuccess(BaseModel):
    """200 응답. change_set_id 는 Task 14 revert 가 사용할 Undo 앵커."""

    change_set_id: str
    updated_task_ids: list[str]
