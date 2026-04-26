"""decision_feedback 운영자 의견 endpoint — Phase 6 Step 6-MVP.

`POST /api/decision-feedback` — 운영자가 [⚠️ 이상한 것 같아요] dialog 에서
제출한 의견을 저장. admin 큐 GET/PATCH 는 Step 6-admin 에서 추가.

검증:
- batch_id 존재 확인 (FK)
- run_label 일치 확인 (다른 run 의 batch 에 의견 다는 것 차단)
- payload_snapshot 필수 — admin 큐 재생용

추후 (Step 6-admin):
- GET /api/admin/decision-feedback?status=open
- PATCH /api/admin/decision-feedback/{id} {status, dev_notes, linked_pr_url}
- PATCH /api/admin/decision-feedback/bulk
- GET /api/admin/decision-feedback/clusters (impact_score 정렬)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.decision_feedback import DecisionFeedback
from app.infrastructure.models.production_batch import ProductionBatch
from app.presentation.schemas.decision_feedback import (
    DecisionFeedbackCreate,
    DecisionFeedbackResponse,
)


router = APIRouter(prefix="/decision-feedback", tags=["decision_feedback"])


@router.post("", response_model=DecisionFeedbackResponse, status_code=201)
def create_decision_feedback(
    payload: DecisionFeedbackCreate,
    db: Session = Depends(get_db),
) -> DecisionFeedbackResponse:
    """운영자 의견 저장.

    1. batch_id FK + run_label 일치 검증
    2. payload_snapshot JSONB 보존 (admin 큐 재생용)
    3. status='open' 으로 INSERT
    """
    batch = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id == payload.batch_id)
        .one_or_none()
    )
    if batch is None:
        raise HTTPException(
            status_code=404, detail=f"batch {payload.batch_id} 를 찾을 수 없습니다"
        )
    if batch.run_label != payload.run_label:
        raise HTTPException(
            status_code=404,
            detail=f"run_label 불일치: payload={payload.run_label}, batch={batch.run_label}",
        )

    fb = DecisionFeedback(
        run_label=payload.run_label,
        batch_id=payload.batch_id,
        task_id=payload.task_id,
        section=payload.section,
        line_anchor=payload.line_anchor,
        constraint_id_hint=payload.constraint_id_hint,
        free_text=payload.free_text,
        operator_id=payload.operator_id,
        status="open",
        payload_snapshot=payload.payload_snapshot,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)

    return DecisionFeedbackResponse(
        id=fb.id,
        created_at=fb.created_at,
        run_label=fb.run_label,
        batch_id=fb.batch_id,
        task_id=fb.task_id,
        section=fb.section,
        line_anchor=fb.line_anchor,
        constraint_id_hint=fb.constraint_id_hint,
        free_text=fb.free_text,
        operator_id=fb.operator_id,
        status=fb.status,
    )
