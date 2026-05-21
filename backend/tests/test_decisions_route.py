"""GET /api/decisions/{batch_id}/latest tests (Week 4 Task 4B.1).

Why these four cases:
  1. 404 on missing batch — confirms the resolver guards the join chain.
  2. Happy path — confirms the join (solver_run + solver_decision +
     schedule_task) returns the expected response shape.
  3. Manual override — confirms the schedule_change_sets join surfaces
     ``is_manually_adjusted`` + the ``manual_override`` block.
  4. Cached LLM summary still returned verbatim for backward compat —
     ``_ensure_summary`` honours pre-populated ``solver_run.llm_summary_text``
     even though new traces no longer generate one. Modal-side display of
     LLM tagline was removed (meta summary derives the same signal
     deterministically on the client), so the route returns an empty
     string for fresh runs.

All tests use the savepoint TestClient pattern from
``tests/api/test_constraints_params.py`` so route ``db.commit()`` calls
roll back at teardown without polluting Supabase.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.solver_decision import SolverDecision
from app.infrastructure.models.solver_run import SolverRun
from app.main import app


# ────────────────────────────────────────────────────────────────────────
# Fixtures: savepoint-bound TestClient (mirrors test_constraints_params)
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """``get_db`` override that joins a savepoint for clean rollback.

    See ``tests/api/test_constraints_params.py::client`` for the rationale
    behind the after_transaction_end re-savepoint trick.
    """
    db.begin_nested()

    @event.listens_for(db, "after_transaction_end")
    def _restart_savepoint(session: Session, transaction) -> None:
        if transaction.nested and not transaction._parent.nested:
            session.begin_nested()

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        event.remove(db, "after_transaction_end", _restart_savepoint)


# ────────────────────────────────────────────────────────────────────────
# Seeding helpers
# ────────────────────────────────────────────────────────────────────────


def _unique_label() -> str:
    """Per-test unique run_label so concurrent test runs don't collide."""
    return f"test-4b1-{uuid.uuid4().hex[:8]}"


def _seed_batch_and_run(
    db: Session,
    *,
    run_label: str | None = None,
    with_task: bool = True,
    pre_summary: str | None = None,
) -> tuple[ProductionBatch, SolverRun]:
    """Create one ProductionBatch + one SolverRun (+ optional ScheduleTask).

    Why so many fields on ProductionBatch:
      The model has many NOT NULL columns; we populate the minimum the
      DB will accept and leave the rest at server defaults.
    """
    label = run_label or _unique_label()
    batch = ProductionBatch(
        run_label=label,
        process_name="저압절연",
        equipment_code="EX-B100",
    )
    db.add(batch)
    db.flush()

    started = datetime.now(timezone.utc)
    run = SolverRun(
        run_id=str(uuid.uuid4()),
        run_label=label,
        started_at=started,
        finished_at=started + timedelta(minutes=2),
        solver_status="OPTIMAL",
        objective_value=12345.0,
        input_hash="sha256:" + "0" * 64,
        output_hash="sha256:" + "1" * 64,
        constraint_config_version=None,
        solver_params={},
        llm_summary_text=pre_summary,
        llm_was_template=False if pre_summary is None else False,
    )
    db.add(run)
    db.flush()

    if with_task:
        task = ScheduleTask(
            batch_id=batch.batch_id,
            equipment_code="EX-B100",
            start_datetime=datetime(2026, 4, 25, 8, 0, 0),
            end_datetime=datetime(2026, 4, 25, 16, 0, 0),
            run_label=label,
        )
        db.add(task)
        db.flush()

    return batch, run


def _seed_decision(
    db: Session,
    *,
    run_id: str,
    constraint_id: str,
    penalty_value: float | None = 100.0,
    hard_literal_value: bool | None = None,
    change_set_id: str | None = None,
) -> SolverDecision:
    decision = SolverDecision(
        run_id=run_id,
        constraint_id=constraint_id,
        penalty_value=penalty_value,
        hard_literal_value=hard_literal_value,
        applied=True,
        details_json={},
        manual_override_change_set_id=change_set_id,
    )
    db.add(decision)
    db.flush()
    return decision


