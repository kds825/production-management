"""감사 추적 API — audit trail 조회"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.services.audit_logger import get_audit_trail
from app.services.llm_explainer import explain_decision_sync  # noqa: F401

router = APIRouter(prefix="/audit", tags=["감사추적"])


@router.get("/{run_label}")
def get_audit(
    run_label: str,
    batch_id: int | None = Query(None),
    task_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """감사 추적 로그 조회"""
    logs = get_audit_trail(run_label, db, batch_id=batch_id, task_id=task_id)
    return {"run_label": run_label, "logs": logs, "total": len(logs)}


@router.get("/explain/{batch_id}", summary="배치 결정 자연어 설명")
def explain_batch(
    batch_id: int,
    task_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """특정 배치의 스케줄링 결정을 자연어로 설명.
    ANTHROPIC_API_KEY 환경변수가 설정되면 LLM 사용, 아니면 템플릿 기반."""
    return explain_decision_sync(batch_id, db, task_id=task_id)
