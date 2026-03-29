from sqlalchemy import Column, String, Integer, Boolean

from app.infrastructure.database import Base


class CustomerMaster(Base):
    __tablename__ = "customer_master"

    customer_code = Column(String(20), primary_key=True)  # C-001
    customer_name = Column(String(100), nullable=False)
    priority = Column(Integer, default=99)  # 1=최우선, 99=최후순위
    due_type = Column(String(20), default="출하기준")  # 도착기준/출하기준
    due_strictness = Column(String(50))  # 최우선-무조건, 특판우선, 여유있음
    require_sample = Column(Boolean, default=False)
    urgency_frequency = Column(String(20))  # 매우잦음, 보통, 낮음
