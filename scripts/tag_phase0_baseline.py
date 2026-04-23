"""Insert current constraint_config rows into constraint_config_history
with a Phase-0-initial marker.

Idempotent: checks for existing marker before inserting. Safe to re-run.

The Week 2 migration (spec §8b) will add `is_baseline`, `baseline_tag_name`,
`baseline_created_by`, `baseline_created_at`, `version_id` columns and will
retroactively flip `is_baseline=TRUE` on these marker rows.

Schema reality (2026-04-23):
  constraint_config_history is a narrow change-log table with columns:
    history_id, constraint_id, changed_at, changed_by,
    old_params_json, new_params_json

  It has NO `notes` column (contrary to the drafted Task 0.2 snippet in the
  plan). The marker string is therefore stored in `changed_by`, which is
  the only free-form string column available. The Week 2 retroactive UPDATE
  can match on `changed_by = 'PHASE0_INITIAL_20260423'` to flip
  `is_baseline=TRUE` and populate `baseline_tag_name='Phase 0 initial'`.

  `old_params_json` is left NULL — semantically correct for a baseline
  (no prior state). `new_params_json` carries each row's current
  `params_json` snapshot.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from app.infrastructure.database import SessionLocal
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.constraint_config_history import ConstraintConfigHistory


PHASE0_MARKER = "PHASE0_INITIAL_20260423"


def main() -> int:
    db = SessionLocal()
    try:
        # Idempotency guard: bail out if any row already carries the marker.
        # This is what makes the script safe to re-run.
        existing = (
            db.query(ConstraintConfigHistory)
            .filter(ConstraintConfigHistory.changed_by == PHASE0_MARKER)
            .first()
        )
        if existing is not None:
            print(f"Already tagged at {existing.changed_at.isoformat()}")
            return 0

        now = datetime.now(timezone.utc)
        rows = db.query(ConstraintConfig).all()
        for r in rows:
            db.add(
                ConstraintConfigHistory(
                    constraint_id=r.constraint_id,
                    changed_at=now,
                    changed_by=PHASE0_MARKER,
                    # Baseline: no prior state. Week 2 migration uses this as
                    # the canonical "Phase 0 initial" snapshot.
                    old_params_json=None,
                    # Coerce NULL → {} because new_params_json is NOT NULL.
                    new_params_json=r.params_json if r.params_json is not None else {},
                )
            )
        db.commit()
        print(f"Tagged {len(rows)} rows as {PHASE0_MARKER}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
