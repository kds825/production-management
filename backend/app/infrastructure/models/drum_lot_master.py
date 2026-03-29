from sqlalchemy import Column, Integer, Numeric

from app.infrastructure.database import Base


class DrumLotMaster(Base):
    __tablename__ = "drum_lot_master"

    drum_id = Column(Integer, primary_key=True, autoincrement=True)
    cross_section = Column(Numeric, nullable=False)  # SQ
    wire_diameter = Column(Numeric)  # 소선경 mm
    wire_count = Column(Integer)  # 소선수
    lot_wire_drawing = Column(Numeric)  # 신선 틀단위 m
    lot_stranding = Column(Numeric)  # 연선 틀단위 m
    daily_production = Column(Numeric)  # 일생산량 m
    setup_time_min = Column(Numeric, default=180)  # 규격교체 시간 분
    drum_weight_ton = Column(Numeric)  # 드럼 중량 ton
