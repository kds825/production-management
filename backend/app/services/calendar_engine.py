"""가동캘린더 엔진 — 공정별 일일 가동시간 기반 종료시각 계산

공정별 가동시간 (일일 유효시간):
  연선 / 연합  : 월~목 22h, 금 14h, 토/일 0h
  저압절연      : 월~목 24h, 금 12h, 토/일 0h
  고압절연      : 월 18h, 화~목 24h, 금 12h, 토/일 0h
  시스(저압/고압): 월~목 24h, 금 12h, 토/일 0h
  기타(default) : 연선/연합과 동일 (월~목 22h, 금 14h)

설비코드 → 공정 카테고리 매핑:
  ST-*  → 연선연합   (ST-T6B0, ST-54BO1/2/3)
  CA-*  → 연선연합   (CA-12BO, CA-4BO, CA-LU)
  EX-B* → 저압절연   (EX-B100)
  EX-CV*→ 고압절연   (EX-CV1, EX-CV2)
  SH-*  → 시스       (SH-A100, SH-A120, SH-A150)

안전교육일 (매월 마지막 2주 월요일): 해당 공정 월요일 유효시간에서 추가 -2h
"""

from __future__ import annotations

import calendar as cal_mod
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.operation_calendar import OperationCalendar

# ── 공정 카테고리별 일일 유효시간 ─────────────────────────────────────────────
# key: weekday (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun)
_PROCESS_HOURS: dict[str, dict[int, float]] = {
    "연선연합": {0: 22, 1: 22, 2: 22, 3: 22, 4: 14, 5: 0, 6: 0},
    "저압절연": {0: 24, 1: 24, 2: 24, 3: 24, 4: 12, 5: 0, 6: 0},
    "고압절연": {0: 18, 1: 24, 2: 24, 3: 24, 4: 12, 5: 0, 6: 0},
    "시스":     {0: 24, 1: 24, 2: 24, 3: 24, 4: 12, 5: 0, 6: 0},
    "default":  {0: 22, 1: 22, 2: 22, 3: 22, 4: 14, 5: 0, 6: 0},
}

# ── 설비코드 prefix → 공정 카테고리 ──────────────────────────────────────────
_EQUIP_PREFIX_CATEGORY: list[tuple[str, str]] = [
    ("ST-",   "연선연합"),   # 연선
    ("CA-",   "연선연합"),   # 연합
    ("EX-B",  "저압절연"),   # 저압절연
    ("EX-CV", "고압절연"),   # 고압절연
    ("SH-",   "시스"),       # 시스 (저압/고압 통합)
]

# ── 금요일 작업 윈도우 종료 시각 (시각 기준) ──────────────────────────────────
# 연선연합: 08:00 + 14h = 22:00 / 나머지: 08:00 + 12h = 20:00
_FRI_END_HOUR: dict[str, int] = {
    "연선연합": 22,
    "저압절연": 20,
    "고압절연": 20,
    "시스":     20,
    "default":  22,
}


def _get_category(equipment_code: str | None) -> str:
    """설비코드로 공정 카테고리 반환. 매칭 없으면 'default'."""
    if equipment_code:
        for prefix, cat in _EQUIP_PREFIX_CATEGORY:
            if equipment_code.startswith(prefix):
                return cat
    return "default"


def get_available_hours(
    target_date: date,
    equipment_code: str | None = None,
    db: Session | None = None,
) -> float:
    """특정 날짜·설비의 유효 가동시간(hr) 반환."""
    weekday = target_date.weekday()

    # 공휴일 체크
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

    cat = _get_category(equipment_code)
    base_hours = _PROCESS_HOURS.get(cat, _PROCESS_HOURS["default"])[weekday]

    if base_hours <= 0:
        return 0.0

    # 안전교육일(매월 마지막 2주 월요일): 추가 -2h
    if weekday == 0 and _is_last_two_mondays(target_date):
        base_hours = max(0.0, base_hours - 2.0)

    return base_hours


def get_working_window(
    target_date: date,
    equipment_code: str | None = None,
) -> tuple[datetime, datetime]:
    """날짜·설비의 작업 시작/종료 시각 반환.

    월~목: 08:00 ~ 익일 08:00 (24h 창)
    금요일: 08:00 ~ [카테고리별 종료시각] (14h 또는 12h)
    """
    weekday = target_date.weekday()
    start = datetime.combine(target_date, time(8, 0))

    if weekday == 4:  # Friday
        cat = _get_category(equipment_code)
        end_hour = _FRI_END_HOUR.get(cat, _FRI_END_HOUR["default"])
        end = datetime.combine(target_date, time(end_hour, 0))
    else:
        # Mon-Thu: 24h 창 (부동시간은 deduction으로 별도 차감)
        end = start + timedelta(hours=24)

    return start, end


def calculate_end_datetime(
    start: datetime,
    duration_min: float,
    db: Session | None = None,
    equipment_code: str | None = None,
) -> datetime:
    """시작시각 + 소요시간(분) → 종료시각 (비가동시간 건너뜀).

    각 날짜별 유효 가용분(effective_min)을 계산하며 remaining을 차감한다.
    effective_min = window_avail_min - deduction_min
      deduction = (창 총시간) - (해당 날 유효시간)
    """
    remaining = duration_min
    current = start
    cat = _get_category(equipment_code)

    for _ in range(200):
        current_date = current.date()
        avail_hours = get_available_hours(current_date, equipment_code, db)

        if avail_hours <= 0:
            # 가동 불가: 다음 날 08:00으로 점프
            current = datetime.combine(current_date + timedelta(days=1), time(8, 0))
            continue

        day_start, day_end = get_working_window(current_date, equipment_code)

        # 창 내 남은 시간 (현재 위치 ~ 창 종료)
        window_avail_min = max(0.0, (day_end - current).total_seconds() / 60)

        # 오늘의 deduction: 창 총시간 - 유효시간
        window_total_min = (day_end - day_start).total_seconds() / 60
        deduction_min = max(0.0, window_total_min - avail_hours * 60)

        # 유효 가용분: deduction은 창 말미에 몰려있으므로 남은 창에서 차감
        if window_avail_min > deduction_min:
            effective_min = window_avail_min - deduction_min
        else:
            effective_min = 0.0

        if remaining <= effective_min:
            return current + timedelta(minutes=remaining)

        remaining -= effective_min
        current = datetime.combine(current_date + timedelta(days=1), time(8, 0))

    # 안전 폴백
    return start + timedelta(minutes=duration_min)


def generate_edu_dates(start_date: date, end_date: date) -> list[date]:
    """계획 기간 내 안전교육일(매월 마지막 2주 월요일) 목록 생성."""
    edu_dates = []
    current = start_date.replace(day=1)

    while current <= end_date:
        year, month = current.year, current.month
        mondays = [
            date(year, month, d)
            for d in range(1, cal_mod.monthrange(year, month)[1] + 1)
            if date(year, month, d).weekday() == 0
        ]
        for monday in mondays[-2:]:
            if start_date <= monday <= end_date:
                edu_dates.append(monday)
        current = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    return edu_dates


def _is_last_two_mondays(d: date) -> bool:
    """해당 날짜가 그 달의 마지막 2개 월요일인지 확인."""
    year, month = d.year, d.month
    mondays = [
        date(year, month, day)
        for day in range(1, cal_mod.monthrange(year, month)[1] + 1)
        if date(year, month, day).weekday() == 0
    ]
    return d in mondays[-2:]