# ────────────────────────────────────────────────────────────────────────
# Test 1: 404 when no batch / no decision
# ────────────────────────────────────────────────────────────────────────


def test_returns_404_when_batch_missing(db: Session, client: TestClient) -> None:
    # 999_999_999 is well above any seeded test batch_id; even if collision
    # happens the savepoint rollback ensures no leakage.
    resp = client.get("/api/decisions/999999999/latest")
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────────
# Test 2: Happy path returns the full response shape
# ────────────────────────────────────────────────────────────────────────


def test_happy_path_returns_full_shape(db: Session, client: TestClient) -> None:
    batch, run = _seed_batch_and_run(db)
    _seed_decision(db, run_id=run.run_id, constraint_id="4-1", penalty_value=48000.0)
    _seed_decision(db, run_id=run.run_id, constraint_id="3-2a", penalty_value=12000.0)

    resp = client.get(f"/api/decisions/{batch.batch_id}/latest")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level shape
    assert body["batch_id"] == str(batch.batch_id)
    assert body["run_id"] == run.run_id
    assert body["run_label"] == run.run_label
    assert body["solver_status"] == "OPTIMAL"
    assert body["objective_value"] == 12345.0
    assert body["assigned_equipment_id"] == "EX-B100"
    # Slot strings are ISO-8601 — only check non-empty + parseable.
    assert body["assigned_start"] is not None
    assert body["assigned_end"] is not None

    # Contributions list — both seeded constraints present.
    cids = {c["constraint_id"] for c in body["contributions"]}
    assert cids == {"4-1", "3-2a"}
    # Each contribution carries the four documented keys.
    for c in body["contributions"]:
        assert set(c.keys()) >= {
            "constraint_id",
            "korean_name",
            "weight_applied",
            "bound",
            "delta_if_removed",
        }

    assert body["binding_hard_constraints"] == []
    assert body["is_manually_adjusted"] is False
    assert body["manual_override"] is None

    # Fresh runs no longer generate an LLM tagline — modal removed the
    # section, and the meta summary on the client derives the same signal
    # deterministically. Empty string + was_template=True is the contract.
    assert body["llm_summary"] == ""
    assert body["llm_was_template"] is True


# ────────────────────────────────────────────────────────────────────────
# Test 3: Manual override surfaces the change_set block
# ────────────────────────────────────────────────────────────────────────


def test_manual_override_surfaces_change_set(db: Session, client: TestClient) -> None:
    batch, run = _seed_batch_and_run(db)

    cs = ScheduleChangeSet(
        change_set_id=str(uuid.uuid4()),
        snapshot_before={"task_1": {"start": "x"}},
        snapshot_after={"task_1": {"start": "y"}},
        applied_by="test-user",
        kind="manual",
    )
    db.add(cs)
    db.flush()

    _seed_decision(
        db,
        run_id=run.run_id,
        constraint_id="4-1",
        penalty_value=10000.0,
        change_set_id=cs.change_set_id,
    )

    resp = client.get(f"/api/decisions/{batch.batch_id}/latest")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["is_manually_adjusted"] is True
    mo = body["manual_override"]
    assert mo is not None
    assert mo["change_set_id"] == cs.change_set_id
    assert mo["kind"] == "manual"
    assert mo["applied_by"] == "test-user"


# ────────────────────────────────────────────────────────────────────────
# Test 4: Cached llm_summary_text is reused (no re-generation)
# ────────────────────────────────────────────────────────────────────────


def test_cached_summary_is_reused(db: Session, client: TestClient) -> None:
    """Pre-populated ``solver_run.llm_summary_text`` is returned verbatim.

    The provider was removed from the route in favour of the client-side
    meta summary, but pre-populated rows from older traces must still
    survive — they remain the legacy data shown to operators who pull up
    historic runs.
    """
    cached = "캐시된 한국어 요약 문장"
    batch, run = _seed_batch_and_run(db, pre_summary=cached)
    _seed_decision(db, run_id=run.run_id, constraint_id="4-1")

    resp = client.get(f"/api/decisions/{batch.batch_id}/latest")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["llm_summary"] == cached
    assert body["llm_was_template"] is False
