from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String

from app.infrastructure.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SpeedMaster(Base):
    __tablename__ = "speed_master"

    speed_id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_code = Column(
        String(20), ForeignKey("equipment_master.equipment_code"), nullable=False
    )
    product_type = Column(String(50))
    cross_section = Column(Numeric)
    line_speed_mpm = Column(Numeric)
    line_speed_hr = Column(Numeric)
    setup_start_min = Column(Numeric, default=0)
    setup_spec_min = Column(Numeric, default=0)
    setup_color_min = Column(Numeric, default=0)
    setup_compound_min = Column(Numeric, default=0)
    updated_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )
