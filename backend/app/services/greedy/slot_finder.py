"""그리디 슬롯 탐색 헬퍼.

설비 timeline 에서 가용한 첫 번째 슬롯을 찾는 단일 함수만 제공.
기존 위치: app.services.schedule_optimizer (Week 3 Task 3A.2 이전 분리됨).
원래 dotted path 는 schedule_optimizer 모듈에서 re-export 로 유지된다 (D7-C).
"""

from __future__ import annotations

from datetime import datetime, timedelta


def _find_available_slot(
    earliest: datetime,
    duration_min: float,
    occupied_slots: list,
    db=None,
    equipment_code: str | None = None,
) -> datetime:
    """설비에서 가용한 첫 번째 슬롯 찾기.

    근무시간(08~22시) 기반 캘린더를 사용하여 실제 종료 시각을 계산한다.
    단순 timedelta 덧셈은 야간/주말을 무시하여 슬롯 겹침을 유발할 수 있다.
    """
    # 지역 import — calendar_engine 은 schedule_optimizer 가 monkeypatch 가능한
    # 형태로 노출하지 않으므로 직접 import 한다. 모듈 로드 시점 순환 방지 목적도 있음.
    from app.services.calendar_engine import calculate_end_datetime

    candidate = earliest
    sorted_slots = sorted(occupied_slots, key=lambda s: s[0])

    for slot_start, slot_end in sorted_slots:
        # 캘린더 기반 종료 시각으로 슬롯 겹침 판단
        if db is not None:
            candidate_end = calculate_end_datetime(
                candidate, duration_min, db, equipment_code
            )
        else:
            candidate_end = candidate + timedelta(minutes=duration_min)
        # 올림 일관성: auto_schedule line 922-925 는 end_dt 를 다음 정각으로 올림
        # 하여 timeline 에 등록한다. 여기서도 같은 규칙을 적용해야 slot_start 비교가
        # 실제 점유 시간과 일치 (Phase C Task 3 root cause — SH-A100 task 28942×28966
        # 19분 overlap 원인).
        if (
            candidate_end.minute > 0
            or candidate_end.second > 0
            or candidate_end.microsecond > 0
        ):
            candidate_end = candidate_end.replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)
        if candidate_end <= slot_start:
            # Fits before this slot
            return candidate
        if candidate < slot_end:
            candidate = slot_end  # Push after this slot

    return candidate
