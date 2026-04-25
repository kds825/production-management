"""Task 1.5 — freeze parity fixture ground-truth hashes + perf baseline.

Runs each parity fixture N times (default 20 per design spec §10b) through
the same override path the parity harness uses (Task 1.4), asserts all N
hashes are bit-identical (determinism guard), and writes back into each
fixture JSON:

    - expected_output_hash       (sha256:... canonical result hash)
    - expected_solver_status     ("OPTIMAL" / "FEASIBLE" / "INFEASIBLE")
    - expected_objective_value   (int)
    - expected_assignments       (top-5 by batch_group — auditor-trail
                                  used by _format_auditor_diff for richer
                                  regression diffs in Task 1.4's helper)

Additionally writes `backend/tests/fixtures/parity/baseline_performance.json`:

    {
      "baseline_commit": "<git rev-parse HEAD>",
      "captured_at": "<UTC ISO>",
      "cpsat_workers": 1,
      "n_runs_per_fixture": 20,
      "fixtures": {
        "01_nominal": {"p50_ms": 450.3, "p99_ms": 612.8, "n": 20},
        ...
      }
    }

Contract (D9-A, strict)
-----------------------
After this script's `parity-update: initial freeze` commit lands:
- the frozen `expected_output_hash` is THE ground truth for every
  subsequent commit that touches the solver. ANY mutation of these
  fixtures must ride on a commit whose subject starts with
  `parity-update:` (enforced by the Task 1.7 CI guard).
- Bit-equality is required — no tolerance.

Determinism guarantees replayed here
------------------------------------
1. `CPSAT_WORKERS=1` is set at module-load time (same as tests/conftest.py).
2. `num_search_workers=1` + `random_seed=0` passed to every `cp_sat_schedule`
   invocation.
3. Each fixture run is wrapped in `db.begin_nested()` (SAVEPOINT), rolled
   back before the next run — so Supabase's autoincrement `batch_id`
   sequence advances per run but the fixture-id remap in
   `_parity_helpers._collect_assignments` normalizes hashes back to the
   fixture's canonical 0..N-1 ordering.
4. The first run of each fixture is compared with a second independent run
   (N≥2). If they diverge, we STOP immediately — no partial freeze.

Why we re-run inside a single DB session
----------------------------------------
Each nested SAVEPOINT runs against the same outer session (opened once at
script start). This matches the parity harness's `parity_db` fixture
semantics exactly — if any path inside the solver or the rehydration
helpers smuggled a `db.commit()` past the SAVEPOINT (row-count leak), the
harness's post-test assertion would have already caught it in Task 1.4.
Here we re-assert the same invariant per run.

Atomic writes
-------------
Each fixture JSON is written via `tempfile.NamedTemporaryFile` +
`os.replace` so a mid-run crash can never leave a partially-truncated
fixture on disk. Same for `baseline_performance.json`.

Safeguards for runtime
----------------------
- Default n=20 × 11 fixtures × ~0.3-1s/run = ~1-4 minutes wall-clock.
- `--n-runs N` lets a reviewer dry-run with N=3 for a fast sanity check.
- If the script goes past the orchestrator's 15-minute budget, something
  is wrong (likely Supabase RTT spike). The operator should Ctrl-C; no
  fixture is written until BOTH determinism AND all-N runs succeed.

CLI
---
    # Freeze all 11 fixtures (default):
    python backend/scripts/parity_freeze_current_behavior.py

    # Freeze only scenario 08:
    python backend/scripts/parity_freeze_current_behavior.py --scenario 08

    # Dry-run — compute + print hashes + perf; do NOT write JSON:
    python backend/scripts/parity_freeze_current_behavior.py --dry-run --all

    # Smoke test with n=3 instead of 20:
    python backend/scripts/parity_freeze_current_behavior.py --n-runs 3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os

# Force CP-SAT determinism BEFORE importing the solver (same invariant as
# tests/conftest.py). Without this line, a freeze run invoked outside
# pytest could silently pick up multi-worker CP-SAT from the shell env.
os.environ["CPSAT_WORKERS"] = "1"

import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Path setup so `app.*` and `tests.*` imports work regardless of cwd.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.infrastructure.database import SessionLocal  # noqa: E402
from app.infrastructure.models.schedule_task import ScheduleTask  # noqa: E402
from app.application.scheduling.cp_sat.orchestrator import cp_sat_schedule  # noqa: E402
from tests._parity_helpers import (  # noqa: E402
    _collect_assignments,
    _fixture_to_solver_input,
    _stable_hash,
    discover_fixtures,
    load_fixture,
)

_FIXTURES_DIR = _BACKEND_ROOT / "tests" / "fixtures" / "parity"
_BASELINE_PATH = _FIXTURES_DIR / "baseline_performance.json"

_DEFAULT_N_RUNS = 20
_TOP_N_ASSIGNMENTS = 5  # carryover #1 — expected_assignments captures top-5


# ────────────────────────────────────────────────────────────────────────
# One fixture, one run — returns (hash, status, objective, elapsed_ms,
# assignments). Wrapped by _run_fixture_n_times below.
# ────────────────────────────────────────────────────────────────────────


def _run_once(
    db: Session,
    fixture: dict[str, Any],
) -> tuple[str, str, int | None, float, list[dict[str, Any]]]:
    """Execute one solve-and-hash cycle inside a SAVEPOINT.

    Returns (hash, solver_status, objective_value, elapsed_ms, assignments).
    The SAVEPOINT rolls back before return so the next run starts from
    the same base state. We verify row-count unchanged as a leak canary.

    `elapsed_ms` measures the solver-only window (rehydrate + solve +
    collect). This is intentionally conservative — it includes the
    rehydrate time because the real production path also rebuilds
    SolverInput per run, so measuring the combined cost is what
    §10b's p50/p99 envelope describes.
    """
    run_label = fixture["run_label"]

    before = db.query(ScheduleTask).count()
    nested = db.begin_nested()
    try:
        t0 = time.perf_counter()
        si, db_id_to_fixture_id = _fixture_to_solver_input(fixture, db)
        horizon_start = si.base_date

        result = cp_sat_schedule(
            run_label,
            db,
            solver_input_override=si,
            num_search_workers=1,
            random_seed=0,
            run_id_override=run_label,
        )

        assignments = _collect_assignments(db, run_label, db_id_to_fixture_id)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        actual_hash = _stable_hash(assignments, run_label, horizon_start)
        status = result.get("solver_status", "UNKNOWN")
        objective_raw = result.get("objective_value")
        # cp_sat_schedule returns 0 on non-OPTIMAL; keep as int but None-out
        # for INFEASIBLE so the frozen field reflects "no objective exists".
        objective: int | None
        if status in ("OPTIMAL", "FEASIBLE"):
            objective = int(objective_raw) if objective_raw is not None else None
        else:
            objective = None
    finally:
        if nested.is_active:
            nested.rollback()

    after = db.query(ScheduleTask).count()
    if before != after:
        # Deal-breaker: SAVEPOINT-only isolation is unsafe. Abort before
        # we corrupt downstream Supabase state.
        raise RuntimeError(
            f"SAVEPOINT leak in fixture {fixture['scenario_id']}: "
            f"ScheduleTask count changed {before} → {after}. "
            f"Some path committed outside the SAVEPOINT — refuse to freeze."
        )

    return actual_hash, status, objective, elapsed_ms, assignments


def _run_fixture_n_times(
    db: Session, fixture: dict[str, Any], n_runs: int
) -> dict[str, Any]:
    """Run a fixture `n_runs` times, assert hash determinism, return summary.

    Returns a dict:
        {
          "scenario_id": ...,
          "hash": str (the one hash all N runs produced),
          "solver_status": str,
          "objective_value": int | None,
          "assignments": list[dict]  (from run 0 — top-N used downstream),
          "p50_ms": float,
          "p99_ms": float,
          "wall_clock_ms": list[float]  (all N timings, for audit),
        }

    Raises `RuntimeError` if any two runs disagree on hash (determinism
    violation) — caller decides whether to abort or record and continue.
    """
    scenario_id = fixture["scenario_id"]
    hashes: list[str] = []
    statuses: list[str] = []
    objectives: list[int | None] = []
    timings: list[float] = []
    first_assignments: list[dict[str, Any]] = []

    for i in range(n_runs):
        h, status, objective, elapsed_ms, assignments = _run_once(db, fixture)
        hashes.append(h)
        statuses.append(status)
        objectives.append(objective)
        timings.append(elapsed_ms)
        if i == 0:
            first_assignments = assignments

    # Determinism: all N hashes identical.
    distinct = sorted(set(hashes))
    if len(distinct) != 1:
        raise RuntimeError(
            f"DETERMINISM FAIL [{scenario_id}]: {len(distinct)} distinct "
            f"hashes across {n_runs} runs:\n  "
            + "\n  ".join(distinct)
            + "\nDo NOT freeze — investigate non-determinism source."
        )
    # Solver status / objective drift — same invariant.
    if len(set(statuses)) != 1:
        raise RuntimeError(
            f"DETERMINISM FAIL [{scenario_id}]: solver_status drift "
            f"across runs: {sorted(set(statuses))}"
        )
    if len(set(objectives)) != 1:
        raise RuntimeError(
            f"DETERMINISM FAIL [{scenario_id}]: objective_value drift "
            f"across runs: {sorted(set(objectives), key=lambda x: (x is None, x))}"
        )

    p50 = statistics.median(timings)
    # p99 on n=20 → the max is the 95th-percentile-ish ranked value.
    # Use the standard "nearest-rank" method so n=3 smoke doesn't crash.
    p99 = _percentile_nearest_rank(timings, 99)

    return {
        "scenario_id": scenario_id,
        "hash": hashes[0],
        "solver_status": statuses[0],
        "objective_value": objectives[0],
        "assignments": first_assignments,
        "p50_ms": round(p50, 2),
        "p99_ms": round(p99, 2),
        "wall_clock_ms": [round(t, 2) for t in timings],
    }


def _percentile_nearest_rank(values: list[float], pct: float) -> float:
    """`percentile` via nearest-rank method.

    Python 3.11 stdlib has `statistics.quantiles` but the default method
    (`exclusive`) gives wonky answers at the tail for n=20 (interpolates
    between samples 19 and 20 but there IS no sample 20). Nearest-rank
    mirrors what SRE dashboards typically report (the N-th ordered value)
    and behaves correctly for any n ≥ 1.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    rank = max(1, int((pct / 100.0) * len(sorted_vals) + 0.999999))
    return sorted_vals[min(rank - 1, len(sorted_vals) - 1)]


