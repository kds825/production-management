"""긴급수주 재최적화 baseline + determinism 테스트.

목적
────
P1 (현재 phase): 변경 전 `reschedule_affected_groups` 의 계약을 고정한다. P4 에서
CP-SAT 로 내부 구현을 바꾸더라도 이 테스트들은 통과해야 한다 (behavior contract).

왜 여기를 테스트하는가:
- `apply_urgent_incremental` 은 ERP Excel 파싱을 포함해 테스트 진입 비용이 큼.
- 실질적 "재최적화" 로직은 `reschedule_affected_groups` 에 모여 있음 — 여기가
  P4 에서 CP-SAT 으로 교체되는 지점 (schedule_optimizer.py:2112 `_run_optimization_once`).
- 따라서 이 레이어에서 계약을 잡아두면 상위 (urgent) 와 하위 (cpsat) 모두 커버.

테스트 범위
───────────
1. 빈 affected_group_keys → 즉시 반환 (no-op).
2. 동일 input 반복 호출 → 동일 output (determinism smoke).
3. 존재하지 않는 run_label + 비어있지 않은 affected_group_keys → 안전한 반환.
4. CP-SAT `frozen_group_keys` 파라미터 시그니처 호환성 + 방어적 동작 (P2 추가).

P2~P4 진행 후 추가될 테스트는 모듈 하단 `# TODO (P4 이후)` 주석 참조.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.cp_sat_optimizer import cp_sat_schedule
from app.services.schedule_optimizer import reschedule_affected_groups

# 다른 테스트와 충돌하지 않는 고유 run_label — 실DB 에 남지 않도록 commit 없음.
_RUN_LABEL = "TEST_URGENT_REOPT_P1_20260420"


# ──────────────────────────────────────────────────────────────────────────────
# Baseline contract tests
# ──────────────────────────────────────────────────────────────────────────────


def test_reschedule_empty_affected_is_noop(db: Session) -> None:
    """빈 affected_group_keys → 빈 result 즉시 반환.

    schedule_optimizer.py:1972-1973 의 early-return 경로. P4 에서 CP-SAT 으로
    교체해도 동일해야 한다 — "건드릴 것 없으면 아무것도 하지 말 것".
    """
    result = reschedule_affected_groups(_RUN_LABEL, db, set())

    assert result == {"total_tasks": 0, "violations": [], "warnings": []}


def test_reschedule_empty_affected_deterministic(db: Session) -> None:
    """동일 input 2 회 호출 → 완전 동일 output.

    determinism 는 P7 의 diff API 가 유의미해지기 위한 필수 전제. 빈 케이스는
    가장 엄격한 determinism 검사 — 외부 상태 의존 없음.
    """
    r1 = reschedule_affected_groups(_RUN_LABEL, db, set())
    r2 = reschedule_affected_groups(_RUN_LABEL, db, set())

    assert r1 == r2


def test_reschedule_nonexistent_run_with_ghost_key_safe(db: Session) -> None:
    """DB 에 없는 run_label + 비어있지 않은 affected set → 예외 없이 반환.

    line 1976-1980 의 task 로드가 빈 list 를 돌려주는 케이스. P4 에서도 DB 조회
    결과가 비면 조기 종료해야 한다 (긴급 로직이 "처리 대상 없음" 을 유연하게
    다룰 수 있는지 회귀 보호).
    """
    result = reschedule_affected_groups(
        _RUN_LABEL + "_GHOST",
        db,
        {"GHOST_GROUP_DOES_NOT_EXIST"},
    )

    assert isinstance(result, dict)
    assert set(result.keys()) >= {"total_tasks", "violations", "warnings"}
    assert result["total_tasks"] == 0


def test_reschedule_result_shape_stable(db: Session) -> None:
    """반환 dict 의 key 집합을 고정. P4 에서 확장하더라도 기존 key 는 보존.

    하위 호환 목적 — 프론트엔드/라우터가 이 key 를 기대하고 있다.
    """
    result = reschedule_affected_groups(_RUN_LABEL, db, set())

    required_keys = {"total_tasks", "violations", "warnings"}
    assert required_keys.issubset(set(result.keys()))
    assert isinstance(result["violations"], list)
    assert isinstance(result["warnings"], list)
    assert isinstance(result["total_tasks"], int)


# ──────────────────────────────────────────────────────────────────────────────
# CP-SAT frozen_group_keys 파라미터 호환성 테스트 (P2)
# ──────────────────────────────────────────────────────────────────────────────
#
# 배경: 긴급수주 추가 시 전역 CP-SAT 재최적화를 돌리되, 이미 진행중/완료/base_date
# 이전 'scheduled' 배치는 기존 start/end/equipment 로 고정되어야 한다.
# `frozen_group_keys` 파라미터는 해당 고정 대상의 batch_group 집합을 받는다.
#
# P2 스코프 — 시그니처 호환성 + 방어적 동작만 검증:
#   - None / 빈 set 은 기존 동작과 동일 (무시)
#   - 존재하지 않는 key → warning 만 추가하고 예외 없이 반환
# 실제 freeze 동작(설비/시간 고정) 품질 검증은 P4 에서 실데이터 fixture 로 추가한다.


def test_cp_sat_frozen_group_keys_accepts_none_and_empty(db: Session) -> None:
    """frozen_group_keys=None 과 frozen_group_keys=set() 은 기존 동작과 동일.

    시그니처 호환성 검증 — 기존 호출부(auto_schedule 등)가 파라미터를 전달하지
    않아도, 또는 빈 set 을 전달해도 예외 없이 동작해야 한다.
    """
    # 존재하지 않는 run_label 로 호출 — CP-SAT 이 "배치 없음" 경로로 빈 결과 반환
    r1 = cp_sat_schedule("NON_EXISTENT_RUN_P2_FROZEN", db, frozen_group_keys=None)
    r2 = cp_sat_schedule("NON_EXISTENT_RUN_P2_FROZEN", db, frozen_group_keys=set())

    # 둘 다 예외 없이 dict 반환 + 필수 키 존재
    assert isinstance(r1, dict)
    assert isinstance(r2, dict)
    for r in (r1, r2):
        assert "total_tasks" in r
        assert "warnings" in r
        assert isinstance(r["warnings"], list)


def test_cp_sat_frozen_group_keys_missing_group_warns(db: Session) -> None:
    """존재하지 않는 frozen_group_keys → 예외 없이 반환 (warning 기록은 허용).

    방어적 동작 검증 — DB 에 없는 batch_group 을 freeze 요청 받아도 솔버가
    죽으면 안 된다. 상위 로직(긴급 재최적화)이 stale key 를 넘겨도 견고해야.
    """
    r = cp_sat_schedule(
        "NON_EXISTENT_RUN_P2_FROZEN_MISSING",
        db,
        frozen_group_keys={"NONEXISTENT_GROUP_KEY"},
    )

    # 경고는 있을 수도 있고 없을 수도 있지만 예외 없이 반환
    assert isinstance(r, dict)
    assert "warnings" in r
    assert isinstance(r["warnings"], list)


# ──────────────────────────────────────────────────────────────────────────────
# TODO (P4 이후)
# ──────────────────────────────────────────────────────────────────────────────
#
# 실제 배치를 seed 해 CP-SAT 재최적화 품질을 검증하는 테스트는 P4 에서 추가한다:
#
# - test_urgent_preserves_frozen_tasks: in_progress/completed/wip_complete 태스크는
#   start_datetime 이 절대 변하지 않는다.
# - test_urgent_reoptimize_beats_greedy_on_tardiness: 동일 fixture 에서 CP-SAT
#   결과의 sum(tardiness) 가 greedy 결과보다 작거나 같다.
# - test_urgent_color_chain_hard_constraint: 시스 클러스터 인접 쌍의 색상 순서가
#   정렬 규칙에 부합한다 (hard constraint 검증 — P3 이후).
# - test_urgent_deterministic_with_seed: random_seed 고정 시 동일 input → 동일
#   task 배치 (버전 diff API 의 신뢰성 기반).
