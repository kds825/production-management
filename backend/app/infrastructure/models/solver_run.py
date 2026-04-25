"""SolverRun ORM — one row per cp_sat_schedule() invocation (Task 2A.3 trace_writer).

Why: Makes solver runs queryable from the admin dashboard (input/output hash,
objective_value, status, constraint_config snapshot version). One-to-many to
SolverDecision captures per-constraint penalty/applied detail.

Schema mirrors migration f6a0935366eb byte-for-byte. Python-side defaults
intentionally omitted for timestamps / JSONB columns — Postgres server_default
is the single source of truth to avoid ORM/DB drift.

Indexes are declared on the migration side (ix_solver_run_run_label,
ix_solver_run_started_at DESC). Column(..., index=True) here would duplicate
the run_label index; we rely on the migration alone.
"""

from sqlalchemy import Boolean, Column, DateTime, Float, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.infrastructure.database import Base


class SolverRun(Base):
    __tablename__ = "solver_run"

    # UUID-shaped string (uuid.uuid4() assigned by trace_writer).
    run_id = Column(String(36), primary_key=True)
    # Human-readable label (e.g. "2026-W17-main"). Indexed on DB side.
    run_label = Column(String(255), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # e.g. "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "MODEL_INVALID" | "UNKNOWN".
    solver_status = Column(String(20), nullable=False)
    objective_value = Column(Float, nullable=True)
    # "sha256:<64 hex chars>" — 7 + 64 = 71 chars. Required for input; output
    # may be null when solver bails out before producing a schedule.
    input_hash = Column(String(71), nullable=False)
    output_hash = Column(String(71), nullable=True)
    # Snapshot of the ConstraintConfig version used for this run (UUID or
    # null for runs before the versioning feature landed).
    constraint_config_version = Column(String(36), nullable=True)
    # CP-SAT parameters actually passed (num_search_workers, max_time_in_seconds,
    # random_seed, ...). server_default '{}'::jsonb lets callers omit.
    solver_params = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    # Task 4B.1: cached Decision Card narrator output. Lazily populated by
    # GET /api/decisions/{batch_id}/latest the first time it renders this
    # run; subsequent renders read the cache. Nullable for backfill safety.
    llm_summary_text = Column(Text, nullable=True)
    # True when the cached summary came from the TemplateProvider fallback
    # because the kiwipiepy hallucination filter tripped on the live
    # provider's output. UI surfaces this so operators can flag low-trust
    # explanations without re-querying the provider.
    llm_was_template = Column(Boolean, nullable=True)

    # One run → many per-constraint decisions. cascade mirrors migration
    # ondelete=CASCADE — deleting a run purges its decisions.
    decisions = relationship(
        "SolverDecision",
        back_populates="run",
        cascade="all, delete-orphan",
    )
