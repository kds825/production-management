"""캘린더 ↔ working-minute 변환 및 base-date 결정 헬퍼.

CP-SAT 솔버는 시간축으로 "근무 분(working minute)" 을 사용한다. 실제 wall-clock
datetime 과 이 추상 축 사이를 오가는 변환 함수, 그리고 run_label 로부터
기준 시각을 결정하는 헬퍼를 모은다.

기존 위치: app.services.cp_sat_optimizer (Week 3 Task 3A.1 이전 분리됨).
원래 dotted path 는 cp_sat_optimizer 모듈에서 re-export 로 유지된다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.domain.constants import _WORK_MIN_PER_DAY


def _work_days_between(d1: date, d2: date) -> int:
    """d1(포함) ~ d2(미포함) 사이의 근무일 수(토·일 제외).

    Legacy fallback — calendar_engine 경로가 db/equipment_code 정보 없을 때 사용.
    공휴일은 반영하지 않는다.
    """
    days = 0
    cur = d1
    while cur < d2:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days


def _working_minutes_between(
    start: datetime,
    end: datetime,
    equipment_code: str | None = None,
    db: Session | None = None,
) -> int:
    """start → end 사이 실제 가용 근무분 (calendar_engine 기반, P9-E).

    calendar_engine.get_working_window / get_available_hours / _get_day_breaks 를
    결합해 공정 카테고리별 가동시간·요일별·휴식·공휴일(db 있을 때) 을 모두 반영.

    가정/한계:
      - tz-naive (KST) — 기존 코드 전체 규칙 준수.
      - equipment_code=None → 'default' 카테고리 (연선연합과 동일 22h Mon-Thu).
      - db=None → OperationCalendar(CAL-HOL) 공휴일 미반영, 주말만 제외.
      - 같은 날 start ~ end 는 day 의 working window 와 교집합 후 휴식 구간 제거.
      - 휴식 구간을 완전히 포함한 start/end 만 차감 (부분 겹침은 차감 분을 clamp).

    Edge case (알려진 근사):
      - start/end 가 working window 밖이면 day_start/day_end 로 clamp.
      - current date 가 금요일 오후이면 FRI_END_HOUR 로 창이 단축됨 — 자동 반영.

    Why: 기존 _work_days_between * _WORK_MIN_PER_DAY 는 하루=840분 고정 근사로
    "공휴일 있는 주에 작업이 하루 더 밀림" 같은 현실을 반영 못 했음. P9-E 로 교체.
    """
    # 지역 import — 최상단 import 가 formatter 에 의해 제거되는 환경 방어.
    from app.infrastructure.calendar_engine import (
        _get_category,
        _get_day_breaks,
        get_available_hours,
        get_working_window,
    )

    if end <= start:
        return 0

    cat = _get_category(equipment_code)
    total_min = 0
    current_date = start.date()
    end_date = end.date()

    while current_date <= end_date:
        # 1) 해당 날짜 사용 가능 시간 (공휴일이면 0 → skip)
        avail_hr = get_available_hours(current_date, equipment_code, db)
        if avail_hr <= 0:
            current_date = current_date + timedelta(days=1)
            continue

        # 2) 해당 날짜 작업 윈도우 (day_start, day_end) — 08:00 시작, cat/요일별 종료
        day_start, day_end = get_working_window(current_date, equipment_code)
        if day_end <= day_start:
            current_date = current_date + timedelta(days=1)
            continue

        # 3) 실제 구간 = [max(start, day_start), min(end, day_end)]
        if current_date == start.date():
            effective_start = max(start, day_start)
        else:
            effective_start = day_start
        if current_date == end_date:
            effective_end = min(end, day_end)
        else:
            effective_end = day_end

        if effective_end <= effective_start:
            current_date = current_date + timedelta(days=1)
            continue

        seg_min = int((effective_end - effective_start).total_seconds() / 60)

        # 4) 휴식 구간 제거 (월~목 연선연합 점심/저녁/간식). 부분 겹침도 정확히 차감.
        breaks = _get_day_breaks(current_date, cat)
        for b_start, b_end in breaks:
            # 겹침 없음 조기 탈출
            if b_start >= effective_end or b_end <= effective_start:
                continue
            overlap_start = max(b_start, effective_start)
            overlap_end = min(b_end, effective_end)
            seg_min -= max(0, int((overlap_end - overlap_start).total_seconds() / 60))

        total_min += max(0, seg_min)
        current_date = current_date + timedelta(days=1)

    return total_min


def _due_work_min(
    due: date,
    base: datetime,
    equipment_code: str | None = None,
    db: Session | None = None,
) -> int:
    """납기일까지 남은 근무 분(CP-SAT 내부 단위).

    P9-E: equipment_code / db 선택 파라미터로 calendar_engine 반영.
    - 제공되면 `_working_minutes_between(base, due 22:00, ...)` 으로 정확 계산.
    - 없으면 기존 legacy 축(_work_days_between * _WORK_MIN_PER_DAY) 유지 —
      `_datetime_to_wmin` 등 legacy wmin 축과 정합 유지용.

    due 의 "하루 끝" 을 22:00 (일반 오후 마감) 기준으로 해석. calendar_engine 의
    day_end 와 min() 을 통해 카테고리별 실제 마감(금요일 14:00 등) 로 자동 clamp.

    Round 2 (MED #13 Past-due 차등): past-due (due < base) 는 음수 반환.
      - 기존: past-due 시 `_working_minutes_between(base, due_end)` 가 end<=start
        이라 0, 혹은 `wd = _work_days_between(base, due)` 도 0 (둘 다 loss of
        signal). 결과: overdue 5일 == overdue 1일 == on-time 동일 처리.
      - 개선: due < base 일 때 -|past 근무 분| 반환. soft tardiness 공식
        `max(0, end - due_wmin)` 이 `end - (-x) = end + x` 로 자연스럽게 커져
        "3일 overdue 는 1일 overdue 의 3배 penalty" 실현. tardiness_hard=True
        경로는 호출부에서 음수 감지 후 제약 skip + warning.
    """
    from datetime import time as _time

    if equipment_code is not None or db is not None:
        due_end = datetime.combine(due, _time(22, 0))
        if due_end <= base:
            # Past due — 음수 반환 (얼마나 지났는지).
            past_min = _working_minutes_between(due_end, base, equipment_code, db)
            return -past_min
        return _working_minutes_between(base, due_end, equipment_code, db)

    # Legacy fallback — _datetime_to_wmin / horizon 과 동일 축 유지.
    if due < base.date():
        # Past due — 음수 근무일 × 하루 분.
        past_wd = _work_days_between(due, base.date())
        return -past_wd * _WORK_MIN_PER_DAY
    wd = _work_days_between(base.date(), due)
    return wd * _WORK_MIN_PER_DAY


def resolve_base_date(run_label: str, base_date: datetime | None = None) -> datetime:
    """run_label/base_date 조합으로 CP-SAT 기준일시를 결정.

    왜 공용 헬퍼로 뽑았는가:
      기존에는 cp_sat_schedule 진입부 (line 842 근처), schedule_optimizer
      의 긴급수주 재최적화 (line 2153) 등 여러 곳에서 동일 폴백 로직이
      복제되어 있었다. warm_start_hints 를 auto_schedule 에서 자동 생성할
      때 `_datetime_to_wmin(existing_task.start_datetime, base_date)` 를
      호출해야 하므로, **cp_sat_schedule 이 내부적으로 쓸 base_date 와
      정확히 동일한 값** 으로 미리 결정해두는 단일 진실 공급원이 필요하다.

    규칙:
      - base_date 가 명시 전달되면 그대로 반환.
      - None 이면 run_label 접두부 YYYYMMDD 로 08:00 생성.
      - 파싱 실패 시 KST 당일 08:00 으로 폴백.
    """
    if base_date is not None:
        return base_date
    try:
        dp = run_label.split("_")[0]
        return datetime(int(dp[:4]), int(dp[4:6]), int(dp[6:8]), 8, 0, 0)
    except Exception:
        from zoneinfo import ZoneInfo

        kst = datetime.now(ZoneInfo("Asia/Seoul"))
        return kst.replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=None)


def _datetime_to_wmin(dt: datetime, base_date: datetime) -> int:
    """datetime 을 CP-SAT 내부 working-minutes 축(하루 840분, 08:00~22:00) 으로 변환.

    Why (C1 fix): CP-SAT 모델의 시간축은 **working-minutes** 로, 하루 840 분
    (_WORK_MIN_PER_DAY, 08:00~22:00) 만 카운트하고 야간/주말은 제외한다.
    `_due_work_min` 은 날짜 단위(wd * _WORK_MIN_PER_DAY) 만 처리하므로 시각/분
    해상도가 없다. frozen task 의 start_datetime / end_datetime 은 hour/minute
    까지 포함한 datetime 이라 정확한 변환을 위해 **시각부 분 offset** 까지
    계산해야 frozen 위치가 솔버 축에서 올바른 지점에 박힌다.

    과거 구현은 `(dt - base_date).total_seconds() // 60` 로 wall-clock delta 를
    반환해 시간축이 어긋났고, 솔버가 frozen 위치를 "눈에 보이는 것보다 훨씬 뒤"
    로 인식해 INFEASIBLE 또는 비합리적 배치 이동을 야기했다.

    변환 규칙:
      - dt < base_date: 0 반환 (이미 지난 시각. `_ALWAYS_FROZEN_STATUSES` 경로에서
        completed 로 판정되어 모델에서 제외되어야 정상; 방어적으로 clamp).
      - dt ≥ base_date: 날짜부(working days) × 840 + 시각부(08:00 기준 분 offset).
      - 시각부가 [08:00, 22:00) 밖이면 경계로 clamp:
          · dt.hour < 8 → 그날 시작점 (0)
          · dt.hour ≥ 22 → 그날 끝 (840)

    가정:
      - tz naive (KST) — 기존 코드 전반의 패턴.
      - within-day offset 은 08:00 기준 선형 offset 으로만 계산하고 휴식
        (점심/저녁/간식) 은 무시한다. CP-SAT 축은 순서 결정용으로 충분하며
        실제 배치는 calendar_engine 이 휴식을 반영한다.
    """
    if dt < base_date:
        return 0
    day_off = _work_days_between(base_date.date(), dt.date()) * _WORK_MIN_PER_DAY
    if dt.hour < 8:
        time_off = 0
    elif dt.hour >= 22:
        time_off = _WORK_MIN_PER_DAY
    else:
        time_off = (dt.hour - 8) * 60 + dt.minute
    return day_off + time_off
