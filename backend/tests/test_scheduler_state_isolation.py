"""Phase 4 step 4 (architecture-target.md §4 P5) — SchedulerState 격리 검증.

`_run_optimization_once` 가 retry 마다 새로 호출되며, 각 호출은 독립적인
runtime state 를 가져야 한다 (cross-contamination 회피). 직전 retry 의
mutation 이 다음 retry 시작 시점에 보이면 안 된다.

본 테스트는 두 가지 layer 에서 invariant 를 freeze:

1. **SchedulerState() 자체 격리** — dataclass 가 default_factory 로 fresh
   mutable 컨테이너를 만드는지 확인. 두 instance 의 timeline / predecessor_map
   등이 서로 다른 객체 (`is not`) 이고, 한쪽 mutate 가 다른 쪽에 영향을 주지 않음.
2. **retry-time 격리** — auto_schedule 가 내부적으로 _run_optimization_once
   를 두 번 이상 호출하는 경우 (Level 3 fallback, overlap retry 등), 두
   호출의 SchedulerState (또는 동등한 per-call locals) 가 독립이어야 함.
   현 구현은 _run_optimization_once 의 local vars (timeline 등) 가 자동
   격리되지만, 본 테스트는 미래 회귀 (e.g., 누가 module-level cache 로 옮길
   때) 를 catch.

후자는 Phase 4 step 5 에서 _run_optimization_once 가 SchedulerState 를 wire
하면 더 단순해진다 — 본 step 4 에서는 dataclass 격리 + 컨벤션 검증만.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.application.scheduling.greedy.scheduler_state import SchedulerState


def test_two_states_have_independent_timeline() -> None:
    """SchedulerState() 두 번 호출 시 timeline dict 가 서로 다른 객체."""
    a = SchedulerState()
    b = SchedulerState()
    assert a.timeline is not b.timeline, (
        "default_factory 가 dict 를 공유 — retry cross-contamination 위험"
    )
    a.timeline.setdefault("EX-B100", []).append(
        (datetime(2026, 4, 21, 8, 0), datetime(2026, 4, 21, 10, 0))
    )
    assert "EX-B100" not in b.timeline, "instance a 의 mutate 가 b 에 누수 — 격리 깨짐"


def test_two_states_have_independent_predecessor_map() -> None:
    """predecessor_map 도 default_factory 로 격리."""
    a = SchedulerState()
    b = SchedulerState()
    assert a.predecessor_map is not b.predecessor_map
    a.predecessor_map[("SO-01", 1)] = 42
    assert b.predecessor_map == {}


@pytest.mark.parametrize(
    "field_name",
    [
        "timeline",
        "predecessor_map",
        "last_batch_on_equip",
        "sq_to_equip",
        "tasks_created",
        "process_end_by_sq",
        "process_first_output_by_sq",
        "core_first_drum_by_main_sq",
        "preempted_remainder",
        "equipment_by_process",
        "speed_map",
    ],
)
def test_all_mutable_fields_are_isolated(field_name: str) -> None:
    """모든 dict / list 필드가 instance 간 별도 객체인지."""
    a = SchedulerState()
    b = SchedulerState()
    assert getattr(a, field_name) is not getattr(b, field_name), (
        f"{field_name} 가 default_factory 없이 공유 default 를 사용 — "
        f"retry contamination 위험"
    )


def test_scalar_fields_default() -> None:
    """scalar 필드 (first_insul_output / welding_min) 의 default 검증."""
    s = SchedulerState()
    assert s.first_insul_output is None
    assert s.welding_min == 30.0
    assert s.constraint_params is None


def test_master_data_population() -> None:
    """master data 영역 (read-only convention) 도 caller 가 채울 수 있는지 확인."""
    s = SchedulerState(
        equipment_by_process={"저압절연": [object()]},
        speed_map={("EX-B100", 120.0): object()},
        welding_min=45.0,
    )
    assert s.equipment_by_process["저압절연"]
    assert s.speed_map[("EX-B100", 120.0)] is not None
    assert s.welding_min == 45.0
    # 다른 instance 는 영향 없음
    other = SchedulerState()
    assert other.equipment_by_process == {}
    assert other.welding_min == 30.0


def test_run_optimization_once_creates_fresh_locals_per_call() -> None:
    """_run_optimization_once 의 retry 격리 컨벤션 — module-level cache 부재 확인.

    현재 구현은 _run_optimization_once 의 local vars (timeline 등) 가 함수
    스코프에 자동 격리. 본 테스트는 이 함수 module 안에 module-level 의
    가변 cache 가 실수로 추가되었는지 detect — 발생 시 retry 격리 깨짐.
    """
    from app.application.scheduling.greedy import optimization_loop as _ol

    # module-level mutable globals 후보 (timeline / predecessor_map / ...) 가
    # 함수 외부에 존재하면 isolation 위반. _MODULE 상수, 함수 정의, dataclass
    # / Protocol / TypeVar 등은 OK.
    suspect_names = [
        "timeline",
        "predecessor_map",
        "last_batch_on_equip",
        "sq_to_equip",
        "tasks_created",
        "process_end_by_sq",
        "process_first_output_by_sq",
        "core_first_drum_by_main_sq",
        "preempted_remainder",
    ]
    for name in suspect_names:
        if hasattr(_ol, name):
            attr = getattr(_ol, name)
            # function / class / module 은 OK. 단, 일반 dict/list 가 module-level
            # 에 노출되면 retry contamination 위험.
            assert not isinstance(attr, (dict, list)), (
                f"optimization_loop module-level 에 mutable {name} 가 노출됨 — "
                f"retry 사이 누수 가능. function-local 로 옮길 것."
            )
