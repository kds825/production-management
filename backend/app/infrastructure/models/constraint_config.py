from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from app.infrastructure.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConstraintConfig(Base):
    __tablename__ = "constraint_config"

    constraint_id = Column(String(10), primary_key=True)
    constraint_name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    is_enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=50)
    impact_level = Column(String(10))
    params_json = Column(JSONB, default={})
    applicable_processes = Column(JSONB, default=[])
    implementation_type = Column(String(20))
    notes = Column(Text)
    updated_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )
