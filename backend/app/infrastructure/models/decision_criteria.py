from sqlalchemy import Column, String, Integer, Text

from app.infrastructure.database import Base


class DecisionCriteria(Base):
    __tablename__ = "decision_criteria"

    criteria_id = Column(Integer, primary_key=True, autoincrement=True)
    criteria_name = Column(
        String(100), nullable=False
    )  # Loss 허용 한도, 최소 잔여 조장 등
    criteria_value = Column(String(50), nullable=False)  # 8, 50, Y, 흑색 etc
    criteria_unit = Column(String(20))  # %, m, -, mm²
    description = Column(Text)
