"""Stage 1 파이프라인 API — ERP 업로드 → 작업지시서 생성 → Excel 다운로드"""

import logging
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.exceptions import SchedulerOverlapError
from app.infrastructure.database import SessionLocal, get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory
from app.application.ingest import (
    create_batches,
    detect_split_candidates,
    execute_auto_splits,
    format_spec_display,
)
from app.application.validation.constraint_checker import validate_all  # noqa: F401 — used in stage2
from app.infrastructure.exporters.excel_exporter import export_plan
from app.application.ingest.run_labeler import (  # noqa: F401 — re-export for tests
    new_run_label as _alloc_run_label,
    parse_base_date_yyyymmdd,
    parse_date_yyyymmdd,
)
from app.application.ingest.pipeline_orchestrator import (  # noqa: F401
    execute_stage1_ingest,
    execute_stage2,
)
from app.application.ingest.stage1 import run_solver_stage  # noqa: F401
from app.application.ingest.stage2 import run_greedy_stage  # noqa: F401

# Public re-export under the helper's canonical name (kept importable from
# the route module so callers / tests can reach it as plan_pipeline.new_run_label
# without going through services.pipeline). Aliased above to avoid colliding
# with the local variable named ``new_run_label`` inside run_stage1_update().
new_run_label = _alloc_run_label  # noqa: F811 — intentional re-export alias
from app.application.scheduling.greedy.auto_schedule import auto_schedule  # noqa: F401, E402 — alias 후 import
from app.application.ingest.wip_matching import match_wip  # noqa: E402
from app.application.ingest.wip_promotion import _promote_expected_to_estimated  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["파이프라인"])


# ---------------------------------------------------------------------------
# AI 분석 결과 인메모리 캐시 — PoC 단계용 단순 dict
# key: run_label, value: {"status": "pending"|"done"|"error", "summary": {...}}
# ---------------------------------------------------------------------------
_ai_cache: dict[str, dict] = {}
_ai_cache_lock = threading.Lock()


def _run_ai_background(run_label: str) -> None:
    """백그라운드 스레드에서 AI 배치 요약을 생성하여 캐시에 저장한다.

    FastAPI BackgroundTasks는 응답 전송 후 실행되므로, request-scoped DB 세션이
    이미 닫혀 있다. 따라서 SessionLocal()로 독립 세션을 생성한다.
    """
    db = SessionLocal()
    try:
        from app.application.decisions.summarize_run import generate_batch_summary_sync

        result = generate_batch_summary_sync(run_label, db)
        with _ai_cache_lock:
            _ai_cache[run_label] = {"status": "done", "summary": result}
        logger.info("[AI Background] run_label=%s 분석 완료", run_label)
    except Exception as exc:
        with _ai_cache_lock:
            _ai_cache[run_label] = {"status": "error", "error": str(exc)}
        logger.warning("[AI Background] run_label=%s 분석 실패: %s", run_label, exc)
    finally:
        db.close()


