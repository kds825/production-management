"""Master-data 로딩 — Equipment / SpeedMaster / ConstraintParams.

원래 ``optimization_loop._run_optimization_once`` 의 lines 174-192 블록.

핵심:
  - ``EquipmentMaster`` 전체를 ``process_name → [Equipment, ...]`` dict 로 그룹.
  - ``SpeedMaster`` 전체를 ``(equipment_code, sq_mm2_float) → SpeedMaster`` lookup.
  - ``ConstraintParams.load(db)`` — 38개 ConstraintConfig row 의 params_json 통합.
  - 4-4 welding_min 도 함께 끌어와 caller 가 별도 호출 안 하게 묶음.

Why 단일 함수:
  세 가지가 항상 같이 필요하고 의존 순서가 없어서 한 번에 로드 + 묶음 반환이
  ``run_label`` 단위 단일 책임에 가장 자연스럽다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain.constants import _DEFAULT_WELDING_MIN
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.speed_master import SpeedMaster
from app.application._shared.constraint_params import ConstraintParams


@dataclass
class MasterData:
    """Greedy 옵티마이저가 setup 단계에서 한 번에 로드하는 마스터 데이터 묶음."""

    equipment_by_process: dict[str, list[EquipmentMaster]]
    speed_map: dict[tuple, SpeedMaster]
    constraint_params: ConstraintParams
    welding_min: int


def load_master_data(db: Session) -> MasterData:
    """3종 마스터 데이터 일괄 로드."""
    equipment_list = db.query(EquipmentMaster).all()
    equipment_by_process: dict[str, list[EquipmentMaster]] = {}
    for eq in equipment_list:
        equipment_by_process.setdefault(eq.process_name, []).append(eq)

    speed_records = db.query(SpeedMaster).all()
    speed_map: dict[tuple, SpeedMaster] = {}
    for sr in speed_records:
        speed_map[(sr.equipment_code, float(sr.cross_section or 0))] = sr

    constraint_params = ConstraintParams.load(db)
    welding_min = constraint_params.get(
        "4-4", "welding_min", default=_DEFAULT_WELDING_MIN
    )

    return MasterData(
        equipment_by_process=equipment_by_process,
        speed_map=speed_map,
        constraint_params=constraint_params,
        welding_min=welding_min,
    )
