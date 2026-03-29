from sqlalchemy import Column, String, Integer, Numeric, DateTime
from datetime import datetime
from app.infrastructure.database import Base


class WipInventory(Base):
    __tablename__ = "wip_inventory"

    wip_id = Column(Integer, primary_key=True, autoincrement=True)
    process_stage = Column(String(30))  # 연선재고, 절연재고, 연합재고, 완제품
    voltage_class = Column(String(20))  # 저압, 고압
    material = Column(String(10))  # CU, AL
    product_name = Column(String(100))
    spec = Column(String(100))  # 200SQ, 50SQ, 1250kcmil
    cross_section = Column(Numeric)
    length_m = Column(Numeric)
    count = Column(Integer, default=1)
    total_length_m = Column(Numeric)
    core_colors = Column(String(100))
    wire_diameter = Column(Numeric)
    wire_count = Column(Integer)
    status = Column(String(20), default="사용가능")  # 사용가능, 사용완료, 스크랩
    run_label = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
