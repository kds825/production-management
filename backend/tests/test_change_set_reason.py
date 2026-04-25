"""Week 5 Task 5B.2 — change-set reason attribution tests.

Why these five cases:
  1. PATCH happy path — confirms override_reason is persisted.
  2. PATCH propagates to solver_decision rows (sets manual_override_change_set_id)
     so the Decision Card derives ``is_manually_adjusted=True``.
  3. PATCH 422 — value outside the four-chip allow-list.
  4. PATCH 404 — unknown change_set_id.
  5. GET /missing-reasons — count returns the expected number for "today".

All tests use the savepoint TestClient pattern from
``tests/test_decisions_route.py`` so route ``db.commit()`` calls roll back at
teardown without polluting Supabase.
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
# Fixtures: savepoint-bound TestClient (mirrors test_decisions_route)
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """``get_db`` override that joins a savepoint for clean rollback.

    Identical pattern to ``tests/test_decisions_route.py::client`` — the
    PATCH route calls ``db.commit()`` which becomes a SAVEPOINT release
    when nested inside the outer transaction.
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
    """Per-test unique run_label so concurrent runs don't collide."""
    return f"test-5b2-{uuid.uuid4().hex[:8]}"


def _seed_change_set(
    db: Session,
    *,
    snapshot_before: dict | None = None,
    snapshot_after: dict | None = None,
    override_reason: str | None = None,
    created_at: datetime | None = None,
) -> ScheduleChangeSet:
    cs = ScheduleChangeSet(
        change_set_id=str(uuid.uuid4()),
        snapshot_before=snapshot_before or {},
        snapshot_after=snapshot_after or {},
        override_reason=override_reason,
    )
    if created_at is not None:
        cs.created_at = created_at
    db.add(cs)
    db.flush()
    return cs


def _seed_batch_run_task(
    db: Session, *, run_label: str | None = None
) -> tuple[ProductionBatch, SolverRun, ScheduleTask]:
    """Create a minimal batch + run + task triple linked by run_label."""
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
        finished_at=started + timedelta(minutes=1),
        solver_status="OPTIMAL",
        objective_value=1.0,
        input_hash="sha256:" + "0" * 64,
        output_hash="sha256:" + "1" * 64,
        constraint_config_version=None,
        solver_params={},
    )
    db.add(run)
    db.flush()

    task = ScheduleTask(
        batch_id=batch.batch_id,
        equipment_code="EX-B100",
        start_datetime=datetime(2026, 4, 25, 8, 0, 0),
        end_datetime=datetime(2026, 4, 25, 16, 0, 0),
        run_label=label,
    )
    db.add(task)
    db.flush()
    return batch, run, task


def _seed_decision(db: Session, run_id: str) -> SolverDecision:
    decision = SolverDecision(
        run_id=run_id,
        constraint_id="4-1",
        penalty_value=100.0,
        hard_literal_value=None,
        applied=True,
        details_json={},
    )
    db.add(decision)
    db.flush()
    return decision


# ────────────────────────────────────────────────────────────────────────
# Test 1: PATCH happy path — override_reason is persisted
# ────────────────────────────────────────────────────────────────────────


