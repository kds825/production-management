"""
Stage2 직접 실행 스크립트 — Supabase pooler의 2분 statement_timeout 회피.

Why:
- Backend HTTP endpoint는 Supabase role-level `statement_timeout=2min` 제약 때문에 auto_schedule 실패.
- 이 스크립트는 session-local SET statement_timeout 을 키워 동일 로직을 실행한다.
- Supabase 에 직접 commit 하므로 프론트엔드 (/scheduler 등) 가 결과를 즉시 조회 가능.

Usage:
    source backend/venv/bin/activate
    cd backend
    python scripts/run_stage2_direct.py <run_label> [base_date_YYYYMMDD] [optimizer]
"""

from __future__ import annotations
import sys
import os
import json
from datetime import datetime
from pathlib import Path

# Ensure backend/app is importable
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
sys.path.insert(0, str(_BACKEND))
os.chdir(_BACKEND)

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.services.constraint_checker import validate_all
from app.services.schedule_optimizer import (  # noqa: F401
    _run_optimization_once,
    _purge_run_tasks,
)
from app.services.cp_sat_optimizer import cp_sat_schedule  # noqa: F401


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_stage2_direct.py <run_label> [base_date] [optimizer]")
        return 2

    run_label = sys.argv[1]
    base_date_str = sys.argv[2] if len(sys.argv) > 2 else None
    optimizer = sys.argv[3] if len(sys.argv) > 3 else "cpsat"

    base_dt = None
    if base_date_str:
        base_dt = datetime.strptime(base_date_str, "%Y%m%d").replace(hour=8, minute=0)

    # Supabase Transaction Pooler(6543)은 트랜잭션 끝날 때마다 세션 상태를 리셋하므로
    # `SET statement_timeout` 이 유지되지 않는다 → 2분 role-level cap 에 걸림.
    # Session Pooler(5432)로 교체 후 session-local SET 을 유지시킨다.
    db_url = settings.DATABASE_URL.replace(":6543/", ":5432/")
    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        pool_recycle=30,
        pool_size=1,
        max_overflow=0,
        connect_args={
            "options": "-c statement_timeout=600000",  # 10min in ms
            "keepalives": 1,
            "keepalives_idle": 10,
            "keepalives_interval": 5,
            "keepalives_count": 3,
        },
    )

    @event.listens_for(engine, "connect")
    def _set_timeout(dbapi_conn, conn_record):
        cur = dbapi_conn.cursor()
        cur.execute("SET statement_timeout = '10min';")
        cur.close()

    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        print(
            f"[stage2-direct] run_label={run_label} base_date={base_dt} optimizer={optimizer}"
        )
        t0 = datetime.now()

        # 검증 라운드 모드: 재시도 래퍼(auto_schedule) 우회, 한 번만 실행 후 강제 commit.
        # Why: auto_schedule 은 겹침이 1건이라도 있으면 3회 retry 후 rollback 해서
        # schedule_task 0 개로 끝난다. 이번 라운드는 PDF 1안과의 시각적 비교가 목적이라,
        # "불완전하지만 보이는" 결과를 commit 해야 의미있는 diff 가 가능하다.
        _purge_run_tasks(db, run_label)

        if optimizer == "cpsat":
            result = cp_sat_schedule(run_label, db, random_seed=0, base_date=base_dt)
            if result.get("solver_status") not in ("OPTIMAL", "FEASIBLE"):
                print(
                    f"[stage2-direct] CP-SAT {result.get('solver_status')} → greedy 폴백"
                )
                _purge_run_tasks(db, run_label)
                result = _run_optimization_once(run_label, db, base_date=base_dt)
        else:
            result = _run_optimization_once(run_label, db, base_date=base_dt)

        violations = validate_all(run_label, db)
        overlap_hits = [v for v in violations if v.get("constraint_id") == "overlap"]
        print(
            f"[stage2-direct] validate_all → {len(violations)} violations "
            f"({len(overlap_hits)} overlap)"
        )
        if overlap_hits:
            print("[stage2-direct] sample overlaps (first 5):")
            for v in overlap_hits[:5]:
                print(f"  - {v}")

        # 강제 commit — 겹침이 있더라도 schedule_task 를 남겨야 PDF 비교가 가능.
        db.commit()
        elapsed = (datetime.now() - t0).total_seconds()

        out = {
            "run_label": run_label,
            "elapsed_sec": elapsed,
            "schedule_summary": {
                k: v
                for k, v in result.items()
                if k
                in (
                    "scheduled_count",
                    "unscheduled_count",
                    "solver_status",
                    "wip_skipped",
                    "total_batches",
                    "makespan",
                    "engine",
                )
            },
            "violation_count": len(violations),
            "overlap_count": len(overlap_hits),
            "violations_head": overlap_hits[:10],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    except Exception as exc:
        db.rollback()
        import traceback

        traceback.print_exc()
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
