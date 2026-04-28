"""기타 체커 — safety_education / equipment_utilization / gc_routing.

constraint_checker.py 분할 (Task 1.6, B-4.2).
"""

from sqlalchemy.orm import Session


def _check_safety_education(tasks, batches, equipment, config) -> list[dict]:
    """안전교육 시간대에 작업 배정 확인"""
    from app.infrastructure.calendar_engine import _is_last_two_mondays
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


def _check_equipment_utilization(
    tasks, batches, equipment, config, db: Session
) -> list[dict]:
    """
    7-2: 설비 가동률 90% 초과 시 '고장 위험' 경고.
    스케줄 기간의 총 가용 시간 대비 실제 사용 시간 비율을 계산한다.
    params_json: {"utilization_threshold": 0.9}
    """
    violations = []
    params = config.params_json or {}
    threshold = float(params.get("utilization_threshold", 0.9))

    if not tasks:
        return violations

    # 스케줄 전체 기간 계산
    min_start = min(t.start_datetime for t in tasks)
    max_end = max(t.end_datetime for t in tasks)
    total_period_hours = (max_end - min_start).total_seconds() / 3600.0

    if total_period_hours <= 0:
        return violations

    # 설비별 총 사용 시간 합산
    usage_hours: dict[str, float] = {}
    for t in tasks:
        duration_h = (t.end_datetime - t.start_datetime).total_seconds() / 3600.0
        usage_hours[t.equipment_code] = (
            usage_hours.get(t.equipment_code, 0.0) + duration_h
        )

    for eq_code, used_h in usage_hours.items():
        eq = equipment.get(eq_code)
        # 설비의 base_working_hours/일 × 기간 일수로 가용 시간 산정
        base_h_day = float((eq.base_working_hours or 20)) if eq else 20.0
        period_days = total_period_hours / 24.0
        available_h = base_h_day * period_days

        if available_h > 0 and (used_h / available_h) > threshold:
            violations.append(
                {
                    "constraint_id": "7-2",
                    "equipment_code": eq_code,
                    "severity": "warning",
                    "detail": (
                        f"설비 {eq_code} 가동률 {used_h / available_h * 100:.1f}%"
                        f" (임계값 {threshold * 100:.0f}%) — 고장 위험"
                    ),
                }
            )
    return violations


def _check_gc_routing(tasks, batches, equipment, config) -> list[dict]:
    """
    9-2: GC 라우팅 제외 — GC 타입 제품이 B100 절연 설비에 배정되면 에러.
    GC 타입: product_group 또는 item_code에 'GC' 포함.
    """
    violations = []
    # B100 계열 절연 설비 코드 (equipment_code가 'B100'으로 시작하는 설비)
    _B100_PREFIX = "B100"

    for t in tasks:
        eq = equipment.get(t.equipment_code)
        if not eq:
            continue
        # B100 절연 설비 여부 확인
        is_b100 = (t.equipment_code or "").upper().startswith(_B100_PREFIX)
        if not is_b100:
            continue

        batch = batches.get(t.batch_id)
        if not batch:
            continue

        # GC 타입 여부 확인 — product_group 또는 remarks에서 탐지
        is_gc = (
            "GC" in (batch.product_group or "").upper()
            or "GC" in (batch.remarks or "").upper()
        )
        if is_gc:
            violations.append(
                {
                    "constraint_id": "9-2",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    "severity": "error",
                    "detail": (
                        f"GC 타입 제품(배치 {t.batch_id})이 B100 절연 설비 {t.equipment_code}에 배정 — "
                        "GC는 B100 라우팅 제외 대상"
                    ),
                }
            )
    return violations
