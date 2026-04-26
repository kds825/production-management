"""Task 1.3 — capture parity fixtures via `build_solver_input`.

Runs each of the 10 parity seed scripts (11 fixtures when counting scenario 10's
`all`/`none` variants), invokes `build_solver_input(db, RUN_LABEL)`, and
serializes the resulting `SolverInput` to JSON under
`backend/tests/fixtures/parity/`. The `expected_*` fields are left blank —
Task 1.5 will freeze them once ground-truth solver outputs are captured.

Why this exists (separate from the seeds): the seeds only write rows. They do
NOT produce the immutable input snapshot that the parity harness needs. The
capture step is the bridge "DB rows → SolverInput JSON".

Determinism constraints
-----------------------
Running `python capture_parity_fixture.py --all` twice back-to-back must
produce a `git diff backend/tests/fixtures/parity/*.json` of zero bytes.
That is THE smoke test. Non-determinism sources addressed:

1. **Autoincrement PK drift (Strategy B — remap).**
   `production_batch.batch_id`, `speed_master.speed_id`,
   `drum_lot_master.drum_id`, `wip_inventory.wip_id` are autoincrement PKs.
   Each re-run of the seed produces different values (Supabase sequence
   advances monotonically even when prior rows are deleted). We therefore
   *remap* these IDs to deterministic 0-indexed integers AFTER loading but
   BEFORE serializing.

   Why remap (Strategy B) and not drop (Strategy A):
   `cp_sat_schedule`'s override path (cp_sat_optimizer.py:1219) does
     `key = b.batch_group or f"_single_{b.batch_id}"`
   i.e. `batch_id` participates in group-key generation when `batch_group`
   is empty. Dropping it would produce different group keys on the first
   parity run (Task 1.4) vs. subsequent runs of the same fixture. Remapping
   to a stable ordinal preserves usability downstream.

   The remap order mirrors `build_solver_input`'s final batch sort
   (process-order → batch_seq → due → priority → -sq_mm2), so the remap is
   deterministic given a fixed seed payload.

2. **Timestamp columns (`created_at`, `updated_at`).**
   These are populated by SQLAlchemy defaults (`datetime.utcnow` /
   `_utcnow()`) at INSERT time, so each seed re-run produces fresh
   timestamps even if every other column is byte-identical. We strip them
   from the serialized output — they are metadata noise irrelevant to
   parity.

3. **dict ordering / list ordering.**
   JSON is dumped with `sort_keys=True` and ORM-row lists are pre-sorted by
   their natural keys before serialization.

4. **Decimal / date / datetime.**
   Decimals are converted to `float` (SolverInput never compares these by
   bit-identity — only by semantic value); datetimes and dates are rendered
   ISO-8601 strings.

Supabase-state hygiene
----------------------
Every seed script wipes its own run_label-scoped rows in `_reset()` at the
top of `seed(db)`. After capture we call `_reset()` again to leave the DB in
pre-capture state — parity fixture rows do NOT linger in Supabase. The only
globally-visible side effect is: autoincrement sequences advance by ~50
values per full capture run (acceptable — these sequences are ~2^31 deep).

Escalation policy
-----------------
If `build_solver_input` raises for any scenario, STOP immediately and
surface the traceback. Do not write a partial fixture. A scenario that
cannot even *build* the input is a scenario misdesign — orchestrator decides.

Usage
-----
    # Capture all 11 fixtures (default):
    python backend/scripts/capture_parity_fixture.py

    # Capture only scenario 04:
    python backend/scripts/capture_parity_fixture.py --scenario 04

    # Capture only scenario 10 (both variants):
    python backend/scripts/capture_parity_fixture.py --scenario 10
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

# Backend path setup so `app.*` imports work regardless of invocation cwd.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.domain.constants import PROCESS_ORDER  # noqa: E402
from app.infrastructure.database import SessionLocal  # noqa: E402
from app.application.scheduling.cp_sat.input_builder import (  # noqa: E402
    SolverInput,
    build_solver_input,
)

_FIXTURES_DIR = _BACKEND_ROOT / "tests" / "fixtures" / "parity"
_SEEDS_PKG = "scripts.seed_parity_scenarios"

# (scenario_filename_stem, output_fixture_basename, variant_kwarg_or_None)
#
# Note: scenario 10 produces TWO fixtures (`10a_all_constraints`,
# `10b_none_constraints`) from a single seed module by varying the `variant`
# argument. All other scenarios map 1:1.
_SCENARIOS: list[tuple[str, str, str | None]] = [
    ("01_nominal", "01_nominal", None),
    ("02_past_due_skew", "02_past_due_skew", None),
    ("03_urgent_reschedule", "03_urgent_reschedule", None),
    ("04_wip_match", "04_wip_match", None),
    ("05_sheath_color_chain", "05_sheath_color_chain", None),
    ("06_stage1_stage2_handoff", "06_stage1_stage2_handoff", None),
    ("07_calendar_edge", "07_calendar_edge", None),
    ("08_capacity_overflow", "08_capacity_overflow", None),
    ("09_single_batch", "09_single_batch", None),
    ("10_all_vs_none_constraints", "10a_all_constraints", "all"),
    ("10_all_vs_none_constraints", "10b_none_constraints", "none"),
    # Phase 3 step 6 (target.md §4): lex_min_time 분기 검증용 시나리오.
    # 12: 모든 due 충족 (lex Phase A 가 T* = 0 반환).
    # 13: 모든 due past-due (lex Phase A 가 T* > 0 반환 → Phase B 실행).
    ("12_all_due_met", "12_all_due_met", None),
    ("13_past_due_forced", "13_past_due_forced", None),
]

# Columns stripped from every serialized ORM row. These are either
# non-deterministic (timestamp defaults) or autoincrement PKs we remap
# elsewhere (`batch_id` is handled separately so it CAN be kept as the
# remapped value).
_STRIP_COLUMNS: frozenset[str] = frozenset({"created_at", "updated_at"})


# ────────────────────────────────────────────────────────────────────────────
# Serialization helpers
# ────────────────────────────────────────────────────────────────────────────


def _jsonify_scalar(value: Any) -> Any:
    """Convert one column value to a JSON-safe scalar.

    - `Decimal` → `float` (SolverInput does not compare by bit-identity).
    - `datetime` / `date` → ISO-8601 string.
    - `None`/`str`/`int`/`float`/`bool`/`list`/`dict` → passthrough (JSONB
      columns like `params_json` / `applicable_processes` already decode to
      native Python).
    - Anything else → `str(value)` fallback.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonify_scalar(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonify_scalar(v) for k, v in value.items()}
    return str(value)


