"""Stage 1 파이프라인 API — ERP 업로드 → 작업지시서 생성 → Excel 다운로드"""

import logging
import threading
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.infrastructure.database import SessionLocal, get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory
from app.services.batch_grouping import (
    create_batches,
    detect_split_candidates,
    format_spec_display,
)
from app.services.constraint_checker import validate_all  # noqa: F401 — used in stage2
from app.services.erp_parser import parse_erp_file
from app.services.excel_exporter import export_plan
from app.services.schedule_optimizer import auto_schedule  # noqa: F401 — used in stage2
from app.services.cp_sat_optimizer import cp_sat_schedule  # CP-SAT 최적화 엔진
from app.services.wip_matching import match_wip

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
        from app.services.llm_explainer import generate_batch_summary_sync

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
    """Stage 1 파이프라인 실행:

    1. ERP .xls 파일을 파싱하여 sales_order 테이블에 적재
    2. 재공(WIP) 매칭 — 기존 재고를 수주에 매칭하여 공정 생략
    3. 수주 데이터를 공정별 production_batch로 변환 (납기 범위 필터 가능)
    4. 연선 그룹 분할 후보 감지 (split_gap_days 이상 납기 간격)

    Returns:
        run_label, 파싱 결과, WIP 매칭 결과, 배치 생성 결과, 통합 경고 목록,
        split_candidates (분할 후보 연선 그룹 목록)
    """
    # ── 기존 실행 데이터 정리 (재실행 시 중복 방지) ─────────────────────────
    from app.infrastructure.models.schedule_task import ScheduleTask as ST

    db.query(func.count(ST.task_id)).scalar()  # warm up
    db.execute(text("DELETE FROM audit_log"))
    db.execute(text("DELETE FROM schedule_task"))
    db.execute(text("DELETE FROM production_batch"))
    # sales_order.wip_id FK 참조 해제 후 wip_inventory 삭제
    # (sales_order 자체는 parse_erp_file에서 전체 삭제 후 재적재)
    db.execute(
        text(
            "UPDATE sales_order SET wip_id = NULL, use_wip = FALSE, wip_type = NULL, actual_length_m = NULL"
        )
    )
    db.execute(text("DELETE FROM wip_inventory"))
    db.commit()

    # run_label — 동일 계획 실행의 모든 레코드를 묶는 식별자
    run_label = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Step 1: ERP 파일 파싱 ──────────────────────────────────────────────────
    erp_content = await erp_file.read()
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
    if wip_file:
        try:
            from app.services.wip_parser import parse_wip_file

            wip_content = await wip_file.read()
            if wip_content:
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

    # ── Step 3: 납기 범위 파싱 ────────────────────────────────────────────────
    parsed_from: date | None = None
    parsed_to: date | None = None
    if date_from:
        try:
            parsed_from = datetime.strptime(date_from, "%Y%m%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"date_from 형식 오류: {date_from} (YYYYMMDD)",
            )
    if date_to:
        try:
            parsed_to = datetime.strptime(date_to, "%Y%m%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"date_to 형식 오류: {date_to} (YYYYMMDD)",
            )

    # ── Step 4: production_batch 생성 ─────────────────────────────────────────
    try:
        batch_result = create_batches(
            run_label, db, date_from=parsed_from, date_to=parsed_to
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"배치 생성 실패: {exc}") from exc

    db.commit()

    # ── Step 5: 연선 그룹 분할 후보 감지 ──────────────────────────────────────
    # commit 이후에 실행해야 flush된 배치가 쿼리에 반영된다.
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
            "기준일자 (YYYY-MM-DD, incremental 모드 전용). "
            "이 날짜 이전에 scheduled 된 배치는 동결, "
            "이후 배치는 긴급수주와 합산 재생성."
        ),
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Stage 1 증분/전체 업데이트 — Freeze & Rebuild.

    완료/진행중 배치를 동결하고, planned 배치만 삭제 후 새 ERP 파일로 재계산한다.
    run_label은 parent_run_label을 그대로 재사용하여 create_batches 필터링 호환성을 유지한다.

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
        if latest:
            parent_run_label = latest[0]
        else:
            # 기존 계획이 없으면 새로 생성 (레거시 모드처럼 동작)
            parent_run_label = datetime.now().strftime("%Y%m%d_%H%M%S")

    # parent_run_label에 해당하는 배치가 존재하는지 확인
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

    # run_label 재사용 — create_batches가 run_label로 필터링하므로 필수
    run_label = parent_run_label

    try:
        # ── 1. Frozen 배치 식별 ───────────────────────────────────────────────
        # base_date(기준일자) 지정 시 (incremental 모드):
        #   hard_frozen  : in_progress / completed / wip_complete — 항상 동결
        #   soft_frozen  : 기준일자 이전에 scheduled 된 배치 — 동결
        #   mutable      : 기준일자 이후 scheduled + 모든 planned → 삭제 후 재생성
        #                  (create_batches 가 원래 수주 + 긴급수주를 합산해 새 배치 생성)
        # base_date 미지정 시: planned 외 모든 상태를 보호 (기존 동작)
        from app.infrastructure.models.schedule_task import ScheduleTask

        cutoff_date: date | None = None
        if upload_mode == "incremental" and base_date:
            try:
                cutoff_date = date.fromisoformat(base_date)
            except ValueError:
                pass

        mutable_scheduled_ids: set[int] = set()

        if cutoff_date:
            cutoff_dt = datetime(cutoff_date.year, cutoff_date.month, cutoff_date.day, 0, 0, 0)
            # 기준일자 이전 ScheduleTask 의 batch_id → soft_frozen 대상
            before_task_batch_ids: set[int] = {
                r[0]
                for r in db.query(ScheduleTask.batch_id).filter(
                    ScheduleTask.run_label == run_label,
                    ScheduleTask.start_datetime.isnot(None),
                    ScheduleTask.start_datetime < cutoff_dt,
                ).all()
            }
            all_scheduled = db.query(ProductionBatch).filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.status == "scheduled",
            ).all()
            hard_frozen = db.query(ProductionBatch).filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.status.in_(["in_progress", "completed", "wip_complete"]),
            ).all()
            # 기준일자 이전 scheduled → frozen
            soft_frozen = [b for b in all_scheduled if b.batch_id in before_task_batch_ids]
            # 기준일자 이후(또는 태스크 없는) scheduled → mutable: 삭제 후 재생성
            mutable_scheduled = [b for b in all_scheduled if b.batch_id not in before_task_batch_ids]
            mutable_scheduled_ids = {b.batch_id for b in mutable_scheduled}
            frozen = hard_frozen + soft_frozen
        else:
            # base_date 미지정: 기존 동작 — planned 외 모든 상태 동결
            frozen = db.query(ProductionBatch).filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.status != "planned",
            ).all()

        # frozen orders: batch_seq >= 1인 실제 수주 배치에서 추출
        frozen_order_keys: set[tuple] = {
            (b.sales_order_id, b.sales_order_line)
            for b in frozen
            if b.batch_seq is not None and b.batch_seq >= 1
        }
        frozen_wip_ids: set[int] = {
            b.wip_matched_id for b in frozen if b.wip_matched_id is not None
        }
        frozen_batch_ids: set[int] = {b.batch_id for b in frozen}

        # ── 1.5 Frozen orders의 모든 공정 배치도 보존 (F-4 fix) ───────────────
        # 연선이 in_progress인데 절연/시스가 planned이면,
        # 같은 order의 모든 공정 배치를 삭제 대상에서 제외해야 한다.
        frozen_order_ids = {k[0] for k in frozen_order_keys}
        if frozen_order_ids:
            related_batches = (
                db.query(ProductionBatch.batch_id)
                .filter(
                    ProductionBatch.run_label == run_label,
                    ProductionBatch.sales_order_id.in_(frozen_order_ids),
                )
                .all()
            )
            protected_batch_ids: set[int] = {b.batch_id for b in related_batches}
        else:
            protected_batch_ids = set()
        # frozen 자체도 protected에 포함
        protected_batch_ids |= frozen_batch_ids

        # ── 2. Planned + mutable_scheduled 배치/스케줄 삭제 ─────────────────────
        # (FK 순서: audit_log → schedule_task → production_batch)
        from app.infrastructure.models.audit_log import AuditLog

        # 삭제 대상 1: protected_batch_ids에 속하지 않는 planned 배치
        planned_query = db.query(ProductionBatch.batch_id).filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "planned",
        )
        if protected_batch_ids:
            planned_query = planned_query.filter(
                ProductionBatch.batch_id.notin_(protected_batch_ids)
            )
        unprotected_planned = planned_query.all()
        # 삭제 대상 2: 기준일자 이후 mutable scheduled 배치 (재생성 대상)
        delete_batch_ids = {b.batch_id for b in unprotected_planned} | mutable_scheduled_ids

        deleted_counts = {"audit_log": 0, "schedule_task": 0, "production_batch": 0}
        if delete_batch_ids:
            # FK 순서 1: audit_log
            deleted_counts["audit_log"] = (
                db.query(AuditLog)
                .filter(AuditLog.batch_id.in_(delete_batch_ids))
                .delete(synchronize_session=False)
            )
            # FK 순서 2: schedule_task
            deleted_counts["schedule_task"] = (
                db.query(ScheduleTask)
                .filter(ScheduleTask.batch_id.in_(delete_batch_ids))
                .delete(synchronize_session=False)
            )
            # FK 순서 3: production_batch
            deleted_counts["production_batch"] = (
                db.query(ProductionBatch)
                .filter(ProductionBatch.batch_id.in_(delete_batch_ids))
                .delete(synchronize_session=False)
            )

        db.flush()

        # ── 3. Sales Order 처리 ───────────────────────────────────────────────
        erp_content = await erp_file.read()
        if not erp_content:
            raise HTTPException(status_code=400, detail="ERP 파일이 비어 있습니다.")

        from app.infrastructure.models.sales_order import SalesOrder
        from app.services.erp_parser import parse_erp_file_incremental

        if upload_mode == "incremental":
            # 기존 orders 유지 + 새 orders만 추가
            parse_result = parse_erp_file_incremental(erp_content, run_label, db)
        else:
            # full: frozen orders의 SalesOrder는 보존, 나머지 삭제 후 새 파일로 교체
            if frozen_order_keys:
                # frozen orders 외의 SalesOrder만 삭제
                # PostgreSQL: tuple_().in_() 사용 가능
                from sqlalchemy import and_, or_

                frozen_conditions = [
                    and_(
                        SalesOrder.order_id == oid,
                        SalesOrder.order_line == oline,
                    )
                    for oid, oline in frozen_order_keys
                ]
                db.query(SalesOrder).filter(
                    SalesOrder.run_label == run_label,
                    ~or_(*frozen_conditions),
                ).delete(synchronize_session=False)
            else:
                # frozen이 없으면 전체 삭제
                db.query(SalesOrder).filter(
                    SalesOrder.run_label == run_label,
                ).delete(synchronize_session=False)

            db.flush()
            # 새 파일에서 파싱 — incremental 파서를 사용하여 frozen orders와의 중복 방지
            parse_result = parse_erp_file_incremental(erp_content, run_label, db)

        # ── 4. WIP 처리 ──────────────────────────────────────────────────────
        wip_warnings: list[str] = []
        if wip_file:
            try:
                from app.infrastructure.models.wip_inventory import WipInventory
                from app.services.wip_parser import parse_wip_file

                wip_content = await wip_file.read()
                if wip_content:
                    # frozen WIP를 제외한 기존 WIP 삭제
                    wip_delete_query = db.query(WipInventory).filter(
                        WipInventory.run_label == run_label,
                    )
                    if frozen_wip_ids:
                        wip_delete_query = wip_delete_query.filter(
                            WipInventory.wip_id.notin_(frozen_wip_ids)
                        )
                    wip_delete_query.delete(synchronize_session=False)
                    db.flush()

                    # 새 WIP 파싱
                    wip_parse = parse_wip_file(wip_content, db, run_label=run_label)
                    wip_warnings.extend(wip_parse.get("warnings", []))
                    if wip_parse["total"] > 0:
                        wip_warnings.append(
                            f"재공실사 {wip_parse['total']}건 등록 완료."
                        )
            except Exception as exc:
                wip_warnings.append(f"재공 파일 파싱 실패: {exc}")

        # ── 5. WIP 매칭 — frozen WIP 제외 ─────────────────────────────────────
        try:
            wip_result = match_wip(
                run_label,
                db,
                exclude_wip_ids=frozen_wip_ids if frozen_wip_ids else None,
            )
        except Exception as exc:
            wip_warnings.append(f"WIP 매칭 실패 (계속 진행): {exc}")
            wip_result = {"matched": 0, "skipped": 0, "details": []}

        # ── 6. Batch Grouping — frozen orders 제외 ────────────────────────────
        try:
            batch_result = create_batches(
                run_label,
                db,
                frozen_order_keys=frozen_order_keys if frozen_order_keys else None,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"배치 생성 실패: {exc}"
            ) from exc

        db.commit()

        # ── 7. Split 감지 ────────────────────────────────────────────────────
        try:
            split_candidates = detect_split_candidates(
                run_label, db, gap_days=split_gap_days
            )
        except Exception as exc:
            logger.warning("[Stage1 Update] 분할 후보 감지 실패: %s", exc)
            split_candidates = []

        warnings = (
            parse_result.get("warnings", [])
            + wip_warnings
            + batch_result.get("warnings", [])
        )

        return {
            "run_label": run_label,
            "upload_mode": upload_mode,
            "frozen": {
                "batch_count": len(frozen_batch_ids),
                "order_count": len(frozen_order_keys),
                "wip_count": len(frozen_wip_ids),
                "protected_batch_count": len(protected_batch_ids),
                "mutable_count": len(mutable_scheduled_ids),
            },
            "deleted": deleted_counts,
            "parse": parse_result,
            "wip": wip_result,
            "batches": batch_result,
            "warnings": warnings,
            "split_candidates": split_candidates,
            # 프론트엔드 토스트 메시지용
            "added_orders": parse_result.get("inserted", 0),
            "created_batch_groups": batch_result.get("total_batches", 0),
            "preserved_batches": len(frozen_batch_ids),
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
    from app.infrastructure.models.wip_inventory import WipInventory

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
    from app.infrastructure.models.wip_inventory import WipInventory
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
    from app.services.llm_explainer import generate_batch_summary_sync

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


@router.post("/stage2", summary="Stage 2: 자동 스케줄링")
def run_stage2(body: dict, db: Session = Depends(get_db)):
    """Stage 2: production_batch → 간트 차트 자동배열 + 제약조건 검증

    body:
        run_label: str (필수)
        base_date: str (선택, YYYYMMDD 형식 — 스케줄 시작 기준일)
    """
    run_label = body.get("run_label")
    if not run_label:
        raise HTTPException(status_code=400, detail="run_label 필수")

    # 기준일자 파싱 — 없으면 auto_schedule이 KST 당일 08:00 사용
    base_date_dt = None
    base_date_str = body.get("base_date")
    if base_date_str:
        try:
            base_date_dt = datetime.strptime(base_date_str, "%Y%m%d").replace(
                hour=8, minute=0
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"base_date 형식 오류: {base_date_str} (YYYYMMDD)",
            )

    optimizer = body.get("optimizer", "cpsat")  # "cpsat" | "greedy"

    try:
        if optimizer == "greedy":
            schedule_result = auto_schedule(run_label, db, base_date=base_date_dt)
            schedule_result["engine"] = "greedy"
        else:
            # CP-SAT 시도 → 실패(INFEASIBLE / 타임아웃) 시 그리디 폴백
            schedule_result = cp_sat_schedule(run_label, db, base_date=base_date_dt)
            schedule_result["engine"] = "cpsat"
            if schedule_result["solver_status"] not in ("OPTIMAL", "FEASIBLE"):
                schedule_result["warnings"].append(
                    "CP-SAT 솔버 미해결 — 그리디 방식으로 재시도합니다"
                )
                # 이미 wip_complete 처리된 배치가 있으므로 그리디를 그대로 이어 실행
                fallback = auto_schedule(run_label, db, base_date=base_date_dt)
                fallback["engine"] = "greedy_fallback"
                fallback["warnings"] = (
                    schedule_result["warnings"] + fallback.get("warnings", [])
                )
                schedule_result = fallback
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"스케줄링 실패: {exc}") from exc

    violations = validate_all(run_label, db)
    db.commit()

    # AI 분석을 백그라운드 스레드로 비동기 실행 — 응답을 블로킹하지 않음
    with _ai_cache_lock:
        _ai_cache[run_label] = {"status": "pending"}
    thread = threading.Thread(target=_run_ai_background, args=(run_label,), daemon=True)
    thread.start()

    return {
        "run_label": run_label,
        "schedule": schedule_result,
        "violations": violations,
        "total_violations": len(violations),
    }


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
    from app.services.wip_template import generate_wip_template

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

    def _to_dict(b: ProductionBatch) -> dict:
        raw_len = float(b.total_length_m or 0)
        wip_stage = wip_stage_map2.get(b.wip_matched_id, "") if b.wip_matched_id else ""
        is_wip = wip_stage in ("연선재고", "절연재고")
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
            "wip_length_m": wip_len,        # WIP 재고 커버량
            "net_length_m": net_len,         # 실제 작업지시량 (WIP 제외)
            "wip_matched_id": b.wip_matched_id,
            "wip_stage": wip_stage or None,
            "product_group": b.product_group or "",
            "status": b.status or "",
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

    total_drums = max(sum(int(b.drum_count or 1) for b in all_individual), 1)
    split_drums = sum(int(b.drum_count or 1) for b in split_off)
    remain_drums = total_drums - split_drums

    # ── 헤더 배치 분할 처리 ──────────────────────────────────────────────────
    if header and (split_off or remaining):
        import math as _math
        from app.infrastructure.models.wip_inventory import WipInventory as WipModel

        orig_dur = float(header.estimated_duration_min or 0)
        orig_len = float(header.total_length_m or 0)
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
                db.query(WipModel.wip_id, WipModel.process_stage, WipModel.total_length_m)
                .filter(WipModel.wip_id.in_(all_wip_ids))
                .all()
            )
            wip_stage_map  = {wid: (ps or "") for wid, ps, _ in wip_rows}
            wip_len_by_id  = {wid: float(tl or 0) for wid, _, tl in wip_rows}

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
            for b in sorted(candidates, key=lambda b: float(b.total_length_m or 0), reverse=True):
                sz = float(b.total_length_m or 0)
                if sz <= budget:
                    selected.append(b)
                    budget -= sz
            return selected

        if lot_size > 0:
            wip_in_split  = [b for b in split_off if _is_wip_strand(b)]
            wip_in_remain = [b for b in remaining  if _is_wip_strand(b)]
            non_wip_split  = [b for b in split_off if not _is_wip_strand(b)]
            non_wip_remain = [b for b in remaining if not _is_wip_strand(b)]

            # 현재 net (WIP 공유 합계 기준 차감)
            base_split_net  = _group_net(split_off)
            base_remain_net = _group_net(remaining)
            lots_current = _lot_count(base_split_net) + _lot_count(base_remain_net)

            # wip_id 별로 묶기
            wip_ids_in_remain = set(b.wip_matched_id for b in wip_in_remain)
            wip_ids_in_split  = set(b.wip_matched_id for b in wip_in_split)

            # 시나리오 A: remain WIP 전부 → split
            # 각 wip_id별 WIP 예산 내에서 split 비-WIP 배치를 선택해 수신
            avail_for_A = list(non_wip_split)  # 수신 후보 (중복 배정 방지용)
            plan_A: list[tuple[int, list]] = []  # (wip_id, 수신배치 목록)
            sim_split_A  = base_split_net
            sim_remain_A = base_remain_net

            for wid in wip_ids_in_remain:
                wip_qty_m = wip_len_by_id.get(wid, 0.0)  # cable m
                src_batches = [b for b in wip_in_remain if b.wip_matched_id == wid]
                # remain_net: WIP 해제 → 해당 배치들이 full 생산으로 복귀
                src_total = sum(float(b.total_length_m or 0) for b in src_batches) * core_mul
                covered_now = max(0.0, src_total - wip_qty_m * core_mul)
                sim_remain_A += src_total - covered_now  # = min(src_total, wip_qty*cm)
                # split_net: WIP 예산 내에서 수신 배치 선택
                recvs = _select_receivers(avail_for_A, wip_qty_m)
                recv_total = sum(float(b.total_length_m or 0) for b in recvs) * core_mul
                sim_split_A -= min(recv_total, wip_qty_m * core_mul)
                plan_A.append((wid, recvs))
                for r in recvs:
                    avail_for_A.remove(r)

            sim_split_A  = max(0.0, sim_split_A)
            sim_remain_A = max(0.0, sim_remain_A)
            lots_A = _lot_count(sim_split_A) + _lot_count(sim_remain_A)

            # 시나리오 B: split WIP 전부 → remain
            avail_for_B = list(non_wip_remain)
            plan_B: list[tuple[int, list]] = []
            sim_split_B  = base_split_net
            sim_remain_B = base_remain_net

            for wid in wip_ids_in_split:
                wip_qty_m = wip_len_by_id.get(wid, 0.0)
                src_batches = [b for b in wip_in_split if b.wip_matched_id == wid]
                src_total = sum(float(b.total_length_m or 0) for b in src_batches) * core_mul
                covered_now = max(0.0, src_total - wip_qty_m * core_mul)
                sim_split_B += src_total - covered_now
                recvs = _select_receivers(avail_for_B, wip_qty_m)
                recv_total = sum(float(b.total_length_m or 0) for b in recvs) * core_mul
                sim_remain_B -= min(recv_total, wip_qty_m * core_mul)
                plan_B.append((wid, recvs))
                for r in recvs:
                    avail_for_B.remove(r)

            sim_split_B  = max(0.0, sim_split_B)
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

        split_due = min((b.due_date for b in split_off if b.due_date), default=header.due_date)
        remain_due = min((b.due_date for b in remaining if b.due_date), default=header.due_date)
        split_pri = min((b.customer_priority or 99 for b in split_off), default=99)
        remain_pri = min((b.customer_priority or 99 for b in remaining), default=99)

        split_wip_count = sum(1 for b in split_off if _is_wip_strand(b))
        remain_wip_count = sum(1 for b in remaining if _is_wip_strand(b))

        # 신규 그룹 헤더 생성
        new_header = ProductionBatch(
            run_label=header.run_label,
            sales_order_id=split_off[0].sales_order_id if split_off else header.sales_order_id,
            sales_order_line=split_off[0].sales_order_line if split_off else header.sales_order_line,
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
            customer_name=split_off[0].customer_name if split_off else header.customer_name,
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

    return {
        "original_group": batch_group,
        "new_group": new_group,
        "moved_batches": updated,
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
    from app.infrastructure.models.wip_inventory import WipInventory

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
    for table in ["audit_log", "schedule_task", "production_batch", "sales_order"]:
        result = db.execute(
            text(f"DELETE FROM {table} WHERE run_label = :rl"),
            {"rl": run_label},
        )
        counts[table] = result.rowcount

    # wip_inventory: run_label 일치 행 삭제
    wip_del = db.execute(
        text("DELETE FROM wip_inventory WHERE run_label = :rl"),
        {"rl": run_label},
    )
    counts["wip_inventory"] = wip_del.rowcount

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
    """
    from app.infrastructure.models.sales_order import SalesOrder

    rows = (
        db.query(
            ProductionBatch.run_label,
            func.count(ProductionBatch.batch_id).label("batch_count"),
            func.min(ProductionBatch.created_at).label("created_at"),
        )
        .group_by(ProductionBatch.run_label)
        .order_by(func.min(ProductionBatch.created_at).desc())
        .all()
    )

    # 런별 외주 건수 — ERP is_outsourced 플래그 기준
    outsource_counts: dict[str, int] = {}
    if rows:
        run_labels = [r.run_label for r in rows]
        outsource_rows = (
            db.query(
                SalesOrder.run_label,
                func.count().label("cnt"),
            )
            .filter(
                SalesOrder.run_label.in_(run_labels),
                SalesOrder.is_outsourced == True,  # noqa: E712
            )
            .group_by(SalesOrder.run_label)
            .all()
        )
        outsource_counts = {r.run_label: r.cnt for r in outsource_rows}

    return [
        {
            "run_label": row.run_label,
            "batch_count": row.batch_count,
            "created_at": row.created_at,
            "outsource_count": outsource_counts.get(row.run_label, 0),
        }
        for row in rows
    ]
