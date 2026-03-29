from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from app.infrastructure.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    run_label = Column(String(50), nullable=False, index=True)
    stage = Column(String(10), nullable=False)  # stage1, stage2
    batch_id = Column(Integer, ForeignKey("production_batch.batch_id"), nullable=True)
    task_id = Column(Integer, ForeignKey("schedule_task.task_id"), nullable=True)
    action_type = Column(
        String(30), nullable=False
    )  # batch_created, wip_matched, equipment_assigned, schedule_placed, constraint_checked
    constraints_applied = Column(
        JSONB
    )  # [{"id":"1-1","name":"...","result":"pass","detail":"..."}]
    decision_reason = Column(Text)
    alternatives_considered = Column(JSONB)  # nullable — only for violations/re-routes
    created_at = Column(DateTime, default=datetime.utcnow)
