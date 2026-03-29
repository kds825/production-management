from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.memory_store import store
from app.infrastructure.models.speed_master import SpeedMaster
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
def list_line_speeds(db: Session = Depends(get_db)) -> list[LineSpeedResponse]:
    """규격별 선속도 테이블 조회.

    PostgreSQL speed_master 테이블에서 읽어 규격(cross_section + product_type)별로
    집계한다. DB에 데이터가 없으면 인메모리 store로 폴백한다.

    응답 형태: [{"spec": "25SQ", "speeds": {"저압절연": 3.1, ...}}, ...]
    """
    rows = db.query(SpeedMaster).order_by(SpeedMaster.cross_section).all()

    if rows:
        # cross_section(SQ) 기준으로 집계: spec → {공정명: 속도}
        speeds_map: dict[str, dict[str, float]] = {}
        for row in rows:
            sq = row.cross_section
            if sq is None:
                continue
            sq_val = float(sq)
            spec = f"{int(sq_val) if sq_val == int(sq_val) else sq_val}SQ"
            process = row.product_type or "기타"
            speed = float(row.line_speed_mpm) if row.line_speed_mpm is not None else 0.0
            if spec not in speeds_map:
                speeds_map[spec] = {}
            # 동일 spec+공정에 여러 설비 데이터가 있을 경우 평균값 사용
            existing = speeds_map[spec].get(process)
            if existing is None:
                speeds_map[spec][process] = speed
            else:
                speeds_map[spec][process] = (existing + speed) / 2

        return [
            LineSpeedResponse(spec=spec, speeds=speeds)
            for spec, speeds in speeds_map.items()
        ]

    # DB에 데이터 없음 → 인메모리 폴백
    speeds_map_mem = store.get_line_speeds()
    return [
        LineSpeedResponse(spec=spec, speeds=speeds)
        for spec, speeds in speeds_map_mem.items()
    ]