@router.post("/stage1", summary="ERP 업로드 → 작업지시서 생성")
async def run_stage1(
    erp_file: UploadFile = File(..., description="ERP 수주 파일 (.xls)"),
    wip_file: UploadFile | None = File(None, description="재공 재고 파일 (선택)"),
    date_from: str | None = Form(None, description="납기 시작일 (YYYYMMDD)"),
    date_to: str | None = Form(None, description="납기 종료일 (YYYYMMDD)"),
    split_gap_days: int = Form(
        3, description="연선 그룹 분할 후보 납기 간격 임계값 (일)"
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Stage 1 파이프라인 실행 (HTTP 어댑터 — 파싱·UploadFile.read() 만 담당).

    Pipeline 본체는 ``services.pipeline.orchestrator.execute_stage1_ingest`` 에
    이관되었다. 이 핸들러는 (a) UploadFile 을 await-read 하고 (b) 폼
    파라미터를 파싱한 뒤 (c) 결과를 그대로 반환한다.

    Returns:
        run_label, 파싱 결과, WIP 매칭 결과, 배치 생성 결과, 통합 경고 목록,
        split_candidates (분할 후보 연선 그룹 목록)
    """
    erp_content = await erp_file.read()
    wip_content = await wip_file.read() if wip_file else None
    parsed_from = parse_date_yyyymmdd(date_from, field_name="date_from")
    parsed_to = parse_date_yyyymmdd(date_to, field_name="date_to")

    return execute_stage1_ingest(
        erp_content=erp_content,
        wip_content=wip_content,
        parsed_from=parsed_from,
        parsed_to=parsed_to,
        split_gap_days=split_gap_days,
        db=db,
    )


@router.post("/stage1/update", summary="Stage 1 증분/전체 업데이트 (Freeze & Rebuild)")
async def run_stage1_update(
    erp_file: UploadFile = File(..., description="ERP 수주 파일 (.xls/.xlsx)"),
    wip_file: UploadFile | None = File(None, description="재공 재고 파일 (선택)"),
    upload_mode: str = Form(..., description="incremental 또는 full"),
    parent_run_label: str | None = Form(
        None, description="기존 계획 실행의 run_label (미지정 시 최신 run 자동 감지)"
    ),
    split_gap_days: int = Form(
        3, description="연선 그룹 분할 후보 납기 간격 임계값 (일)"
    ),
    base_date: str | None = Form(
        None,
        description=(
            "기준일자 (YYYY-MM-DD). Stage 1에서는 필터가 아니라 기록/Stage 2 "
            "자동배열 앵커 용도. frozen 기준은 상태(status)만 사용: "
            "in_progress / completed / wip_complete 만 보존, 나머지 "
            "planned / scheduled 는 업데이트된 수주로 재생성."
        ),
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Stage 1 증분/전체 업데이트 — 신규 run_label 발급 + Freeze 복제.

    이전 설계와의 차이:
      - parent_run_label 을 재사용하지 않고, 매 호출마다 new_run_label (타임스탬프)
        을 새로 발급한다. 이전 run 의 ProductionBatch / ScheduleTask 는
        그대로 보존 → 버전 비교 가능.
      - frozen (in_progress / completed / wip_complete) 배치와 그 수주의 다른 공정
        배치, 해당 ScheduleTask 를 new_run_label 로 복제 (parent_run_label 기록).
      - 중복 배치 방지: frozen_order_keys 는 status-only 로 계산 (batch_seq 필터 제거).
        create_batches 가 frozen_order_keys 를 수신해 해당 수주는 재배치하지 않는다.
      - SalesOrder 의 PK 는 (order_id, order_line) 이라 run_label 버전 불가.
        비동결 SalesOrder 는 new_run_label 로 forward-roll (UPDATE) 하고, 동결
        SalesOrder 는 parent run_label 을 그대로 유지해 list_runs 외주 집계를 보존.

    upload_mode:
      - "incremental": 기존 수주를 유지하고 새 수주만 추가
      - "full": 동결 수주 외 전부 삭제 후 새 파일로 교체
    """
    # ── 입력 검증 ─────────────────────────────────────────────────────────────
    if upload_mode not in ("incremental", "full"):
        raise HTTPException(
            status_code=400,
            detail=f"upload_mode는 'incremental' 또는 'full'이어야 합니다: {upload_mode}",
        )

    # parent_run_label 자동 감지 — 미지정 시 최신 run_label 사용
    if not parent_run_label:
        latest = (
            db.query(ProductionBatch.run_label)
            .order_by(ProductionBatch.created_at.desc())
            .first()
        )
        parent_run_label = latest[0] if latest else None

    # parent_run_label에 해당하는 배치가 존재하는지 확인
    existing_count = 0
    if parent_run_label:
        existing_count = (
            db.query(func.count(ProductionBatch.batch_id))
            .filter(ProductionBatch.run_label == parent_run_label)
            .scalar()
        )
    if not existing_count and upload_mode == "incremental":
        raise HTTPException(
            status_code=404,
            detail="증분 업데이트할 기존 계획이 없습니다. 먼저 '전체 교체'로 초기 계획을 생성하세요.",
        )

    # 신규 run_label 발급 — 이전 run 은 건드리지 않고 새 버전으로 분기
    new_run_label = _alloc_run_label()

    try:
        from app.infrastructure.models.sales_order import SalesOrder
        from app.infrastructure.models.schedule_task import ScheduleTask
        from app.infrastructure.parsers.erp_parser import parse_erp_file_incremental
        from sqlalchemy import and_, or_

        # ── 1. Frozen 배치 식별 (parent_run_label 기준) ─────────────────────────
        # 룰: 상태(status) 기준만 사용 — in_progress / completed / wip_complete 만 보존.
        # base_date 는 Stage 1 에서 필터 기준이 아님 (Stage 2 자동배열 앵커 용도).
        if parent_run_label:
            frozen = (
                db.query(ProductionBatch)
                .filter(
                    ProductionBatch.run_label == parent_run_label,
                    ProductionBatch.status.in_(
                        ["in_progress", "completed", "wip_complete"]
                    ),
                )
                .all()
            )
        else:
            frozen = []

        # frozen_order_keys: status-only (batch_seq 필터 제거 — 기존 버그 수정).
        # 연선 헤더(batch_seq=-1) 도 frozen 이면 포함해야 Full 모드에서 해당 수주가
        # create_batches 재배치에서 제외되어 중복 배치가 발생하지 않는다.
        frozen_order_keys: set[tuple] = {
            (b.sales_order_id, b.sales_order_line)
            for b in frozen
            if b.sales_order_id and b.sales_order_line is not None
        }
        frozen_order_ids = {k[0] for k in frozen_order_keys}
        frozen_wip_ids: set[int] = {
            b.wip_matched_id for b in frozen if b.wip_matched_id is not None
        }
        frozen_batch_ids: set[int] = {b.batch_id for b in frozen}

        # ── 2. "보존" 대상 배치: frozen 자신 + 동일 수주의 다른 공정 배치 ──────
        # 연선이 in_progress인데 절연/시스가 planned 이면 이 세 배치 모두 new_run_label
        # 로 복제되어야 버전 B 에서도 동일 수주의 공정 체인이 끊어지지 않는다.
        #
        # Bug fix: "동일 수주" 식별은 (sales_order_id, sales_order_line) tuple
        # 기준. 이전 구현은 `sales_order_id.in_(frozen_order_ids)` 로 order_id
        # 만 비교해서, 같은 order_id 의 **다른 line** (non-frozen) 배치까지
        # 복제 대상에 포함됨 → create_batches 재생성 경로와 교차 → 중복 insert
        # (run 20260420_224009 에서 107건 영향). tuple 매칭으로 교정.
        if frozen_order_keys and parent_run_label:
            related_order_cond = or_(
                *[
                    and_(
                        ProductionBatch.sales_order_id == oid,
                        ProductionBatch.sales_order_line == oline,
                    )
                    for oid, oline in frozen_order_keys
                ]
            )
            related_batches = (
                db.query(ProductionBatch)
                .filter(
                    ProductionBatch.run_label == parent_run_label,
                    or_(
                        related_order_cond,
                        ProductionBatch.batch_id.in_(frozen_batch_ids),
                    ),
                )
                .all()
            )
        else:
            related_batches = list(frozen)
        protected_batch_ids: set[int] = {b.batch_id for b in related_batches}

        # ── 1.4 Full 모드 diff pre-snapshot (T2b) ─────────────────────────────
        # 새 파일과 대사해 added/updated/deleted/preserved 분류를 낸다. mutation
        # 직전에 order_id set 을 찍어 둬야 사후 비교가 가능.
        pre_order_ids_full: set[str] = set()
        if upload_mode == "full" and parent_run_label:
            pre_order_ids_full = {
                row[0]
                for row in db.query(SalesOrder.order_id)
                .filter(SalesOrder.run_label == parent_run_label)
                .distinct()
                .all()
                if row[0]
            }

        # ── 3. 보존 배치를 new_run_label 로 복제 (parent_run_label 기록) ───────
        # SQLAlchemy 컬럼 이름 자동 추출로 모든 데이터 그대로 복사. batch_id 는
        # autoincrement 이므로 제외, run_label / parent_run_label 은 재지정.
        old_to_new_batch_id: dict[int, int] = {}
        batch_columns = [
            c.name
            for c in ProductionBatch.__table__.columns
            if c.name not in ("batch_id", "created_at")
        ]
        for b in related_batches:
            data = {col: getattr(b, col) for col in batch_columns}
            data["run_label"] = new_run_label
            data["parent_run_label"] = parent_run_label
            new_b = ProductionBatch(**data)
            db.add(new_b)
            db.flush()  # batch_id 확정
            old_to_new_batch_id[b.batch_id] = new_b.batch_id

        # ── 4. 보존 배치의 ScheduleTask 를 new_run_label 로 복제 ──────────────
        # 간트 표시에 필요한 설비/시간 슬롯 정보. Stage 2 의 _purge_run_tasks 가
        # frozen 배치 task 는 보존하도록 이미 수정되어 있음 (해당 PR 참조).
        if old_to_new_batch_id:
            old_batch_ids = list(old_to_new_batch_id.keys())
            old_tasks = (
                db.query(ScheduleTask)
                .filter(
                    ScheduleTask.run_label == parent_run_label,
                    ScheduleTask.batch_id.in_(old_batch_ids),
                )
                .all()
            )
            task_columns = [
                c.name
                for c in ScheduleTask.__table__.columns
                if c.name not in ("task_id", "created_at")
            ]
            for t in old_tasks:
                data = {col: getattr(t, col) for col in task_columns}
                data["run_label"] = new_run_label
                data["batch_id"] = old_to_new_batch_id[t.batch_id]
                db.add(ScheduleTask(**data))
            db.flush()

        # ── 5. 비동결 SalesOrder / WipInventory 를 new_run_label 로 forward-roll ──
        # SalesOrder PK = (order_id, order_line) 이므로 run_label 버전 불가.
        # 동결 수주는 parent 라벨을 유지 → list_runs 외주 집계 보존.
        # 비동결만 new_run_label 로 옮겨서 create_batches / wip_matching 이
        # new_run_label 스코프에서 일관되게 동작하도록 한다.
        if parent_run_label:
            if frozen_order_keys:
                frozen_cond = or_(
                    *[
                        and_(
                            SalesOrder.order_id == oid,
                            SalesOrder.order_line == oline,
                        )
                        for oid, oline in frozen_order_keys
                    ]
                )
                db.query(SalesOrder).filter(
                    SalesOrder.run_label == parent_run_label,
                    ~frozen_cond,
                ).update({"run_label": new_run_label}, synchronize_session=False)
            else:
                db.query(SalesOrder).filter(
                    SalesOrder.run_label == parent_run_label,
                ).update({"run_label": new_run_label}, synchronize_session=False)
            # WipInventory: 동결 WIP 제외하고 forward-roll
            wip_update_q = db.query(WipInventory).filter(
                WipInventory.run_label == parent_run_label,
            )
            if frozen_wip_ids:
                wip_update_q = wip_update_q.filter(
                    WipInventory.wip_id.notin_(frozen_wip_ids)
                )
            wip_update_q.update({"run_label": new_run_label}, synchronize_session=False)
            db.flush()

        # ── 6. Sales Order 처리 (new_run_label 스코프) ─────────────────────────
        erp_content = await erp_file.read()
        if not erp_content:
            raise HTTPException(status_code=400, detail="ERP 파일이 비어 있습니다.")

        deleted_counts = {"audit_log": 0, "schedule_task": 0, "production_batch": 0}

        if upload_mode == "incremental":
            # 기존 orders (forward-roll 로 new_run_label 라벨 완료) 유지 + 새 수주만 append
            pre_parse_order_keys: set[tuple] = {
                (row[0], row[1])
                for row in db.query(SalesOrder.order_id, SalesOrder.order_line)
                .filter(SalesOrder.run_label == new_run_label)
                .all()
            }
            parse_result = parse_erp_file_incremental(erp_content, new_run_label, db)
        else:
            # full: 방금 forward-roll 된 비동결 SalesOrder 를 전부 삭제 후 새 파일로 교체.
            # 동결 SalesOrder 는 parent run_label 에 남아 있으므로 이 쿼리 영향 없음.
            db.query(SalesOrder).filter(
                SalesOrder.run_label == new_run_label,
            ).delete(synchronize_session=False)
            db.flush()
            # pre-parse 스냅샷 (new_run_label 스코프) — 이 파일로 실제 추가된 수주 계산용
            pre_parse_order_keys = set()
            # 새 파일 파싱 — incremental 파서로 동일 (order_id, drum_length_m) 중복 감지
            parse_result = parse_erp_file_incremental(erp_content, new_run_label, db)

        # ── 7. WIP 처리 ──────────────────────────────────────────────────────
        wip_warnings: list[str] = []
        if wip_file:
            try:
                from app.infrastructure.parsers.wip_parser import parse_wip_file

                wip_content = await wip_file.read()
                if wip_content:
                    # new_run_label 스코프의 기존 WIP 삭제 (frozen 은 parent 라벨에 남아 있음)
                    db.query(WipInventory).filter(
                        WipInventory.run_label == new_run_label,
                    ).delete(synchronize_session=False)
                    db.flush()

                    # 새 WIP 파싱 — new_run_label 로 적재
                    wip_parse = parse_wip_file(wip_content, db, run_label=new_run_label)
                    wip_warnings.extend(wip_parse.get("warnings", []))
                    if wip_parse["total"] > 0:
                        wip_warnings.append(
                            f"재공실사 {wip_parse['total']}건 등록 완료."
                        )
            except Exception as exc:
                wip_warnings.append(f"재공 파일 파싱 실패: {exc}")

        # ── 8. WIP 매칭 — frozen WIP 제외 ─────────────────────────────────────
        try:
            wip_result = match_wip(
                new_run_label,
                db,
                exclude_wip_ids=frozen_wip_ids if frozen_wip_ids else None,
            )
        except Exception as exc:
            wip_warnings.append(f"WIP 매칭 실패 (계속 진행): {exc}")
            wip_result = {"matched": 0, "skipped": 0, "details": []}

        # ── 9. Batch Grouping — frozen orders 제외 (중복 배치 방지 불변식) ─────
        # frozen_order_keys 는 status-only 로 뽑혔으므로 헤더(batch_seq=-1) 까지
        # 포함되어 있어, 해당 수주가 어떤 공정에서든 물리 시작/완료되었으면 재배치 X.
        try:
            batch_result = create_batches(
                new_run_label,
                db,
                frozen_order_keys=frozen_order_keys if frozen_order_keys else None,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"배치 생성 실패: {exc}"
            ) from exc

        # ── 9a. "이 파일로 추가된 주문에서 나온 배치" 분리 집계 ───────────────
        # Why: batch_result.total_batches 는 "비동결 수주 전체를 다시 배치화한
        # 재생성 총계" 다. 사용자가 업로드한 파일의 실제 증분이 얼마인지 알기
        # 어려워 오해가 발생 (e.g. 20건 업로드 → 952 배치 표시). pre-parse 스냅샷
        # 과 차집합으로 new_from_file_order_keys 를 계산, 해당 주문에 속한
        # planned 배치만 공정별로 다시 집계해 new_from_file 로 응답에 포함.
        post_parse_order_keys: set[tuple] = {
            (row[0], row[1])
            for row in db.query(SalesOrder.order_id, SalesOrder.order_line)
            .filter(SalesOrder.run_label == new_run_label)
            .all()
        }
        new_from_file_keys = post_parse_order_keys - pre_parse_order_keys
        new_from_file_summary: dict = {"total_batches": 0, "by_process": {}}
        if new_from_file_keys:
            new_order_ids = {k[0] for k in new_from_file_keys}
            new_file_batches = (
                db.query(ProductionBatch.process_name)
                .filter(
                    ProductionBatch.run_label == new_run_label,
                    ProductionBatch.status == "planned",
                    ProductionBatch.sales_order_id.in_(new_order_ids),
                )
                .all()
            )
            by_proc: dict[str, int] = {}
            for row in new_file_batches:
                proc = row[0] or "기타"
                by_proc[proc] = by_proc.get(proc, 0) + 1
            new_from_file_summary = {
                "total_batches": len(new_file_batches),
                "by_process": by_proc,
            }

        db.commit()

        # ── 10. 자동 분할 (긴급 수주 후순위 드럼 → 자동 분리) ───────────────
        auto_split_result: dict = {"auto_split_count": 0, "splits": []}
        try:
            auto_split_result = execute_auto_splits(
                new_run_label, db, gap_days=split_gap_days
            )
            if auto_split_result["auto_split_count"] > 0:
                logger.info(
                    "[Stage1 Update] 자동 분할 %d건 적용",
                    auto_split_result["auto_split_count"],
                )
        except Exception as exc:
            logger.warning("[Stage1 Update] 자동 분할 실패 (계속 진행): %s", exc)

        # ── 11. Split 후보 감지 (자동 분할 후 잔여 후보) ─────────────────────
        try:
            split_candidates = detect_split_candidates(
                new_run_label, db, gap_days=split_gap_days
            )
        except Exception as exc:
            logger.warning("[Stage1 Update] 분할 후보 감지 실패: %s", exc)
            split_candidates = []

        warnings = (
            parse_result.get("warnings", [])
            + wip_warnings
            + batch_result.get("warnings", [])
        )

        # ── 진단 warning: frozen 판정 결과를 명시적으로 노출 ───────────────────
        # Why: "이전 런의 어떤 배치가 frozen 으로 복제돼 새 런에서도 그 자리에
        # 박혔는지" 가 EDD 역전·비정상 배치 원인 추적의 핵심. 기존에는 frozen
        # 카운트만 response 에 있어 "어떤 batch_group 이 왜 frozen 됐나" 를 UI
        # 에서 알 수 없었음. status 별 요약 + 상위 N 개 batch_group 을 warning
        # 에 평문 추가해 사용자/개발자가 plan_pipeline 응답만으로 진단 가능.
        if frozen:
            _frozen_by_status: dict[str, int] = {}
            _frozen_groups: dict[str, int] = {}
            for _fb in frozen:
                _frozen_by_status[_fb.status] = _frozen_by_status.get(_fb.status, 0) + 1
                _bg = _fb.batch_group or f"_single_{_fb.batch_id}"
                _frozen_groups[_bg] = _frozen_groups.get(_bg, 0) + 1
            _status_parts = ", ".join(
                f"{_s}×{_c}" for _s, _c in sorted(_frozen_by_status.items())
            )
            warnings.append(
                f"[Frozen] parent_run={parent_run_label} → {len(frozen)}건 복제 "
                f"({_status_parts}) / batch_group {len(_frozen_groups)}개"
            )
            # 상위 10개 batch_group 을 별도 warning 으로 노출 (UI 에서 잘림 방지).
            _top_groups = sorted(_frozen_groups.items(), key=lambda kv: -kv[1])[:10]
            if _top_groups:
                warnings.append(
                    "[Frozen groups] "
                    + ", ".join(f"{_bg}×{_c}" for _bg, _c in _top_groups)
                )

        # ── 12. Frozen 배치 요약 (T2a) — 복제된 new_run_label 기준 ───────────
        # 프론트엔드가 "진행중/완료 보존" 뱃지로 표시할 수 있도록 frozen 배치의
        # 최소 필드를 직렬화해 응답에 포함. 복제본의 batch_id (new_run_label 안)
        # 을 내려줘야 UI 에서 해당 run 상세 조회 시 연결된다.
        frozen_batches_payload = []
        if old_to_new_batch_id and frozen:
            # 복제된 frozen 배치를 new_run_label 에서 다시 로드
            new_frozen_ids = [
                old_to_new_batch_id[b.batch_id]
                for b in frozen
                if b.batch_id in old_to_new_batch_id
            ]
            if new_frozen_ids:
                new_frozen = (
                    db.query(ProductionBatch)
                    .filter(ProductionBatch.batch_id.in_(new_frozen_ids))
                    .all()
                )
                frozen_batches_payload = [
                    {
                        "batch_id": b.batch_id,
                        "batch_group": b.batch_group,
                        "process_name": b.process_name,
                        "status": b.status,
                        "customer_name": b.customer_name,
                        "due_date": b.due_date.isoformat() if b.due_date else None,
                        "item_code": b.item_code,
                        "product_group": b.product_group,
                        "voltage": b.voltage,
                        "sq_mm2": float(b.sq_mm2) if b.sq_mm2 is not None else None,
                        "sheath_color": b.sheath_color,
                        "drum_count": b.drum_count,
                        "total_length_m": (
                            float(b.total_length_m)
                            if b.total_length_m is not None
                            else None
                        ),
                        "sales_order_id": b.sales_order_id,
                        "sales_order_line": b.sales_order_line,
                        "equipment_code": b.equipment_code,
                    }
                    for b in new_frozen
                ]

        # ── 13. Full 모드 diff 요약 (T2b) ───────────────────────────────────
        # 새 파일 파싱 후 post-snapshot 을 pre 와 대사해 added/updated/deleted/
        # preserved_frozen 분류. order_id 레벨 (드럼/line 무관).
        # post_order_ids: new_run_label 의 SalesOrder + parent 에 남은 frozen 수주.
        diff_summary: dict | None = None
        if upload_mode == "full":
            post_order_ids_new = {
                row[0]
                for row in db.query(SalesOrder.order_id)
                .filter(SalesOrder.run_label == new_run_label)
                .distinct()
                .all()
                if row[0]
            }
            post_order_ids = post_order_ids_new | frozen_order_ids
            _common = pre_order_ids_full & post_order_ids
            diff_summary = {
                "added": len(post_order_ids - pre_order_ids_full),
                "updated": len(_common - frozen_order_ids),
                "deleted": len(pre_order_ids_full - post_order_ids),
                "preserved_frozen": len(_common & frozen_order_ids),
            }

        return {
            "run_label": new_run_label,
            "parent_run_label": parent_run_label,
            "upload_mode": upload_mode,
            "frozen": {
                "batch_count": len(frozen_batch_ids),
                "order_count": len(frozen_order_keys),
                "wip_count": len(frozen_wip_ids),
                "protected_batch_count": len(protected_batch_ids),
                "copied_batch_count": len(old_to_new_batch_id),
            },
            "frozen_batches": frozen_batches_payload,
            "diff_summary": diff_summary,
            "deleted": deleted_counts,
            "parse": parse_result,
            "wip": wip_result,
            "batches": batch_result,
            # T2a-2: 이 파일로 추가된 주문에서 나온 planned 배치 집계 (라벨 구분용)
            "new_from_file": new_from_file_summary,
            "warnings": warnings,
            "split_candidates": split_candidates,
            "auto_split": auto_split_result,
            # 프론트엔드 토스트 메시지용
            "added_orders": parse_result.get("inserted", 0),
            "created_batch_groups": batch_result.get("total_batches", 0),
            "preserved_batches": len(old_to_new_batch_id),
            "auto_split_count": auto_split_result["auto_split_count"],
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("[Stage1 Update] 실패 — 롤백 완료")
        raise HTTPException(
            status_code=500,
            detail=f"Stage 1 업데이트 실패 (롤백 완료): {exc}",
        ) from exc


@router.post(
    "/stage1/urgent",
    summary="[REMOVED] 긴급 수주 — /stage1/update upload_mode=incremental 로 대체",
    deprecated=True,
    status_code=410,
)
async def apply_urgent_order(
    erp_file: UploadFile | None = File(None),  # noqa: ARG001 — schema parity
    run_label: str | None = Form(None),  # noqa: ARG001
    gap_days: int = Form(3),  # noqa: ARG001
) -> None:
    """410 Gone — 사용 중단 (frontend 미호출, urgent_scheduler 의 FK cycle 미패치).

    현재 긴급수주 플로우:
      POST /api/pipeline/stage1/update
        upload_mode=incremental
        parent_run_label=<기존 run>
        base_date=<YYYY-MM-DD>

    이 엔드포인트의 실제 핸들러였던 `urgent_scheduler.apply_urgent_incremental`
    는 newly_created header batch 가 wip_inventory.source_batch_id 로 참조될 때
    FK NO ACTION 위반을 일으킨다 (Week 9 통합 테스트에서 발견). 패치 대신 해당
    플로우의 단일 진입점을 `/stage1/update` 로 일원화하여 표면을 줄였다.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "이 엔드포인트는 사용 중단되었습니다. "
            "긴급수주는 POST /api/pipeline/stage1/update 에 "
            "upload_mode='incremental' + parent_run_label + base_date 를 함께 보내 주세요."
        ),
    )


@router.get("/stage1/{run_label}/batches", summary="배치 목록 JSON")
def list_batches(run_label: str, db: Session = Depends(get_db)) -> list[dict]:
    """지정한 run_label의 production_batch 데이터를 JSON으로 반환한다.

    공정 순서(연선→B100→A100→A120) 내림차순 SQ 정렬.
    """
    # 공정 정렬 우선순위 — CASE WHEN 대신 Python 후처리
    PROCESS_ORDER = {"연선": 0, "B100": 1, "A100": 2, "A120": 3}
    # 시스색 정렬 — 원본 Excel 기준: 흑→갈→회→청→녹/황→흑/적
    COLOR_ORDER = {"흑": 0, "갈": 1, "회": 2, "청": 3, "녹/황": 4, "흑/적": 5}

    from app.infrastructure.models.sales_order import SalesOrder

    # production_batch + sales_order + wip_inventory JOIN
    rows = (
        db.query(
            ProductionBatch,
            SalesOrder.order_status,
            WipInventory.length_m,
            WipInventory.count,
            WipInventory.core_colors,
            WipInventory.process_stage,
        )
        .outerjoin(
            SalesOrder,
            (ProductionBatch.sales_order_id == SalesOrder.order_id)
            & (ProductionBatch.sales_order_line == SalesOrder.order_line),
        )
        .outerjoin(
            WipInventory,
            ProductionBatch.wip_matched_id == WipInventory.wip_id,
        )
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq != -1,  # 헤더 배치(스케줄러 전용) 제외
        )
        .all()
    )
    batches = [
        (b, os or "대기", wip_len_m, wip_cnt, wip_cc, wip_ps)
        for b, os, wip_len_m, wip_cnt, wip_cc, wip_ps in rows
    ]
    if not batches:
        raise HTTPException(
            status_code=404,
            detail=f"run_label '{run_label}'에 해당하는 배치가 없습니다.",
        )

    # 공정 순서 → SQ 내림차순 → 색상 지정 순서 정렬
    batches.sort(
        key=lambda item: (
            PROCESS_ORDER.get(item[0].process_name, 99),
            -(float(item[0].sq_mm2 or 0)),
            COLOR_ORDER.get(item[0].sheath_color, 99),
        )
    )

    return [
        {
            "batch_id": b.batch_id,
            "process_name": b.process_name,
            "sq_mm2": float(b.sq_mm2 or 0),
            "core_count": b.core_count or 1,
            "sheath_color": b.sheath_color or "",
            "total_length_m": float(b.total_length_m or 0),
            "drum_count": b.drum_count,
            "drum_length_m": float(b.drum_length_m or 0),
            "customer_name": b.customer_name,
            "due_date": str(b.due_date or ""),
            "product_group": b.product_group,
            "sales_order_id": b.sales_order_id,
            "wip_matched_id": b.wip_matched_id,
            "wip_process_stage": wip_ps or None,
            "wip_length_m": float(wip_len_m) if wip_len_m is not None else None,
            "wip_count": int(wip_cnt) if wip_cnt is not None else None,
            "wip_core_colors": wip_cc or None,
            "status": b.status,
            "order_status": order_status,
            "remarks": b.remarks,
            "spec_raw": format_spec_display(
                getattr(b, "spec_raw", None), b.core_count or 1, float(b.sq_mm2 or 0)
            ),
            "core_colors": b.core_colors or "",
            "voltage": b.voltage,
            "equipment_code": b.equipment_code,
            "batch_group": b.batch_group,
        }
        for b, order_status, wip_len_m, wip_cnt, wip_cc, wip_ps in batches
    ]


@router.get("/stage1/{run_label}/wip-inventory", summary="SM 재공 재고 목록")
def list_wip_inventory(run_label: str, db: Session = Depends(get_db)) -> list[dict]:
    """wip_inventory 테이블을 직접 조회해서 반환한다.

    - run_label에 해당하는 모든 WIP 재고 (status 무관)
    - production_batch.wip_matched_id 로 어떤 배치에 매칭됐는지 batch_id / batch_group 포함
    """
    from app.infrastructure.models.sales_order import SalesOrder
    from sqlalchemy import or_

    # wip_inventory 전체 조회
    # run_label이 일치하거나 NULL인 경우 모두 포함 (기존 데이터 호환)
    wip_rows = (
        db.query(WipInventory)
        .filter(
            or_(
                WipInventory.run_label == run_label,
                WipInventory.run_label.is_(None),
            )
        )
        .order_by(WipInventory.process_stage, WipInventory.cross_section.desc())
        .all()
    )

    # WIP process_stage 룩업 (연선재고→연선, 절연재고→절연 등 공정 매칭용)
    wip_process_stages: dict[int, str] = {
        w.wip_id: (w.process_stage or "") for w in wip_rows
    }

    # 매칭 정보: wip_id → (batch_id, batch_group) — 한 번에 로드해서 N+1 방지
    # wip_matched_id가 같은 배치가 여러 개일 수 있음(연선·절연·시스 모두 전파됨).
    # WIP process_stage와 batch process_name이 일치하는 배치를 우선 선택한다.
    matched_batches = (
        db.query(
            ProductionBatch.wip_matched_id,
            ProductionBatch.batch_id,
            ProductionBatch.batch_group,
            ProductionBatch.process_name,
        )
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.wip_matched_id.isnot(None),
        )
        .all()
    )
    wip_match_map: dict[int, dict] = {}
    for row in matched_batches:
        wip_process = wip_process_stages.get(row.wip_matched_id, "")
        proc = row.process_name or ""
        # WIP 공정과 배치 공정이 일치하는지 확인
        is_match = (
            ("연선" in wip_process and "연선" in proc)
            or ("절연" in wip_process and ("절연" in proc or "B100" in proc))
            or ("시스" in wip_process and "시스" in proc)
        )
        # 아직 미등록이거나 공정이 더 잘 맞는 배치로 갱신
        if row.wip_matched_id not in wip_match_map or is_match:
            wip_match_map[row.wip_matched_id] = {
                "matched_batch_id": row.batch_id,
                "matched_batch_group": row.batch_group,
            }

    # wip_id별 실제 사용량(m) — 환산수량(ordered_qty_m × core_count) 합산
    # 4C 케이블은 심선 4가닥 소비하므로 WIP 드럼에서 ×4 차감
    from sqlalchemy import func as sqla_func, case

    used_m_rows = (
        db.query(
            SalesOrder.wip_id,
            sqla_func.sum(
                SalesOrder.ordered_qty_m
                * case((SalesOrder.core_count > 0, SalesOrder.core_count), else_=1)
            ),
        )
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.wip_id.isnot(None),
        )
        .group_by(SalesOrder.wip_id)
        .all()
    )
    wip_used_m: dict[int, float] = {
        wid: float(total or 0) for wid, total in used_m_rows
    }

    return [
        {
            "wip_id": w.wip_id,
            "process_stage": w.process_stage or "",
            "voltage_class": w.voltage_class or "",
            "material": w.material or "",
            "product_name": w.product_name or "",
            "spec": w.spec or "",
            "cross_section": float(w.cross_section) if w.cross_section else None,
            "length_m": float(w.length_m) if w.length_m else 0,
            "count": w.count or 1,
            "total_length_m": float(w.total_length_m) if w.total_length_m else 0,
            "core": w.core,
            "core_colors": w.core_colors or "",
            "status": w.status or "",
            "used_m": wip_used_m.get(w.wip_id, 0),
            "matched_batch_id": wip_match_map.get(w.wip_id, {}).get("matched_batch_id"),
            "matched_batch_group": wip_match_map.get(w.wip_id, {}).get(
                "matched_batch_group"
            ),
        }
        for w in wip_rows
    ]


@router.get("/stage1/{run_label}/outsourced", summary="외주 분류 수주 목록")
def list_outsourced_orders(run_label: str, db: Session = Depends(get_db)) -> list[dict]:
    """지정한 run_label에서 외주로 분류된 수주(is_outsourced=True) 목록을 반환한다.

    외주 품목은 production_batch에 포함되지 않으므로 sales_order에서 직접 조회한다.
    """
    from app.infrastructure.models.sales_order import SalesOrder

    orders = (
        db.query(SalesOrder)
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.is_outsourced == True,  # noqa: E712
        )
        .order_by(SalesOrder.due_date, SalesOrder.order_id)
        .all()
    )

    return [
        {
            "order_id": o.order_id,
            "order_line": o.order_line,
            "product_group": o.product_group or "",
            "spec_raw": o.spec_raw or "",
            "sheath_color": o.sheath_color or "",
            "customer_name": o.customer_name or "",
            "due_date": str(o.due_date or ""),
            "total_length_m": float(o.ordered_qty_m or 0),
            "drum_length_m": float(o.drum_length_m or 0),
            "drum_count": o.drum_count or 0,
            "voltage": o.voltage or "",
            "order_status": o.order_status or "대기",
            "reason": "ERP 외주계획 지정",
        }
        for o in orders
    ]


@router.get("/stage1/{run_label}/ai-summary", summary="AI 배치 분석 요약")
def get_ai_summary(run_label: str, db: Session = Depends(get_db)):
    """run_label 전체 배치를 LLM으로 분석하여 핵심 인사이트를 반환한다.

    Returns:
        totalBatches, totalProductionM, riskCount, highlights, insights
    """
    from app.application.decisions.summarize_run import generate_batch_summary_sync

    return generate_batch_summary_sync(run_label, db)


@router.get("/stage1/{run_label}/export", summary="작업지시서 Excel 다운로드")
def export_stage1(run_label: str, db: Session = Depends(get_db)) -> StreamingResponse:
    """지정한 run_label의 production_batch 데이터를 Excel(.xlsx)로 변환하여 반환한다.

    파일명: 작업지시서_{run_label}.xlsx
    Content-Disposition: attachment (브라우저에서 다운로드 트리거)
    """
    try:
        output = export_plan(run_label, db)
    except ValueError as exc:
        # run_label에 해당하는 데이터가 없는 경우
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Excel 생성 실패: {exc}") from exc

    # RFC 5987 인코딩 없이 ASCII 파일명 사용; 한글은 Content-Disposition 파라미터 오염 방지를 위해
    # filename*=UTF-8''... 형식이 이상적이나 브라우저 호환성 이슈로 단순 ASCII 사용
    safe_label = run_label.replace(" ", "_")
    filename = f"production_plan_{safe_label}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _parse_stage2_body(body: dict) -> tuple[str, datetime | None, str]:
    """POST /stage2 body 파싱 공통 루틴 (sync/async 경로 공유)."""
    run_label = body.get("run_label")
    if not run_label:
        raise HTTPException(status_code=400, detail="run_label 필수")

    base_date_dt = parse_base_date_yyyymmdd(body.get("base_date"))
    optimizer = body.get("optimizer", "cpsat")  # "cpsat" | "greedy"
    return run_label, base_date_dt, optimizer


def _start_ai_background(run_label: str) -> None:
    """AI 백그라운드 분석을 큐잉하는 어댑터.

    Why an adapter: orchestrator.execute_stage2 는 in-memory _ai_cache /
    _ai_cache_lock 를 직접 만지지 않도록 의도적으로 cache 소유권을 본 라우트
    모듈에 남겼다 (GET /stage2/{run_label}/ai-status 가 같은 dict 를 읽기
    때문). 이 어댑터가 cache mutation + thread.start 를 하나로 묶어
    orchestrator 가 단일 콜만 하면 되도록 한다.
    """
    with _ai_cache_lock:
        _ai_cache[run_label] = {"status": "pending"}
    thread = threading.Thread(target=_run_ai_background, args=(run_label,), daemon=True)
    thread.start()


def _execute_stage2_core(
    run_label: str,
    base_date_dt: datetime | None,
    optimizer: str,
    db: Session,
) -> dict:
    """Stage2 핵심 로직 thin shim — orchestrator.execute_stage2 위임.

    sync `/pipeline/stage2` 와 async `/pipeline/stage2/async` 의 공유 구현.
    SchedulerOverlapError 는 여기서 잡지 않고 호출자가 매핑하도록 전파한다
    (sync 는 200 + overlap_alert 응답, async job 은 status=overlap_alert
    저장).

    Why a shim and not a direct alias to execute_stage2: 본 함수 자체를
    monkeypatch 하는 테스트 (test_stage2_async_job) 가 있어 함수 객체가
    plan_pipeline 모듈 namespace 에 살아 있어야 한다. 또한 orchestrator 가
    auto_schedule / validate_all 을 DI 로 받게 했기 때문에, 라우트 모듈에서
    monkeypatch 가능한 두 심볼을 호출 시점에 전달할 수 있다.
    """
    return execute_stage2(
        run_label,
        base_date_dt,
        optimizer,
        db,
        auto_schedule_fn=auto_schedule,
        validate_all_fn=validate_all,
        ai_background_starter=_start_ai_background,
    )


@router.post("/stage2", summary="Stage 2: 자동 스케줄링 (동기)")
def run_stage2(body: dict, db: Session = Depends(get_db)):
    """Stage 2 동기 경로 — 기존 호환 유지.

    왜 동기를 유지하는가:
      기존 테스트 (test_schedule_route_overlap) 및 프론트 일부 흐름이
      이 엔드포인트의 즉시 응답에 의존. async 경로는 `/stage2/async`
      로 분리하여 점진 이관 가능하도록 함.

    body:
        run_label: str (필수)
        base_date: str (선택, YYYYMMDD 형식 — 스케줄 시작 기준일)
        optimizer: "cpsat" | "greedy" (기본 "cpsat")
    """
    run_label, base_date_dt, optimizer = _parse_stage2_body(body)

    try:
        return _execute_stage2_core(run_label, base_date_dt, optimizer, db)
    except SchedulerOverlapError as exc:
        # 왜 200: 이 예외는 '겹침 재시도 실패' 비즈니스 시그널이지 서버 장애가 아니다.
        # 프론트가 overlap_alert=True 플래그로 경고 배너를 표시할 수 있도록 성공 코드로 반환.
        # DB rollback 은 auto_schedule 내부에서 이미 수행됨(기존 스케줄 불변).
        db.rollback()
        logger.warning(
            "stage2 overlap alert — run_label=%s attempts=%s violations=%s",
            run_label,
            exc.attempts,
            len(exc.violations),
        )
        return {
            "run_label": run_label,
            "status": "overlap_alert",
            "overlap_alert": True,
            "message": str(exc),
            "violations": exc.violations,
            "total_violations": len(exc.violations),
            "attempts": exc.attempts,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"스케줄링 실패: {exc}") from exc


@router.post("/stage2/async", summary="Stage 2: 자동 스케줄링 (비동기 job 제출)")
def run_stage2_async(body: dict) -> dict:
    """Stage 2 비동기 경로 — 즉시 job_id 반환, 실제 처리는 백그라운드 스레드.

    왜 async 경로가 필요한가:
      동기 엔드포인트는 10분 급 Stage2 처리 동안 uvicorn 워커 + threadpool
      슬롯을 점유해 동시 요청 응답성을 악화시키고, 프론트 입장에서 진행률
      표시가 불가능해 UX 가 나쁘다. 이 경로는 job 을 큐에 등록하고 즉시
      반환 → 프론트는 `GET /stage2/status/{job_id}` 로 폴링.

    Returns:
        { "job_id": str, "status": "running", "run_label": str }
    """
    from app.infrastructure.database import SessionLocal
    from app.application.stage2_job_queue import Stage2JobRequest, submit_job

    run_label, base_date_dt, optimizer = _parse_stage2_body(body)

    def _runner(req: Stage2JobRequest, db: Session) -> dict:
        # SchedulerOverlapError 는 큐 워커가 overlap_alert 상태로 매핑.
        # 다른 예외는 큐 워커가 error 상태로 기록하고 로그에 남김.
        return _execute_stage2_core(req.run_label, req.base_date, req.optimizer, db)

    req = Stage2JobRequest(
        run_label=run_label, base_date=base_date_dt, optimizer=optimizer
    )
    job_id = submit_job(req, _runner, SessionLocal)
    return {"job_id": job_id, "status": "running", "run_label": run_label}


@router.get(
    "/stage2/status/{job_id}",
    summary="Stage 2 비동기 job 상태 조회",
)
def get_stage2_status(job_id: str) -> dict:
    """job 상태 반환.

    상태 필드:
      - status: "running" | "done" | "overlap_alert" | "error"
      - result: done/overlap_alert 일 때만 채워짐 (sync 응답과 동일 구조)
      - error: error 일 때만 채워짐
      - started_at / finished_at: ISO-8601 UTC
    """
    from app.application.stage2_job_queue import get_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job_id={job_id} 없음")
    return job


@router.get("/stage2/{run_label}/ai-status", summary="AI 분석 진행 상태 조회")
def get_ai_status(run_label: str):
    """Stage 2 실행 후 비동기 AI 분석의 진행 상태를 반환한다.

    Returns:
        status: "pending" | "done" | "error"
        summary: AI 분석 결과 (status=done일 때만)
        error: 오류 메시지 (status=error일 때만)
    """
    with _ai_cache_lock:
        cached = _ai_cache.get(run_label)
    if not cached:
        return {"status": "pending"}
    return cached


@router.post("/stage2/{run_label}/trigger-reanalysis", summary="AI 재분석 트리거")
def trigger_reanalysis(run_label: str):
    """블록 변경(이동/분할/연장/재배치) 후 AI 분석을 재실행한다.

    캐시를 pending으로 초기화하고 백그라운드 스레드에서 AI 분석을 다시 실행한다.
    즉시 반환하여 프론트엔드를 블로킹하지 않는다.
    """
    with _ai_cache_lock:
        _ai_cache[run_label] = {"status": "pending"}
    thread = threading.Thread(target=_run_ai_background, args=(run_label,), daemon=True)
    thread.start()
    return {"status": "pending", "message": f"AI 재분석 시작: {run_label}"}


@router.get("/wip-template", summary="재공실사 Excel 템플릿 다운로드")
def download_wip_template() -> StreamingResponse:
    """드롭다운 validation이 포함된 재공실사 데이터 입력 템플릿을 반환한다."""
    from app.infrastructure.exporters.wip_template import generate_wip_template

    output = generate_wip_template()
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=wip_template.xlsx"},
    )


@router.get("/batch-group/{batch_group:path}/orders", summary="배치 그룹 내 수주 목록")
def list_batch_group_orders(batch_group: str, db: Session = Depends(get_db)):
    """지정한 batch_group에 속하는 production_batch 목록을 반환한다.

    연선 그룹(ST- 접두사):
      - batch_seq=-1 헤더(틀단위 집계)와 batch_seq=1 개별 수주 배치로 구성된다.
      - 개별 수주 배치를 run_label+process_name+sq_mm2로 추가 조회하여 항상 포함한다.
        (batch_group이 구버전 값으로 저장돼 batch_group 필터로 찾히지 않는 경우 대비)
    기타 그룹: batch_group 필터 결과를 그대로 반환한다.
    """
    by_group = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == batch_group)
        .order_by(ProductionBatch.batch_seq.asc(), ProductionBatch.due_date.asc())
        .all()
    )

    if not by_group:
        raise HTTPException(
            status_code=404,
            detail=f"batch_group '{batch_group}'에 해당하는 배치가 없습니다.",
        )

    # batch_group 필터 결과를 그대로 사용 (분할된 그룹은 각자의 batch_group만 표시)
    batches = by_group

    # WIP 재고 수량 조회 — 연선/절연재고 사용 배치는 net qty에서 차감해야 함
    from app.infrastructure.models.wip_inventory import WipInventory as WipModel

    wip_ids = [b.wip_matched_id for b in batches if b.wip_matched_id is not None]
    wip_qty_map: dict[int, float] = {}
    wip_stage_map2: dict[int, str] = {}
    if wip_ids:
        wip_rows = (
            db.query(WipModel.wip_id, WipModel.total_length_m, WipModel.process_stage)
            .filter(WipModel.wip_id.in_(wip_ids))
            .all()
        )
        wip_qty_map = {wid: float(tl or 0) for wid, tl, _ in wip_rows}
        wip_stage_map2 = {wid: (ps or "") for wid, _, ps in wip_rows}

    from app.domain.batch_sheath_keys import _WIP_COVERED_PROCESSES

    def _to_dict(b: ProductionBatch) -> dict:
        raw_len = float(b.total_length_m or 0)
        wip_stage = wip_stage_map2.get(b.wip_matched_id, "") if b.wip_matched_id else ""
        # 이 배치의 공정이 WIP에 의해 커버되는 경우에만 차감
        # 예: 절연재고 WIP + 절연 공정 → 차감 / 절연재고 WIP + 시스 공정 → 차감 안 함
        is_wip = bool(wip_stage) and (
            b.process_name or ""
        ) in _WIP_COVERED_PROCESSES.get(wip_stage, set())
        wip_len = wip_qty_map.get(b.wip_matched_id, 0.0) if is_wip else 0.0
        net_len = max(raw_len - wip_len, 0.0)
        return {
            "batch_id": b.batch_id,
            "batch_seq": b.batch_seq,
            "sales_order_id": b.sales_order_id,
            "spec_raw": format_spec_display(
                getattr(b, "spec_raw", None), b.core_count or 1, float(b.sq_mm2 or 0)
            ),
            "sheath_color": b.sheath_color or "",
            "customer_name": b.customer_name or "",
            "due_date": str(b.due_date or ""),
            "drum_length_m": float(b.drum_length_m or 0),
            "drum_count": b.drum_count or 1,
            "total_length_m": raw_len,
            "wip_length_m": wip_len,  # WIP 재고 커버량
            "net_length_m": net_len,  # 실제 작업지시량 (WIP 제외)
            "wip_matched_id": b.wip_matched_id,
            "wip_stage": wip_stage or None,
            "product_group": b.product_group or "",
            "status": b.status or "",
            "core_count": int(b.core_count or 1),
        }

    return [_to_dict(b) for b in batches]


@router.get(
    "/batch-group/{batch_group:path}/process-flow",
    summary="배치 그룹의 연관 공정 흐름 조회",
)
def get_process_flow(batch_group: str, db: Session = Depends(get_db)):
    """주어진 batch_group과 동일 수주를 공유하는 모든 batch_group을 공정 순서로 반환한다.

    이전공정/다음공정 네비게이션에 사용된다.
    예: 연선(ST-240) → 절연(저압절연_240SQ) → 시스(A120_흑_240SQ) 순서로 반환.
    """
    from sqlalchemy import tuple_

    from app.domain.constants import PROCESS_ORDER
    from app.infrastructure.models.schedule_task import (
        ScheduleTask as ScheduleTaskModel,
    )

    # 1. 현재 batch_group의 run_label과 수주 키 조회
    current_batches = (
        db.query(
            ProductionBatch.sales_order_id,
            ProductionBatch.sales_order_line,
            ProductionBatch.run_label,
        )
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq != -1,  # 헤더 제외
        )
        .all()
    )

    if not current_batches:
        return []

    order_keys = list({(b.sales_order_id, b.sales_order_line) for b in current_batches})
    run_label = current_batches[0].run_label

    # 2. 동일 run_label 내에서 동일 수주를 포함하는 모든 batch_group 조회
    related = (
        db.query(
            ProductionBatch.batch_group,
            ProductionBatch.process_name,
        )
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq != -1,
            tuple_(
                ProductionBatch.sales_order_id,
                ProductionBatch.sales_order_line,
            ).in_(order_keys),
        )
        .distinct()
        .all()
    )

    # 3. batch_group별 공정 정보 집계
    groups: dict[str, dict] = {}
    for r in related:
        bg = r.batch_group
        if bg and bg not in groups:
            groups[bg] = {
                "batch_group": bg,
                "process_name": r.process_name,
                "order": PROCESS_ORDER.get(r.process_name, 50),
            }

    # 4. schedule_task에서 설비·시간 정보 보강
    for bg_info in groups.values():
        task = (
            db.query(
                ScheduleTaskModel.equipment_code,
                ScheduleTaskModel.start_datetime,
                ScheduleTaskModel.end_datetime,
            )
            .filter(ScheduleTaskModel.batch_group == bg_info["batch_group"])
            .first()
        )
        if task:
            bg_info["equipment_code"] = task.equipment_code
            bg_info["start_datetime"] = (
                task.start_datetime.isoformat() if task.start_datetime else None
            )
            bg_info["end_datetime"] = (
                task.end_datetime.isoformat() if task.end_datetime else None
            )

    # 5. 공정 순서 → batch_group 이름 순으로 정렬
    result = sorted(groups.values(), key=lambda x: (x["order"], x["batch_group"]))
    return result


@router.post("/batch-group/{batch_group:path}/split", summary="배치 그룹 분할")
def split_batch_group(
    batch_group: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """배치 그룹을 2개로 분할한다.

    지정된 batch_ids의 batch_group을 '{원래그룹}_{suffix}'로 변경하고,
    헤더 배치(batch_seq=-1)를 비율로 분할하여 신규 그룹에 새 헤더를 생성한다.
    기존 schedule_task는 삭제되며 프론트에서 재스케줄링(Stage 2)을 트리거해야 한다.

    body:
        batch_ids: list[int]  — 새 그룹으로 이동할 batch_id 목록 (seq >= 1)
        suffix: str           — 새 그룹 이름 접미사 (기본값: "B")
    """
    from app.infrastructure.models.schedule_task import (
        ScheduleTask as ScheduleTaskModel,
    )

    batch_ids: list[int] = body.get("batch_ids", [])
    # 프론트는 "suffix" 키로 전송; "new_group_suffix" 레거시도 허용
    suffix: str = body.get("suffix") or body.get("new_group_suffix") or "B"

    if not batch_ids:
        raise HTTPException(status_code=400, detail="분리할 batch_ids가 비어 있습니다.")

    batch_id_set = set(batch_ids)

    # 원래 그룹에 해당 배치가 존재하는지 검증
    existing = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_id.in_(batch_ids),
            ProductionBatch.batch_group == batch_group,
        )
        .count()
    )
    if existing == 0:
        raise HTTPException(
            status_code=404,
            detail=f"batch_group '{batch_group}'에서 지정된 batch_ids를 찾을 수 없습니다.",
        )

    new_group = f"{batch_group}_{suffix}"

    # ── 헤더 배치 및 개별 배치 조회 ─────────────────────────────────────────
    header = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq == -1,
        )
        .first()
    )
    all_individual = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq >= 1,
        )
        .all()
    )
    # 사용자가 선택한 분할 배치 (초기값 — WIP 최적화 후 변경될 수 있음)
    split_off = [b for b in all_individual if b.batch_id in batch_id_set]
    remaining = [b for b in all_individual if b.batch_id not in batch_id_set]

    remain_lots: int = -1  # -1 = 미계산 (Phase 2 그룹 또는 헤더 없는 경우)

    # ── 헤더 배치 분할 처리 ──────────────────────────────────────────────────
    if header and (split_off or remaining):
        import math as _math
        from app.infrastructure.models.wip_inventory import WipInventory as WipModel

        orig_dur = float(header.estimated_duration_min or 0)
        lot_size = float(header.drum_length_m or 0)  # 틀단위 (m)
        core_mul = int(header.core_count or 1)

        # ── WIP 연선/절연재고 배치 파악 및 수량 로드 ────────────────────────
        all_wip_ids = [
            b.wip_matched_id for b in all_individual if b.wip_matched_id is not None
        ]
        wip_stage_map: dict[int, str] = {}
        wip_len_by_id: dict[int, float] = {}  # wip_id → WIP 재고량 (cable m)
        if all_wip_ids:
            wip_rows = (
                db.query(
                    WipModel.wip_id, WipModel.process_stage, WipModel.total_length_m
                )
                .filter(WipModel.wip_id.in_(all_wip_ids))
                .all()
            )
            wip_stage_map = {wid: (ps or "") for wid, ps, _ in wip_rows}
            wip_len_by_id = {wid: float(tl or 0) for wid, _, tl in wip_rows}

        def _is_wip_strand(b: ProductionBatch) -> bool:
            """연선/절연재고 WIP 사용 배치"""
            if not b.wip_matched_id:
                return False
            return wip_stage_map.get(b.wip_matched_id, "") in ("연선재고", "절연재고")

        def _net_qty(b: ProductionBatch) -> float:
            """배치의 실제 생산 필요량 (strand m).
            WIP 커버량을 차감하되, WIP qty < 수주 qty이면 잔여분을 포함.
            """
            full = float(b.total_length_m or 0) * core_mul
            if not _is_wip_strand(b):
                return full
            wip_cov = wip_len_by_id.get(b.wip_matched_id, 0.0) * core_mul
            return max(0.0, full - wip_cov)

        # ── WIP 재매칭 최적화 ────────────────────────────────────────────────
        # 배치(수주)는 그룹 이동 없이 그대로 유지.
        # WIP 전체를 split 또는 remain 한 쪽에만 몰아서 총 틀 수를 최소화.
        #
        # 시나리오 A: WIP 전부 → split
        # 시나리오 B: WIP 전부 → remain
        # → 둘 중 총 틀 수가 적은 쪽 적용. 같으면 현재 상태 유지.

        def _lot_count(net: float) -> int:
            if net <= 0:
                return 0
            return _math.ceil(net / lot_size)

        # ── WIP를 wip_id 단위로 묶어서 처리하는 헬퍼 ──────────────────────────
        from collections import defaultdict as _dd

        def _group_net(batches_list: list) -> float:
            """배치 목록의 실제 생산 필요량 (strand m).
            같은 wip_id를 공유하는 배치들의 합계에서 WIP 재고량을 차감.
            """
            wip_groups: dict = _dd(list)
            non_wip = 0.0
            for b in batches_list:
                if _is_wip_strand(b):
                    wip_groups[b.wip_matched_id].append(b)
                else:
                    non_wip += float(b.total_length_m or 0) * core_mul
            wip_net = 0.0
            for wid, grp in wip_groups.items():
                orders_total = sum(float(b.total_length_m or 0) for b in grp) * core_mul
                wip_qty = wip_len_by_id.get(wid, 0.0) * core_mul
                wip_net += max(0.0, orders_total - wip_qty)
            return non_wip + wip_net

        def _select_receivers(candidates: list, wip_budget_m: float) -> list:
            """WIP 예산(cable m) 내에서 수신 배치를 내림차순으로 greedy 선택.
            선택된 배치들의 합계 ≤ wip_budget_m 을 보장.
            """
            budget = wip_budget_m
            selected = []
            for b in sorted(
                candidates, key=lambda b: float(b.total_length_m or 0), reverse=True
            ):
                sz = float(b.total_length_m or 0)
                if sz <= budget:
                    selected.append(b)
                    budget -= sz
            return selected

        if lot_size > 0:
            wip_in_split = [b for b in split_off if _is_wip_strand(b)]
            wip_in_remain = [b for b in remaining if _is_wip_strand(b)]
            non_wip_split = [b for b in split_off if not _is_wip_strand(b)]
            non_wip_remain = [b for b in remaining if not _is_wip_strand(b)]

            # 현재 net (WIP 공유 합계 기준 차감)
            base_split_net = _group_net(split_off)
            base_remain_net = _group_net(remaining)
            lots_current = _lot_count(base_split_net) + _lot_count(base_remain_net)

            # wip_id 별로 묶기
            wip_ids_in_remain = set(b.wip_matched_id for b in wip_in_remain)
            wip_ids_in_split = set(b.wip_matched_id for b in wip_in_split)

            # 시나리오 A: remain WIP 전부 → split
            # 각 wip_id별 WIP 예산 내에서 split 비-WIP 배치를 선택해 수신
            avail_for_A = list(non_wip_split)  # 수신 후보 (중복 배정 방지용)
            plan_A: list[tuple[int, list]] = []  # (wip_id, 수신배치 목록)
            sim_split_A = base_split_net
            sim_remain_A = base_remain_net

            for wid in wip_ids_in_remain:
                wip_qty_m = wip_len_by_id.get(wid, 0.0)  # cable m
                src_batches = [b for b in wip_in_remain if b.wip_matched_id == wid]
                # remain_net: WIP 해제 → 해당 배치들이 full 생산으로 복귀
                src_total = (
                    sum(float(b.total_length_m or 0) for b in src_batches) * core_mul
                )
                covered_now = max(0.0, src_total - wip_qty_m * core_mul)
                sim_remain_A += src_total - covered_now  # = min(src_total, wip_qty*cm)
                # split_net: WIP 예산 내에서 수신 배치 선택
                recvs = _select_receivers(avail_for_A, wip_qty_m)
                recv_total = sum(float(b.total_length_m or 0) for b in recvs) * core_mul
                sim_split_A -= min(recv_total, wip_qty_m * core_mul)
                plan_A.append((wid, recvs))
                for r in recvs:
                    avail_for_A.remove(r)

            sim_split_A = max(0.0, sim_split_A)
            sim_remain_A = max(0.0, sim_remain_A)
            lots_A = _lot_count(sim_split_A) + _lot_count(sim_remain_A)

            # 시나리오 B: split WIP 전부 → remain
            avail_for_B = list(non_wip_remain)
            plan_B: list[tuple[int, list]] = []
            sim_split_B = base_split_net
            sim_remain_B = base_remain_net

            for wid in wip_ids_in_split:
                wip_qty_m = wip_len_by_id.get(wid, 0.0)
                src_batches = [b for b in wip_in_split if b.wip_matched_id == wid]
                src_total = (
                    sum(float(b.total_length_m or 0) for b in src_batches) * core_mul
                )
                covered_now = max(0.0, src_total - wip_qty_m * core_mul)
                sim_split_B += src_total - covered_now
                recvs = _select_receivers(avail_for_B, wip_qty_m)
                recv_total = sum(float(b.total_length_m or 0) for b in recvs) * core_mul
                sim_remain_B -= min(recv_total, wip_qty_m * core_mul)
                plan_B.append((wid, recvs))
                for r in recvs:
                    avail_for_B.remove(r)

            sim_split_B = max(0.0, sim_split_B)
            sim_remain_B = max(0.0, sim_remain_B)
            lots_B = _lot_count(sim_split_B) + _lot_count(sim_remain_B)

            # 최적 시나리오 적용
            if lots_A < lots_current and lots_A <= lots_B:
                # remain WIP 해제
                for b in wip_in_remain:
                    b.wip_matched_id = None
                # split 수신 배치에 wip_id 부여
                for wid, recvs in plan_A:
                    for r in recvs:
                        r.wip_matched_id = wid
            elif lots_B < lots_current and lots_B < lots_A:
                # split WIP 해제
                for b in wip_in_split:
                    b.wip_matched_id = None
                # remain 수신 배치에 wip_id 부여
                for wid, recvs in plan_B:
                    for r in recvs:
                        r.wip_matched_id = wid

        def _calc_lots(batches: list) -> tuple[int, float]:
            """WIP 공유 합계 기준 net qty로 틀 수와 연선 작업량 계산"""
            net = _group_net(batches)
            if lot_size > 0 and net > 0:
                lots = _math.ceil(net / lot_size)
                return lots, lots * lot_size
            elif net > 0:
                return 1, net
            else:
                return 0, 0.0

        # ── 각 그룹 net qty 및 틀 수 계산 (WIP 연선/절연재고 제외) ──────────
        split_lots, split_len_work = _calc_lots(split_off)
        remain_lots, remain_len_work = _calc_lots(remaining)

        split_len_raw = sum(float(b.total_length_m or 0) for b in split_off)
        remain_len_raw = sum(float(b.total_length_m or 0) for b in remaining)
        split_len = split_len_work  # 헤더 total_length_m = 실제 연선 작업량
        remain_len = remain_len_work

        # duration은 원본 비율로 배분 (작업량 기준)
        total_work = split_len + remain_len
        split_dur = orig_dur * split_len / total_work if total_work > 0 else 0
        remain_dur = orig_dur * remain_len / total_work if total_work > 0 else 0

        split_due = min(
            (b.due_date for b in split_off if b.due_date), default=header.due_date
        )
        remain_due = min(
            (b.due_date for b in remaining if b.due_date), default=header.due_date
        )
        split_pri = min((b.customer_priority or 99 for b in split_off), default=99)
        remain_pri = min((b.customer_priority or 99 for b in remaining), default=99)

        split_wip_count = sum(1 for b in split_off if _is_wip_strand(b))
        remain_wip_count = sum(1 for b in remaining if _is_wip_strand(b))

        # 신규 그룹 헤더 생성
        new_header = ProductionBatch(
            run_label=header.run_label,
            sales_order_id=split_off[0].sales_order_id
            if split_off
            else header.sales_order_id,
            sales_order_line=split_off[0].sales_order_line
            if split_off
            else header.sales_order_line,
            item_code=header.item_code,
            routing_code=header.routing_code,
            process_name=header.process_name,
            batch_seq=-1,
            drum_count=split_lots,
            drum_length_m=header.drum_length_m,
            total_length_m=split_len,
            extra_length_m=0,
            sq_mm2=header.sq_mm2,
            core_count=header.core_count,
            core_colors=header.core_colors,
            sheath_color=header.sheath_color,
            customer_name=split_off[0].customer_name
            if split_off
            else header.customer_name,
            due_date=split_due,
            customer_priority=split_pri,
            line_speed_mpm=header.line_speed_mpm,
            setup_time_min=header.setup_time_min,
            estimated_duration_min=split_dur,
            status="planned",
            product_group=header.product_group,
            voltage=header.voltage,
            conductor_material=header.conductor_material,
            stranding_type=header.stranding_type,
            batch_group=new_group,
            spec_raw=header.spec_raw,
            remarks=(
                f"연선그룹 {len(split_off)}건 {split_lots}틀 / "
                f"수주총량 {split_len_raw:.0f}m → 연선작업량 {split_len:.0f}m"
                + (f" (WIP {split_wip_count}건 제외)" if split_wip_count else "")
                + f" (분할 {suffix}, 틀단위 {lot_size:.0f}m)"
            ),
        )
        db.add(new_header)

        # 원본 헤더 업데이트 (잔여 그룹)
        header.drum_count = remain_lots
        header.total_length_m = remain_len
        header.estimated_duration_min = remain_dur
        header.due_date = remain_due
        header.customer_priority = remain_pri
        header.remarks = (
            f"연선그룹 {len(remaining)}건 {remain_lots}틀 / "
            f"수주총량 {remain_len_raw:.0f}m → 연선작업량 {remain_len:.0f}m"
            + (f" (WIP {remain_wip_count}건 제외)" if remain_wip_count else "")
            + f" (분할 잔여, 틀단위 {lot_size:.0f}m)"
        )

    # ── 헤더 잔여 작업 0인 경우 wip_complete 처리 (Phase 1 연선 그룹) ─────────
    # remain_lots == 0이면 잔여 배치 전부 WIP 재고로 충당됨 → 연선 작업 불필요.
    # Stage 2가 이 그룹을 1분짜리 불가시 태스크로 생성하지 않도록 미리 처리.
    # (remain_lots == -1은 Phase 2 그룹 / 헤더 없음 → 이 처리 불필요)
    if header and remain_lots == 0:
        header.status = "wip_complete"

    # ── 개별 배치들의 그룹 변경 ──────────────────────────────────────────────
    # batch_id_set: WIP greedy 재배정 이후 최종 split_off 집합 (line 1191에서 갱신됨)
    final_batch_ids = list(batch_id_set)
    updated = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_id.in_(final_batch_ids),
            ProductionBatch.batch_group == batch_group,
        )
        .update({ProductionBatch.batch_group: new_group}, synchronize_session=False)
    )

    # ── schedule_task 정리 (FK: audit_log → schedule_task) ───────────────────
    task_ids_old = [
        t.task_id
        for t in db.query(ScheduleTaskModel.task_id)
        .filter(ScheduleTaskModel.batch_group == batch_group)
        .all()
    ]
    task_ids_new = [
        t.task_id
        for t in db.query(ScheduleTaskModel.task_id)
        .filter(ScheduleTaskModel.batch_group == new_group)
        .all()
    ]
    all_task_ids = task_ids_old + task_ids_new
    if all_task_ids:
        db.execute(
            text("DELETE FROM audit_log WHERE task_id IN :ids"),
            {"ids": tuple(all_task_ids)},
        )

    db.query(ScheduleTaskModel).filter(
        ScheduleTaskModel.batch_group == batch_group
    ).delete(synchronize_session=False)

    db.query(ScheduleTaskModel).filter(
        ScheduleTaskModel.batch_group == new_group
    ).delete(synchronize_session=False)

    db.commit()

    # ── 잔여 planned 배치 수 조회 (프론트 메시지용) ──────────────────────────
    # Stage 2 재실행 후 원본 그룹의 간트 블록 생성 여부를 클라이언트에 알린다.
    # "planned" 배치가 0이면 전체 WIP 충당 → 간트 블록 미표시 예상.
    original_remaining_planned = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.status == "planned",
        )
        .count()
    )

    return {
        "original_group": batch_group,
        "new_group": new_group,
        "moved_batches": updated,
        "original_remaining_planned": original_remaining_planned,
    }


@router.patch("/batch/{batch_id}/status", summary="배치 상태 변경")
def update_batch_status(batch_id: int, body: dict, db: Session = Depends(get_db)):
    """배치 하나의 status를 변경한다 (planned / in_progress / completed).

    간트 인라인 배지 클릭 및 컨텍스트 메뉴에서 호출된다.
    동일 batch_group 내 헤더(batch_seq=-1)와 해당 배치만 변경한다.
    """
    new_status = body.get("status")
    valid_statuses = {"planned", "in_progress", "completed"}
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"유효하지 않은 status: {new_status}. 허용: {valid_statuses}",
        )

    batch = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_id == batch_id).first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail=f"배치 {batch_id} 없음")

    batch.status = new_status

    # 동일 batch_group 의 헤더(batch_seq=-1)도 함께 갱신한다.
    # 헤더는 그룹 전체의 대표 상태를 나타내므로 개별 배치 변경 시 동기화가 필요하다.
    header = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch.batch_group,
            ProductionBatch.batch_seq == -1,
        )
        .first()
    )
    if header:
        header.status = new_status

    # WIP 예상 → 실적_추정 승격: completed 전환 시만 동작, 나머지는 no-op
    _promote_expected_to_estimated(batch.batch_id, new_status, db)
    if header and header.batch_id != batch.batch_id:
        # cascade: header 도 함께 completed 로 전환됐으므로 header WIP 도 승격
        _promote_expected_to_estimated(header.batch_id, new_status, db)

    db.commit()
    return {
        "batch_id": batch_id,
        "status": new_status,
        "batch_group": batch.batch_group,
    }


@router.patch("/batch/{batch_id}", summary="배치 수정")
def update_batch(batch_id: int, body: dict, db: Session = Depends(get_db)):
    """배치의 편집 가능 필드를 수정한다.

    허용 필드: sheath_color, drum_count, drum_length_m, remarks, total_length_m
    drum_count 또는 drum_length_m 변경 시 total_length_m을 자동 재계산한다.
    """
    batch = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_id == batch_id).first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail=f"배치 {batch_id} 없음")

    allowed = {
        "sheath_color",
        "drum_count",
        "drum_length_m",
        "remarks",
        "total_length_m",
        "status",
    }
    for key, val in body.items():
        if key in allowed:
            setattr(batch, key, val)

    # status 유효성 검증
    if "status" in body:
        valid_statuses = {"planned", "in_progress", "completed"}
        if body["status"] not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"유효하지 않은 status: {body['status']}. 허용: {valid_statuses}",
            )

    # total_length_m 재계산 — drum_count 또는 drum_length_m이 변경된 경우
    if "drum_count" in body or "drum_length_m" in body:
        batch.total_length_m = float(batch.drum_length_m or 0) * (batch.drum_count or 1)

    # WIP 예상 → 실적_추정 승격: status 필드가 있을 때만, completed 여부는 헬퍼가 판단
    if "status" in body:
        _promote_expected_to_estimated(batch.batch_id, body["status"], db)

    db.commit()
    return {"batch_id": batch_id, "updated": list(body.keys())}


@router.patch(
    "/batch-group/{batch_group:path}/status",
    summary="배치 그룹 전체 상태 일괄 변경",
)
def update_batch_group_status(
    batch_group: str, body: dict, db: Session = Depends(get_db)
):
    """배치 그룹 내 모든 배치의 status를 일괄 변경한다.

    데모 시나리오에서 다수 배치를 한번에 진행중/완료로 전환할 때 사용.
    body: {"status": "in_progress" | "completed" | "planned"}
    """
    new_status = body.get("status")
    valid_statuses = {"planned", "in_progress", "completed"}
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"유효하지 않은 status: {new_status}. 허용: {valid_statuses}",
        )

    batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == batch_group)
        .all()
    )
    if not batches:
        raise HTTPException(status_code=404, detail=f"배치 그룹 '{batch_group}' 없음")

    updated_ids = []
    for b in batches:
        b.status = new_status
        updated_ids.append(b.batch_id)
        # WIP 예상 → 실적_추정 승격: completed 전환 시만 동작, 나머지는 no-op
        _promote_expected_to_estimated(b.batch_id, new_status, db)

    db.commit()
    return {
        "batch_group": batch_group,
        "status": new_status,
        "updated_count": len(updated_ids),
        "batch_ids": updated_ids,
    }


@router.get("/batch-status-summary", summary="배치 상태 요약")
def get_batch_status_summary(db: Session = Depends(get_db)):
    """현재 모든 배치의 상태별 집계를 반환한다.

    업로드 전 확인 모달에서 사용: frozen 배치 수, planned 배치 수, WIP 현황.
    """

    # 상태별 배치 수 집계 (batch_seq >= 1인 실제 배치만, -1은 그룹 헤더)
    status_counts = (
        db.query(ProductionBatch.status, func.count(ProductionBatch.batch_id))
        .filter(ProductionBatch.batch_seq >= 1)
        .group_by(ProductionBatch.status)
        .all()
    )
    summary: dict[str, int] = {}
    for status, count in status_counts:
        summary[status] = count
    planned = summary.get("planned", 0)
    non_planned = sum(c for s, c in summary.items() if s != "planned")

    # frozen 배치에 매칭된 WIP 수 (planned 외 모든 상태)
    frozen_wip_count = (
        db.query(func.count(ProductionBatch.wip_matched_id))
        .filter(
            ProductionBatch.status != "planned",
            ProductionBatch.wip_matched_id.isnot(None),
        )
        .scalar()
    ) or 0

    # 사용 가능한 WIP 수
    available_wip_count = (
        db.query(func.count(WipInventory.wip_id))
        .filter(WipInventory.status == "사용가능")
        .scalar()
    ) or 0

    # 전체 수주 수
    from app.infrastructure.models.sales_order import SalesOrder

    total_orders = db.query(func.count(SalesOrder.order_id)).scalar() or 0

    return {
        "planned": planned,
        "scheduled": summary.get("scheduled", 0),
        "wip_complete": summary.get("wip_complete", 0),
        "in_progress": summary.get("in_progress", 0),
        "completed": summary.get("completed", 0),
        "total_batches": planned + non_planned,
        "frozen_count": non_planned,
        "frozen_wip_count": frozen_wip_count,
        "available_wip_count": available_wip_count,
        "total_orders": total_orders,
    }


@router.delete("/runs/{run_label}", summary="특정 계획 실행 삭제")
def delete_run(run_label: str, db: Session = Depends(get_db)):
    """특정 run_label의 실행 데이터를 삭제한다.

    - audit_log, schedule_task, production_batch, sales_order: run_label 행 삭제
    - wip_inventory: run_label 행 삭제 + 해당 run에서 매칭된 다른 WIP 행 상태 초기화
      (matched_order_id → NULL, status → 사용가능)
    """
    counts = {}

    # production_batch / wip_inventory / sales_order 는 상호 FK 로 얽혀 있다:
    #   - wip_inventory.source_batch_id → production_batch.batch_id  (RESTRICT)
    #   - production_batch.wip_matched_id → wip_inventory.wip_id      (RESTRICT)
    #   - sales_order.wip_id              → wip_inventory.wip_id      (RESTRICT)
    # 어느 쪽을 먼저 지워도 다른 쪽이 막는다. 따라서
    #  (a) 삭제 대상 run 바깥에서 들어오는 FK 는 모두 NULL 로 끊어두고,
    #  (b) 자식 → 부모 순서로 삭제한다.

    # (a-1) 이 run 의 production_batch → wip_inventory 참조 해제 (동일 run 내부 순환)
    db.execute(
        text("UPDATE production_batch SET wip_matched_id = NULL WHERE run_label = :rl"),
        {"rl": run_label},
    )

    # (a-2) 다른 run 의 wip_inventory 가 이 run 의 production_batch 를 참조 중이면 NULL 로 끊기
    db.execute(
        text(
            """
            UPDATE wip_inventory
            SET source_batch_id = NULL
            WHERE source_batch_id IN (
                SELECT batch_id FROM production_batch WHERE run_label = :rl
            )
            """
        ),
        {"rl": run_label},
    )

    # (a-3) 다른 run 의 sales_order 가 이 run 의 wip_inventory 를 참조 중이면 NULL 로 끊기
    db.execute(
        text(
            """
            UPDATE sales_order
            SET wip_id = NULL
            WHERE wip_id IN (
                SELECT wip_id FROM wip_inventory WHERE run_label = :rl
            )
            """
        ),
        {"rl": run_label},
    )

    # (b) 자식 → 부모 순으로 삭제한다.
    #     audit_log / schedule_task / sales_order 는 production_batch·wip_inventory 를 참조하므로 먼저,
    #     그 다음 wip_inventory, 마지막으로 production_batch.
    for table in ["audit_log", "schedule_task", "sales_order"]:
        result = db.execute(
            text(f"DELETE FROM {table} WHERE run_label = :rl"),
            {"rl": run_label},
        )
        counts[table] = result.rowcount

    wip_del = db.execute(
        text("DELETE FROM wip_inventory WHERE run_label = :rl"),
        {"rl": run_label},
    )
    counts["wip_inventory"] = wip_del.rowcount

    pb_del = db.execute(
        text("DELETE FROM production_batch WHERE run_label = :rl"),
        {"rl": run_label},
    )
    counts["production_batch"] = pb_del.rowcount

    # wip_inventory: 이 run에서 매칭(사용완료)됐지만 다른 run_label을 가진 WIP 상태 초기화.
    # production_batch가 이미 삭제됐으므로 wip_matched_id 역참조가 깨진 WIP를 정리한다.
    # matched_order_id는 "order_id:order_line" 형식 — 해당 run의 sales_order가 지워졌으므로
    # 더 이상 유효하지 않다. status를 사용가능으로 되돌리고 매칭 정보를 초기화한다.
    wip_reset = db.execute(
        text("""
            UPDATE wip_inventory
            SET status = '사용가능',
                matched_order_id = NULL
            WHERE status = '사용완료'
              AND run_label != :rl
              AND matched_order_id IS NOT NULL
        """),
        {"rl": run_label},
    )
    counts["wip_inventory_reset"] = wip_reset.rowcount

    db.commit()
    total = counts["wip_inventory"] + sum(
        v
        for k, v in counts.items()
        if k not in ("wip_inventory", "wip_inventory_reset")
    )
    if total == 0 and counts.get("wip_inventory_reset", 0) == 0:
        raise HTTPException(status_code=404, detail=f"run_label '{run_label}' 없음")
    return {"run_label": run_label, "deleted": counts, "total": total}


@router.get("/runs", summary="계획 실행 이력 목록")
def list_runs(db: Session = Depends(get_db)) -> list[dict]:
    """저장된 모든 run_label 목록을 배치 수 및 최초 생성 시각과 함께 반환한다.

    최신 실행이 상단에 오도록 created_at 내림차순 정렬.
    outsource_count: ERP 외주 플래그(is_outsourced=True) 수주 건수.
    parent_run_label: stage1/update 로 파생된 경우 어느 이전 run 의 후속인지.
    """
    from app.infrastructure.models.sales_order import SalesOrder

    rows = (
        db.query(
            ProductionBatch.run_label,
            func.count(ProductionBatch.batch_id).label("batch_count"),
            func.min(ProductionBatch.created_at).label("created_at"),
            # 같은 run 내에서 parent_run_label 은 모두 동일 (stage1/update 에서 일괄 설정).
            # NULL 과 non-NULL 이 섞일 경우 MAX 로 비-NULL 우선. 최초 run 은 NULL 유지.
            func.max(ProductionBatch.parent_run_label).label("parent_run_label"),
        )
        # `test-` prefix 는 pytest fixture 가 SAVEPOINT 밖에서 commit 되어 leak
        # 됐을 때만 등장. 운영 UI 가 test fixture 를 최신 run 으로 골라 0배치
        # 화면을 띄우는 회귀를 막기 위해 응답에서 제외.
        .filter(~ProductionBatch.run_label.like("test-%"))
        .group_by(ProductionBatch.run_label)
        .order_by(func.min(ProductionBatch.created_at).desc())
        .all()
    )

    # 런별 외주 건수 — ProductionBatch.sales_order_id 기준으로 SalesOrder 조인.
    # SalesOrder.run_label 은 forward-roll 로 최신 run 을 가리킬 수 있으므로
    # pb.run_label 기준으로 집계해야 버전별 정확한 외주 건수를 얻는다.
    outsource_counts: dict[str, int] = {}
    if rows:
        run_labels = [r.run_label for r in rows]
        outsource_rows = (
            db.query(
                ProductionBatch.run_label,
                func.count(func.distinct(ProductionBatch.sales_order_id)).label("cnt"),
            )
            .join(
                SalesOrder,
                ProductionBatch.sales_order_id == SalesOrder.order_id,
            )
            .filter(
                ProductionBatch.run_label.in_(run_labels),
                SalesOrder.is_outsourced == True,  # noqa: E712
            )
            .group_by(ProductionBatch.run_label)
            .all()
        )
        outsource_counts = {r.run_label: r.cnt for r in outsource_rows}

    return [
        {
            "run_label": row.run_label,
            "batch_count": row.batch_count,
            "created_at": row.created_at,
            "parent_run_label": row.parent_run_label,
            "outsource_count": outsource_counts.get(row.run_label, 0),
        }
        for row in rows
    ]


@router.get("/runs/compare", summary="두 run 간 배치/스케줄 diff")
def compare_runs(
    before: str,
    after: str,
    db: Session = Depends(get_db),
) -> dict:
    """두 run_label 의 ProductionBatch + ScheduleTask 를 비교해 added/removed/
    moved/unchanged 로 분류한다.

    stable key = (sales_order_id, sales_order_line, process_name, batch_seq).
    batch_id 는 run 마다 autoincrement 로 달라지므로 논리적 식별자가 필요.
    응답 스키마는 ScheduleDiffResponse (types/diff.ts) 와 호환 — task_id 필드에
    stable key 의 문자열 표현을 넣어 UI 에서 dedup 하기 편하도록 함.
    """
    from app.infrastructure.models.schedule_task import ScheduleTask

    if before == after:
        raise HTTPException(status_code=400, detail="before 와 after 는 달라야 합니다")

    # 두 run 의 배치 + task 를 한 번에 로드 (N+1 방지)
    def _load(run_label: str) -> dict[tuple, dict]:
        """stable_key → {batch, task, meta} 매핑."""
        rows = (
            db.query(ProductionBatch, ScheduleTask)
            .outerjoin(
                ScheduleTask,
                (ScheduleTask.batch_id == ProductionBatch.batch_id)
                & (ScheduleTask.run_label == ProductionBatch.run_label),
            )
            .filter(ProductionBatch.run_label == run_label)
            .all()
        )
        result: dict[tuple, dict] = {}
        for b, t in rows:
            key = (
                b.sales_order_id or "",
                b.sales_order_line if b.sales_order_line is not None else 0,
                b.process_name or "",
                b.batch_seq if b.batch_seq is not None else 0,
            )
            # batch_seq=-1 (연선 헤더) 가 여러 수주를 묶은 경우 sales_order_id 가
            # 비어 있을 수 있음 — batch_group 으로 보강.
            if not key[0]:
                key = (b.batch_group or f"UNK_{b.batch_id}",) + key[1:]
            start_iso = t.start_datetime.isoformat() if t and t.start_datetime else None
            end_iso = t.end_datetime.isoformat() if t and t.end_datetime else None
            result[key] = {
                "batch_id": b.batch_id,
                "equipment_code": (t.equipment_code if t else None) or b.equipment_code,
                "start": start_iso,
                "end": end_iso,
                "process_name": b.process_name,
                "batch_group": b.batch_group,
                "sales_order_id": b.sales_order_id,
                "sales_order_line": b.sales_order_line,
                "customer_name": b.customer_name,
                "status": b.status,
                "sheath_color": b.sheath_color,
                "sq_mm2": float(b.sq_mm2) if b.sq_mm2 is not None else None,
                "due_date": b.due_date.isoformat() if b.due_date else None,
            }
        return result

    before_map = _load(before)
    after_map = _load(after)

    if not before_map and not after_map:
        raise HTTPException(
            status_code=404,
            detail=f"두 run 모두 배치가 없습니다: before={before}, after={after}",
        )

    before_keys = set(before_map.keys())
    after_keys = set(after_map.keys())
    added_keys = after_keys - before_keys
    removed_keys = before_keys - after_keys
    common_keys = before_keys & after_keys

    def _delta_hours(a_iso: str | None, b_iso: str | None) -> float | None:
        if not a_iso or not b_iso:
            return None
        try:
            a_dt = datetime.fromisoformat(a_iso)
            b_dt = datetime.fromisoformat(b_iso)
            return (b_dt - a_dt).total_seconds() / 3600.0
        except Exception:
            return None

    def _key_str(k: tuple) -> str:
        return "|".join(str(x) for x in k)

    moved_tasks: list[dict] = []
    unchanged_task_ids: list[str] = []

    for key in sorted(common_keys, key=_key_str):
        b_row = before_map[key]
        a_row = after_map[key]
        start_changed = b_row["start"] != a_row["start"]
        end_changed = b_row["end"] != a_row["end"]
        eq_changed = b_row["equipment_code"] != a_row["equipment_code"]

        if not (start_changed or end_changed or eq_changed):
            unchanged_task_ids.append(_key_str(key))
            continue

        moved_tasks.append(
            {
                "task_id": _key_str(key),
                "old_start": b_row["start"],
                "old_end": b_row["end"],
                "old_equipment": b_row["equipment_code"],
                "new_start": a_row["start"],
                "new_end": a_row["end"],
                "new_equipment": a_row["equipment_code"],
                "start_delta_hours": _delta_hours(b_row["start"], a_row["start"]),
                "end_delta_hours": _delta_hours(b_row["end"], a_row["end"]),
                "equipment_changed": eq_changed,
                "batch_group": a_row["batch_group"],
                "process_name": a_row["process_name"],
                "sales_order_id": a_row["sales_order_id"],
                "customer_name": a_row["customer_name"],
                "sheath_color": a_row["sheath_color"],
                "cross_section": a_row["sq_mm2"],
            }
        )

    added_tasks: list[dict] = []
    for key in sorted(added_keys, key=_key_str):
        a_row = after_map[key]
        added_tasks.append(
            {
                "task_id": _key_str(key),
                "start": a_row["start"],
                "end": a_row["end"],
                "equipment": a_row["equipment_code"],
                "batch_group": a_row["batch_group"],
                "process_name": a_row["process_name"],
                "sales_order_id": a_row["sales_order_id"],
                "customer_name": a_row["customer_name"],
                "sheath_color": a_row["sheath_color"],
                "cross_section": a_row["sq_mm2"],
                "due_date": a_row["due_date"],
            }
        )

    removed_tasks: list[dict] = []
    for key in sorted(removed_keys, key=_key_str):
        b_row = before_map[key]
        removed_tasks.append(
            {
                "task_id": _key_str(key),
                "start": b_row["start"],
                "end": b_row["end"],
                "equipment": b_row["equipment_code"],
                "batch_group": b_row["batch_group"],
                "process_name": b_row["process_name"],
                "sales_order_id": b_row["sales_order_id"],
                "customer_name": b_row["customer_name"],
                "sheath_color": b_row["sheath_color"],
                "cross_section": b_row["sq_mm2"],
                "due_date": b_row["due_date"],
            }
        )

    return {
        "run_label_before": before,
        "run_label_after": after,
        "kind": "run_compare",
        "created_at": datetime.now().isoformat(),
        "summary": {
            "moved": len(moved_tasks),
            "added": len(added_tasks),
            "removed": len(removed_tasks),
            "unchanged": len(unchanged_task_ids),
            "total_before": len(before_keys),
            "total_after": len(after_keys),
        },
        "moved_tasks": moved_tasks,
        "added_tasks": added_tasks,
        "removed_tasks": removed_tasks,
        "unchanged_task_ids": unchanged_task_ids,
    }


# ---------------------------------------------------------------------------
# Task 2.3: batch_group 미배정 (unassign) — 단일 트랜잭션 엔드포인트
#
# 왜 여기서 commit하는가:
#   batch_group_lifecycle.unassign_batch_group은 flush만 수행 (Eng Critical #1).
#   라우트가 서비스 flush 직후 audit_log INSERT를 추가하고 단 한 번만 commit하여,
#   "unassign 상태 전이 + 감사 로그"가 원자적으로 함께 persist되거나 둘 다 롤백되게 한다.
# ---------------------------------------------------------------------------


@router.post(
    "/batch-group/{batch_group}/unassign",
    summary="batch_group 전체를 미배정으로 soft-delete (사유 기록)",
)
def unassign_batch_group_endpoint(
    batch_group: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """단일 트랜잭션: 서비스 flush + audit_log INSERT + commit.

    Body:
        { "reason": "자재지연" | "설비고장" | "납기재협상" | "기타" }
        생략 또는 None이면 서비스가 '기타'로 저장.

    Responses:
        200: { batch_group, affected_batches, affected_tasks, reason, idempotent }
        400: 상태(planned 외) / WIP 매칭 / reason 검증 오류
        404: batch_group 없음
    """
    from app.infrastructure.models.audit_log import AuditLog
    from app.application.validation.batch_group_lifecycle import (
        BatchGroupNotFoundError,
        BatchGroupReasonError,
        BatchGroupStatusError,
        BatchGroupWipMatchedError,
        unassign_batch_group,
    )

    reason = (body or {}).get("reason")

    try:
        result = unassign_batch_group(db, batch_group, reason=reason)
    except BatchGroupNotFoundError as exc:
        # 404: 존재하지 않는 batch_group — 상태 전이 없이 즉시 실패
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        BatchGroupStatusError,
        BatchGroupWipMatchedError,
        BatchGroupReasonError,
    ) as exc:
        # 400: 비즈니스 제약 위반 (상태/WIP/reason) — 서비스는 변경 전에 예외를 던지므로
        # rollback까지 할 필요는 없으나 세션 상태를 명시적으로 되돌려 다음 쿼리 안전 보장.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 멱등 호출(이미 모두 unassigned)에는 새 audit row를 남기지 않는다 — 감사 로그가
    # 실제 상태 전이에 1:1 대응하도록 유지 (중복 '변경 없음' 기록 방지).
    if not result.get("idempotent"):
        # run_label은 서비스 결과에 없으므로 첫 affected_batch에서 조회 (정통 소스).
        # 없으면 'manual' (sm_inventory.py 기존 컨벤션: wip.run_label or "manual").
        batch_run_label: str | None = None
        first_batch_id = (
            result["affected_batches"][0] if result["affected_batches"] else None
        )
        if first_batch_id is not None:
            batch_run_label = (
                db.query(ProductionBatch.run_label)
                .filter(ProductionBatch.batch_id == first_batch_id)
                .scalar()
            )

        db.add(
            AuditLog(
                run_label=batch_run_label or "manual",
                stage="stage1",
                action_type="BATCH_GROUP_UNASSIGNED",
                batch_id=first_batch_id,
                decision_reason=(
                    f"batch_group {batch_group} unassigned "
                    f"(reason={result['reason']}, "
                    f"tasks={len(result['affected_tasks'])})"
                ),
            )
        )

    db.commit()
    return result


# ---------------------------------------------------------------------------
# POST /pipeline/batch-group/{bg}/restore — unassigned → planned 복원 (Task 3.3)
#
# 왜 단일 트랜잭션:
#   batch_group_lifecycle.restore_batch_group은 flush만 수행 (Eng Critical #2).
#   라우트가 서비스 flush 직후 audit_log INSERT를 추가하고 한 번만 commit하여,
#   "상태 복원 + 감사 로그"가 원자적으로 persist 되거나 둘 다 롤백되게 한다.
#
# conflicts가 있을 경우:
#   서비스는 mutate하지 않고 반환하므로 commit/rollback 없이 바로 409로 매핑.
# ---------------------------------------------------------------------------


@router.post(
    "/batch-group/{batch_group}/restore",
    summary="unassigned batch_group을 원래 자리로 복원 (status flip)",
)
def restore_batch_group_endpoint(
    batch_group: str, db: Session = Depends(get_db)
) -> dict:
    """단일 트랜잭션: 서비스 flush → audit_log INSERT → commit.

    Responses:
        200: 성공 — restored_tasks 배열 (멱등이면 빈 배열)
        404: batch_group 없음
        400: unassigned 외 상태 혼재
        409: 원래 자리 점유됨 — detail.conflicts 에 상세 반환
    """
    from app.infrastructure.models.audit_log import AuditLog
    from app.application.validation.batch_group_lifecycle import (
        BatchGroupNotFoundError,
        BatchGroupStatusError,
        restore_batch_group,
    )

    try:
        result = restore_batch_group(db, batch_group)
    except BatchGroupNotFoundError as exc:
        # 404: 존재하지 않는 batch_group — 상태 전이 없이 즉시 실패
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BatchGroupStatusError as exc:
        # 400: planned 외 상태 혼재 — 서비스가 변경 전 예외 throw. 세션 clean 유지.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result["conflicts"]:
        # 서비스는 아무것도 mutate하지 않고 반환 — commit 하지 않아도 세션 clean.
        # detail은 dict로 전달해 프론트가 conflicts 배열을 바로 파싱할 수 있게 함.
        raise HTTPException(
            status_code=409,
            detail={"conflicts": result["conflicts"]},
        )

    # 멱등 호출(이미 모두 planned)에는 새 audit row를 남기지 않는다 — 감사 로그가
    # 실제 상태 전이에 1:1 대응하도록 유지 (unassign 엔드포인트와 동일 규칙).
    if not result.get("idempotent"):
        # run_label은 서비스 결과에 없으므로 복원된 batch 중 하나에서 조회.
        # 없으면 'manual' (unassign 엔드포인트와 동일 컨벤션).
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_group == batch_group)
            .first()
        )
        run_label = (batch.run_label if batch else None) or "manual"
        first_batch_id = batch.batch_id if batch else None

        db.add(
            AuditLog(
                run_label=run_label,
                stage="stage1",
                action_type="BATCH_GROUP_RESTORED",
                batch_id=first_batch_id,
                decision_reason=(
                    f"batch_group {batch_group} restored, "
                    f"tasks={len(result['restored_tasks'])}"
                ),
            )
        )

    db.commit()
    return result


# ---------------------------------------------------------------------------
# POST /pipeline/batch-group/{bg}/restore-at — anchor 기준 재배치 preview (Task 1.2)
#
# no-mutation preview: 서비스가 flush/commit 없이 계산 결과만 반환한다.
# 프론트가 CascadePreviewModal로 렌더 후 확정 시 bulk-update-v2로 일괄 반영.
# ---------------------------------------------------------------------------


from app.presentation.schemas.restore_at import RestoreAtRequest, RestoreAtResponse  # noqa: E402
from app.application.validation.batch_group_lifecycle import (  # noqa: E402
    BatchGroupNotFoundError,
    BatchGroupStatusError,
    compute_restore_at_plan,
)


@router.post(
    "/batch-group/{batch_group}/restore-at",
    summary="unassigned batch_group 을 anchor 위치 기준으로 재배치 preview (no mutation)",
)
def restore_batch_group_at_endpoint(
    batch_group: str,
    body: RestoreAtRequest,
    db: Session = Depends(get_db),
) -> RestoreAtResponse:
    """no-mutation preview — 프론트가 이 결과를 CascadePreviewModal 로 렌더 →
    확정 시 bulk-update-v2 로 일괄 반영.

    Responses:
        200: RestoreAtResponse (task_positions + cascade-preview-v2 호환 필드)
        400: unassigned 외 상태 혼재 (BatchGroupStatusError)
        404: batch_group 없음 (BatchGroupNotFoundError)
        422: body 검증 실패 (Pydantic)
    """
    try:
        result = compute_restore_at_plan(
            db,
            batch_group=batch_group,
            anchor_equipment_code=body.anchor_equipment_code,
            anchor_start=body.anchor_start,
        )
    except BatchGroupNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BatchGroupStatusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RestoreAtResponse(
        batch_group=result.batch_group,
        task_positions=[tp.__dict__ for tp in result.task_positions],
        pushes=result.pushes,
        pulls=result.pulls,
        unresolved=result.unresolved,
        request_id=result.request_id,
        can_auto_resolve=result.can_auto_resolve,
        iter_count=result.iter_count,
        truncated=result.truncated,
    )


# ---------------------------------------------------------------------------
# GET /pipeline/batch-group-snapshots — unassigned batch_group 목록 (Task 3.3)
#
# 프론트 OrderInbox가 새로고침 시 호출. batch_group 단위 집계 + unassign_reason 포함.
#
# equipment_group 관련:
#   백엔드 ProductionBatch에는 equipment_group 컬럼이 없다 (Task 3.1 검증).
#   process_name을 그대로 노출하고, 프론트의 toEquipmentGroup(process_name, batch_group)
#   헬퍼가 canonicalize 한다 — 백엔드/프론트 계약 단순화.
# ---------------------------------------------------------------------------


@router.get(
    "/batch-group-snapshots",
    summary="unassigned 상태 batch_group 목록 (사유 + 공정체인 포함)",
)
def list_batch_group_snapshots(db: Session = Depends(get_db)) -> dict:
    """Returns: { "groups": [BatchGroupSnapshot] } — 프론트 OrderInbox 용."""
    rows = (
        db.query(ProductionBatch).filter(ProductionBatch.status == "unassigned").all()
    )
    groups: dict[str, dict] = {}
    for b in rows:
        g = groups.setdefault(
            b.batch_group,
            {
                "batch_group": b.batch_group,
                "customer": b.customer_name or "",
                "spec": b.spec_raw or "",
                "color": b.sheath_color or "",
                "total_length_m": 0.0,
                "delivery_date": b.due_date.isoformat() if b.due_date else "",
                "processes": [],
                "order_count": 0,
                "unassign_reason": b.unassign_reason or "기타",
            },
        )
        g["total_length_m"] += float(b.total_length_m or 0)
        g["order_count"] += 1
        # equipment_group은 백엔드 모델에 없음 — process_name을 그대로 노출.
        # 프론트의 toEquipmentGroup(process_name, batch_group)이 canonicalize.
        g["processes"].append(
            {
                "process": b.process_name or "",
                "equipment_group": b.process_name or "",
            }
        )
    return {"groups": list(groups.values())}
