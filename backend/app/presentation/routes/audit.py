"""감사 추적 API — audit trail 조회 + SM재고 라이프사이클 관리"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.services.audit_logger import get_audit_trail
from app.services.llm_explainer import explain_decision_sync  # noqa: F401
from app.services.sm_inventory import (
    create_shortage_batches,
    get_wip_summary,
    update_wip_actual,
)

router = APIRouter(prefix="/audit", tags=["감사추적"])


@router.get("/{run_label}")
def get_audit(
    run_label: str,
    batch_id: int | None = Query(None),
    task_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """감사 추적 로그 조회"""
    logs = get_audit_trail(run_label, db, batch_id=batch_id, task_id=task_id)
    return {"run_label": run_label, "logs": logs, "total": len(logs)}


@router.get("/explain/{batch_id}", summary="배치 결정 자연어 설명")
def explain_batch(
    batch_id: int,
    task_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """특정 배치의 스케줄링 결정을 자연어로 설명.
    ANTHROPIC_API_KEY 환경변수가 설정되면 LLM 사용, 아니면 템플릿 기반."""
    return explain_decision_sync(batch_id, db, task_id=task_id)


# ── SM 재고 라이프사이클 엔드포인트 ──────────────────────────────────────────


@router.patch("/wip/{wip_id}/actual", tags=["SM재고"])
def update_wip_actual_endpoint(
    wip_id: int,
    body: dict,
    db: Session = Depends(get_db),
):
    """SM재고 예상→실적 업데이트.

    body: { "actual_length_m": float }
    variance_m = actual - expected 로 자동 계산되며,
    부족(음수)이고 수주에 매칭된 WIP이면 감사 로그에 기록한다.
    """
    actual = body.get("actual_length_m")
    if actual is None:
        raise HTTPException(status_code=400, detail="actual_length_m required")
    result = update_wip_actual(wip_id, float(actual), db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    db.commit()
    return result


@router.post("/wip/shortage-batches", tags=["SM재고"])
def create_shortage_batches_endpoint(
    body: dict,
    db: Session = Depends(get_db),
):
    """부족분 추가 배치 자동 생성.

    body: { "run_label": str }
    variance_m < 0 인 실적 WIP을 스캔하여 부족량만큼 planned 배치를 생성한다.
    """
    run_label = body.get("run_label")
    if not run_label:
        raise HTTPException(status_code=400, detail="run_label required")
    result = create_shortage_batches(run_label, db)
    db.commit()
    return result


@router.get("/wip/summary/{run_label}", tags=["SM재고"])
def get_wip_summary_endpoint(
    run_label: str,
    db: Session = Depends(get_db),
):
    """SM재고 요약 — 예상/실적/차이/매칭 현황"""
    return get_wip_summary(run_label, db)
