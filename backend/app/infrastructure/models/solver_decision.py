"""SolverDecision ORM — per-constraint, per-run trace row.

Why: Each constraint (e.g. "4-1 color-change minimization") has a penalty_value
(soft) or hard_literal_value (hard) and an `applied` flag for the run. Stored
separately from SolverRun so the admin dashboard can join/aggregate without
bloating the run row. manual_override_change_set_id links back to a
ScheduleChangeSet when the decision reflects a user override (Task 14).

Schema mirrors migration f6a0935366eb byte-for-byte. BigInteger + autoincrement
emits BIGSERIAL on Postgres — matches the migration's implicit sequence; we do
NOT add an explicit Sequence(...) to avoid duplicate DDL.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.infrastructure.database import Base


class SolverDecision(Base):
    __tablename__ = "solver_decision"

    decision_id = Column(BigInteger, primary_key=True, autoincrement=True)
    # FK CASCADE: deleting the parent SolverRun purges its decisions.
    run_id = Column(
        String(36),
        ForeignKey("solver_run.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    # Matches ConstraintConfig.constraint_id shape (e.g. "4-1", "3-2a").
    constraint_id = Column(String(10), nullable=False)
    # Soft constraint penalty (null for pure-hard constraints).
    penalty_value = Column(Float, nullable=True)
    # Hard constraint literal truthiness (null for pure-soft).
    hard_literal_value = Column(Boolean, nullable=True)
    # Whether this constraint was enforced in the final schedule.
    applied = Column(Boolean, nullable=False)
    # Free-form per-constraint details (violation counts, break cases, etc.).
    details_json = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    # SET NULL: if a change_set is deleted we keep the solver trace but drop
    # the dangling reference. Null = not driven by a manual override.
    manual_override_change_set_id = Column(
        String(36),
        ForeignKey("schedule_change_sets.change_set_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    run = relationship("SolverRun", back_populates="decisions")
