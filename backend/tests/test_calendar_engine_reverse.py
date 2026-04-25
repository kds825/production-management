"""역방향 duration 헬퍼 테스트 — calculate_start_datetime."""

from datetime import datetime, timedelta

from app.infrastructure.calendar_engine import (
    calculate_end_datetime,
    calculate_start_datetime,
)


def test_reverse_round_trip_weekday_no_break():
    # 저압절연(24h): 월요일 오후 14:00에서 3시간 뒤 = 17:00
    end = datetime(2026, 4, 20, 17, 0)  # Monday
    start = calculate_start_datetime(end, 180, equipment_code="EX-B100")
    assert start == datetime(2026, 4, 20, 14, 0)

    # 순방향 재계산 → 동일 end 복원
    recomputed_end = calculate_end_datetime(start, 180, equipment_code="EX-B100")
    assert recomputed_end == end


def test_reverse_across_weekend():
    # 저압절연 끝이 월요일 09:00 → 2h 역산 = 금요일 11:00 (토·일 건너뜀)
    # 금요일 가용시간 12h (08:00~20:00)
    end = datetime(2026, 4, 20, 9, 0)  # Monday
    start = calculate_start_datetime(end, 120, equipment_code="EX-B100")
    # 월요일 09:00 기준 2h 역산 = 월요일 07:00 → 금요일 20:00 - 1h = 19:00...
    # 정확히는 월요일 08:00까지 1h (월요일 08:00~09:00), 나머지 1h는 금요일 19:00~20:00
    # 따라서 start는 금요일 19:00
    assert start == datetime(2026, 4, 17, 19, 0)


def test_reverse_across_break_stranding():
    # 연선연합(연선): 월~목 휴식 12:00~13:00, 18:00~18:30, 22:00~22:30
    # 월요일 13:30 종료, 1h 역산 → 휴식 12:00~13:00 건너뛰어 11:30
    end = datetime(2026, 4, 20, 13, 30)  # Monday
    start = calculate_start_datetime(end, 60, equipment_code="ST-T6B0")
    assert start == datetime(2026, 4, 20, 11, 30)


def test_reverse_exact_window_boundary():
    # 금요일 20:00 종료 (저압절연 금요일 종료) → 1h 역산 = 금요일 19:00
    end = datetime(2026, 4, 17, 20, 0)  # Friday
    start = calculate_start_datetime(end, 60, equipment_code="EX-B100")
    assert start == datetime(2026, 4, 17, 19, 0)


def test_reverse_zero_duration_is_end():
    end = datetime(2026, 4, 20, 10, 0)
    start = calculate_start_datetime(end, 0, equipment_code="EX-B100")
    assert start == end


def test_reverse_with_none_equipment_defaults():
    # equipment_code=None → 'default' 카테고리 (연선연합과 동일 22h 일일, 월~목 휴식 3개)
    # 월요일 13:30 종료, 1h 역산 → 휴식 12:00~13:00 건너뛰어 11:30
    end = datetime(2026, 4, 20, 13, 30)
    start = calculate_start_datetime(end, 60, equipment_code=None)
    assert start == datetime(2026, 4, 20, 11, 30)


def test_reverse_across_multiple_breaks_stranding():
    # 연선연합: 월요일 23:00 종료에서 8h 역산
    # 건너야 할 휴식: 22:00-22:30, 18:00-18:30, 12:00-13:00
    # 계산:
    #   23:00 → 22:30 (30min 사용, 휴식 건너뜀) → 18:30 (4h 사용, 휴식 건너뜀)
    #   → 13:00 (5h 30min 사용, 휴식 건너뜀) → 이미 8h 초과, 정확히 13:00+α
    # 총 일한 시간 = 8h. 23:00 - 22:30 = 30min, 22:00 - 18:30 = 3h30min (4h 누적), 18:00 - 13:00 = 5h (9h 누적). 5h 중 4h만 사용 → 13:00 + 1h = 14:00
    end = datetime(2026, 4, 20, 23, 0)
    start = calculate_start_datetime(end, 8 * 60, equipment_code="ST-T6B0")
    assert start == datetime(2026, 4, 20, 14, 0)


def test_reverse_long_duration_does_not_hit_safety_fallback():
    # 400h 역산 (현장 상한 가까움): 결과가 end - 400h 단순 감산과 다르면 안전 폴백 미발동
    end = datetime(2026, 4, 20, 10, 0)
    start = calculate_start_datetime(end, 400 * 60, equipment_code="EX-B100")
    # 안전 폴백이 발동하면 start == end - 400h (weekend 미고려). 캘린더 스킵이 작동하면 더 이른 시각.
    assert start < end - timedelta(hours=400)
