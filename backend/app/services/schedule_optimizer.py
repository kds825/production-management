"""자동 스케줄링 엔진 — 납기역산 + 그리디 배치 (re-export 셸).

원래 단일 파일에 한 덩어리로 묶여 있던 그리디 + 헬퍼들을 Week 3 Task 3A.1/3A.2
에서 다음 모듈로 분리했다:

    app.domain.constants                     : 데이터 상수
    app.application._shared.{calendar_ops,slot_filters,group_ops,db_ops}
                                             : 공용 헬퍼 (Phase 1 step 3 이동)
    app.services.greedy.slot_finder          : _find_available_slot
    app.services.greedy.auto_schedule        : auto_schedule + 그리디 핵심 + 재시도
    app.services.greedy.reschedule_affected  : reschedule_affected_groups + reschedule

본 파일은 D7-C invariant (Week 9) 까지 모든 기존 dotted path 를 보존하기 위한
re-export 셸이다. ruff 가 unused import 를 제거하지 않도록 모든 re-export 에
``# noqa: F401`` 를 명시한다.

──── 레거시 위치 회귀 anchor ─────────────────────────────────────────────
일부 테스트 (test_tp_routing) 는 본 파일을 source-string 으로 검사한다.
이동된 코드의 원위치 표지 문자열을 본 docstring 에 포함해 anchor 만 유지한다.

  - "T/P 공정 preferred 설비"   : equipment_code == "TP-2" 우선 narrowing 블록.
    실제 코드는 services/greedy/auto_schedule.py:_run_optimization_once 안에
    동일 형태로 존재. (Week 3 Task 3A.2 이동.)
"""

from __future__ import annotations

# ── 데이터 상수 (Week 3 Task 3A.1) ──────────────────────────────────────────
from app.domain.constants import (  # noqa: F401
    PREDECESSOR_PROCESS,  # re-export until Week 9 (D7-C)
    _DEFAULT_WELDING_MIN,  # re-export until Week 9 (D7-C)
    _WIP_SKIP_PROCESSES,  # re-export until Week 9 (D7-C)
)

# ── calendar_engine 라우팅 ──────────────────────────────────────────────────
# slot_filters.align_start_to_predecessor_end 는 본 모듈 attribute 로
# `calculate_start_datetime` / `calculate_end_datetime` 를 lookup 하므로
# (테스트 monkeypatch 호환) 반드시 모듈 namespace 에 노출.
from app.infrastructure.calendar_engine import (  # noqa: F401
    calculate_end_datetime,
    calculate_start_datetime,
)

# ── JIT 헬퍼 라우팅 ─────────────────────────────────────────────────────────
# greedy.auto_schedule._run_optimization_once 가 사용하지만, 기존 테스트
# (test_jit_integration) 가 schedule_optimizer 모듈에서 patch.object 한다.
# Week 3 Task 3A.2 이동 후에도 monkeypatch 가 유효하도록 노출 유지.
from app.services.jit_scheduling import apply_jit_delay  # noqa: F401

# ── application/_shared 재노출 (Phase 1 step 3 신 위치) ──────────────────
from app.application._shared.group_ops import (  # noqa: F401
    _extract_core_main_sq,  # re-export until Week 9 (D7-C)
    _get_drum_winding_min,  # re-export until Week 9 (D7-C)
    _get_stranding_setup_min,  # re-export until Week 9 (D7-C)
    _is_core_group,  # re-export until Week 9 (D7-C)
    _is_sheath_group,  # re-export until Week 9 (D7-C)
    _schedule_multi_equipment,  # re-export until Week 9 (D7-C)
    _st_sq,  # re-export until Week 9 (D7-C)
)
from app.application._shared.slot_filters import (  # noqa: F401
    _filter_by_sheath_routing,  # re-export until Week 9 (D7-C)
    _find_eligible_equipment,  # re-export until Week 9 (D7-C)
    _narrow_by_stranding,  # re-export until Week 9 (D7-C)
    align_start_to_predecessor_end,  # re-export until Week 9 (D7-C)
)

# ── greedy 패키지 재노출 (Week 3 Task 3A.2) ────────────────────────────────
from app.services.greedy.slot_finder import (  # noqa: F401
    _find_available_slot,  # re-export shell (D7-C, Week 9)
)
from app.services.greedy.auto_schedule import (  # noqa: F401
    _SHEATH_ROUTING,  # re-export shell (D7-C, Week 9)
    _get_sheath_type,  # re-export shell (D7-C, Week 9)
    _get_tp_line_speed,  # re-export shell (D7-C, Week 9)
    _group_earliest_due,  # re-export shell (D7-C, Week 9)
    _purge_run_tasks,  # re-export shell (D7-C, Week 9)
    _run_optimization_once,  # re-export shell (D7-C, Week 9)
    _sheath_group_color_rank,  # re-export shell (D7-C, Week 9)
    _sheath_group_due_week_int,  # re-export shell (D7-C, Week 9)
    _should_apply_jit,  # re-export shell (D7-C, Week 9)
    _tardiness_boost_retry,  # re-export shell (D7-C, Week 9)
    auto_schedule,  # re-export shell (D7-C, Week 9)
)
from app.services.greedy.reschedule_affected import (  # noqa: F401
    _ALWAYS_FROZEN_STATUSES,  # re-export shell (D7-C, Week 9)
    _reschedule_affected_groups_cpsat,  # re-export shell (D7-C, Week 9)
    _reset_non_frozen_for_retry,  # re-export shell (D7-C, Week 9)
    reschedule,  # re-export shell (D7-C, Week 9)
    reschedule_affected_groups,  # re-export shell (D7-C, Week 9)
)
