from fastapi import APIRouter, HTTPException

from app.infrastructure.memory_store import store
from app.presentation.schemas import EquipmentResponse

router = APIRouter(prefix="/equipment", tags=["설비"])


@router.get("", response_model=list[EquipmentResponse])
def list_equipment() -> list[EquipmentResponse]:
    """전체 설비 목록 조회"""
    return [
        EquipmentResponse(
            id=eq.id,
            name=eq.name,
            process_type=eq.process_type,
            capabilities=eq.capabilities,
            capacity_tons_per_month=eq.capacity_tons_per_month,
            max_diameter_mm=eq.max_diameter_mm,
            status=eq.status,
        )
        for eq in store.list_equipment()
    ]


@router.get("/{equipment_id}", response_model=EquipmentResponse)
def get_equipment(equipment_id: str) -> EquipmentResponse:
    """설비 단건 조회"""
    eq = store.get_equipment(equipment_id)
    if eq is None:
        raise HTTPException(
            status_code=404, detail=f"설비 '{equipment_id}'를 찾을 수 없습니다."
        )
    return EquipmentResponse(
        id=eq.id,
        name=eq.name,
        process_type=eq.process_type,
        capabilities=eq.capabilities,
        capacity_tons_per_month=eq.capacity_tons_per_month,
        max_diameter_mm=eq.max_diameter_mm,
        status=eq.status,
    )
