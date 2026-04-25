"""감사 추적 기록 — 모든 스케줄링 결정의 근거를 기록"""

from sqlalchemy.orm import Session

from app.infrastructure.models.audit_log import AuditLog


def log_decision(
    db: Session,
    run_label: str,
    stage: str,
    action_type: str,
    batch_id: int | None = None,
    task_id: int | None = None,
    constraints_applied: list[dict] | None = None,
    reason: str = "",
    alternatives: list[dict] | None = None,
):
    """감사 로그 1건 기록"""
    entry = AuditLog(
        run_label=run_label,
        stage=stage,
        batch_id=batch_id,
        task_id=task_id,
        action_type=action_type,
        constraints_applied=constraints_applied or [],
        decision_reason=reason,
        alternatives_considered=alternatives,
    )
    db.add(entry)


def get_audit_trail(
    run_label: str,
    db: Session,
    batch_id: int | None = None,
    task_id: int | None = None,
) -> list[dict]:
    """감사 로그 조회"""
    query = db.query(AuditLog).filter(AuditLog.run_label == run_label)
    if batch_id:
        query = query.filter(AuditLog.batch_id == batch_id)
    if task_id:
        query = query.filter(AuditLog.task_id == task_id)

    logs = query.order_by(AuditLog.created_at.asc()).all()

    return [
        {
            "log_id": log.log_id,
            "stage": log.stage,
            "batch_id": log.batch_id,
            "task_id": log.task_id,
            "action_type": log.action_type,
            "constraints_applied": log.constraints_applied,
            "decision_reason": log.decision_reason,
            "alternatives_considered": log.alternatives_considered,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
