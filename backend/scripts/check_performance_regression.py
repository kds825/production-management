"""Task 1.6 — parity-runtime regression check vs frozen baseline.

Compares current parity-harness runtimes against the p99 envelope frozen
in `backend/tests/fixtures/parity/baseline_performance.json` (Task 1.5).

Runs each parity fixture N times through the same override path used by
the parity harness (Task 1.4) and the freeze script (Task 1.5), measures
wall-clock per run, and verdicts every fixture:

    ratio = current_p99 / baseline_p99

    ratio ≤ warn_ratio (default 1.5)  → OK
    warn_ratio < ratio ≤ fail_ratio   → WARN
    ratio > fail_ratio (default 2.0)  → FAIL

Exit codes
----------
    0  all fixtures OK or WARN (CI does NOT block; WARN is advisory)
    1  at least one fixture FAILed (CI blocks — perf regression)
    2  internal error (baseline missing, determinism failure, bad CLI)
    3  parity hash mismatch vs fixture's expected_output_hash
       (the parity test suite already catches this; this script is a
       belt-and-braces check for out-of-band runs like `make perf-check`)

Determinism & isolation
-----------------------
Same discipline as `parity_freeze_current_behavior.py`:
  1. `CPSAT_WORKERS=1` set at module import, BEFORE solver import.
  2. `num_search_workers=1`, `random_seed=0` on every cp_sat_schedule call.
  3. Each fixture run wrapped in `db.begin_nested()` (SAVEPOINT), rolled
     back before the next — `ScheduleTask` row-count leak canary asserted.
  4. All N runs per fixture MUST produce the same hash. If not, the
     fixture can't report a stable p99 — exit 3 (hash/determinism drift).

Read-only on fixtures. NEVER writes baseline_performance.json or any
parity fixture JSON. If hash drift is detected, we report it and exit
with code 3 — we do NOT rewrite the fixture. That's parity_freeze_…'s
job and must ride on a `parity-update:` commit.

CLI
---
    # Check all 11 fixtures against baseline (default, ~30-60s):
    python backend/scripts/check_performance_regression.py

    # Quick single-fixture iteration:
    python backend/scripts/check_performance_regression.py --scenario 01 --n-runs 2

    # Strict CI — escalate WARN to FAIL:
    python backend/scripts/check_performance_regression.py --fail-on-warn

    # Simulate a regression (manual smoke test):
    python backend/scripts/check_performance_regression.py --scenario 01 \
        --n-runs 1 --fail-ratio 0.01

    # Verbose: per-run timings for debugging flaky CI:
    python backend/scripts/check_performance_regression.py --scenario 08 -v

Why default N=5 (vs freeze's N=20)
----------------------------------
Freeze needs a stable 99th percentile — it gets that from n=20 (nearest-
rank p99 = max on n=20 converges on the true tail). Runtime-regression
detection has a different SLO: we want to catch order-of-magnitude shifts
quickly, not measure the tail to 3 sig figs. N=5 × 11 fixtures ≈ 30-60s
is a practical CI budget (the 4.4s scenario 08 alone is ~22s at N=5).

For N<20 we treat `max(timings)` as `current_p99` — conservative and
consistent with freeze's nearest-rank at small N. The `--n-runs 20`
override lets a reviewer reproduce the freeze's exact p99 methodology.
"""

from __future__ import annotations

import argparse
import json
import os

# Force CP-SAT determinism BEFORE importing the solver. Same invariant
# as tests/conftest.py and parity_freeze_current_behavior.py. Without
# this, a perf-check invoked outside pytest could silently pick up the
# ambient CPSAT_WORKERS from the shell (multi-core → nondeterministic
# timing AND hash).
os.environ["CPSAT_WORKERS"] = "1"

import statistics
import sys
import time
import traceback  # noqa: F401  — used in `if __name__` exit-hygiene handler
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Path setup — mirrors parity_freeze_current_behavior.py so `app.*` /
# `tests.*` imports work regardless of cwd (make target vs manual run).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# ── CWD-robust .env loading ─────────────────────────────────────────────
# `app/config.py` sets `env_file = ".env"` (relative to CWD), so running
# this script from repo root instead of `backend/` silently falls back
# to the DATABASE_URL default (local Docker DB) — whose schema drifts
# from Supabase and triggers UndefinedColumn on the first query. Loading
# `backend/.env` explicitly here makes the script CWD-agnostic without
# touching `app/config.py` (broader scope — separate task).
#
# `override=False` respects values already set by the caller (CI, devs
# running `DATABASE_URL=... python scripts/...`), matching pydantic-
# settings' own precedence (env > .env file).
load_dotenv(_BACKEND_ROOT / ".env", override=False)

