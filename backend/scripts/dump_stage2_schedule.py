"""
Stage2 완료 후 schedule_task 덤프 — 설비/시간 기준 PDF 1안 비교용.

Usage:
    python scripts/dump_stage2_schedule.py <run_label> <out_json_path>
"""

from __future__ import annotations
import sys
import json
import os
from pathlib import Path
from datetime import datetime

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
sys.path.insert(0, str(_BACKEND))
os.chdir(_BACKEND)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import settings


def main():
    run_label = sys.argv[1]
    out_path = sys.argv[2]

    db_url = settings.DATABASE_URL.replace(":6543/", ":5432/")
    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        connect_args={"options": "-c statement_timeout=300000"},
    )
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        q = text("""
            SELECT
                s.task_id, s.batch_id, s.equipment_code,
                s.start_datetime, s.end_datetime,
                s.setup_time_min,
                b.process_name, b.sq_mm2, b.core_count, b.spec_raw,
                b.batch_group, b.drum_count, b.drum_length_m, b.total_length_m,
                b.sales_order_id, b.customer_name, b.due_date,
                b.voltage, b.conductor_material, b.stranding_type, b.sheath_color
            FROM schedule_task s
            JOIN production_batch b ON s.batch_id = b.batch_id
            WHERE s.run_label = :rl
            ORDER BY s.equipment_code, s.start_datetime
        """)
        from decimal import Decimal

        rows = []
        for r in db.execute(q, {"rl": run_label}).mappings():
            rec = dict(r)
            for k, v in list(rec.items()):
                if hasattr(v, "isoformat"):
                    rec[k] = v.isoformat()
                elif isinstance(v, Decimal):
                    rec[k] = float(v)
            rows.append(rec)
        print(f"[dump] {len(rows)} schedule_task rows for run_label={run_label}")
        with open(out_path, "w") as f:
            json.dump(
                {
                    "run_label": run_label,
                    "dumped_at": datetime.now().isoformat(),
                    "tasks": rows,
                },
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        print(f"[dump] wrote {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
