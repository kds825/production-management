"""Parity harness (Task 1.4) — bit-exact regression gate for cp_sat_schedule.

This is the centerpiece of Week 1. For the next 8 weeks of refactoring
every commit that touches the solver must leave these hashes unchanged
(or explicitly update them via a `parity-update:` commit per Task 1.7).

Flow per fixture
----------------
1. Load fixture JSON from `backend/tests/fixtures/parity/<scenario>.json`.
2. Reconstruct `SolverInput` via `_fixture_to_solver_input` — this
   inserts ProductionBatch rows into the SAVEPOINT-wrapped session so
   the solver's `ScheduleTask` FK flush succeeds.
3. Call `cp_sat_schedule(run_label, db, solver_input_override=si,
   num_search_workers=1, random_seed=0, run_id_override=run_label)`.
4. Collect the ScheduleTask rows written by the solver (still in
   SAVEPOINT, not yet committed).
5. Compute `_stable_hash(assignments, run_label, horizon_start)`.
6. Compare to `fixture["expected_output_hash"]`:
     - blank → FAIL with actionable "copy this hash into fixture" msg
       (Task 1.5 freeze protocol).
     - present → strict equality per D9-A. Mismatch → auditor diff.
7. SAVEPOINT rollback. Assert `ScheduleTask` row count unchanged.

Determinism
-----------
- `CPSAT_WORKERS=1` set by conftest at module-load time.
- `num_search_workers=1` + `random_seed=0` passed explicitly.
- Fixture `base_date` is authoritative — no KST-now fallback.

Why parametrize instead of one-test-per-fixture
-----------------------------------------------
11 near-identical tests would explode test collection output. pytest's
`indirect=False` parametrization over fixture file stems gives us one
test function, 11 test IDs (`test_parity[01_nominal]` etc.), and
preserves `-k 01` filtering (used by Makefile `parity-fixture` target).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.models.schedule_task import ScheduleTask
from app.services.cp_sat_optimizer import cp_sat_schedule
from tests._parity_helpers import (
    _collect_assignments,
    _fixture_to_solver_input,
    _format_auditor_diff,
    _stable_hash,
    discover_fixtures,
    load_fixture,
)

pytestmark = pytest.mark.parity


# ────────────────────────────────────────────────────────────────────────
# parity_db — SAVEPOINT wrapper + row-count leak guard
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="function")
def parity_db(db: Session):
    """SAVEPOINT wrapper for parity tests — every mutation rolls back.

    `cp_sat_schedule` calls `db.add(ScheduleTask(...))` + `db.flush()`
    (no explicit commit), and we also insert ProductionBatch rows via
    `_fixture_to_solver_input`. Wrapping the test body in
    `db.begin_nested()` gives us a SAVEPOINT; on yield-end we explicitly
    roll it back.

    The post-yield row-count assertion is the leak canary: if any code
    path accidentally issued a true `db.commit()` mid-test, the SAVEPOINT
    couldn't roll it back and the count would differ. If this assertion
    ever fires, the SAVEPOINT-only isolation is unsafe and Task 1.4 has
    to be redesigned (likely: dedicated transactional-test DB + full
    session re-creation per test).
    """
    before = db.query(ScheduleTask).count()
    nested = db.begin_nested()
    try:
        yield db
    finally:
        if nested.is_active:
            nested.rollback()
    after = db.query(ScheduleTask).count()
    assert before == after, (
        f"parity_db leaked {after - before} ScheduleTask rows — "
        f"SAVEPOINT rollback failed. Likely a db.commit() fired "
        f"inside the solver or helper path. This is a deal-breaker: "
        f"do NOT mark Task 1.4 done until the leak source is identified."
    )


# ────────────────────────────────────────────────────────────────────────
# Fixture discovery for parametrization
# ────────────────────────────────────────────────────────────────────────


def _fixture_paths_for_run(config: pytest.Config) -> list[Path]:
    """Resolve fixture set honoring `--parity-quick`."""
    quick = bool(config.getoption("--parity-quick"))
    return discover_fixtures(quick=quick)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize `test_parity` dynamically from discovered fixtures.

    Using `pytest_generate_tests` (vs. `@pytest.mark.parametrize` with
    module-level `discover_fixtures()` call) lets us read the
    `--parity-quick` flag at collection time — a decorator-argument
    would be evaluated at import before the option is registered.
    """
    if "fixture_path" not in metafunc.fixturenames:
        return
    paths = _fixture_paths_for_run(metafunc.config)
    metafunc.parametrize(
        "fixture_path",
        paths,
        ids=[p.stem for p in paths],
    )