from sqlalchemy.orm import Session  # noqa: E402

from app.infrastructure.database import SessionLocal  # noqa: E402
from app.infrastructure.models.schedule_task import ScheduleTask  # noqa: E402
from app.services.cp_sat_optimizer import cp_sat_schedule  # noqa: E402
from tests._parity_helpers import (  # noqa: E402
    _collect_assignments,
    _fixture_to_solver_input,
    _stable_hash,
    discover_fixtures,
    load_fixture,
)

_FIXTURES_DIR = _BACKEND_ROOT / "tests" / "fixtures" / "parity"
_BASELINE_PATH = _FIXTURES_DIR / "baseline_performance.json"

_DEFAULT_N_RUNS = 5
_DEFAULT_WARN_RATIO = 1.5
_DEFAULT_FAIL_RATIO = 2.0

# Exit-code vocabulary — see module docstring. Centralized here so the
# --help output and the actual sys.exit() calls can't drift.
_EXIT_OK = 0
_EXIT_PERF_FAIL = 1
_EXIT_INTERNAL = 2
_EXIT_HASH_DRIFT = 3


# ────────────────────────────────────────────────────────────────────────
# Per-fixture result
# ────────────────────────────────────────────────────────────────────────


@dataclass
class _FixtureResult:
    """One fixture's verdict + supporting data for the summary table."""

    scenario_id: str
    baseline_p99_ms: float
    current_p99_ms: float
    ratio: float
    verdict: str  # "OK" | "WARN" | "FAIL" | "HASH_DRIFT"
    timings_ms: list[float]
    hash_drift: bool
    expected_hash: str | None
    actual_hashes: list[str]  # distinct hashes observed (canary)

    @property
    def verdict_tag(self) -> str:
        """ANSI-color tag for terminal output (no color in CI/pipe)."""
        if not sys.stdout.isatty():
            return self.verdict
        colors = {
            "OK": "\033[32m",  # green
            "WARN": "\033[33m",  # yellow
            "FAIL": "\033[31m",  # red
            "HASH_DRIFT": "\033[35m",  # magenta — distinct from perf FAIL
        }
        reset = "\033[0m"
        return f"{colors.get(self.verdict, '')}{self.verdict}{reset}"


# ────────────────────────────────────────────────────────────────────────
# Single-run executor — same contract as parity_freeze._run_once but we
# keep a private copy to avoid importing internals the freeze script
# marks as "_" prefixed (friendlier semver for future refactors).
# ────────────────────────────────────────────────────────────────────────


