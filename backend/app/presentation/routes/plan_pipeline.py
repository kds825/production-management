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
from app.services.batch_grouping import (
    create_batches,
    detect_split_candidates,
    format_spec_display,
)
from app.services.constraint_checker import validate_all  # noqa: F401 — used in stage2
from app.services.erp_parser import parse_erp_file
from app.services.excel_exporter import export_plan
from app.services.schedule_optimizer import auto_schedule  # noqa: F401 — used in stage2
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

    try:
        schedule_result = auto_schedule(run_label, db, base_date=base_date_dt)
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

    # 연선 그룹(ST-)은 헤더 외에 개별 수주 배치도 항상 포함한다.
    # batch_group이 구버전 형식("연선_300SQ")으로 저장된 경우에도 동작하도록
    # run_label + process_name + sq_mm2 + batch_seq=1 조건으로 추가 조회한다.
    if batch_group.startswith("ST-"):
        header = next(
            (b for b in by_group if b.batch_seq is not None and b.batch_seq < 0),
            None,
        )
        if header:
            order_batches = (
                db.query(ProductionBatch)
                .filter(
                    ProductionBatch.run_label == header.run_label,
                    ProductionBatch.process_name == header.process_name,
                    ProductionBatch.sq_mm2 == header.sq_mm2,
                    ProductionBatch.batch_seq == 1,  # 개별 수주 배치만 (CORE=0 제외)
                )
                .order_by(
                    ProductionBatch.due_date.asc(), ProductionBatch.sales_order_id.asc()
                )
                .all()
            )
            # 헤더 + 개별 수주 배치 (batch_id 기준 중복 제거)
            seen = {header.batch_id}
            combined = [header]
            for b in order_batches:
                if b.batch_id not in seen:
                    seen.add(b.batch_id)
                    combined.append(b)
            batches = combined
        else:
            # 헤더가 없으면 by_group 결과 그대로 사용
            batches = by_group
    else:
        batches = by_group

    def _to_dict(b: ProductionBatch) -> dict:
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
            "total_length_m": float(b.total_length_m or 0),
            "wip_matched_id": b.wip_matched_id,
            "product_group": b.product_group or "",
            "status": b.status or "",
        }

    return [_to_dict(b) for b in batches]


@router.post("/batch-group/{batch_group:path}/split", summary="배치 그룹 분할")
def split_batch_group(
    batch_group: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """배치 그룹을 2개로 분할한다.

    지정된 batch_ids의 batch_group을 '{원래그룹}_{suffix}'로 변경하고,
    해당 batch_group의 기존 schedule_task를 삭제한다.
    프론트에서 재스케줄링(Stage 2)을 트리거해야 한다.

    body:
        batch_ids: list[int]  — 새 그룹으로 이동할 batch_id 목록
        new_group_suffix: str — 새 그룹 이름 접미사 (기본값: "B")
    """
    from app.infrastructure.models.schedule_task import (
        ScheduleTask as ScheduleTaskModel,
    )

    batch_ids = body.get("batch_ids", [])
    suffix = body.get("new_group_suffix", "B")

    if not batch_ids:
        raise HTTPException(status_code=400, detail="분리할 batch_ids가 비어 있습니다.")

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

    # 지정된 배치들의 그룹 변경
    updated = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_id.in_(batch_ids),
            ProductionBatch.batch_group == batch_group,
        )
        .update({ProductionBatch.batch_group: new_group}, synchronize_session=False)
    )

    # 기존 batch_group의 schedule_task 삭제 (분할 후 재스케줄링 필요)
    db.query(ScheduleTaskModel).filter(
        ScheduleTaskModel.batch_group == batch_group
    ).delete(synchronize_session=False)

    # 새 batch_group의 schedule_task도 삭제 (혹시 존재하면)
    db.query(ScheduleTaskModel).filter(
        ScheduleTaskModel.batch_group == new_group
    ).delete(synchronize_session=False)

    db.commit()

    return {
        "original_group": batch_group,
        "new_group": new_group,
        "moved_batches": updated,
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
    }
    for key, val in body.items():
        if key in allowed:
            setattr(batch, key, val)

    # total_length_m 재계산 — drum_count 또는 drum_length_m이 변경된 경우
    if "drum_count" in body or "drum_length_m" in body:
        batch.total_length_m = float(batch.drum_length_m or 0) * (batch.drum_count or 1)

    db.commit()
    return {"batch_id": batch_id, "updated": list(body.keys())}


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
