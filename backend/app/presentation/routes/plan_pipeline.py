"""Stage 1 파이프라인 API — ERP 업로드 → 작업지시서 생성 → Excel 다운로드"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.services.batch_grouping import create_batches
from app.services.constraint_checker import validate_all  # noqa: F401 — used in stage2
from app.services.erp_parser import parse_erp_file
from app.services.excel_exporter import export_plan
from app.services.schedule_optimizer import auto_schedule  # noqa: F401 — used in stage2
from app.services.wip_matching import match_wip

router = APIRouter(prefix="/pipeline", tags=["파이프라인"])


@router.post("/stage1", summary="ERP 업로드 → 작업지시서 생성")
async def run_stage1(
    erp_file: UploadFile = File(..., description="ERP 수주 파일 (.xls)"),
    wip_file: UploadFile | None = File(None, description="재공 재고 파일 (선택)"),
    date_from: str | None = Form(None, description="납기 시작일 (YYYYMMDD)"),
    date_to: str | None = Form(None, description="납기 종료일 (YYYYMMDD)"),
    db: Session = Depends(get_db),
) -> dict:
    """Stage 1 파이프라인 실행:

    1. ERP .xls 파일을 파싱하여 sales_order 테이블에 적재
    2. 재공(WIP) 매칭 — 기존 재고를 수주에 매칭하여 공정 생략
    3. 수주 데이터를 공정별 production_batch로 변환 (납기 범위 필터 가능)

    Returns:
        run_label, 파싱 결과, WIP 매칭 결과, 배치 생성 결과, 통합 경고 목록
    """
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
                wip_parse = parse_wip_file(wip_content, db)
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
    }


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

    return {
        "run_label": run_label,
        "schedule": schedule_result,
        "violations": violations,
        "total_violations": len(violations),
    }


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


@router.get("/runs", summary="계획 실행 이력 목록")
def list_runs(db: Session = Depends(get_db)) -> list[dict]:
    """저장된 모든 run_label 목록을 배치 수 및 최초 생성 시각과 함께 반환한다.

    최신 실행이 상단에 오도록 created_at 내림차순 정렬.
    """
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

    return [
        {
            "run_label": row.run_label,
            "batch_count": row.batch_count,
            "created_at": row.created_at,
        }
        for row in rows
    ]
