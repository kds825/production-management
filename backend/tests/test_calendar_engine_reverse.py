"""역방향 duration 헬퍼 테스트 — calculate_start_datetime."""

from datetime import datetime

from app.services.calendar_engine import (
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
