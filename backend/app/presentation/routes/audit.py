"""감사 추적 API — audit trail 조회"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.services.audit_logger import get_audit_trail

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
