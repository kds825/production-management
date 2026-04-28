"""개별 배치 / 배치 그룹 상태 변경 API.

원본: ``plan_pipeline.py`` 의 /batch/* + /batch-group/{name}/status +
/batch-status-summary endpoint 들을 sub-router 로 분리 (Task 1.2).
URL path 변경 없음.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.ingest.wip_promotion import _promote_expected_to_estimated
from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory

router = APIRouter()


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
