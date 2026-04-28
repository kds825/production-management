"""Pipeline orchestrator — Stage 1 + Stage 2 coordinator (Week 4 Task 4A.1).

Top-level entry that the HTTP route delegates to. Picks the engine branch
(``run_solver_stage`` for cpsat, ``run_greedy_stage`` for greedy), runs
constraint validation, commits the DB, and queues the AI background task.

Why DI of ``auto_schedule_fn`` / ``validate_all_fn`` / ``ai_background_starter``:

* ``auto_schedule`` is exposed as a *module attribute* on
  ``app.presentation.routes.plan_pipeline`` and monkeypatched by
  ``test_schedule_route_overlap`` (which raises ``SchedulerOverlapError``
  to verify the 200 + overlap_alert mapping). Importing it here directly
  would freeze the reference and defeat the patch.
* ``validate_all`` similarly needs to remain swappable from the route's
  module namespace for parity tests that may stub it in future.
* ``ai_background_starter`` is a callable that the route binds to
  ``_run_ai_background`` (which still owns the in-memory ``_ai_cache``
  PoC dict and threading.Lock). We don't move that cache here because the
  route's GET ``/stage2/{run_label}/ai-status`` endpoint reads it and
  duplicating the lock across modules would break atomicity.

Contract:
* SchedulerOverlapError is *propagated*, never caught here. The route's
  sync handler maps it to HTTP 200 + overlap_alert; the async job worker
  maps it to status="overlap_alert".
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.application.ingest import create_batches, detect_split_candidates
from app.infrastructure.parsers.erp_parser import parse_erp_file
from app.application.ingest.run_labeler import new_run_label
from app.application.ingest.stage1 import run_solver_stage
from app.application.ingest.stage2 import run_greedy_stage
from app.application.ingest.wip_matching import match_wip

logger = logging.getLogger(__name__)


def _purge_run_data(db: Session) -> None:
    """Stage 1 fresh-run 진입 시 기존 데이터 정리.

    재실행 중복을 막기 위해 audit_log / schedule_task / production_batch 를
    싹 비우고, sales_order 의 wip_id FK 참조를 null 화한 뒤 wip_inventory 도
    정리한다. sales_order 자체는 parse_erp_file 가 전체 삭제 후 재적재하므로
    여기서 건드리지 않는다.

    Why warm-up query: SQLite + SQLAlchemy session 의 첫 query 가 늦게
    바인딩되는 환경에서 이후 DELETE 가 dropped 되는 회귀를 방지하기 위한
    보험 (legacy 컨벤션 유지).
    """
    from app.infrastructure.models.schedule_task import ScheduleTask as ST

    db.query(func.count(ST.task_id)).scalar()  # warm up
    db.execute(text("DELETE FROM audit_log"))
    db.execute(text("DELETE FROM schedule_task"))
    # decision_feedback.batch_id → production_batch.batch_id (NO ACTION).
    # Phase 6 신설 테이블이 purge 순서에 빠져 있어 fresh-run 시 FK 위반이 났다.
    db.execute(text("DELETE FROM decision_feedback"))
    # Why: wip_inventory ↔ production_batch 가 양방향 FK 로 잡혀 있다.
    #   - wip_inventory.source_batch_id → production_batch.batch_id
    #   - production_batch.wip_matched_id → wip_inventory.wip_id
    # NO ACTION 정책이라 한쪽만 먼저 지우면 양쪽 모두 FK 위반이 난다. 따라서
    # production_batch.wip_matched_id 를 먼저 NULL 화하여 cycle 을 끊은 뒤
    # wip_inventory → production_batch 순으로 삭제한다. main 의 잘못된 순서를
    # 정정 (Week 9 통합 테스트에서 발견된 pre-existing 회귀).
    db.execute(text("UPDATE production_batch SET wip_matched_id = NULL"))
    db.execute(
        text(
            "UPDATE sales_order SET wip_id = NULL, use_wip = FALSE, "
            "wip_type = NULL, actual_length_m = NULL"
        )
    )
    db.execute(text("DELETE FROM wip_inventory"))
    db.execute(text("DELETE FROM production_batch"))
    db.commit()


def execute_stage1_ingest(
    *,
    erp_content: bytes,
    wip_content: bytes | None,
    parsed_from: date | None,
    parsed_to: date | None,
    split_gap_days: int,
    db: Session,
) -> dict[str, Any]:
    """Run the fresh ERP-ingest Stage 1 pipeline (HTTP /pipeline/stage1).

    Sequence:
      1. Purge prior run data (fresh-run 정책 — incremental 은 별도 경로).
      2. Allocate run_label.
      3. Parse ERP file into sales_order rows.
      4. Optionally parse WIP file + run match_wip.
      5. Build production_batch rows (date_from/date_to filtered).
      6. Detect split-candidates (after commit so flushed batches are visible).

    Why HTTP-typed exceptions here: the original route inlined every error
    into HTTPException(4xx/5xx) with localised Korean messages used by the
    front-end. Centralising preserves the wire contract verbatim.
    """
    _purge_run_data(db)
    run_label = new_run_label()

    # ── Step 1: ERP 파일 파싱 ────────────────────────────────────────────────
    if not erp_content:
        raise HTTPException(status_code=400, detail="ERP 파일이 비어 있습니다.")
    try:
        parse_result = parse_erp_file(erp_content, run_label, db)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"ERP 파일 파싱 실패: {exc}"
        ) from exc

    # ── Step 2: WIP 파일 파싱 + 매칭 ──────────────────────────────────────────
    wip_warnings: list[str] = []
    if wip_content:
        try:
            from app.infrastructure.parsers.wip_parser import parse_wip_file

            wip_parse = parse_wip_file(wip_content, db, run_label=run_label)
            wip_warnings.extend(wip_parse.get("warnings", []))
            if wip_parse["total"] > 0:
                wip_warnings.append(f"재공실사 {wip_parse['total']}건 등록 완료.")
        except Exception as exc:
            wip_warnings.append(f"재공 파일 파싱 실패: {exc}")

    try:
        wip_result = match_wip(run_label, db)
    except Exception as exc:
        wip_warnings.append(f"WIP 매칭 실패 (계속 진행): {exc}")
        wip_result = {"matched": 0, "skipped": 0, "details": []}

    # ── Step 3: production_batch 생성 ─────────────────────────────────────────
    try:
        batch_result = create_batches(
            run_label, db, date_from=parsed_from, date_to=parsed_to
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"배치 생성 실패: {exc}") from exc

    db.commit()

    # ── Step 4: 연선 그룹 분할 후보 감지 ──────────────────────────────────────
    # commit 이후에 실행해야 flush 된 배치가 쿼리에 반영된다.
    try:
        split_candidates = detect_split_candidates(
            run_label, db, gap_days=split_gap_days
        )
    except Exception as exc:
        logger.warning("[Stage1] 분할 후보 감지 실패 (계속 진행): %s", exc)
        split_candidates = []

    warnings = (
        parse_result.get("warnings", [])
        + wip_warnings
        + batch_result.get("warnings", [])
    )

    return {
        "run_label": run_label,
        "parse": parse_result,
        "wip": wip_result,
        "batches": batch_result,
        "warnings": warnings,
        "outsource_count": batch_result.get("outsource_count", 0),
        "split_candidates": split_candidates,
    }


def execute_stage2(
    run_label: str,
    base_date_dt: datetime | None,
    optimizer: str,
    db: Session,
    *,
    auto_schedule_fn: Callable[..., dict[str, Any]],
    validate_all_fn: Callable[..., list[Any]],
    ai_background_starter: Callable[[str], None],
) -> dict[str, Any]:
    """Run the chosen scheduling engine, validate, commit, kick AI background.

    sync ``/pipeline/stage2`` and async ``/pipeline/stage2/async`` share
    this implementation through the route's thin shim
    ``_execute_stage2_core``. Returns the response payload contract:

        {
          "run_label": str,
          "schedule": {... engine: "cpsat"|"greedy"|"greedy_fallback" ...},
          "violations": list,
          "total_violations": int,
          "overlap_alert": False,   # always False on success path; the
                                    # caller flips it to True when mapping
                                    # SchedulerOverlapError
        }
    """
    if optimizer == "greedy":
        schedule_result = run_greedy_stage(
            run_label, db, base_date_dt, auto_schedule_fn=auto_schedule_fn
        )
    else:
        # CP-SAT 경로도 auto_schedule 의 retry+validate 래퍼를 타도록 통합
        # (Fix P0-4A). CP-SAT 실패/타임아웃 시 내부에서 그리디로 폴백하고,
        # 겹침 감지 시 random_seed 를 바꿔가며 재시도한다.
        schedule_result = run_solver_stage(
            run_label, db, base_date_dt, auto_schedule_fn=auto_schedule_fn
        )

    violations = validate_all_fn(run_label, db)
    db.commit()

    # AI 분석을 백그라운드 스레드로 비동기 실행 — 응답을 블로킹하지 않음.
    # ai_background_starter 는 route 의 _run_ai_background 를 트리거하는
    # 어댑터 (cache mutation + thread.start) 이며 route 모듈에 in-memory
    # cache 가 살아 있으므로 여기서 직접 cache 를 만지지 않는다.
    ai_background_starter(run_label)

    return {
        "run_label": run_label,
        "schedule": schedule_result,
        "violations": violations,
        "total_violations": len(violations),
        "overlap_alert": False,
    }
