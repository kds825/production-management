"""Dump constraint_config rows for Phase 0 archive."""

import json
import sys
from datetime import datetime
from app.infrastructure.database import SessionLocal
from app.infrastructure.models.constraint_config import ConstraintConfig


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.query(ConstraintConfig).all()
        json.dump(
            {
                "exported_at": datetime.utcnow().isoformat() + "Z",
                "row_count": len(rows),
                "rows": [
                    {
                        k: getattr(r, k)
                        for k in [
                            "constraint_id",
                            "constraint_name",
                            "category",
                            "is_enabled",
                            "priority",
                            "impact_level",
                            "params_json",
                            "applicable_processes",
                            "implementation_type",
                            "notes",
                        ]
                    }
                    for r in rows
                ],
            },
            sys.stdout,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
