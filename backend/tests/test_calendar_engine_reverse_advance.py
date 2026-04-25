"""`reverse_advance` — forward advance 의 거울 연산 테스트.

Context 관습 주석
-----------------
Task 명세는 추상적인 `ctx` 인터페이스 (working_hours=(8,18),
working_days={0..4}, holidays=set()) 를 전제하지만, 본 프로젝트의
`calendar_engine` 은 `equipment_code` 기반 공정 카테고리로 작업창을
관리한다 (자세한 규칙은 calendar_engine.py 모듈 docstring 참조).

따라서 `calendar_ctx` fixture 는 `{"equipment_code": "EX-B100", "db": None}`
를 반환하도록 고정. EX-B100 은 **저압절연** 카테고리로 월~목 08:00~익일 08:00
24h 창 (휴식 없음), 금 08:00~20:00 12h 창 → 휴식 보정이 없어 역방향
산술이 명확하게 비교 가능하다.

아래 assertion 값은 위 실제 calendar 모델에 맞춰 재계산되었으며, task
명세의 `(8,18)` 가정과 차이나는 지점은 docstring 에 근거를 남겨둔다.
"""

from datetime import datetime, timedelta

from app.infrastructure.calendar_engine import reverse_advance


def test_simple_no_gap(calendar_ctx):
    """평일 근무시간 내 단순 역방향: 월요일 17:00 에서 2h 빼면 월요일 15:00."""
    result = reverse_advance(
        datetime(2026, 4, 20, 17, 0), timedelta(hours=2), ctx=calendar_ctx
    )
    assert result == datetime(2026, 4, 20, 15, 0)


def test_skips_weekend(calendar_ctx):
    """월요일 10:00 에서 4h 역이동 → 금요일 내 착지 (주말 skip).

    EX-B100 (저압절연) 월요일 shift start = 08:00 → 월 10:00 에서 뒤로 2h
    소진하면 월 08:00 에 도달. 남은 2h 는 직전 영업일(금요일) 종료시각
    20:00 에서 뒤로 소진 → 금요일 18:00. task 명세의 Fri 14:00 은 (8,18)
    10h 창 기준이지만, 본 프로젝트는 금요일 08:00~20:00 12h 창이라 값 조정.
    """
    result = reverse_advance(
        datetime(2026, 4, 20, 10, 0), timedelta(hours=4), ctx=calendar_ctx
    )
    assert result.weekday() == 4  # Friday
    assert result == datetime(2026, 4, 17, 18, 0)


def test_crosses_day_boundary(calendar_ctx):
    """화요일 08:00 에서 6h 역이동 → 월요일 shift 내부에서 뒷걸음.

    EX-B100 (저압절연) 월요일 shift = 월 08:00 ~ 화 08:00 (24h). 경계시각인
    화 08:00 은 월 shift 의 '종료 직후' 로 해석되어 월 shift 내부로 걸어
    들어간다. 6h 역산 → 화 08:00 - 6h = 화 02:00 (= 월 shift 후반).
    task 명세의 Mon 12:00 은 (8,18) 10h 창 기준 결과. 본 프로젝트 24h 창
    기준으로는 다음날 02:00 이 정확.
    """
    result = reverse_advance(
        datetime(2026, 4, 21, 8, 0), timedelta(hours=6), ctx=calendar_ctx
    )
    assert result == datetime(2026, 4, 21, 2, 0)


def test_zero_duration(calendar_ctx):
    dt = datetime(2026, 4, 20, 10, 0)
    assert reverse_advance(dt, timedelta(0), ctx=calendar_ctx) == dt