def _run_once(
    db: Session,
    fixture: dict[str, Any],
) -> tuple[str, float]:
    """Execute one solve-and-hash cycle inside a SAVEPOINT.

    Returns (hash, elapsed_ms). Only the solver-window timing matters
    for regression detection; we pair it with the hash to catch out-of-
    band parity drift without importing the whole solver_status /
    objective_value chain (which the parity test suite validates).

    Row-count leak canary: `ScheduleTask.count()` before/after MUST
    match — the SAVEPOINT should roll back everything. If not, something
    inside the solver path bypassed the nested txn (e.g., a stray
    `db.commit()`) and we cannot trust the isolation. Raise loud.
    """
    run_label = fixture["run_label"]

    before = db.query(ScheduleTask).count()
    nested = db.begin_nested()
    try:
        t0 = time.perf_counter()
        si, db_id_to_fixture_id = _fixture_to_solver_input(fixture, db)
        horizon_start = si.base_date

        cp_sat_schedule(
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
    finally:
        if nested.is_active:
            nested.rollback()

    after = db.query(ScheduleTask).count()
    if before != after:
        raise RuntimeError(
            f"SAVEPOINT leak in fixture {fixture['scenario_id']}: "
            f"ScheduleTask count {before} → {after}. "
            "Some path committed outside the SAVEPOINT — aborting."
        )

    return actual_hash, elapsed_ms


# ────────────────────────────────────────────────────────────────────────
# p99 methodology
# ────────────────────────────────────────────────────────────────────────


def _percentile_nearest_rank(values: list[float], pct: float) -> float:
    """Same nearest-rank method as the freeze script.

    For small N (our default 5), this reduces to `max(values)` at pct=99,
    which is the conservative regression-detection choice: any single
    slow run trips the ratio. For N≥20 (user override), this converges
    on the true 99th percentile.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    rank = max(1, int((pct / 100.0) * len(sorted_vals) + 0.999999))
    return sorted_vals[min(rank - 1, len(sorted_vals) - 1)]


# ────────────────────────────────────────────────────────────────────────
# Fixture-level check
# ────────────────────────────────────────────────────────────────────────


def _check_fixture(
    db: Session,
    fixture_path: Path,
    baseline_p99_ms: float,
    n_runs: int,
    warn_ratio: float,
    fail_ratio: float,
    verbose: bool,
) -> _FixtureResult:
    """Run `fixture` N times, compare p99 vs baseline, return verdict.

    Invariants:
      - All N runs must produce the same hash (determinism guard). If
        not, verdict=HASH_DRIFT and the caller exits 3. We still return
        timings for the summary table — operators need to see how bad
        the scatter was.
      - Hash MUST equal `fixture["expected_output_hash"]` (parity). If
        not, verdict=HASH_DRIFT; caller exits 3. This catches out-of-
        band drift that the parity test suite would already flag in CI
        but perf-check may be invoked standalone.
    """
    fixture = load_fixture(fixture_path)
    scenario_id = fixture["scenario_id"]
    expected_hash = fixture.get("expected_output_hash")

    hashes: list[str] = []
    timings: list[float] = []
    for i in range(n_runs):
        h, elapsed_ms = _run_once(db, fixture)
        hashes.append(h)
        timings.append(elapsed_ms)
        if verbose:
            print(
                f"    [{scenario_id} run {i + 1}/{n_runs}] "
                f"{elapsed_ms:7.2f}ms  {h[:23]}..."
            )

    distinct_hashes = sorted(set(hashes))
    current_p99 = _percentile_nearest_rank(timings, 99)
    ratio = current_p99 / baseline_p99_ms if baseline_p99_ms > 0 else float("inf")

    # Determinism / parity hash check first — those dominate the verdict.
    hash_drift = False
    if len(distinct_hashes) != 1:
        hash_drift = True
    elif expected_hash is not None and distinct_hashes[0] != expected_hash:
        hash_drift = True

    if hash_drift:
        verdict = "HASH_DRIFT"
    elif ratio > fail_ratio:
        verdict = "FAIL"
    elif ratio > warn_ratio:
        verdict = "WARN"
    else:
        verdict = "OK"

    return _FixtureResult(
        scenario_id=scenario_id,
        baseline_p99_ms=baseline_p99_ms,
        current_p99_ms=current_p99,
        ratio=ratio,
        verdict=verdict,
        timings_ms=[round(t, 2) for t in timings],
        hash_drift=hash_drift,
        expected_hash=expected_hash,
        actual_hashes=distinct_hashes,
    )


# ────────────────────────────────────────────────────────────────────────
# Baseline loader
# ────────────────────────────────────────────────────────────────────────


def _load_baseline(baseline_path: Path) -> dict[str, Any]:
    """Load + validate the frozen baseline. SystemExit(2) on problems."""
    if not baseline_path.exists():
        print(
            f"[perf-check] baseline missing: {baseline_path}\n"
            "  Run `python backend/scripts/parity_freeze_current_behavior.py`"
            " to capture it first.",
            file=sys.stderr,
        )
        raise SystemExit(_EXIT_INTERNAL)
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[perf-check] cannot parse baseline: {exc}", file=sys.stderr)
        raise SystemExit(_EXIT_INTERNAL) from exc
    if "fixtures" not in data or not isinstance(data["fixtures"], dict):
        print(
            f"[perf-check] baseline has no `fixtures` dict: {baseline_path}",
            file=sys.stderr,
        )
        raise SystemExit(_EXIT_INTERNAL)
    return data


def _select_fixtures(scenario: str | None) -> list[Path]:
    """Pick fixture paths per CLI (`--scenario NN` or `--all`)."""
    all_paths = discover_fixtures(quick=False)
    if scenario is None:
        return all_paths
    matches = [
        p for p in all_paths if p.stem.startswith(f"{scenario}_") or p.stem == scenario
    ]
    if not matches:
        print(
            f"[perf-check] no fixture matches scenario prefix '{scenario}'. "
            f"Available: {[p.stem for p in all_paths]}",
            file=sys.stderr,
        )
        raise SystemExit(_EXIT_INTERNAL)
    return matches


# ────────────────────────────────────────────────────────────────────────
# Output formatting
# ────────────────────────────────────────────────────────────────────────


def _format_summary_table(results: list[_FixtureResult]) -> str:
    """Render the per-fixture summary table."""
    header = (
        f"  {'scenario':<30} {'baseline_p99':>12} {'current_p99':>12} "
        f"{'ratio':>7}  verdict"
    )
    rule = "  " + "─" * (len(header) - 2)
    lines = [header, rule]
    for r in sorted(results, key=lambda x: x.scenario_id):
        ratio_s = f"{r.ratio:.2f}×" if r.ratio != float("inf") else "∞"
        lines.append(
            f"  {r.scenario_id:<30} "
            f"{r.baseline_p99_ms:>10.2f}ms {r.current_p99_ms:>10.2f}ms "
            f"{ratio_s:>7}  {r.verdict_tag}"
        )
    return "\n".join(lines)


def _format_hash_drift_detail(r: _FixtureResult) -> str:
    """Detailed diagnostic for HASH_DRIFT results — one block per fixture."""
    lines = [f"  scenario        : {r.scenario_id}"]
    if len(r.actual_hashes) != 1:
        lines.append(
            f"  determinism     : {len(r.actual_hashes)} distinct hashes "
            f"across {len(r.timings_ms)} runs"
        )
        for h in r.actual_hashes:
            lines.append(f"    - {h}")
    elif r.expected_hash is not None and r.actual_hashes[0] != r.expected_hash:
        lines.append("  parity mismatch : expected != actual")
        lines.append(f"    expected : {r.expected_hash}")
        lines.append(f"    actual   : {r.actual_hashes[0]}")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────
# Driver
# ────────────────────────────────────────────────────────────────────────


def main() -> int:
    args = _parse_args()

    if args.warn_ratio <= 0 or args.fail_ratio <= 0:
        print(
            "[perf-check] --warn-ratio and --fail-ratio must be positive.",
            file=sys.stderr,
        )
        return _EXIT_INTERNAL
    if args.fail_ratio < args.warn_ratio:
        print(
            f"[perf-check] --fail-ratio ({args.fail_ratio}) must be ≥ "
            f"--warn-ratio ({args.warn_ratio}).",
            file=sys.stderr,
        )
        return _EXIT_INTERNAL
    if args.n_runs < 1:
        print("[perf-check] --n-runs must be ≥ 1.", file=sys.stderr)
        return _EXIT_INTERNAL

    baseline_path = Path(args.baseline) if args.baseline else _BASELINE_PATH
    baseline = _load_baseline(baseline_path)
    baseline_fixtures: dict[str, Any] = baseline["fixtures"]

    paths = _select_fixtures(args.scenario)

    print(
        f"[perf-check] baseline={baseline_path.name} "
        f"baseline_commit={str(baseline.get('baseline_commit', ''))[:12]} "
        f"n_runs={args.n_runs} warn={args.warn_ratio:.2f}× "
        f"fail={args.fail_ratio:.2f}×"
    )
    print(
        f"[perf-check] fixtures={len(paths)} "
        f"CPSAT_WORKERS={os.environ['CPSAT_WORKERS']}"
    )

    db: Session = SessionLocal()
    results: list[_FixtureResult] = []
    total_t0 = time.perf_counter()
    try:
        for path in paths:
            scenario_id = path.stem
            baseline_entry = baseline_fixtures.get(scenario_id)
            if baseline_entry is None:
                print(
                    f"[perf-check] {scenario_id}: no baseline entry — skipping. "
                    "(Re-run freeze script if a new fixture was added.)",
                    file=sys.stderr,
                )
                continue
            baseline_p99 = float(baseline_entry["p99_ms"])

            try:
                result = _check_fixture(
                    db,
                    path,
                    baseline_p99_ms=baseline_p99,
                    n_runs=args.n_runs,
                    warn_ratio=args.warn_ratio,
                    fail_ratio=args.fail_ratio,
                    verbose=args.verbose,
                )
            except RuntimeError as exc:
                # SAVEPOINT leak or similar invariant violation — abort
                # loudly. Do NOT continue: downstream fixtures might be
                # running against dirty state.
                print(
                    f"\n[perf-check] INTERNAL ERROR [{scenario_id}]: {exc}",
                    file=sys.stderr,
                )
                return _EXIT_INTERNAL
            results.append(result)
            if args.verbose:
                # Blank line between verbose per-fixture blocks for readability.
                print()
    finally:
        db.rollback()
        db.close()

    total_elapsed = time.perf_counter() - total_t0

    print()
    print(_format_summary_table(results))
    print(
        f"\n[perf-check] done in {total_elapsed:.1f}s — "
        f"{len(results)} fixtures × {args.n_runs} runs"
    )

    # Verdict aggregation — hash drift > perf fail > warn > ok.
    hash_drift = [r for r in results if r.verdict == "HASH_DRIFT"]
    fails = [r for r in results if r.verdict == "FAIL"]
    warns = [r for r in results if r.verdict == "WARN"]

    if hash_drift:
        print(
            f"\n[perf-check] HASH_DRIFT in {len(hash_drift)} fixture(s) — "
            "parity test suite should also be failing; investigate BEFORE "
            "trusting perf numbers:",
            file=sys.stderr,
        )
        for r in hash_drift:
            print(_format_hash_drift_detail(r), file=sys.stderr)
        # Hash drift dominates: even if perf would FAIL, the FAIL number
        # is meaningless when the solver produced a different schedule.
        return _EXIT_HASH_DRIFT

    if fails:
        print(
            f"\n[perf-check] FAIL: {len(fails)} fixture(s) exceeded "
            f"{args.fail_ratio:.2f}× baseline_p99:",
            file=sys.stderr,
        )
        for r in fails:
            print(
                f"  {r.scenario_id}: {r.current_p99_ms:.2f}ms vs "
                f"baseline {r.baseline_p99_ms:.2f}ms  ({r.ratio:.2f}×)",
                file=sys.stderr,
            )
        return _EXIT_PERF_FAIL

    if warns:
        print(f"\n[perf-check] WARN: {len(warns)} fixture(s) in 1.5-2× band:")
        for r in warns:
            print(
                f"  {r.scenario_id}: {r.current_p99_ms:.2f}ms vs "
                f"baseline {r.baseline_p99_ms:.2f}ms  ({r.ratio:.2f}×)"
            )
        if args.fail_on_warn:
            print(
                "[perf-check] --fail-on-warn: treating WARN as FAIL.", file=sys.stderr
            )
            return _EXIT_PERF_FAIL

    # Summary stats — operators like to see the distribution even on green.
    if results:
        ratios = [r.ratio for r in results]
        print(
            f"[perf-check] ratio distribution: "
            f"min={min(ratios):.2f}× "
            f"median={statistics.median(ratios):.2f}× "
            f"max={max(ratios):.2f}×"
        )

    print("[perf-check] OK — no perf regressions detected.")
    return _EXIT_OK


_EXIT_CODE_HELP = """\
Exit codes
----------
  0  OK (or WARN without --fail-on-warn)
  1  FAIL — at least one fixture exceeded --fail-ratio × baseline_p99
  2  internal error (baseline missing/malformed, SAVEPOINT leak, bad CLI)
  3  HASH_DRIFT — determinism failed OR actual hash ≠ expected_output_hash
"""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Task 1.6: compare current parity-harness p99 runtimes vs "
            "baseline_performance.json. Block at 2× (FAIL), warn at 1.5×."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EXIT_CODE_HELP,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--all",
        action="store_true",
        help="Check every fixture (default).",
    )
    g.add_argument(
        "--scenario",
        type=str,
        metavar="NN",
        help="Check only fixtures whose stem starts with 'NN_' (e.g. '04', '10').",
    )
    p.add_argument(
        "--baseline",
        type=str,
        metavar="PATH",
        default=None,
        help=(
            "Override path to baseline_performance.json "
            "(default: backend/tests/fixtures/parity/baseline_performance.json)."
        ),
    )
    p.add_argument(
        "--n-runs",
        type=int,
        default=_DEFAULT_N_RUNS,
        help=(
            f"Runs per fixture for p99 estimation (default {_DEFAULT_N_RUNS}). "
            "For N<20, p99 = max(timings) via nearest-rank — conservative. "
            "Use N≥20 to match the freeze script's p99 methodology."
        ),
    )
    p.add_argument(
        "--warn-ratio",
        type=float,
        default=_DEFAULT_WARN_RATIO,
        help=f"Warn threshold as multiple of baseline_p99 (default {_DEFAULT_WARN_RATIO}).",
    )
    p.add_argument(
        "--fail-ratio",
        type=float,
        default=_DEFAULT_FAIL_RATIO,
        help=f"Fail threshold as multiple of baseline_p99 (default {_DEFAULT_FAIL_RATIO}).",
    )
    p.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Escalate WARN to FAIL (exit 1). For strict CI gates.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print per-run timings + hash for each fixture (debug flaky CI).",
    )
    return p.parse_args()


if __name__ == "__main__":
    # Per spec A1: exit 1 is RESERVED for perf FAIL. Any other unhandled
    # exception (sqlalchemy.exc.ProgrammingError from a schema mismatch,
    # psycopg2 OperationalError from a DB outage, FileNotFoundError from
    # a bad --baseline path that slipped past our own validator, etc.)
    # must map to _EXIT_INTERNAL so CI doesn't mis-file infra failures
    # as perf regressions. The traceback still goes to stderr so CI logs
    # remain debuggable.
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — intentional top-level catch-all
        print(
            f"[perf-check] INTERNAL ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        sys.exit(_EXIT_INTERNAL)
