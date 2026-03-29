from sqlalchemy import Column, String, Integer, Numeric, ForeignKey

from app.infrastructure.database import Base


class SpeedMaster(Base):
    __tablename__ = "speed_master"

    speed_id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_code = Column(
        String(20), ForeignKey("equipment_master.equipment_code"), nullable=False
    )
    product_type = Column(String(50))  # TFR-CV 1C, TFR-CV 2C, HFCO, TFR-GV, 고압CV etc
    cross_section = Column(Numeric)  # SQ mm²
    line_speed_mpm = Column(Numeric)  # m/min
    line_speed_hr = Column(Numeric)  # m/hr (= mpm × 60, auto-calculable)
    setup_start_min = Column(Numeric, default=0)  # 시작 셋업시간
    setup_spec_min = Column(Numeric, default=0)  # 규격교체 시간
    setup_color_min = Column(Numeric, default=0)  # 색상교체 시간
    setup_compound_min = Column(Numeric, default=0)  # 컴파운드 교체 시간