# ────────────────────────────────────────────────────────────────────────
# The one test
# ────────────────────────────────────────────────────────────────────────


def test_parity(parity_db: Session, fixture_path: Path) -> None:
    """Run `cp_sat_schedule` on a fixture and compare the stable hash.

    See module docstring for the full flow. On blank expected hash
    (pre-Task-1.5 state) we FAIL with the actual hash in the message —
    that is exactly the value Task 1.5's freeze step will paste back
    into `fixture["expected_output_hash"]`.
    """
    fixture = load_fixture(fixture_path)
    scenario_id = fixture["scenario_id"]
    run_label = fixture["run_label"]
    expected_hash = fixture.get("expected_output_hash", "")

    # 1-2. Rehydrate + insert batches under the SAVEPOINT.
    solver_input, db_id_to_fixture_id = _fixture_to_solver_input(fixture, parity_db)
    horizon_start = solver_input.base_date

    # 3. Run the solver with deterministic knobs.
    result = cp_sat_schedule(
        run_label,
        parity_db,
        solver_input_override=solver_input,
        num_search_workers=1,
        random_seed=0,
        run_id_override=run_label,
    )
    assert result["solver_status"] != "UNKNOWN", (
        f"{scenario_id}: solver returned UNKNOWN status — "
        f"result={result!r}. Likely a solver-internal early-return "
        f"path that indicates Task 1.1 override plumbing is broken."
    )

    # 4. Collect assignments from ScheduleTask (still in SAVEPOINT).
    assignments = _collect_assignments(parity_db, run_label, db_id_to_fixture_id)

    # 5. Compute stable hash.
    actual_hash = _stable_hash(assignments, run_label, horizon_start)

    # 6. Compare.
    if not expected_hash:
        # Pre-freeze state (Task 1.4 initial commit). FAIL with the
        # actual hash so Task 1.5 can copy it back into the fixture.
        pytest.fail(
            f"\n\n[Task 1.5 freeze needed] {scenario_id}: "
            f"fixture has no expected_output_hash.\n"
            f"  Actual hash: {actual_hash}\n"
            f"  Solver status: {result['solver_status']}\n"
            f"  Objective value: {result.get('objective_value', 'N/A')}\n"
            f"  n_assignments: {len(assignments)}\n"
            f"\n"
            f"To freeze: paste the actual hash into "
            f"backend/tests/fixtures/parity/{scenario_id}.json "
            f"(Task 1.5 automates this)."
        )

    # D9-A strict: no tolerance.
    if actual_hash != expected_hash:
        # Task 1.5 freeze captured `expected_assignments` (top-5 per fixture)
        # for richer EXPECTED-vs-ACTUAL diffs. Pass it through if present;
        # `_format_auditor_diff` gracefully falls back to ACTUAL-only when
        # the fixture is pre-freeze (key absent → .get returns None).
        diff_msg = _format_auditor_diff(
            scenario_id=scenario_id,
            expected_hash=expected_hash,
            actual_hash=actual_hash,
            actual_assignments=assignments,
            horizon_start=horizon_start,
            expected_assignments=fixture.get("expected_assignments"),
        )
        pytest.fail(diff_msg)
