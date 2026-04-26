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

from app.application.decisions.feedback_clustering import list_clusters
from app.infrastructure.database import get_db
from app.infrastructure.models.decision_feedback import DecisionFeedback
from app.infrastructure.models.production_batch import ProductionBatch
from app.presentation.schemas.decision_feedback import (
    DecisionFeedbackAdminPatch,
    DecisionFeedbackBulkPatch,
    DecisionFeedbackCreate,
    DecisionFeedbackResponse,
)


router = APIRouter(prefix="/decision-feedback", tags=["decision_feedback"])


@router.get("/me", response_model=list[DecisionFeedbackResponse])
def list_my_feedback(
    operator_id: str,
    db: Session = Depends(get_db),
) -> list[DecisionFeedbackResponse]:
    """운영자 자기 의견 history (CEO §1 my-feedback view).

    `operator_id` 쿼리 — PoC 단계라 신뢰. JWT 도입 후 헤더 / claim 로 교체.
    """
    rows = (
        db.query(DecisionFeedback)
        .filter(DecisionFeedback.operator_id == operator_id)
        .order_by(DecisionFeedback.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        DecisionFeedbackResponse(
            id=r.id,
            created_at=r.created_at,
            run_label=r.run_label,
            batch_id=r.batch_id,
            task_id=r.task_id,
            section=r.section,
            line_anchor=r.line_anchor,
            constraint_id_hint=r.constraint_id_hint,
            free_text=r.free_text,
            operator_id=r.operator_id,
            status=r.status,
        )
        for r in rows
    ]


@router.get("/unread-count")
def unread_resolution_count(
    operator_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """topbar bell icon — 운영자가 아직 못 본 fixed/wontfix 처리 건수.

    PoC: status in ('fixed', 'wontfix') 모두 unread 로 카운트. UI review §12 — 운영자가
    bell 클릭 후 my-feedback view 진입 시 reset 은 후속 spec.
    """
    from sqlalchemy import func

    cnt = (
        db.query(func.count(DecisionFeedback.id))
        .filter(
            DecisionFeedback.operator_id == operator_id,
            DecisionFeedback.status.in_(("fixed", "wontfix")),
        )
        .scalar()
        or 0
    )
    return {"unread": int(cnt)}


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


# ── admin 큐 (Step 6-admin) ────────────────────────────────────────────


admin_router = APIRouter(prefix="/admin/decision-feedback", tags=["decision_feedback"])


def _to_response(fb: DecisionFeedback) -> DecisionFeedbackResponse:
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


@admin_router.get("", response_model=list[DecisionFeedbackResponse])
def list_feedback(
    status: str | None = "open",
    db: Session = Depends(get_db),
) -> list[DecisionFeedbackResponse]:
    """admin 큐 — status 필터 + created_at DESC."""
    q = db.query(DecisionFeedback)
    if status:
        q = q.filter(DecisionFeedback.status == status)
    rows = q.order_by(DecisionFeedback.created_at.desc()).limit(200).all()
    return [_to_response(r) for r in rows]


@admin_router.get("/clusters")
def list_feedback_clusters(
    status: str | None = "open",
    db: Session = Depends(get_db),
) -> list[dict]:
    """(process_name, line_anchor) cluster + impact_score 정렬 (CEO §8)."""
    return list_clusters(db, status_filter=status, limit=50)


@admin_router.patch("/bulk")
def bulk_update_feedback(
    payload: DecisionFeedbackBulkPatch,
    db: Session = Depends(get_db),
) -> dict:
    """bulk action — IDs 일괄 status 변경. wontfix 시 dev_notes 필수."""
    if payload.status == "wontfix" and not (
        payload.dev_notes and payload.dev_notes.strip()
    ):
        raise HTTPException(
            status_code=422,
            detail="wontfix 처리는 운영자에게 전달할 사유 (dev_notes) 가 필요합니다",
        )
    rows = db.query(DecisionFeedback).filter(DecisionFeedback.id.in_(payload.ids)).all()
    if not rows:
        raise HTTPException(status_code=404, detail="해당 의견을 찾을 수 없습니다")
    updated = 0
    for r in rows:
        r.status = payload.status
        if payload.dev_notes is not None:
            r.dev_notes = payload.dev_notes
        updated += 1
    db.commit()
    return {"updated": updated, "status": payload.status}


@admin_router.patch("/{fb_id}", response_model=DecisionFeedbackResponse)
def update_feedback(
    fb_id: int,
    payload: DecisionFeedbackAdminPatch,
    db: Session = Depends(get_db),
) -> DecisionFeedbackResponse:
    """단건 status / dev_notes / linked_pr_url 변경."""
    fb = db.query(DecisionFeedback).filter(DecisionFeedback.id == fb_id).one_or_none()
    if fb is None:
        raise HTTPException(status_code=404, detail=f"feedback {fb_id} 미존재")
    if payload.status == "wontfix" and not (
        (payload.dev_notes or fb.dev_notes or "").strip()
    ):
        raise HTTPException(
            status_code=422,
            detail="wontfix 처리는 운영자에게 전달할 사유 (dev_notes) 가 필요합니다",
        )
    if payload.status is not None:
        fb.status = payload.status
    if payload.dev_notes is not None:
        fb.dev_notes = payload.dev_notes
    if payload.linked_pr_url is not None:
        fb.linked_pr_url = payload.linked_pr_url
    db.commit()
    db.refresh(fb)
    return _to_response(fb)
