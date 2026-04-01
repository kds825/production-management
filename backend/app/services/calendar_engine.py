"""가동캘린더 엔진 — 날짜별 가용 작업시간 계산"""

from datetime import date, datetime, timedelta
import calendar as cal_mod

from sqlalchemy.orm import Session

from app.infrastructure.models.operation_calendar import OperationCalendar


def get_available_hours(
    target_date: date, equipment_code: str | None = None, db: Session = None
) -> float:
    """특정 날짜의 가용 작업시간(hr) 반환"""
    weekday = target_date.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    # Check holiday first
    if db:
        holiday = (
            db.query(OperationCalendar)
            .filter(
                OperationCalendar.rule_code == "CAL-HOL",
                OperationCalendar.specific_date == target_date,
            )
            .first()
        )
        if holiday:
            return 0.0

    # 토/일: 공장 미가동
    if weekday >= 5:
        return 0.0

    # Friday: 작업윈도우 08:00~자정(16hr) - 부동 10hr = 6hr 유효
    # (참고: 16hr에서 10hr 부동 차감. 현장 기준 금요일 14시 이후 대부분 정리)
    if weekday == 4:
        return 6.0

    # Mon~Thu: 작업윈도우 08:00~다음날08:00(24hr) - 부동 2hr = 22hr
    base_hours = 22.0

    # Safety education: last 2 Mondays of month
    if weekday == 0 and _is_last_two_mondays(target_date):
        base_hours -= 2.0  # 22 - 2 = 20hr

    return base_hours


def get_working_window(target_date: date) -> tuple[datetime, datetime]:
    """날짜의 작업 시작/종료 시각 반환"""
    weekday = target_date.weekday()
    start = datetime.combine(target_date, datetime.min.time().replace(hour=8))

    if weekday == 4:  # Friday
        end = datetime.combine(
            target_date, datetime.min.time().replace(hour=0)
        ) + timedelta(days=1)  # midnight
    else:
        end = start + timedelta(hours=24)  # next day 08:00

    return start, end


def generate_edu_dates(start_date: date, end_date: date) -> list[date]:
    """계획 기간 내 안전교육일 (매월 마지막 2주 월요일) 목록 생성"""
    edu_dates = []
    current = start_date.replace(day=1)

    while current <= end_date:
        year, month = current.year, current.month
        # Find all Mondays in this month
        mondays = []
        for day in range(1, cal_mod.monthrange(year, month)[1] + 1):
            d = date(year, month, day)
            if d.weekday() == 0:  # Monday
                mondays.append(d)

        # Last 2 Mondays
        for monday in mondays[-2:]:
            if start_date <= monday <= end_date:
                edu_dates.append(monday)

        # Next month
        if month == 12:
            current = date(year + 1, 1, 1)
        else:
            current = date(year, month + 1, 1)

    return edu_dates


def calculate_end_datetime(
    start: datetime, duration_min: float, db: Session = None
) -> datetime:
    """시작시각 + 소요시간(분)으로 종료시각 계산 (비가용시간 건너뛰기)"""
    remaining = duration_min
    current = start

    max_iterations = 100  # safety
    for _ in range(max_iterations):
        current_date = current.date()
        avail_hours = get_available_hours(current_date, db=db)

        if avail_hours <= 0:
            # Skip this day entirely
            current = datetime.combine(
                current_date + timedelta(days=1), datetime.min.time().replace(hour=8)
            )
            continue

        _, day_end = get_working_window(current_date)

        # Available minutes from current time to end of working window
        available_min = max(0, (day_end - current).total_seconds() / 60)

        # Subtract deduction (부동시간: 월~목 2hr=120min, 금 10hr=600min)
        weekday = current_date.weekday()
        if weekday == 4:  # Friday
            deduction_min = 600.0
        elif weekday == 0 and _is_last_two_mondays(current_date):
            deduction_min = 240.0  # 안전교육일 4hr
        else:
            deduction_min = 120.0  # Mon~Thu 2hr
        if available_min > deduction_min:
            effective_min = available_min - deduction_min
        else:
            # 남은 가용시간이 부동시간보다 짧음 → 이 날 잔여는 부동시간으로 간주
            effective_min = 0.0

        if remaining <= effective_min:
            # Task finishes today
            return current + timedelta(minutes=remaining)

        # Task continues to next day
        remaining -= effective_min
        next_date = current_date + timedelta(days=1)
        current = datetime.combine(next_date, datetime.min.time().replace(hour=8))

    # Fallback: just add duration linearly
    return start + timedelta(minutes=duration_min)


def _is_last_two_mondays(d: date) -> bool:
    """해당 날짜가 그 달의 마지막 2개 월요일인지 확인"""
    year, month = d.year, d.month
    last_day = cal_mod.monthrange(year, month)[1]
    mondays = [
        date(year, month, day)
        for day in range(1, last_day + 1)
        if date(year, month, day).weekday() == 0
    ]
    return d in mondays[-2:]
