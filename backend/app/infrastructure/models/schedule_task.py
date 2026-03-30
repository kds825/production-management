from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey
from datetime import datetime
from app.infrastructure.database import Base


class ScheduleTask(Base):
    __tablename__ = "schedule_task"

    task_id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("production_batch.batch_id"), nullable=False)
    equipment_code = Column(
        String(20), ForeignKey("equipment_master.equipment_code"), nullable=False
    )
    start_datetime = Column(DateTime, nullable=False)
    end_datetime = Column(DateTime, nullable=False)
    predecessor_task_id = Column(
        Integer, ForeignKey("schedule_task.task_id"), nullable=True
    )
    setup_time_min = Column(Numeric, default=0)
    status = Column(
        String(20), default="scheduled"
    )  # scheduled, in_progress, completed
    run_label = Column(String(50), index=True)
    batch_group = Column(String(50), index=True)  # 배치 그룹 식별자 (간트 블록 1개)
    created_at = Column(DateTime, default=datetime.utcnow)
