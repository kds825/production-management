"""Cascade Preview v2 — Pydantic 스키마.

Task 11: v2 계약 전용. Legacy v1 (`AffectedTask/CascadeConflict`) 는 routes/schedules.py
에 남아있는 `cascade-preview-legacy` 엔드포인트가 프로젝트 주요 schemas 모듈에서 직접
선언해 사용한다 (v2 와 스키마 자체가 달라 재사용 없음).

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
