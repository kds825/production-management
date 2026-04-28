"""28개 제약조건 검증 엔진 — 스케줄링 결과 사후 검증 (orchestrator).

체커 함수 본문은 카테고리별 sub-module(`_checks_hard/_checks_due/_checks_setup/
_checks_calendar/_checks_material/_checks_misc`) 에 분산. 본 파일은 외부 진입점
(`validate_all`, `validate_overlap_only`, `has_overlap`) 과 dispatch 만 담당.

외부 import path 호환성을 위해 모든 `_check_*` 함수는 본 모듈에서도 re-export
한다 (테스트 / monkeypatch 호환).
"""

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.equipment_master import EquipmentMaster

# Phase 1 Task 1.5/1.6 (B-4.1/B-4.2) — 카테고리별 sub-module 로 분할.
# 외부 import path (constraint_checker.validate_all/_check_*) 보존을 위한 re-export.
from app.application.validation._checks_hard import (
    _check_overlap,
    _check_precedence,
    _check_sq_range,
)
from app.application.validation._checks_due import (
    _check_delivery,
    _check_due_type,
    _check_priority_order,
)
from app.application.validation._checks_setup import (
    _check_color_group,
    _check_setup_time,
)
from app.application.validation._checks_calendar import (
    _check_friday_hours,
    _check_holiday,
    _check_absence_hours,
)
from app.application.validation._checks_material import (
    _check_material_separation,
    _check_defect_buffer,
    _check_material_availability,
    _check_raw_material_availability,
    _check_procurement_lead_time,
)
from app.application.validation._checks_misc import (
    _check_safety_education,
    _check_equipment_utilization,
    _check_gc_routing,
)


def has_overlap(violations: list[dict]) -> bool:
    """validate_all 결과에 겹침 위반이 하나라도 있는지.

    DRY: 기존에는 호출자마다 `[v for v in violations if v.get("constraint_id") == "overlap"]`
    를 인라인으로 반복했음. overlap 검출 로직을 단일 함수로 집약해 유지보수성을 높인다.
    """
    return any(v.get("constraint_id") == "overlap" for v in violations)


def validate_overlap_only(run_label: str, db: Session) -> list[dict]:
    """재시도 판단 전용 경량 검증 — 겹침만 체크.

    왜 분리했는가:
      auto_schedule 의 재시도 루프는 "겹침이 있으면 다시 돌린다" 만 필요.
      전체 28개 체커를 돌리는 validate_all 은 재시도마다 공통 로드(tasks/batches/
      equipment/constraints 4 쿼리) + 체커 loop 를 반복해 불필요한 비용이 크다.
      이 함수는 ScheduleTask 만 로드하고 _check_overlap 만 실행해
      재시도 판단 속도를 높인다.

    최종 Stage2 응답에는 여전히 validate_all 을 사용해 모든 violation 을 반환.
    """
    tasks = db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    return _check_overlap(tasks)


def validate_all(run_label: str, db: Session) -> list[dict]:
    """모든 활성 제약조건으로 스케줄 검증. Returns list of violations."""

    violations = []

    # Load data
    tasks = db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    batches = {
        b.batch_id: b
        for b in db.query(ProductionBatch)
        .filter(ProductionBatch.run_label == run_label)
        .all()
    }
    equipment = {e.equipment_code: e for e in db.query(EquipmentMaster).all()}
    constraints = (
        db.query(ConstraintConfig).filter(ConstraintConfig.is_enabled == True).all()  # noqa: E712
    )

    constraint_map = {c.constraint_id: c for c in constraints}

    # 체커 함수 시그니처: (tasks, batches, equipment, config) → list[dict]
    # db 접근이 필요한 체커는 클로저로 db 캡처
    checkers = {
        "1-1": _check_priority_order,
        "1-2": _check_due_type,
        "3-3": _check_color_group,
        "4-1": _check_setup_time,
        "5-1": _check_sq_range,
        "6-1": _check_safety_education,
        "6-2": _check_friday_hours,
        "6-3": lambda t, b, e, c: _check_absence_hours(t, b, e, c, db),
        "6-4": lambda t, b, e, c: _check_holiday(t, b, e, c, db),
        "7-1": _check_defect_buffer,
        "7-2": lambda t, b, e, c: _check_equipment_utilization(t, b, e, c, db),
        "8-1": _check_material_availability,
        "8-2": _check_procurement_lead_time,
        "8-3": _check_raw_material_availability,
        "9-1": _check_precedence,
        "9-2": _check_gc_routing,
        "10-2": _check_material_separation,
    }

    # Also check universal constraints
    violations.extend(_check_overlap(tasks))
    violations.extend(_check_delivery(tasks, batches))

    for cid, checker_fn in checkers.items():
        if cid in constraint_map:
            try:
                v = checker_fn(tasks, batches, equipment, constraint_map[cid])
                violations.extend(v)
            except Exception as e:
                violations.append(
                    {
                        "constraint_id": cid,
                        "severity": "error",
                        "detail": f"체커 실행 오류: {str(e)}",
                    }
                )

    return violations
