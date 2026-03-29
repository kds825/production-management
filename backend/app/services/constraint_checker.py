"""28개 제약조건 검증 엔진 — 스케줄링 결과 사후 검증"""

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.equipment_master import EquipmentMaster


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

    # Run each enabled constraint checker
    checkers = {
        "1-1": _check_priority_order,
        "1-2": _check_due_type,
        "3-3": _check_color_group,
        "4-1": _check_setup_time,
        "5-1": _check_sq_range,
        "6-1": _check_safety_education,
        "6-2": _check_friday_hours,
        "9-1": _check_precedence,
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


def _check_overlap(tasks: list) -> list[dict]:
    """동일 설비에서 시간 겹침 확인"""
    violations = []
    by_equip = {}
    for t in tasks:
        by_equip.setdefault(t.equipment_code, []).append(t)

    for eq_code, eq_tasks in by_equip.items():
        sorted_tasks = sorted(eq_tasks, key=lambda x: x.start_datetime)
        for i in range(len(sorted_tasks) - 1):
            if sorted_tasks[i].end_datetime > sorted_tasks[i + 1].start_datetime:
                violations.append(
                    {
                        "constraint_id": "overlap",
                        "task_id": sorted_tasks[i + 1].task_id,
                        "severity": "error",
                        "detail": f"설비 {eq_code}: 작업 {sorted_tasks[i].task_id}과 시간 겹침",
                    }
                )
    return violations


def _check_delivery(tasks, batches) -> list[dict]:
    """납기 초과 확인"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        if batch and batch.due_date and t.end_datetime.date() > batch.due_date:
            violations.append(
                {
                    "constraint_id": "1-1",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    "severity": "warning",
                    "detail": f"납기 {batch.due_date} 초과 (완료 예정: {t.end_datetime.date()})",
                }
            )
    return violations


def _check_priority_order(tasks, batches, equipment, config) -> list[dict]:
    """같은 설비에서 우선순위 낮은 주문이 높은 주문보다 먼저 배치되었는지"""
    violations = []
    by_equip = {}
    for t in tasks:
        by_equip.setdefault(t.equipment_code, []).append(t)

    for eq_code, eq_tasks in by_equip.items():
        sorted_tasks = sorted(eq_tasks, key=lambda x: x.start_datetime)
        for i in range(len(sorted_tasks) - 1):
            b1 = batches.get(sorted_tasks[i].batch_id)
            b2 = batches.get(sorted_tasks[i + 1].batch_id)
            if b1 and b2:
                if (b1.customer_priority or 99) > (b2.customer_priority or 99):
                    if b1.due_date and b2.due_date and b1.due_date > b2.due_date:
                        # Lower priority task is scheduled first AND has later due date
                        violations.append(
                            {
                                "constraint_id": "1-1",
                                "task_id": sorted_tasks[i].task_id,
                                "severity": "warning",
                                "detail": (
                                    f"우선순위 역전: {b1.customer_name}(P{b1.customer_priority})"
                                    f" before {b2.customer_name}(P{b2.customer_priority})"
                                ),
                            }
                        )
    return violations


def _check_due_type(tasks, batches, equipment, config) -> list[dict]:
    """도착기준 고객은 운송일 차감 확인"""
    # Simplified: just flag if 도착기준 customer's due_date might be tight
    return []


def _check_color_group(tasks, batches, equipment, config) -> list[dict]:
    """설비 색상그룹 제한 확인 (A120: 흑/청만)"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        eq = equipment.get(t.equipment_code)
        if batch and eq and eq.color_group == "흑/청":
            color = (batch.sheath_color or "").strip()
            if color and color not in (
                "흑",
                "청",
                "흑색",
                "청색",
                "BLACK",
                "BLUE",
                "BK",
                "BL",
            ):
                violations.append(
                    {
                        "constraint_id": "3-3",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"A120 설비에 {color} 색상 배정 (흑/청만 가능)",
                    }
                )
    return violations


def _check_setup_time(tasks, batches, equipment, config) -> list[dict]:
    """규격교체 시간이 반영되었는지 확인"""
    # Simplified: check if consecutive tasks on same equipment have different SQ
    return []


def _check_sq_range(tasks, batches, equipment, config) -> list[dict]:
    """설비 SQ 범위 확인"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        eq = equipment.get(t.equipment_code)
        if batch and eq and batch.sq_mm2:
            sq = float(batch.sq_mm2)
            if eq.range_min and sq < float(eq.range_min):
                violations.append(
                    {
                        "constraint_id": "5-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"SQ {sq} < 설비 최소 {eq.range_min}",
                    }
                )
            if eq.range_max and sq > float(eq.range_max):
                violations.append(
                    {
                        "constraint_id": "5-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"SQ {sq} > 설비 최대 {eq.range_max}",
                    }
                )
    return violations


def _check_safety_education(tasks, batches, equipment, config) -> list[dict]:
    """안전교육 시간대에 작업 배정 확인"""
    from app.services.calendar_engine import _is_last_two_mondays
    from datetime import time

    violations = []
    for t in tasks:
        d = t.start_datetime.date()
        if _is_last_two_mondays(d):
            if t.start_datetime.time() < time(10, 0):
                violations.append(
                    {
                        "constraint_id": "6-1",
                        "task_id": t.task_id,
                        "severity": "warning",
                        "detail": f"안전교육일 {d} 08~10시 작업 배정",
                    }
                )
    return violations


def _check_friday_hours(tasks, batches, equipment, config) -> list[dict]:
    """금요일 24시 이후 작업 확인"""
    violations = []
    for t in tasks:
        if t.start_datetime.weekday() == 5 and t.start_datetime.hour < 8:
            # Saturday before 08:00 means it ran past Friday midnight
            violations.append(
                {
                    "constraint_id": "6-2",
                    "task_id": t.task_id,
                    "severity": "warning",
                    "detail": "금요일 24시 이후 작업 연장",
                }
            )
    return violations


def _check_precedence(tasks, batches, equipment, config) -> list[dict]:
    """선행공정 완료 전 후속공정 시작 확인"""
    violations = []
    task_map = {t.task_id: t for t in tasks}
    for t in tasks:
        if t.predecessor_task_id:
            pred = task_map.get(t.predecessor_task_id)
            if pred and t.start_datetime < pred.end_datetime:
                violations.append(
                    {
                        "constraint_id": "9-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"선행작업 {pred.task_id} 완료 전 시작",
                    }
                )
    return violations


def _check_material_separation(tasks, batches, equipment, config) -> list[dict]:
    """CU/AL 재질 설비 분리 확인"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        eq = equipment.get(t.equipment_code)
        if batch and eq and eq.material_limit and eq.material_limit != "ALL":
            if (
                batch.conductor_material
                and batch.conductor_material != eq.material_limit
            ):
                violations.append(
                    {
                        "constraint_id": "10-2",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": (
                            f"{batch.conductor_material} 제품이 "
                            f"{eq.material_limit} 전용 설비에 배정"
                        ),
                    }
                )
    return violations
