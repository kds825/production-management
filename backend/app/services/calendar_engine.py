"""가동캘린더 엔진 — 공정별 일일 가동시간 기반 종료시각 계산

공정별 가동시간 (일일 유효시간):
  연선 / 연합  : 월~목 22h (휴식 2h 포함 24h 창), 금 14h, 토/일 0h
  저압절연      : 월~목 24h, 금 12h, 토/일 0h
  고압절연      : 월 18h, 화~목 24h, 금 12h, 토/일 0h
  시스(저압/고압): 월~목 24h, 금 12h, 토/일 0h
  기타(default) : 연선/연합과 동일 (월~목 22h, 금 14h)

일일 부동(휴식) 시간 — 연선/연합·default, 월~목:
  점심  12:00 ~ 13:00  (60 min)
  저녁  18:00 ~ 18:30  (30 min)
  간식  22:00 ~ 22:30  (30 min)
  합계: 2h

  ※ 저압절연·시스·고압절연은 24h 가동(휴식 없음)으로 처리.
  ※ 고압절연 월요일은 08:00~02:00 다음날(18h) 창으로 처리.

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
    ("ST-",   "연선연합"),
    ("CA-",   "연선연합"),
    ("EX-B",  "저압절연"),
    ("EX-CV", "고압절연"),
    ("SH-",   "시스"),
]

# ── 금요일 작업 윈도우 종료 시각 ──────────────────────────────────────────────
_FRI_END_HOUR: dict[str, int] = {
    "연선연합": 22,
    "저압절연": 20,
    "고압절연": 20,
    "시스":     20,
    "default":  22,
}

# ── 일일 부동(휴식) 시간대 — 연선연합·default, 월~목 적용 ────────────────────
# (start_time, end_time): 해당 시간대는 생산 불가
_DAILY_BREAKS: dict[str, list[tuple[time, time]]] = {
    "연선연합": [
        (time(12, 0), time(13, 0)),   # 점심  60 min
        (time(18, 0), time(18, 30)),  # 저녁  30 min
        (time(22, 0), time(22, 30)),  # 간식  30 min
    ],
    "default": [
        (time(12, 0), time(13, 0)),
        (time(18, 0), time(18, 30)),
        (time(22, 0), time(22, 30)),
    ],
    # 저압절연·시스·고압절연: 24h 가동 → 휴식 없음
    "저압절연": [],
    "고압절연": [],
    "시스":     [],
}


def _get_category(equipment_code: str | None) -> str:
    """설비코드로 공정 카테고리 반환. 매칭 없으면 'default'."""
    if equipment_code:
        for prefix, cat in _EQUIP_PREFIX_CATEGORY:
            if equipment_code.startswith(prefix):
                return cat
    return "default"


def _get_day_breaks(d: date, cat: str) -> list[tuple[datetime, datetime]]:
    """해당 날짜의 휴식 구간 목록 반환 (datetime 쌍, 정렬됨).

    금요일·토·일은 빈 리스트 반환 (휴식 정의 불필요).
    """
    if d.weekday() >= 4:  # Fri/Sat/Sun
        return []
    raw = _DAILY_BREAKS.get(cat, _DAILY_BREAKS["default"])
    return [(datetime.combine(d, s), datetime.combine(d, e)) for s, e in raw]


def get_available_hours(
    target_date: date,
    equipment_code: str | None = None,
    db: Session | None = None,
) -> float:
    """특정 날짜·설비의 유효 가동시간(hr) 반환."""
    weekday = target_date.weekday()

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
    """날짜·설비의 작업 윈도우 (시작, 종료) 반환.

    연선연합·default 월~목: 08:00 ~ 익일 08:00 (24h 창, 내부 휴식은 별도 스킵)
    고압절연 월요일        : 08:00 ~ 익일 02:00 (18h 창)
    저압절연·시스 월~목    : 08:00 ~ 익일 08:00 (24h 창, 휴식 없음)
    금요일                 : 08:00 ~ 카테고리별 종료시각
    토·일                  : 창 없음 (start == end)
    """
    weekday = target_date.weekday()
    cat = _get_category(equipment_code)
    start = datetime.combine(target_date, time(8, 0))

    if weekday >= 5:  # 토·일
        return start, start

    if weekday == 4:  # 금요일
        end_hour = _FRI_END_HOUR.get(cat, _FRI_END_HOUR["default"])
        return start, datetime.combine(target_date, time(end_hour, 0))

    # 월~목
    avail_h = _PROCESS_HOURS.get(cat, _PROCESS_HOURS["default"])[weekday]
    if avail_h <= 0:
        return start, start

    if avail_h == 24:
        # 24h 가동: 익일 08:00까지 (휴식 없음)
        end = start + timedelta(hours=24)
    else:
        # 22h(연선연합) → 08:00 ~ 익일 08:00 (창 24h, 휴식 2h를 내부에서 스킵)
        # 18h(고압절연 월) → 08:00 ~ 익일 02:00
        if cat in ("연선연합", "default"):
            end = start + timedelta(hours=24)   # 창은 24h, 휴식을 내부 스킵으로 처리
        else:
            end = start + timedelta(hours=avail_h)  # 고압절연 월: 18h 창

    return start, end


def calculate_end_datetime(
    start: datetime,
    duration_min: float,
    db: Session | None = None,
    equipment_code: str | None = None,
) -> datetime:
    """시작시각 + 소요시간(분) → 종료시각.

    각 날짜별 작업 윈도우 내에서 휴식 구간을 실시간으로 건너뛰며 시간을 소진한다.
    같은 날 여러 배치가 걸쳐도 휴식 구간은 정확히 한 번만 반영된다.
    """
    remaining = duration_min
    current = start
    cat = _get_category(equipment_code)

    for _ in range(1000):
        if remaining <= 0:
            return current

        current_date = current.date()
        avail_hours = get_available_hours(current_date, equipment_code, db)

        if avail_hours <= 0:
            # 비가동일 → 다음 날 08:00
            current = datetime.combine(current_date + timedelta(days=1), time(8, 0))
            continue

        _, day_end = get_working_window(current_date, equipment_code)

        if current >= day_end:
            current = datetime.combine(current_date + timedelta(days=1), time(8, 0))
            continue

        # 오늘의 휴식 구간 목록 (정렬됨)
        day_breaks = _get_day_breaks(current_date, cat)

        # 현재 위치가 휴식 구간 내에 있으면 휴식 종료 시각으로 이동
        for brk_s, brk_e in day_breaks:
            if brk_s <= current < brk_e:
                current = brk_e
                break

        if current >= day_end:
            current = datetime.combine(current_date + timedelta(days=1), time(8, 0))
            continue

        # current 이후의 다음 휴식 구간 탐색
        next_brk_s: datetime | None = None
        next_brk_e: datetime | None = None
        for brk_s, brk_e in day_breaks:
            if brk_s > current:
                next_brk_s = brk_s
                next_brk_e = brk_e
                break

        # 다음 정지 지점: 다음 휴식 시작 or 창 종료 중 빠른 것
        if next_brk_s is not None and next_brk_s < day_end:
            work_until = next_brk_s
        else:
            work_until = day_end

        avail_min = (work_until - current).total_seconds() / 60

        if remaining <= avail_min:
            return current + timedelta(minutes=remaining)

        remaining -= avail_min

        if next_brk_s is not None and work_until == next_brk_s:
            # 휴식 구간 건너뜀
            current = next_brk_e  # type: ignore[assignment]
        else:
            # 창 종료 → 다음 날 08:00
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
