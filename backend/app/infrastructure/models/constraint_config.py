from sqlalchemy import Column, String, Integer, Boolean, Text
from sqlalchemy.dialects.postgresql import JSONB
from app.infrastructure.database import Base


class ConstraintConfig(Base):
    __tablename__ = "constraint_config"

    constraint_id = Column(String(10), primary_key=True)  # 1-1, 1-2, ... 10-5
    constraint_name = Column(String(100), nullable=False)
    category = Column(
        String(50), nullable=False
    )  # 납기/우선순위, SM수량/재고, 색상관리...
    is_enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=50)  # 1=최고
    impact_level = Column(String(10))  # ★★★, ★★, ★
    params_json = Column(JSONB, default={})  # flexible parameters
    applicable_processes = Column(JSONB, default=[])  # ["연선", "저압절연", ...]
    implementation_type = Column(String(20))  # table_param, code_logic, hybrid
    notes = Column(Text)
