from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.database import get_db
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.presentation.schemas import EquipmentResponse

router = APIRouter(prefix="/equipment", tags=["설비"])


def _db_equipment_to_response(eq: EquipmentMaster) -> EquipmentResponse:
    """equipment_master DB 레코드를 프론트엔드 응답 형태로 변환.

    capabilities: range_min~range_max 범위를 SQ 규격 목록으로 생성.
    표준 SQ 단계표를 기준으로 범위 내 규격만 포함한다.
    """
    # 표준 SQ 규격 단계 (전선 업계 일반 기준)
    standard_sq = [
        1.5,
        2.5,
        4,
        6,
        10,
        16,
        25,
        35,
        50,
        70,
        95,
        120,
        150,
        185,
        240,
        300,
        325,
        400,
        500,
        630,
        800,
        1000,
    ]
    rmin = float(eq.range_min) if eq.range_min is not None else None
    rmax = float(eq.range_max) if eq.range_max is not None else None

    if rmin is not None and rmax is not None:
        capabilities = [
            f"{int(sq) if sq == int(sq) else sq}SQ"
            for sq in standard_sq
            if rmin <= sq <= rmax
        ]
    else:
        capabilities = []

    return EquipmentResponse(
        id=eq.equipment_code,
        name=eq.equipment_name,
        process_type=eq.process_name,
        capabilities=capabilities,
        capacity_tons_per_month=100.0,  # 현재 DB에 capacity 컬럼 없음 — 기본값 사용
        max_diameter_mm=None,
        status="available",
    )


@router.get("", response_model=list[EquipmentResponse])
def list_equipment(db: Session = Depends(get_db)) -> list[EquipmentResponse]:
    """전체 설비 목록 조회.

    PostgreSQL equipment_master 테이블에서 읽는다.
    DB에 데이터가 없으면 인메모리 store로 폴백한다.
    """
    # 공정 순서(신선→연선→절연→연합/TP→시스) → 설비코드 순으로 정렬
    process_order = case(
        PROCESS_ORDER,
        value=EquipmentMaster.process_name,
        else_=99,
    )
    db_equips = (
        db.query(EquipmentMaster)
        .order_by(process_order, EquipmentMaster.equipment_code)
        .all()
    )

    return [_db_equipment_to_response(eq) for eq in db_equips]


@router.get("/{equipment_id}", response_model=EquipmentResponse)
def get_equipment(
    equipment_id: str, db: Session = Depends(get_db)
) -> EquipmentResponse:
    """설비 단건 조회.

    PostgreSQL에서 먼저 조회하고, 없으면 인메모리 store를 확인한다.
    """
    db_eq = (
        db.query(EquipmentMaster)
        .filter(EquipmentMaster.equipment_code == equipment_id)
        .first()
    )

    if db_eq:
        return _db_equipment_to_response(db_eq)

    raise HTTPException(
        status_code=404, detail=f"설비 '{equipment_id}'를 찾을 수 없습니다."
    )
