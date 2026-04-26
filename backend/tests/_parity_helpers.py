"""Parity harness helpers (Task 1.4).

Separated from `test_parity_harness.py` so the test file stays focused on
flow (load → run → hash → compare) and these reconstruction / formatting
primitives can be unit-tested or reused by future parity tooling.

Scope
-----
- `_stable_hash`: the canonical scheduling-output hash (design spec §6).
- `_fixture_to_solver_input`: rehydrates a fixture JSON envelope into a
  live `SolverInput` with ORM batches inserted into the caller's DB
  session (so that `cp_sat_schedule`'s `ScheduleTask` FK → batch_id
  resolves at flush time).
- `_format_auditor_diff`: human-readable auditor-trail diff on hash
  mismatch — shows expected vs actual hash (truncated) and top-N
  diverging assignments with batch_group / equipment / start-minute.
- `_collect_assignments`: pulls the `ScheduleTask` rows written by the
  solver (this run_label only) into the dict shape `_stable_hash` expects.

Design notes
------------
### Why we insert batches into the live DB instead of passing detached
ORM rows directly
`cp_sat_schedule` writes `ScheduleTask` rows via `db.add(ScheduleTask(
batch_id=rep.batch_id, ...)); db.flush()`. `schedule_task.batch_id` is a
FK to `production_batch.batch_id`. If the `ProductionBatch` objects in
`SolverInput.batches` are merely detached (constructed via
`ProductionBatch(**row_dict)` with the fixture's remapped batch_id=0..N-1),
the flush fails with a FK violation — those rows don't exist in the DB.

So we insert them. But that means the DB's autoincrement sequence assigns
fresh `batch_id`s (not the fixture's 0..N-1). To preserve the fixture's
original batch identity for hash stability, we record the mapping
`db_batch_id → fixture_batch_id` and, before hashing, we rewrite any
`_single_{db_id}` batch_group string back to `_single_{fixture_id}`.

Rationale: `cp_sat_schedule` constructs
    key = b.batch_group or f"_single_{b.batch_id}"
for every batch, so when `batch_group == ""` (scenarios 01/02/08/09) the
group_key embeds `batch_id`. Without the reverse-remap, the hash varies
per test invocation because Supabase's batch sequence advances globally.

### Why we pass DB masterdata (EquipmentMaster/SpeedMaster/ConstraintParams)
from the live DB rather than rehydrating from the fixture
Masterdata rows have natural PKs (equipment_code) or are tables the seeds
don't modify (the capture script writes `equipment_list` etc. to the
fixture for diff-reviewability, not for reconstruction). Rehydrating
from fixture JSON would force us to re-insert 26 EquipmentMaster rows,
~200 SpeedMaster rows, and 38 ConstraintConfig rows per test — which
would (a) conflict with existing rows by PK and (b) require stripping &
reinserting each time. The simpler invariant: **masterdata is frozen
outside the parity harness**. If masterdata drifts between capture and
parity-run, the hash changes — and THAT is a signal the parity contract
was broken (user changed a calendar rule or added an equipment), not a
harness bug. Fixture masterdata is therefore a diff-review artifact, not
a reconstruction source.

The fields we DO reconstruct from fixture: `batches` (the scenario-
specific data), `base_date`, `wip_skipped`, and everything downstream
re-derived from the live DB via a streamlined `build_solver_input`-
equivalent path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.wip_inventory import WipInventory
from app.application._shared.constraint_params import ConstraintParams
from app.domain.constants import _DEFAULT_WELDING_MIN, _WIP_SKIP_PROCESSES
from app.application.scheduling.cp_sat.input_builder import SolverInput

# ── Fixture column → ProductionBatch attr ───────────────────────────────
# Columns we strip on rehydration. `created_at` is populated by the
# SQLAlchemy default; including it in the kwargs would overwrite the
# default with a naive-ISO-str → type confusion.
_BATCH_SKIP_COLS = frozenset({"created_at"})

# Columns that need Python type coercion from JSON primitives.
_DATE_COLS = frozenset({"due_date"})
_DATETIME_COLS = frozenset({"created_at"})  # unused — skipped above
# Numeric columns: `Decimal` in DB, but SQLAlchemy accepts `float`/`int`;
# JSON gives us float/int already. No coercion needed.

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "parity"


# ────────────────────────────────────────────────────────────────────────
# Stable hash (design spec §6)
# ────────────────────────────────────────────────────────────────────────


def _stable_hash(
    assignments: list[dict[str, Any]],
    run_label: str,
    horizon_start: datetime,
) -> str:
    """Canonical hash of a scheduling result (design spec §6).

    Hashes the sorted tuple `(group_key, equipment_id, start_minute)`
    across all assignments, then SHA-256s the JSON of `[run_label,
    sorted_items]`. Returns `"sha256:<64 hex chars>"`.

    `start_minute` is integer minutes from `horizon_start` — avoids
    timezone drift while preserving 1-minute resolution (D9-A: strict;
    no tolerance). See `_stable_hash`'s design doc §6 reference in
    docs/plans/2026-04-23-production-handoff-refactor-plan.md.
    """
    items = sorted(
        (
            a.get("group_key", a.get("production_batch_id", "")),
            a["equipment_id"],
            int((a["assigned_start"] - horizon_start).total_seconds() // 60),
        )
        for a in assignments
    )
    payload = json.dumps([run_label, items], sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ────────────────────────────────────────────────────────────────────────
# Fixture → SolverInput rehydration
# ────────────────────────────────────────────────────────────────────────


def _rehydrate_batch(row: dict[str, Any]) -> ProductionBatch:
    """Build a transient `ProductionBatch` from a fixture row dict.

    The caller is expected to `db.add()` + `db.flush()` the returned
    instance so DB assigns a real `batch_id` (the fixture's remapped
    0..N-1 is overwritten on flush — we use the DB value downstream).
    """
    # Task 1.4 follow-up: document the contract that every fixture row
    # carries the remapped `batch_id` (0..N-1) from Task 1.3 capture. If
    # this ever fires, the fixture JSON is corrupt — fail fast here
    # rather than silently dropping the implicit "no batch_id" row later
    # in `_fixture_to_solver_input` where the mapping dict would quietly
    # miss an entry.
    assert "batch_id" in row, (
        f"fixture batch row missing `batch_id` — corrupt fixture? row keys: "
        f"{sorted(row.keys())}"
    )
    kwargs: dict[str, Any] = {}
    for k, v in row.items():
        if k in _BATCH_SKIP_COLS:
            continue
        if k == "batch_id":
            # Drop fixture's remapped ID — let DB assign. Mapping is
            # recorded by the caller via fixture_batch_id index in list.
            continue
        if k in _DATE_COLS and isinstance(v, str):
            kwargs[k] = date.fromisoformat(v)
        else:
            kwargs[k] = v
    return ProductionBatch(**kwargs)


def _fixture_to_solver_input(
    fixture: dict[str, Any], db: Session
) -> tuple[SolverInput, dict[int, int]]:
    """Rehydrate a parity fixture into a live `SolverInput`.

    Side-effects
    ------------
    Inserts N `ProductionBatch` rows into the caller's session. Caller
    MUST wrap this in a SAVEPOINT (`db.begin_nested()`) to roll the
    inserts back at test teardown. Masterdata tables (EquipmentMaster,
    SpeedMaster, ConstraintConfig, DrumLotMaster, WipInventory) are
    read fresh from the live DB — see module docstring for rationale.

    Returns
    -------
    (solver_input, db_id_to_fixture_id)
        `db_id_to_fixture_id` maps the real `batch_id` assigned by
        Supabase to the fixture's original 0..N-1 position. Used by
        `_remap_single_group_keys` post-hash to stabilize hashes for
        fixtures whose batches have empty `batch_group`.
    """
    si_dict = fixture["solver_input"]

    # ── batches: rehydrate + insert so FK resolves at solver flush ──
    batches: list[ProductionBatch] = []
    fixture_order: list[int] = []  # fixture_batch_id per insertion index
    for row in si_dict["batches"]:
        fixture_order.append(int(row["batch_id"]))
        pb = _rehydrate_batch(row)
        db.add(pb)
        batches.append(pb)
    db.flush()  # assigns real batch_id on each ProductionBatch

    db_id_to_fixture_id: dict[int, int] = {
        int(pb.batch_id): fixture_order[idx] for idx, pb in enumerate(batches)
    }

    # Now re-sort batches with the SAME key build_solver_input uses,
    # so the override path feeds the solver a list in bit-identical order.
    batches.sort(
        key=lambda b: (
            PROCESS_ORDER.get(b.process_name, 50),
            b.batch_seq or 0,
            b.due_date or date.max,
            b.customer_priority or 99,
            -(float(b.sq_mm2 or 0)),
        )
    )

    # ── WIP skip replay (input_builder §1 mirror) ──────────────────
    # Scenario 04 uses a WIP match. We need to replay the same status
    # mutation + wip_skipped count here because the fixture's JSON
    # already reflects POST-skip state, but our freshly-inserted rows
    # are pre-skip. Mirror input_builder.build_solver_input lines 182-200.
    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id}
    wip_stage_map: dict[int, str] = {}
    if wip_ids:
        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

    schedulable: list[ProductionBatch] = []
    wip_skipped = 0
    for b in batches:
        if b.wip_matched_id and b.wip_matched_id in wip_stage_map:
            skip_set = _WIP_SKIP_PROCESSES.get(wip_stage_map[b.wip_matched_id], set())
            if b.process_name in skip_set:
                b.status = "wip_complete"
                wip_skipped += 1
                continue
        schedulable.append(b)
    batches = schedulable

    # ── base_date from fixture (authoritative — captured at seed time) ──
    base_date = datetime.fromisoformat(si_dict["base_date"])

    # ── masterdata from live DB (not fixture — see docstring) ──
    equipment_list = db.query(EquipmentMaster).all()
    equipment_by_process: dict[str, list[EquipmentMaster]] = {}
    for eq in equipment_list:
        equipment_by_process.setdefault(eq.process_name, []).append(eq)

    _speed_rows = db.query(SpeedMaster).all()
    speed_map: dict[tuple[str, float], SpeedMaster] = {
        (sr.equipment_code, float(sr.cross_section or 0)): sr for sr in _speed_rows
    }
    color_setup_map: dict[str, float | None] = {}
    for sr in _speed_rows:
        code = sr.equipment_code
        if code not in color_setup_map:
            color_setup_map[code] = sr.setup_color_min
        elif color_setup_map[code] is None and sr.setup_color_min is not None:
            color_setup_map[code] = sr.setup_color_min

    constraint_params = ConstraintParams.load(db)
    # The default must match input_builder.build_solver_input's fallback.
    # If the Week-3 refactor silently retires `_DEFAULT_WELDING_MIN` and
    # switches to a different constant, hashes drift across all fixtures
    # that hit this branch. Keep these two paths wired through the same
    # symbol so a rename forces a compiler-visible breakage instead of a
    # silent parity regression.
    welding_min = constraint_params.get(
        "4-4", "welding_min", default=_DEFAULT_WELDING_MIN
    )

    sq_to_wire_d: dict[int, float] = {
        int(d.cross_section): float(d.wire_diameter)
        for d in db.query(DrumLotMaster).all()
        if d.wire_diameter is not None
    }

    si = SolverInput(
        batches=batches,
        wip_skipped=wip_skipped,
        base_date=base_date,
        equipment_list=equipment_list,
        equipment_by_process=equipment_by_process,
        speed_map=speed_map,
        color_setup_map=color_setup_map,
        constraint_params=constraint_params,
        welding_min=welding_min,
        sq_to_wire_d=sq_to_wire_d,
    )
    return si, db_id_to_fixture_id


# ────────────────────────────────────────────────────────────────────────
# Assignment collection + single-batch remap
# ────────────────────────────────────────────────────────────────────────


def _remap_single_group_key(group_key: str, db_id_to_fixture_id: dict[int, int]) -> str:
    """Rewrite `_single_{db_id}` → `_single_{fixture_id}` for hash stability.

    `cp_sat_schedule` writes `batch_group=gk` where `gk = b.batch_group
    or f"_single_{b.batch_id}"`. When the fixture captured a batch with
    blank batch_group, the live-run batch_id differs from the fixture's
    because Supabase's autoincrement advances globally. We reverse the
    remap so the hash matches the fixture-capture invariant.

    Non-`_single_` keys pass through unchanged.
    """
    if not group_key.startswith("_single_"):
        return group_key
    try:
        db_id = int(group_key[len("_single_") :])
    except ValueError:
        return group_key
    fixture_id = db_id_to_fixture_id.get(db_id)
    if fixture_id is None:
        return group_key
    return f"_single_{fixture_id}"


def _collect_assignments(
    db: Session,
    run_label: str,
    db_id_to_fixture_id: dict[int, int],
) -> list[dict[str, Any]]:
    """Read `ScheduleTask` rows for this run into the hash-input shape.

    `cp_sat_schedule` flushed (but did not commit) N tasks tagged with
    `run_label`. We read them, rewrite `_single_*` group keys via the
    fixture-id remap, and return the list in the format
    `_stable_hash` expects:
        {group_key, equipment_id, production_batch_id, assigned_start}
    """
    rows = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.run_label == run_label)
        .order_by(ScheduleTask.batch_group, ScheduleTask.start_datetime)
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "group_key": _remap_single_group_key(
                    r.batch_group or "", db_id_to_fixture_id
                ),
                "equipment_id": r.equipment_code,
                "production_batch_id": db_id_to_fixture_id.get(r.batch_id, r.batch_id),
                "assigned_start": r.start_datetime,
            }
        )
    return out


# ────────────────────────────────────────────────────────────────────────
# Auditor's-trail diff (design spec §6)
# ────────────────────────────────────────────────────────────────────────


def _format_auditor_diff(
    *,
    scenario_id: str,
    expected_hash: str,
    actual_hash: str,
    actual_assignments: list[dict[str, Any]],
    horizon_start: datetime,
    top_n: int = 5,
    expected_assignments: list[dict[str, Any]] | None = None,
) -> str:
    """Emit a structured, debuggable diff message on hash mismatch.

    Per design spec §6 and D9-A (strict — no tolerance). Shows:
      - scenario_id
      - expected vs actual hash (truncated) and full hash on second line
      - side-by-side top-N EXPECTED vs ACTUAL assignments when the fixture
        carries `expected_assignments` (Task 1.5 freeze contract). Each row
        compares (batch_group, equipment, start_offset_min).

    When `expected_assignments` is None (fixture pre-freeze or malformed)
    we fall back to printing ACTUAL only, which is the same info the
    Task 1.5 freeze protocol originally relied on.
    """
    # Project actuals to the same (batch_group, equipment, start_min)
    # tuple shape that `expected_assignments` uses — lets us render
    # EXPECTED vs ACTUAL rows aligned on (batch_group, equipment) key.
    actual_sorted = sorted(
        actual_assignments,
        key=lambda a: (a.get("group_key") or "", a["equipment_id"]),
    )
    actual_rows: list[tuple[str, str, int]] = [
        (
            a.get("group_key") or "",
            a["equipment_id"],
            int((a["assigned_start"] - horizon_start).total_seconds() // 60),
        )
        for a in actual_sorted[:top_n]
    ]
    expected_rows: list[tuple[str, str, int]] | None = None
    if expected_assignments is not None:
        expected_rows = sorted(
            (
                (
                    e.get("batch_group") or "",
                    e["equipment_id"],
                    int(e.get("start_offset_min", 0)),
                )
                for e in expected_assignments[:top_n]
            ),
            key=lambda t: (t[0], t[1]),
        )

    lines = [
        "",
        "═══ PARITY HASH MISMATCH ═══",
        f"scenario  : {scenario_id}",
        f"expected  : {expected_hash[:23]}...  (full: {expected_hash})",
        f"actual    : {actual_hash[:23]}...  (full: {actual_hash})",
        f"horizon   : {horizon_start.isoformat()}",
        f"n_assign  : {len(actual_assignments)}",
        "",
    ]
    if expected_rows is not None:
        lines.append(
            f"Top-{min(top_n, max(len(actual_rows), len(expected_rows)))} "
            "EXPECTED vs ACTUAL (by batch_group, equipment):"
        )
        header = (
            f"  {'batch_group':<26}  {'eq (exp)':<10} "
            f"{'start (exp)':>11}   {'eq (act)':<10} {'start (act)':>11}   diff?"
        )
        lines.append(header)
        # Align by (batch_group, equipment); if shapes differ, show "—".
        max_rows = max(len(expected_rows), len(actual_rows))
        for i in range(max_rows):
            exp = expected_rows[i] if i < len(expected_rows) else None
            act = actual_rows[i] if i < len(actual_rows) else None
            bg = (exp[0] if exp else act[0] if act else "")[:26]
            exp_eq = exp[1] if exp else "—"
            exp_min = f"{exp[2]}" if exp else "—"
            act_eq = act[1] if act else "—"
            act_min = f"{act[2]}" if act else "—"
            differs = "✗" if (exp != act) else " "
            lines.append(
                f"  {bg:<26}  {exp_eq:<10} {exp_min:>11}   "
                f"{act_eq:<10} {act_min:>11}   {differs}"
            )
    else:
        lines.append(
            "Actual assignments (top-{n} by batch_group):".format(
                n=min(top_n, len(actual_assignments))
            )
        )
        lines.append(f"  {'batch_group':<32} {'equipment':<12} {'start_min':>10}")
        for bg, eq, start_min in actual_rows:
            lines.append(f"  {bg[:32]:<32} {eq:<12} {start_min:>10}")
    if len(actual_assignments) > top_n:
        lines.append(f"  ... and {len(actual_assignments) - top_n} more")
    lines.append("")
    lines.append("D9-A strict: no tolerance. A 1-minute drift on ANY assignment fails.")
    lines.append(
        "To update after an intentional change, use commit-msg prefix "
        "'parity-update:' (CI guard Task 1.7)."
    )
    lines.append("")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────
# Fixture discovery
# ────────────────────────────────────────────────────────────────────────


# Scenarios used by --parity-quick. Chosen per design spec §Task 1.4:
# one nominal, one complex (sheath color chain), one edge (capacity overflow).
QUICK_SCENARIOS: frozenset[str] = frozenset(
    {"01_nominal", "05_sheath_color_chain", "08_capacity_overflow"}
)


#: Non-scenario JSON files that live in the fixtures dir and MUST be
#: excluded from parity test discovery. `baseline_performance.json` is
#: Task 1.5's perf-envelope artifact (n=20 p50/p99 per fixture) — it
#: lacks `scenario_id`/`solver_input` and is not a test input.
_NON_SCENARIO_STEMS: frozenset[str] = frozenset({"baseline_performance"})


def discover_fixtures(quick: bool = False) -> list[Path]:
    """Return sorted list of parity fixture JSON paths.

    Excludes `_NON_SCENARIO_STEMS` (e.g., `baseline_performance.json` —
    the n=20 perf baseline written by Task 1.5's freeze script).

    When `quick=True`, filter to `QUICK_SCENARIOS` for `--parity-quick`.
    """
    all_paths = sorted(
        p for p in FIXTURES_DIR.glob("*.json") if p.stem not in _NON_SCENARIO_STEMS
    )
    if not quick:
        return all_paths
    return [p for p in all_paths if p.stem in QUICK_SCENARIOS]


def load_fixture(path: Path) -> dict[str, Any]:
    """Read and parse a fixture JSON."""
    return json.loads(path.read_text(encoding="utf-8"))
