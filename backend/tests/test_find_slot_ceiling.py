"""`_find_available_slot` 이 올림 end 를 반영하여 overlap 유발 안 하는지 검증.

Root cause (Phase C Task 3 → Phase D 해결): schedule_optimizer 는 최종
end_dt 를 다음 정각으로 올림하여 timeline 에 등록. 그러나 `_find_available_slot`
내부는 올림 전 candidate_end 로 slot 비교 → 실제 점유보다 짧은 interval 로
판정하여 tight window 에 task 를 밀어넣음. SH-A100 task 28942 × 28966 의
19분 overlap 이 이 버그.
"""

from __future__ import annotations

from datetime import datetime

from app.application.scheduling.greedy.slot_finder import _find_available_slot
def test_find_slot_respects_ceiling_before_adjacent_slot():
    """duration 67min 이지만 end 올림 후 05:00 → 04:41 slot 과 충돌해야."""
    earliest = datetime(2026, 4, 8, 3, 0)  # 03:00
    duration = 67  # raw wall 04:07 → 올림 05:00
    occupied = [(datetime(2026, 4, 8, 4, 41), datetime(2026, 4, 8, 8, 0))]
    # db=None → timedelta fallback. 올림 규칙만 검증.
    result = _find_available_slot(
        earliest, duration, occupied, db=None, equipment_code="SH-A100"
    )
    # 03:00 에 67min duration 이면 raw end=04:07, 올림=05:00. (04:41, 08:00) 과
    # 충돌 → 05:00 이 04:41 초과이므로 slot 에 진입, candidate = 08:00 으로 push.
    assert result == datetime(2026, 4, 8, 8, 0), (
        f"올림 적용 후에는 08:00 으로 push되어야 함. 실제: {result}"
    )


def test_find_slot_exact_hour_end_no_ceiling():
    """raw end 가 이미 정각이면 올림 영향 없음 (기존 동작 유지)."""
    earliest = datetime(2026, 4, 8, 3, 0)
    duration = 60  # end 04:00 정확히 정각
    occupied = [(datetime(2026, 4, 8, 4, 41), datetime(2026, 4, 8, 8, 0))]
    result = _find_available_slot(
        earliest, duration, occupied, db=None, equipment_code="SH-A100"
    )
    # raw 04:00 <= 04:41 OK → 03:00 fit
    assert result == datetime(2026, 4, 8, 3, 0), (
        f"정각 종료 케이스는 fit 해야 함. 실제: {result}"
    )


def test_find_slot_ceiling_causes_push_from_gap():
    """작은 틈 사이에 겨우 맞는 task 도 올림 때문에 push 되는 경우."""
    # 03:00-04:00 빈 틈. duration=59min → raw 03:59 → 올림 04:00 → fit 할까?
    # 04:00 <= 04:00 참 → fit. 경계 조건 확인.
    earliest = datetime(2026, 4, 8, 3, 0)
    duration = 59
    occupied = [(datetime(2026, 4, 8, 4, 0), datetime(2026, 4, 8, 8, 0))]
    result = _find_available_slot(
        earliest, duration, occupied, db=None, equipment_code="SH-A100"
    )
    # raw 03:59 → 올림 04:00 → `04:00 <= 04:00` True → fit at 03:00
    assert result == datetime(2026, 4, 8, 3, 0)


def test_find_slot_no_occupied():
    """occupied_slots 비어 있으면 earliest 그대로."""
    earliest = datetime(2026, 4, 8, 3, 0)
    result = _find_available_slot(earliest, 120, [], db=None, equipment_code="SH-A100")
    assert result == earliest
