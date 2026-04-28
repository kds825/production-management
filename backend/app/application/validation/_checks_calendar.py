"""캘린더 체커 — friday_hours / holiday / absence_hours.

constraint_checker.py 분할 (Task 1.6, B-4.2).
"""

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.operation_calendar import OperationCalendar


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


def _check_holiday(tasks, batches, equipment, config, db: Session) -> list[dict]:
    """
    6-4: 공휴일 작업 배정 확인.
    operation_calendar에서 rule_code = 'CAL-HOL'인 날짜를 읽어 겹치는 작업 탐지.
    """
    violations = []

    # 공휴일 날짜 목록 로드
    holidays = (
        db.query(OperationCalendar)
        .filter(OperationCalendar.rule_code == "CAL-HOL")
        .all()
    )
    holiday_dates = {h.specific_date for h in holidays if h.specific_date is not None}

    if not holiday_dates:
        return violations

    for t in tasks:
        task_date = t.start_datetime.date()
        if task_date in holiday_dates:
            violations.append(
                {
                    "constraint_id": "6-4",
                    "task_id": t.task_id,
                    "severity": "error",
                    "detail": f"공휴일 {task_date}에 작업 배정",
                }
            )
    return violations


def _check_absence_hours(tasks, batches, equipment, config, db: Session) -> list[dict]:
    """
    6-3: 부재자 계획 — 일별 스케줄 총 시간이 가용 시간(기준 - 부재 차감)을 초과하는지 확인.
    params_json: {"absence_reduction_hours": 8, "daily_available_hours": 40}
    absence_reduction_hours: 부재로 차감되는 인시 (기본 8hr = 1인 1일)
    daily_available_hours: 정상 일별 총 가용 인시 (기본 40hr = 2교대 × 2설비 등)
    """
    violations = []
    params = config.params_json or {}
    absence_h = float(params.get("absence_reduction_hours", 0))
    base_available_h = float(params.get("daily_available_hours", 40))

    if absence_h <= 0:
        # 부재 차감이 0이면 검증 불필요
        return violations

    adjusted_available_h = base_available_h - absence_h

    # 일별 총 스케줄 시간 집계
    daily_usage: dict[date, float] = {}
    for t in tasks:
        task_date = t.start_datetime.date()
        duration_h = (t.end_datetime - t.start_datetime).total_seconds() / 3600.0
        daily_usage[task_date] = daily_usage.get(task_date, 0.0) + duration_h

    for day, used_h in daily_usage.items():
        if used_h > adjusted_available_h:
            violations.append(
                {
                    "constraint_id": "6-3",
                    "severity": "warning",
                    "detail": (
                        f"{day}: 스케줄 {used_h:.1f}h > 가용 {adjusted_available_h:.1f}h"
                        f" (부재 차감 {absence_h:.1f}h 적용)"
                    ),
                }
            )
    return violations
