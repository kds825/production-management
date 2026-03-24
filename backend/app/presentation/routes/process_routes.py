from fastapi import APIRouter

from app.infrastructure.memory_store import store
from app.presentation.schemas import (
    LineSpeedResponse,
    ProcessRouteResponse,
    ProcessStepResponse,
)

router = APIRouter(tags=["공정 경로"])


@router.get("/process-routes", response_model=list[ProcessRouteResponse])
def list_process_routes() -> list[ProcessRouteResponse]:
    """전체 공정 경로 목록 조회"""
    result = []
    for route in store.list_routes():
        steps = [
            ProcessStepResponse(
                order=s.order,
                process_type=s.process_type,
                equipment_ids=s.equipment_ids,
                is_optional=s.is_optional,
            )
            for s in route.steps
        ]
        result.append(
            ProcessRouteResponse(
                id=route.id,
                voltage=route.voltage,
                core_count_range=route.core_count_range,
                steps=steps,
                description=route.description,
            )
        )
    return result


@router.get("/line-speeds", response_model=list[LineSpeedResponse])
def list_line_speeds() -> list[LineSpeedResponse]:
    """규격별 선속도 테이블 조회"""
    speeds_map = store.get_line_speeds()
    return [
        LineSpeedResponse(spec=spec, speeds=speeds)
        for spec, speeds in speeds_map.items()
    ]
