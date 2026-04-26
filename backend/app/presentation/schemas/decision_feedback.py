"""decision_feedback Pydantic schema — Phase 6 Step 6-MVP.

POST /api/decision-feedback 요청·응답 스키마. admin 큐 GET/PATCH 는 Step 6-admin
에서 추가.

핵심 (2nd opinion review):
- payload_snapshot 은 frontend 가 보낸 그대로 저장 — DecisionCard 응답 전체.
  6개월 retention. batch 삭제 후에도 admin 큐 재생 가능.
- attachment_path 는 안정화 trial 한정 로컬 디스크. 본 schema 에서 raw 바이너리
  업로드는 다루지 않음 (multipart 별도 endpoint, Step 6-admin 단계에서).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DecisionFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_label: str = Field(..., max_length=50)
    batch_id: int
    task_id: Optional[int] = None

    section: Literal[
        "why",
        "impact",
        "handoff",
        "equipment_day",
        "bundle",
        "alternatives",
        "general",
    ]
    line_anchor: str = Field(..., max_length=50)
    constraint_id_hint: Optional[str] = Field(default=None, max_length=10)

    free_text: str = Field(..., min_length=1, max_length=5000)
    operator_id: str = Field(..., max_length=50)

    payload_snapshot: dict
    """DecisionCard 응답 전체 — frontend 가 보낸 그대로 보관."""


class DecisionFeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    created_at: datetime
    run_label: str
    batch_id: int
    task_id: Optional[int]
    section: str
    line_anchor: str
    constraint_id_hint: Optional[str]
    free_text: str
    operator_id: str
    status: str