def _orm_to_dict(
    row: Any, *, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Project an ORM row to a plain dict of column → JSON-safe value.

    Column list is pulled from `__table__.columns` so we get a deterministic
    schema-ordered projection (SQLAlchemy preserves declaration order).
    `_STRIP_COLUMNS` are excluded. `overrides` lets callers replace specific
    column values (used for `batch_id` remapping).
    """
    overrides = overrides or {}
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        name = col.name
        if name in _STRIP_COLUMNS:
            continue
        if name in overrides:
            out[name] = _jsonify_scalar(overrides[name])
        else:
            out[name] = _jsonify_scalar(getattr(row, name))
    return out


def _batch_sort_key(b: Any) -> tuple:
    """Stable deterministic sort for batches (mirrors build_solver_input §1)."""
    return (
        PROCESS_ORDER.get(b.process_name, 50),
        b.batch_seq or 0,
        b.due_date.isoformat() if b.due_date else "9999-12-31",
        b.customer_priority or 99,
        -(float(b.sq_mm2 or 0)),
        # Tiebreaker: sales_order_id/line to guarantee total order even if
        # upstream sort left two rows in insertion order.
        b.sales_order_id or "",
        b.sales_order_line or 0,
    )


def _serialize_solver_input(si: SolverInput) -> dict[str, Any]:
    """Turn a `SolverInput` into a fully JSON-safe dict.

    Strategy B (remap autoincrement PKs):
      - `batch_id` → 0..N-1 in the batches list order.
      - `speed_id` → 0..N-1 in sorted speed_master order.
      - `drum_id` is only inside `sq_to_wire_d` (already projected to
        int-keyed dict) — no remap needed.
      - `wip_id` does not appear on any SolverInput field directly — it's
        indirectly referenced via `ProductionBatch.wip_matched_id` which
        is a FK into wip_inventory. WIP rows for scenario 04 use a fixed
        PK (_PARITY_WIP_ID=990404), so that column is already stable.

    Other non-PK fields (`equipment_code`, `constraint_id`, etc.) are stable
    natural keys — no remap needed.
    """
    # ── batches: sort deterministically then remap batch_id ───────────────
    sorted_batches = sorted(si.batches, key=_batch_sort_key)
    batches_out: list[dict[str, Any]] = []
    for new_id, b in enumerate(sorted_batches):
        batches_out.append(_orm_to_dict(b, overrides={"batch_id": new_id}))

    # ── equipment_list: sort by equipment_code (natural PK) ───────────────
    eq_list = sorted(si.equipment_list, key=lambda e: e.equipment_code)
    equipment_list_out = [_orm_to_dict(e) for e in eq_list]

    # ── equipment_by_process: sort dict keys, sort each list by eq_code ───
    ebp_out: dict[str, list[dict[str, Any]]] = {}
    for proc in sorted(si.equipment_by_process.keys()):
        ebp_out[proc] = [
            _orm_to_dict(e)
            for e in sorted(
                si.equipment_by_process[proc], key=lambda e: e.equipment_code
            )
        ]

    # ── speed_map: list of [eq_code, cross_section, serialized_speed_row],
    #    sorted by (eq_code, cross_section). Each speed_master row has
    #    `speed_id` (autoincrement) → remapped to its ordinal here. ──────
    speed_map_items = sorted(
        si.speed_map.items(), key=lambda kv: (kv[0][0], float(kv[0][1]))
    )
    speed_map_out: list[list[Any]] = []
    for new_speed_id, ((eq_code, cs), sr) in enumerate(speed_map_items):
        speed_map_out.append(
            [
                eq_code,
                float(cs),
                _orm_to_dict(sr, overrides={"speed_id": new_speed_id}),
            ]
        )

    # ── color_setup_map: already dict[str, float|None] — just sort keys ──
    color_setup_out = {
        k: _jsonify_scalar(v) for k, v in sorted(si.color_setup_map.items())
    }

    # ── constraint_params: dataclass `ConstraintParams` → `by_id` dict ──
    cp_out = {
        cid: {k: _jsonify_scalar(v) for k, v in params.items()}
        for cid, params in sorted(si.constraint_params.by_id.items())
    }

    # ── sq_to_wire_d: int keys. JSON dumps int keys as strings, so we
    #    pre-stringify to make `sort_keys=True` produce the same order as
    #    Python dict-sort on ints (numerical). Use zero-padded width so
    #    lexicographic == numeric for the SQ range we care about (1..999). ──
    sq_to_wire_d_out = {
        str(k): _jsonify_scalar(v) for k, v in sorted(si.sq_to_wire_d.items())
    }

    return {
        "base_date": si.base_date.isoformat(),
        "wip_skipped": int(si.wip_skipped),
        "welding_min": float(si.welding_min),
        "batches": batches_out,
        "equipment_list": equipment_list_out,
        "equipment_by_process": ebp_out,
        "speed_map": speed_map_out,
        "color_setup_map": color_setup_out,
        "constraint_params": cp_out,
        "sq_to_wire_d": sq_to_wire_d_out,
    }


# ────────────────────────────────────────────────────────────────────────────
# Capture flow
# ────────────────────────────────────────────────────────────────────────────


def _import_seed(module_stem: str):
    """Import `backend.scripts.seed_parity_scenarios.<stem>` and return it."""
    return importlib.import_module(f"{_SEEDS_PKG}.{module_stem}")


def _capture_one(
    db: Session,
    *,
    seed_module_stem: str,
    fixture_basename: str,
    variant: str | None,
) -> Path:
    """Seed → build_solver_input → serialize → write JSON → _reset.

    Returns the written fixture path. Raises on any failure (no partial
    writes — we never trap `build_solver_input` exceptions silently).
    """
    seed_mod = _import_seed(seed_module_stem)
    run_label = seed_mod.RUN_LABEL

    # 1. seed (seeds call db.commit internally; that's fine — we clean up at
    #    the end of this function via _reset).
    if variant is None:
        seed_mod.seed(db)
    else:
        seed_mod.seed(db, variant=variant)

    # 2. build SolverInput. Let exceptions propagate.
    solver_input = build_solver_input(db, run_label)

    # 3. serialize to the fixture envelope.
    payload = {
        "scenario_id": fixture_basename,
        "run_label": run_label,
        "variant": variant,
        "solver_input": _serialize_solver_input(solver_input),
        # Expected-output fields — filled by Task 1.5 once n=20 ground truth
        # is captured. Left blank in Task 1.3.
        "expected_output_hash": "",
        "expected_solver_status": "",
        "expected_objective_value": None,
    }

    out_path = _FIXTURES_DIR / f"{fixture_basename}.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 4. cleanup — each seed exposes a module-private `_reset` we invoke to
    #    remove the run_label-scoped rows. Scenario 10 has _reset_payload +
    #    _reset_constraints; invoke both.
    _invoke_cleanup(db, seed_mod)

    return out_path


def _invoke_cleanup(db: Session, seed_mod) -> None:
    """Call the seed module's reset function(s) and commit cleanup.

    Why commit here: the seed already committed its rows, so the outer
    session sees them as "real". We must issue an explicit DELETE + COMMIT
    to restore Supabase to its pre-capture state.

    Why `rollback()` first: `build_solver_input` mutates `batch.status =
    "wip_complete"` in-place for WIP-skipped batches (scenario 04). Those
    mutations are pending in the session (post-seed-commit, pre-flush). If
    we let the seed's `_reset()` call `db.flush()` on its DELETEs while
    those mutations are still pending, SQLAlchemy tries to UPDATE a row the
    DELETE already removed → `StaleDataError`. `rollback()` drops the
    in-memory pending changes without touching the durable seed rows
    (which were committed before `build_solver_input` was called).
    """
    db.rollback()
    if hasattr(seed_mod, "_reset"):
        seed_mod._reset(db)
    # Scenario 10 has a different reset API.
    if hasattr(seed_mod, "_reset_payload"):
        seed_mod._reset_payload(db)
    if hasattr(seed_mod, "_reset_constraints"):
        seed_mod._reset_constraints(db)
    db.commit()


def capture_all(db: Session) -> list[Path]:
    """Capture every scenario (incl. 10a/10b). Returns written paths."""
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for seed_stem, fixture_name, variant in _SCENARIOS:
        p = _capture_one(
            db,
            seed_module_stem=seed_stem,
            fixture_basename=fixture_name,
            variant=variant,
        )
        n_batches = _peek_batch_count(p)
        print(f"[ok] {fixture_name}.json  ({n_batches} batches, variant={variant!r})")
        written.append(p)
    return written


def capture_one_by_index(db: Session, scenario_idx: str) -> list[Path]:
    """Capture only scenarios whose seed filename starts with `scenario_idx`.

    e.g. `--scenario 10` captures both `10a` and `10b`. `--scenario 04`
    captures the single `04_wip_match` fixture.
    """
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    matches = [s for s in _SCENARIOS if s[0].startswith(f"{scenario_idx}_")]
    if not matches:
        raise SystemExit(
            f"No scenario matches prefix '{scenario_idx}_'. "
            f"Known seeds: {sorted({s[0] for s in _SCENARIOS})}"
        )
    written: list[Path] = []
    for seed_stem, fixture_name, variant in matches:
        p = _capture_one(
            db,
            seed_module_stem=seed_stem,
            fixture_basename=fixture_name,
            variant=variant,
        )
        print(f"[ok] {fixture_name}.json")
        written.append(p)
    return written


def _peek_batch_count(path: Path) -> int:
    """Cheap sanity helper for CLI output."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data["solver_input"]["batches"])
    except Exception:
        return -1


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Capture parity fixtures from seed scripts via build_solver_input. "
            "Default: --all (all 11 fixtures)."
        )
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--all",
        action="store_true",
        help="Capture every scenario (default behavior).",
    )
    g.add_argument(
        "--scenario",
        type=str,
        default=None,
        metavar="NN",
        help="Capture only scenarios matching this 2-digit prefix (e.g. '04').",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    db = SessionLocal()
    try:
        if args.scenario:
            written = capture_one_by_index(db, args.scenario)
        else:
            written = capture_all(db)
    finally:
        db.close()

    print(f"\nwrote {len(written)} fixture(s) to {_FIXTURES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
