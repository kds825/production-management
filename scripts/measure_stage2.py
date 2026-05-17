"""Phase 6 step 11 — Stage 2 wall-time 측정.

용도: lex Phase A skip + calendar holiday prefetch 효과 확인.

전략:
  1. 가장 최근 production_batch 가 있는 run_label 찾기.
  2. _purge_run_tasks 로 schedule_task 정리 + status='planned' reset (clean state).
  3. execute_stage2 직접 호출 (HTTP 안 거치고 pipeline_orchestrator 의 entry).
  4. stage2_wall_s / solve / validate / commit 분해 출력.

실행:
    cd backend && source venv/bin/activate
    python -m scripts.measure_stage2
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

# project root 를 sys.path 에 — backend.app.* import 가능하게.
_HERE = Path(__file__).resolve()
_BACKEND_ROOT = _HERE.parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_ROOT))

# 로그 출력 — auto_schedule 의 attempt path / wall-time 라인을 보기 위해.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
)


def _resolve_run_label(db) -> str:
    """가장 production_batch 가 많은 run_label 반환 (실 워크로드 측정용)."""
    from sqlalchemy import func

    from app.infrastructure.models.production_batch import ProductionBatch

    row = (
        db.query(
            ProductionBatch.run_label, func.count(ProductionBatch.batch_id).label("n")
        )
        .group_by(ProductionBatch.run_label)
        .order_by(func.count(ProductionBatch.batch_id).desc())
        .first()
    )
    if not row:
        raise RuntimeError("production_batch 가 비어있다 — 먼저 Stage 1 실행 필요")
    return row[0]


def _purge_for_clean_measurement(db, run_label: str) -> int:
    """schedule_task 정리 + status='planned' reset (idempotent measurement)."""
    from app.application.scheduling.greedy.auto_schedule import _purge_run_tasks
    from app.infrastructure.models.production_batch import ProductionBatch

    _purge_run_tasks(db, run_label)
    n_planned = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "planned",
        )
        .count()
    )
    return n_planned


def _noop_ai_starter(run_label: str) -> None:
    """AI background task 는 측정 대상이 아니므로 no-op."""
    pass


def main() -> int:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    from app.application.ingest.pipeline_orchestrator import execute_stage2
    from app.application.scheduling.greedy.auto_schedule import auto_schedule
    from app.application.validation.constraint_checker import validate_all
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        run_label = _resolve_run_label(db)
        n = _purge_for_clean_measurement(db, run_label)
        print(f"== Measurement target == run_label={run_label} planned_batches={n}")

        # 첫 호출 — cProfile 로 wrap (Phase 6 step 13 hotspot 식별).
        # CUMULATIVE 정렬, top 40 출력.
        import cProfile
        import io
        import pstats

        _prof = cProfile.Profile()
        t0 = time.perf_counter()
        _prof.enable()
        result_1 = execute_stage2(
            run_label=run_label,
            base_date_dt=None,
            optimizer="cpsat",
            db=db,
            auto_schedule_fn=auto_schedule,
            validate_all_fn=validate_all,
            ai_background_starter=_noop_ai_starter,
        )
        _prof.disable()
        wall_1 = time.perf_counter() - t0
        _buf = io.StringIO()
        pstats.Stats(_prof, stream=_buf).sort_stats("cumulative").print_stats(40)
        print("\n== cProfile top 40 by cumulative ==")
        print(_buf.getvalue())
        sched_1 = result_1["schedule"]
        print(
            f"\n== Call 1 == total_wall={wall_1:.2f}s "
            f"(solve={result_1.get('stage2_solve_wall_s')}s "
            f"validate={result_1.get('stage2_validate_wall_s')}s "
            f"commit={result_1.get('stage2_commit_wall_s')}s)"
        )
        print(
            f"   engine={sched_1.get('engine')} solver_mode={sched_1.get('solver_mode')} "
            f"lex_status={sched_1.get('lex_status')} retry_adopted={sched_1.get('retry_adopted')} "
            f"retry_skipped_reason={sched_1.get('retry_skipped_reason')}"
        )
        print(
            f"   total_tasks={sched_1.get('total_tasks')} violations={result_1.get('total_violations')}"
        )

        # 두 번째 호출 (re-invocation 가드 검증)
        t0 = time.perf_counter()
        result_2 = execute_stage2(
            run_label=run_label,
            base_date_dt=None,
            optimizer="cpsat",
            db=db,
            auto_schedule_fn=auto_schedule,
            validate_all_fn=validate_all,
            ai_background_starter=_noop_ai_starter,
        )
        wall_2 = time.perf_counter() - t0
        sched_2 = result_2["schedule"]
        print(
            f"\n== Call 2 (re-invocation) == total_wall={wall_2:.2f}s "
            f"(solve={result_2.get('stage2_solve_wall_s')}s)"
        )
        print(
            f"   engine={sched_2.get('engine')} solver_mode={sched_2.get('solver_mode')} "
            f"lex_status={sched_2.get('lex_status')}"
        )
        print(
            f"   total_tasks={sched_2.get('total_tasks')} violations={result_2.get('total_violations')}"
        )

        print("\n== Summary ==")
        print(f"  Call 1 stage2_wall_s = {wall_1:.2f}s")
        print(
            f"  Call 2 stage2_wall_s = {wall_2:.2f}s  (re-invocation should also be cpsat)"
        )
        if sched_2.get("engine") == "cp_sat":
            print("  ✓ re-invocation 회귀 가드 통과 — 두 번째도 cp_sat")
        else:
            print(f"  ✗ re-invocation 회귀 — 두 번째 engine={sched_2.get('engine')}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
