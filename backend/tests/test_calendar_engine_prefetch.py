"""calendar_engine holiday prefetch — cache on/off 동등성 회귀 가드 (Phase 6 step 10).

Why:
    PoC 시연 (987 batch) profile 에서 ``calendar_engine.get_available_hours``
    가 8341회 호출 — 매 호출이 ``OperationCalendar.rule_code='CAL-HOL'`` SQL
    쿼리 1회를 발생. stage2 wall-time 의 ~100s 가 이 lookup 의 누적 round-trip.
    ``prime_holiday_cache`` 가 stage2 entrypoint 에서 1회 prefetch 후 ContextVar
    lookup 으로 대체.

본 테스트는 cache primed/not-primed 양쪽이 같은 결과를 반환함을 확인 — 휴일
시맨틱이 prefetch 도입으로 깨지지 않음을 회귀 가드한다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.infrastructure.calendar_engine import (
    _is_holiday,
    get_available_hours,
    prime_holiday_cache,
    reset_holiday_cache,
)
from app.infrastructure.models.operation_calendar import OperationCalendar


@pytest.fixture(autouse=True)
def _reset_cache_each_test():
    """매 테스트 끝에 cache 를 None 으로 복원 (context leak 방지)."""
    yield
    reset_holiday_cache()


def _seed_holiday(db, target: date) -> None:
    db.add(
        OperationCalendar(
            rule_code="CAL-HOL",
            rule_name=f"테스트 휴일 {target.isoformat()}",
            specific_date=target,
        )
    )
    db.flush()


def test_is_holiday_cache_off_db_path(db):
    """cache None → DB 쿼리 경로 (기존 동작)."""
    holiday = date(2026, 5, 5)  # 어린이날
    _seed_holiday(db, holiday)
    reset_holiday_cache()  # 명시적으로 cache off

    assert _is_holiday(holiday, db) is True
    assert _is_holiday(date(2026, 5, 6), db) is False


def test_is_holiday_cache_on_no_db_needed(db):
    """primed cache → DB 안 거치고 lookup."""
    holiday = date(2026, 5, 5)
    _seed_holiday(db, holiday)
    primed = prime_holiday_cache(db)
    assert holiday in primed

    # db=None 으로 호출해도 cache hit 으로 동작해야 함
    assert _is_holiday(holiday, db=None) is True
    assert _is_holiday(date(2026, 5, 6), db=None) is False


def test_get_available_hours_parity_cache_on_off(db):
    """cache on/off 양쪽이 동일 결과 반환 — 가장 중요한 회귀 가드."""
    holiday = date(2026, 5, 5)  # Tue
    _seed_holiday(db, holiday)

    # cache off (기존 경로)
    reset_holiday_cache()
    hours_off_holiday = get_available_hours(holiday, "EX-B100", db)
    hours_off_weekday = get_available_hours(date(2026, 5, 6), "EX-B100", db)
    hours_off_saturday = get_available_hours(date(2026, 5, 9), "EX-B100", db)

    # cache on
    prime_holiday_cache(db)
    hours_on_holiday = get_available_hours(holiday, "EX-B100", db)
    hours_on_weekday = get_available_hours(date(2026, 5, 6), "EX-B100", db)
    hours_on_saturday = get_available_hours(date(2026, 5, 9), "EX-B100", db)

    assert hours_off_holiday == hours_on_holiday == 0.0, (
        "휴일은 cache 유무 무관 0.0 시간"
    )
    assert hours_off_weekday == hours_on_weekday, "평일은 cache 유무 무관 동일 시간"
    assert hours_off_saturday == hours_on_saturday == 0.0, (
        "토요일은 cache 유무 무관 0.0 시간"
    )


def test_reset_cache_falls_back_to_db(db):
    """reset 후엔 cache 가 cleared 되어 다시 DB 경로."""
    holiday = date(2026, 5, 5)
    _seed_holiday(db, holiday)
    prime_holiday_cache(db)
    assert _is_holiday(holiday, db=None) is True

    reset_holiday_cache()
    # cache None → db=None 이면 False (DB 못 봄), db 있으면 True
    assert _is_holiday(holiday, db=None) is False
    assert _is_holiday(holiday, db) is True


def test_prime_returns_frozenset_of_dates(db):
    """prime 의 반환값이 frozenset[date] 인지 확인 — caller 가 size 검증 등 가능."""
    _seed_holiday(db, date(2026, 5, 5))
    _seed_holiday(db, date(2026, 6, 6))
    primed = prime_holiday_cache(db)
    assert isinstance(primed, frozenset)
    assert all(isinstance(d, date) for d in primed)
    assert len(primed) >= 2
