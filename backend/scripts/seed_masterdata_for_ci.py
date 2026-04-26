"""Seed the minimum masterdata needed for parity harness to run in CI.

For CI use only — loads equipment_master / speed_master / constraint_config /
drum_lot_master rows that the 11 parity fixtures live-read through
`_fixture_to_solver_input` (see backend/tests/_parity_helpers.py module
docstring on the "masterdata live-read" contract).

Why this exists
---------------
The parity seed scripts in ``backend/scripts/seed_parity_scenarios/`` only
write **scenario-specific** rows (sales_order, production_batch, narrow
operation_calendar rows). They deliberately do NOT touch masterdata
tables — the parity contract is that masterdata is frozen outside the
harness and any drift changes the hash (that's a feature, not a bug).

In dev this is fine: masterdata is already populated via Supabase. In CI
we boot a throwaway Postgres from a fresh ``alembic upgrade head`` with
zero rows, so ``_fixture_to_solver_input`` would get back an empty
``equipment_list`` / ``speed_map`` and every parity scenario would crash
at solver input build.

Source of truth
---------------
The fixtures themselves carry a complete masterdata snapshot (captured
by Task 1.3's ``capture_parity_fixture.py`` at the time Task 1.5 froze
the hashes). Fixture ``01_nominal.json`` is authoritative for the set of
equipment / speed / constraint rows 9 of the 11 scenarios share;
scenarios 10a/10b mutate constraint_config on the fly, so they do not
need additional seed beyond what 01 provides.

Rather than hand-curate masterdata and risk drift, we read the snapshot
straight out of ``01_nominal.json``. The script is therefore pure-CI
concern and adds no new file to keep in sync — any ``parity-update:``
commit that rehashes fixtures will also pick up here.

Idempotency
-----------
Each masterdata table is cleared and reinserted. Safe to re-run. Does
NOT touch ``production_batch`` / ``sales_order`` / ``schedule_task`` —
those are owned by scenario seeds and test fixtures.

NEVER run against Supabase — this resets masterdata tables. The
``DATABASE_URL`` check below refuses to run if it points at
``supabase.co``. CI sets ``DATABASE_URL`` to the throwaway Postgres.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy.orm import Session

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_PATH = _BACKEND_ROOT / "tests" / "fixtures" / "parity" / "01_nominal.json"


def _guard_not_supabase() -> None:
    """Refuse to mutate Supabase-hosted databases.

    Dev/runtime is Supabase-native. This script targets the GitHub Actions
    service-container Postgres (CI-only, ephemeral, ``localhost:5432`` from
    inside the runner). Any DATABASE_URL carrying ``supabase.co`` means the
    caller mis-wired env and is about to wipe real masterdata.
    """
    url = os.environ.get("DATABASE_URL", "")
    if "supabase.co" in url:
        print(
            f"[seed-ci] REFUSING: DATABASE_URL points at Supabase ({url!r}). "
            "This script resets masterdata tables and is CI-only.",
            file=sys.stderr,
        )
        sys.exit(2)


def _load_snapshot() -> dict:
    """Load masterdata snapshot from the canonical fixture."""
    if not _FIXTURE_PATH.exists():
        print(
            f"[seed-ci] FATAL: canonical fixture missing: {_FIXTURE_PATH}. "
            "Did Task 1.5 rename the baseline fixture? If so, update "
            "_FIXTURE_PATH here.",
            file=sys.stderr,
        )
        sys.exit(3)
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))["solver_input"]


def seed_masterdata(db: Session) -> dict[str, int]:
    """Clear-and-reinsert parity masterdata. Returns per-table rowcount."""
    # Lazy imports: at test-collection time ``app.infrastructure.*`` may
    # not yet be on the path if the caller is running from outside
    # backend/. Local import keeps ``python -c "import seed_masterdata_for_ci"``
    # from exploding in the smoke test below.
    from app.infrastructure.models.constraint_config import ConstraintConfig
    from app.infrastructure.models.drum_lot_master import DrumLotMaster
    from app.infrastructure.models.equipment_master import EquipmentMaster
    from app.infrastructure.models.speed_master import SpeedMaster

    si = _load_snapshot()

    # ── equipment_master ─────────────────────────────────────────────
    db.query(EquipmentMaster).delete(synchronize_session=False)
    db.flush()
    for eq in si["equipment_list"]:
        db.add(EquipmentMaster(**eq))

    # ── speed_master ─────────────────────────────────────────────────
    # Fixture speed_map is a list of ``[code, cross_section, row_dict]``
    # triples. We only need row_dict. speed_id in the snapshot is a
    # zero-based synthetic — drop it and let autoincrement assign fresh.
    db.query(SpeedMaster).delete(synchronize_session=False)
    db.flush()
    for _code, _cs, row in si["speed_map"]:
        payload = {k: v for k, v in row.items() if k != "speed_id"}
        db.add(SpeedMaster(**payload))

    # ── constraint_config ────────────────────────────────────────────
    # Fixture only carries ``{constraint_id: params_json}`` — other
    # columns (constraint_name, category, priority) are not read by
    # ``ConstraintParams.load`` (see app/services/constraint_params.py)
    # so we supply placeholder values that satisfy NOT NULL columns.
    # Admin UI & unrelated tests don't run in this CI workflow.
    db.query(ConstraintConfig).delete(synchronize_session=False)
    db.flush()
    for cid, params in si["constraint_params"].items():
        db.add(
            ConstraintConfig(
                constraint_id=cid,
                constraint_name=f"parity-ci:{cid}",
                category="ci-seed",
                is_enabled=True,
                priority=50,
                impact_level="중",
                params_json=params or {},
                applicable_processes=[],
                implementation_type="auto",
                notes="Seeded for CI by seed_masterdata_for_ci.py (parity harness).",
            )
        )

    # ── drum_lot_master ──────────────────────────────────────────────
    # Fixture ``sq_to_wire_d`` = ``{cross_section: wire_diameter}``.
    # _fixture_to_solver_input reads only ``cross_section`` + ``wire_diameter``
    # (see _parity_helpers.py line ~275), so other columns stay defaulted.
    db.query(DrumLotMaster).delete(synchronize_session=False)
    db.flush()
    for cs, wd in si["sq_to_wire_d"].items():
        db.add(DrumLotMaster(cross_section=float(cs), wire_diameter=float(wd)))

    db.commit()

    return {
        "equipment_master": len(si["equipment_list"]),
        "speed_master": len(si["speed_map"]),
        "constraint_config": len(si["constraint_params"]),
        "drum_lot_master": len(si["sq_to_wire_d"]),
    }


def main() -> int:
    _guard_not_supabase()

    # Late import so ``-c "import ..."`` smoke tests don't need a DB.
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        counts = seed_masterdata(db)
    finally:
        db.close()

    for table, n in counts.items():
        print(f"[seed-ci] {table}: {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
