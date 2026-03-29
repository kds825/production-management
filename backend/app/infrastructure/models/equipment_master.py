from sqlalchemy import Column, String, Numeric

from app.infrastructure.database import Base


class EquipmentMaster(Base):
    __tablename__ = "equipment_master"

    equipment_code = Column(String(20), primary_key=True)  # DS-C11D
    equipment_name = Column(String(50), nullable=False)
    process_name = Column(
        String(30), nullable=False
    )  # 신선, 연선, 고압절연, 저압절연, 연합, T/P, 저압시스, 고압시스
    material_limit = Column(String(10))  # CU, AL, ALL, null
    range_min = Column(Numeric)
    range_max = Column(Numeric)
    range_unit = Column(String(10), default="SQ")  # SQ, Ø, mm
    stranding_method = Column(String(50))  # 7연선, 19연선, 37연선, 61연선 etc
    color_group = Column(String(50))  # 흑/청, 전색상, null
    base_working_hours = Column(Numeric, default=20)
    shift_type = Column(String(20), default="2교대")
    calendar_rule_code = Column(
        String(20), nullable=True
    )  # CAL-STD, CAL-FRI 등 (논리적 참조)
