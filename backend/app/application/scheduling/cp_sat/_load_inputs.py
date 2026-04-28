"""cp_sat_schedule() 의 §1-3 입력 로드 단계 — Phase 2 Task 2.6 (B-3.1) 추출.

원본: ``orchestrator.py:344-476`` 본문 그대로. DB 경로 (override None) +
override 경로 두 분기를 그대로 보존하여 parity bitwise equality 보장.

Why dataclass 한 개:
    호출자(``cp_sat_schedule``)가 §4 이후에 사용할 7~8 개 변수를 한 번에
    돌려받기 위함. 추상화 도입은 금지(CLAUDE.md) — 단순한 결과 묶음만.

Why ``early_return`` 필드:
    "배치 없음" 게이트는 §1-3 안에서 ``return result`` 하던 분기였다.
    추출 후에도 동일 의미를 유지하기 위해 dict (또는 None) 으로 신호.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.application._shared.constraint_params import ConstraintParams
from app.application.scheduling.cp_sat import SolverInput
from app.domain.constants import (
    PROCESS_ORDER,
    _DEFAULT_WELDING_MIN,
    _WIP_SKIP_PROCESSES,
)
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.speed_master import SpeedMaster


@dataclass
class _SolverLoadOutput:
    """§1-3 의 결과 묶음.

    ``early_return`` 가 None 이 아니면 호출자가 그 dict 를 그대로 result 에
    반영하고 조기 반환해야 함.
    """

    base_date: datetime
    batches: list
    equipment_by_process: dict
    speed_map: dict
    color_setup_map: dict
    constraint_params: ConstraintParams
    welding_min: float
    sq_to_wire_d: dict
    wip_skipped: int
    early_return: dict | None  # "배치 없음" → result, 아니면 None


def load_solver_inputs(
    run_label: str,
    db: Session,
    *,
    base_date: datetime | None = None,
    solver_input_override: SolverInput | None = None,
) -> _SolverLoadOutput:
    """원본: orchestrator.py:344-476 본문 그대로.

    DB 경로 (override None) + override 경로 두 분기 보존.
    """
    if solver_input_override is None:
        # ── 1. 배치 로드 ──────────────────────────────────────────────────────
        batches = (
            db.query(ProductionBatch)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.status == "planned",
            )
            .order_by(
                ProductionBatch.due_date.asc(),
                ProductionBatch.customer_priority.asc(),
                ProductionBatch.batch_seq.asc(),
            )
            .all()
        )
        batches.sort(
            key=lambda b: (
                PROCESS_ORDER.get(b.process_name, 50),
                b.batch_seq or 0,
                b.due_date or date.max,
                b.customer_priority or 99,
                -(float(b.sq_mm2 or 0)),
            )
        )

        if not batches:
            return _SolverLoadOutput(
                base_date=base_date,  # type: ignore[arg-type]
                batches=[],
                equipment_by_process={},
                speed_map={},
                color_setup_map={},
                constraint_params=None,  # type: ignore[arg-type]
                welding_min=0.0,
                sq_to_wire_d={},
                wip_skipped=0,
                early_return={
                    "warnings": ["배치 없음 — Stage 1을 먼저 실행하세요"],
                },
            )

        # WIP 스킵
        wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id}
        wip_stage_map: dict[int, str] = {}
        if wip_ids:
            from app.infrastructure.models.wip_inventory import WipInventory

            wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
            wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

        schedulable: list[ProductionBatch] = []
        wip_skipped = 0
        for b in batches:
            if b.wip_matched_id and b.wip_matched_id in wip_stage_map:
                skip_set = _WIP_SKIP_PROCESSES.get(
                    wip_stage_map[b.wip_matched_id], set()
                )
                if b.process_name in skip_set:
                    b.status = "wip_complete"
                    wip_skipped += 1
                    continue
            schedulable.append(b)
        batches = schedulable

        # ── 2. 기준일시 ───────────────────────────────────────────────────────
        if base_date is None:
            try:
                dp = run_label.split("_")[0]
                base_date = datetime(int(dp[:4]), int(dp[4:6]), int(dp[6:8]), 8, 0, 0)
            except Exception:
                from zoneinfo import ZoneInfo

                kst = datetime.now(ZoneInfo("Asia/Seoul"))
                base_date = kst.replace(
                    hour=8, minute=0, second=0, microsecond=0
                ).replace(tzinfo=None)

        # ── 3. 마스터 데이터 로드 ─────────────────────────────────────────────
        equipment_list = db.query(EquipmentMaster).all()
        equipment_by_process: dict[str, list[EquipmentMaster]] = {}
        for eq in equipment_list:
            equipment_by_process.setdefault(eq.process_name, []).append(eq)

        # SpeedMaster 한 번 로드. speed_map 은 (eq, sq) lookup, color_setup_map 은
        # "색상 교체 시간은 설비 파라미터" 라서 sq 와 무관한 equipment_code → setup_color_min
        # 인덱스. 기존 코드는 색상 교체가 발생할 때마다 SpeedMaster 를 재조회(N+1)
        # 하여 원격 Supabase 왕복이 누적됐음 — 한 번의 메모리 조회로 대체.
        _speed_rows = db.query(SpeedMaster).all()
        speed_map: dict[tuple, SpeedMaster] = {
            (sr.equipment_code, float(sr.cross_section or 0)): sr for sr in _speed_rows
        }
        # 동일 설비에 여러 sq row 가 있으면 setup_color_min 은 첫 non-null 을 채택
        # (현 DB 스키마상 설비별로 일정하다는 전제 — 과거 조회 로직 `.first()` 와 동치).
        color_setup_map: dict[str, float | None] = {}
        for sr in _speed_rows:
            code = sr.equipment_code
            if code not in color_setup_map:
                color_setup_map[code] = sr.setup_color_min
            elif color_setup_map[code] is None and sr.setup_color_min is not None:
                color_setup_map[code] = sr.setup_color_min

        # ConstraintConfig 프리페치 (4-2 색상교체 fallback 등에서 재사용)
        constraint_params = ConstraintParams.load(db)

        # 용접 시간 (4-4): ConstraintParams 통합 경로로 조회 (하위 호환 default 유지)
        welding_min = constraint_params.get(
            "4-4", "welding_min", default=_DEFAULT_WELDING_MIN
        )

        # SQ → 소선경 매핑 (연선 셋업 3-tier 계산용)
        sq_to_wire_d: dict[int, float] = {
            int(d.cross_section): float(d.wire_diameter)
            for d in db.query(DrumLotMaster).all()
            if d.wire_diameter is not None
        }

        return _SolverLoadOutput(
            base_date=base_date,
            batches=batches,
            equipment_by_process=equipment_by_process,
            speed_map=speed_map,
            color_setup_map=color_setup_map,
            constraint_params=constraint_params,
            welding_min=welding_min,
            sq_to_wire_d=sq_to_wire_d,
            wip_skipped=wip_skipped,
            early_return=None,
        )
    else:
        # Override 경로 — 필드 이름을 로컬 변수로 rebind. `batches` 만 list()
        # 로 사본화 (§4 이후 로직이 in-place 로 섞일 여지 방어). 나머지 dict 도
        # 얕은 복사 — ORM 값(ConstraintParams/EquipmentMaster 등) 자체는 공유.
        batches = list(solver_input_override.batches)
        wip_skipped = solver_input_override.wip_skipped
        # "배치 없음" 게이트 — 원본 L1098 과 동일 메시지. override 의 batches 가
        # 비면 여기서 조기 반환해 parity 유지.
        if not batches:
            early: dict[str, Any] = {
                "warnings": ["배치 없음 — Stage 1을 먼저 실행하세요"],
            }
            if wip_skipped:
                early["wip_skipped"] = wip_skipped
            return _SolverLoadOutput(
                base_date=solver_input_override.base_date,
                batches=[],
                equipment_by_process={},
                speed_map={},
                color_setup_map={},
                constraint_params=solver_input_override.constraint_params,
                welding_min=solver_input_override.welding_min,
                sq_to_wire_d={},
                wip_skipped=wip_skipped,
                early_return=early,
            )
        # `base_date` 는 SolverInput 이 이미 확정값을 담고 있음 (run_label
        # 파생 또는 명시). 호출측 base_date kwarg 는 무시 — override 가 진실.
        base_date = solver_input_override.base_date
        equipment_by_process = dict(solver_input_override.equipment_by_process)
        speed_map = dict(solver_input_override.speed_map)
        color_setup_map = dict(solver_input_override.color_setup_map)
        constraint_params = solver_input_override.constraint_params
        welding_min = solver_input_override.welding_min
        sq_to_wire_d = dict(solver_input_override.sq_to_wire_d)

        return _SolverLoadOutput(
            base_date=base_date,
            batches=batches,
            equipment_by_process=equipment_by_process,
            speed_map=speed_map,
            color_setup_map=color_setup_map,
            constraint_params=constraint_params,
            welding_min=welding_min,
            sq_to_wire_d=sq_to_wire_d,
            wip_skipped=wip_skipped,
            early_return=None,
        )