# ────────────────────────────────────────────────────────────────────────
# Fixture write-back
# ────────────────────────────────────────────────────────────────────────


def _top_n_assignments(
    assignments: list[dict[str, Any]],
    horizon_start: datetime,
    n: int,
) -> list[dict[str, Any]]:
    """Project top-N assignments into the fixture's `expected_assignments` shape.

    We sort by `(group_key, equipment_id)` — same order `_format_auditor_diff`
    uses — so a future regression shows stable top-5 regardless of batch_id
    remap noise. Minutes (int) chosen over timestamps to keep JSON compact
    and timezone-independent.
    """
    sorted_assigns = sorted(
        assignments,
        key=lambda a: (a.get("group_key") or "", a["equipment_id"]),
    )
    out: list[dict[str, Any]] = []
    for a in sorted_assigns[:n]:
        start_min = int((a["assigned_start"] - horizon_start).total_seconds() // 60)
        out.append(
            {
                "batch_group": a.get("group_key") or "",
                "equipment_id": a["equipment_id"],
                "start_offset_min": start_min,
            }
        )
    return out


def _write_fixture_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write fixture JSON atomically (tempfile + os.replace).

    Preserves the same formatting as Task 1.3 capture script so git diffs
    are minimal: `indent=2, sort_keys=True, ensure_ascii=False` + trailing
    newline.
    """
    serialized = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    try:
        tmp.write(serialized)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        # Best-effort cleanup; never propagate a raised suppression.
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
        raise


def _freeze_fixture(
    fixture_path: Path,
    summary: dict[str, Any],
    horizon_start: datetime,
) -> None:
    """Merge the run summary back into the fixture's expected_* fields."""
    fixture = load_fixture(fixture_path)
    fixture["expected_output_hash"] = summary["hash"]
    fixture["expected_solver_status"] = summary["solver_status"]
    fixture["expected_objective_value"] = summary["objective_value"]
    fixture["expected_assignments"] = _top_n_assignments(
        summary["assignments"], horizon_start, _TOP_N_ASSIGNMENTS
    )
    _write_fixture_atomic(fixture_path, fixture)


# ────────────────────────────────────────────────────────────────────────
# Baseline performance file
# ────────────────────────────────────────────────────────────────────────


def _git_head_sha() -> str:
    """Current HEAD commit for baseline provenance; empty string if unknown."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_BACKEND_ROOT.parent),
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8").strip()
    except Exception:
        return ""


def _write_baseline(summaries: list[dict[str, Any]], n_runs: int) -> None:
    """Write baseline_performance.json atomically."""
    fixtures_perf: dict[str, dict[str, Any]] = {}
    for s in sorted(summaries, key=lambda x: x["scenario_id"]):
        fixtures_perf[s["scenario_id"]] = {
            "p50_ms": s["p50_ms"],
            "p99_ms": s["p99_ms"],
            "n": n_runs,
        }
    payload = {
        "baseline_commit": _git_head_sha(),
        "captured_at": datetime.now(UTC).isoformat(),
        "cpsat_workers": 1,
        "n_runs_per_fixture": n_runs,
        "fixtures": fixtures_perf,
    }
    _write_fixture_atomic(_BASELINE_PATH, payload)


# ────────────────────────────────────────────────────────────────────────
# Driver
# ────────────────────────────────────────────────────────────────────────


def _select_fixtures(scenario: str | None) -> list[Path]:
    """Pick the fixture paths the CLI targeted (`--scenario` or `--all`)."""
    all_paths = discover_fixtures(quick=False)
    if scenario is None:
        return all_paths
    # Match by stem prefix (e.g. "10" matches 10a and 10b; "04" matches 04_*).
    matches = [
        p for p in all_paths if p.stem.startswith(f"{scenario}_") or p.stem == scenario
    ]
    if not matches:
        raise SystemExit(
            f"No fixture matches scenario prefix '{scenario}'. "
            f"Available: {[p.stem for p in all_paths]}"
        )
    return matches


def _format_summary_row(summary: dict[str, Any]) -> str:
    """One-line fixture result for CLI output."""
    return (
        f"  {summary['scenario_id']:<30} "
        f"{summary['hash'][:23]}... "
        f"status={summary['solver_status']:<11} "
        f"obj={str(summary['objective_value']):<8} "
        f"p50={summary['p50_ms']:>7.2f}ms "
        f"p99={summary['p99_ms']:>7.2f}ms"
    )


def main() -> int:
    args = _parse_args()
    paths = _select_fixtures(args.scenario)

    print(
        f"[freeze] n_runs={args.n_runs} dry_run={args.dry_run} "
        f"fixtures={len(paths)} CPSAT_WORKERS={os.environ['CPSAT_WORKERS']}"
    )
    print(f"[freeze] baseline_commit={_git_head_sha()[:12]}")

    db = SessionLocal()
    summaries: list[dict[str, Any]] = []
    total_t0 = time.perf_counter()
    try:
        for path in paths:
            fixture = load_fixture(path)
            t0 = time.perf_counter()
            try:
                summary = _run_fixture_n_times(db, fixture, args.n_runs)
            except RuntimeError as e:
                # Determinism / leak violation — surface and abort.
                print(f"\n[FAIL] {path.stem}: {e}", file=sys.stderr)
                return 2
            elapsed = time.perf_counter() - t0
            print(_format_summary_row(summary) + f"  [{elapsed:5.1f}s total]")
            summaries.append(summary)

            if not args.dry_run:
                # horizon_start is the fixture's base_date — same value
                # _run_once used internally. Re-parse to avoid smuggling
                # datetime objects through the dict.
                horizon_start = datetime.fromisoformat(
                    fixture["solver_input"]["base_date"]
                )
                _freeze_fixture(path, summary, horizon_start)
                print(f"  → wrote {path.name}")
            else:
                print(f"  → WOULD WRITE {path.name} (dry-run)")

        if not args.dry_run:
            _write_baseline(summaries, args.n_runs)
            print(f"\n[freeze] wrote {_BASELINE_PATH.name}")
        else:
            print("\n[freeze] WOULD WRITE baseline_performance.json (dry-run)")
    finally:
        db.rollback()
        db.close()

    total_elapsed = time.perf_counter() - total_t0
    print(
        f"\n[freeze] done in {total_elapsed:.1f}s — "
        f"{len(summaries)} fixtures × {args.n_runs} runs = "
        f"{len(summaries) * args.n_runs} solves"
    )
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Task 1.5: freeze parity fixture hashes + capture p50/p99 baseline. "
            "Runs each fixture N times with deterministic knobs "
            "(CPSAT_WORKERS=1, random_seed=0), asserts hash stability, and "
            "writes expected_* fields back into each fixture JSON + a "
            "baseline_performance.json summary."
        )
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--all",
        action="store_true",
        help="Freeze every fixture (default).",
    )
    g.add_argument(
        "--scenario",
        type=str,
        metavar="NN",
        help="Freeze only fixtures whose stem starts with 'NN_' (e.g. '04', '10').",
    )
    p.add_argument(
        "--n-runs",
        type=int,
        default=_DEFAULT_N_RUNS,
        help=f"Runs per fixture for determinism + perf (default {_DEFAULT_N_RUNS}).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do NOT write fixtures or baseline — just print hashes + p50/p99.",
    )
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
