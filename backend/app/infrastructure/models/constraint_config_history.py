"""ConstraintConfig params_json 변경 이력.

Why: 감사 관점 — 누가/언제/무엇을 바꿨는지 UI 에 노출.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConstraintConfigHistory(Base):
    __tablename__ = "constraint_config_history"

    history_id = Column(Integer, primary_key=True, autoincrement=True)
    constraint_id = Column(
        String(10),
        ForeignKey("constraint_config.constraint_id"),
        nullable=False,
        index=True,
    )
    changed_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    changed_by = Column(String(100), nullable=True)
    old_params_json = Column(JSONB, nullable=True)
    new_params_json = Column(JSONB, nullable=False)
