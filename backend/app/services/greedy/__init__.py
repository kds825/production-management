"""Greedy 스케줄링 패키지.

기존 schedule_optimizer.py 에 한 덩어리로 묶여 있던 그리디 경로 (auto_schedule,
reschedule_affected_groups, reschedule, _find_available_slot) 를 책임 단위로
분리한다 (Week 3 Task 3A.2). 기존 dotted path
``app.services.schedule_optimizer.<sym>`` 은 D7-C invariant (Week 9) 까지
re-export 셸로 유지된다.

서브모듈:
    slot_finder        — _find_available_slot
    auto_schedule      — auto_schedule + 헬퍼 (_purge_run_tasks,
                          _tardiness_boost_retry, _run_optimization_once 등)
    reschedule_affected — reschedule_affected_groups + reschedule + 헬퍼
"""

from app.services.greedy.slot_finder import _find_available_slot  # noqa: F401