def test_patch_reason_updates_override_reason(db: Session, client: TestClient) -> None:
    cs = _seed_change_set(db)
    resp = client.patch(
        f"/api/change-sets/{cs.change_set_id}/reason",
        json={"reason": "납기 변경"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated_change_set"] == cs.change_set_id
    # No tasks → no decisions affected.
    assert body["decisions_updated"] == 0

    db.expire_all()
    refreshed = db.get(ScheduleChangeSet, cs.change_set_id)
    assert refreshed is not None
    assert refreshed.override_reason == "납기 변경"


# ────────────────────────────────────────────────────────────────────────
# Test 2: PATCH propagates to solver_decision (manual_override link set)
# ────────────────────────────────────────────────────────────────────────


def test_patch_reason_flips_manual_override_on_solver_decisions(
    db: Session, client: TestClient
) -> None:
    """A change_set whose snapshot references a task that shares a run_label
    with a SolverRun should propagate the override link to that run's
    decisions when the operator attributes a reason."""
    _, run, task = _seed_batch_run_task(db)
    decision = _seed_decision(db, run_id=run.run_id)

    cs = _seed_change_set(
        db,
        snapshot_before={
            str(task.task_id): {
                "start": task.start_datetime.isoformat(),
                "end": task.end_datetime.isoformat(),
                "equipment_code": task.equipment_code,
            }
        },
        snapshot_after={
            str(task.task_id): {
                "start": (task.start_datetime + timedelta(hours=1)).isoformat(),
                "end": (task.end_datetime + timedelta(hours=1)).isoformat(),
                "equipment_code": task.equipment_code,
            }
        },
    )

    resp = client.patch(
        f"/api/change-sets/{cs.change_set_id}/reason",
        json={"reason": "현장 긴급"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decisions_updated"] == 1

    db.expire_all()
    refreshed = (
        db.query(SolverDecision)
        .filter(SolverDecision.decision_id == decision.decision_id)
        .one()
    )
    assert refreshed.manual_override_change_set_id == cs.change_set_id

    # Clearing the reason must drop the back-link so the Decision Card
    # stops surfacing "manually adjusted".
    resp_clear = client.patch(
        f"/api/change-sets/{cs.change_set_id}/reason",
        json={"reason": None},
    )
    assert resp_clear.status_code == 200, resp_clear.text
    db.expire_all()
    refreshed = (
        db.query(SolverDecision)
        .filter(SolverDecision.decision_id == decision.decision_id)
        .one()
    )
    assert refreshed.manual_override_change_set_id is None


# ────────────────────────────────────────────────────────────────────────
# Test 3: PATCH 422 on disallowed reason
# ────────────────────────────────────────────────────────────────────────


def test_patch_reason_rejects_unknown_chip(db: Session, client: TestClient) -> None:
    cs = _seed_change_set(db)
    resp = client.patch(
        f"/api/change-sets/{cs.change_set_id}/reason",
        json={"reason": "기타"},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_code"] == "invalid_reason"
    assert detail["offending_value"] == "기타"
    # 기타 must NEVER be in the allow-list (spec §8d explicitly forbids it).
    assert "기타" not in detail["allowed"]


# ────────────────────────────────────────────────────────────────────────
# Test 4: PATCH 404 on unknown change_set_id
# ────────────────────────────────────────────────────────────────────────


def test_patch_reason_404_unknown_change_set(client: TestClient) -> None:
    resp = client.patch(
        "/api/change-sets/does-not-exist/reason",
        json={"reason": "설비 고장"},
    )
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────────
# Test 5: GET /missing-reasons returns expected count
# ────────────────────────────────────────────────────────────────────────


def test_missing_reasons_counts_unattributed_today(
    db: Session, client: TestClient
) -> None:
    """Only change_sets created on/after 'today' midnight (UTC) AND with
    NULL override_reason should count. We seed three: one with reason
    (excluded), one fresh + null (included), one stale + null (excluded
    when ``since`` defaults to today)."""
    # Baseline: query the count BEFORE any seeding so concurrent runs
    # don't cross-contaminate. We assert the delta, not absolute value.
    baseline = client.get("/api/change-sets/missing-reasons").json()["count"]

    # Fresh + null → counted.
    _seed_change_set(db, override_reason=None)
    # Fresh + with reason → not counted.
    _seed_change_set(db, override_reason="자재 부족")
    # Stale + null → not counted at default ``since``.
    _seed_change_set(
        db,
        override_reason=None,
        created_at=datetime.utcnow() - timedelta(days=30),
    )

    after = client.get("/api/change-sets/missing-reasons").json()
    # +1: only the fresh-and-null row added one.
    assert after["count"] == baseline + 1
    assert "since" in after
