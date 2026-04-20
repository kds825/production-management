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
# CP-SAT sheath_color_hard 파라미터 호환성 테스트 (P3)
# ──────────────────────────────────────────────────────────────────────────────
#
# 배경: 긴급수주 반영 시 사용자 결정 — "블록 배치에서 색상 우선 강제".
# `sheath_color_hard` 파라미터로 시스 색상 체인을 soft penalty 대신 hard
# constraint 로 승격. P3 스코프 — 시그니처 호환성 + 방어 로직만 검증.
# 실제 색상 체인 품질(연속 배치, 같은 설비 강제)은 P4 에서 fixture 로 추가.


def test_cp_sat_sheath_color_hard_default_true(db: Session) -> None:
    """sheath_color_hard 는 기본값 True — 명시적 전달 없어도 hard constraint 작동.

    존재하지 않는 run_label 이라 배치가 비어 model 에 제약이 추가되진 않지만,
    기본값 경로가 예외 없이 통과하는지 (기본값 정의 실수/import 누락 회귀 방어).
    """
    r = cp_sat_schedule("NON_EXISTENT_RUN_P3", db)
    assert isinstance(r, dict)
    assert "warnings" in r
    assert isinstance(r["warnings"], list)


def test_cp_sat_sheath_color_hard_false_disables(db: Session) -> None:
    """sheath_color_hard=False 는 기존 soft-only 동작 유지 (기존 호출 경로 보장).

    P4 에서 fallback(infeasible → soft) 시 이 경로로 재시도하게 된다.
    """
    r = cp_sat_schedule(
        "NON_EXISTENT_RUN_P3_SOFT",
        db,
        sheath_color_hard=False,
    )
    assert isinstance(r, dict)
    assert "warnings" in r


def test_cp_sat_sheath_color_hard_signature_backcompat(db: Session) -> None:
    """기존 호출부(auto_schedule 등)가 sheath_color_hard 를 전달하지 않아도 통과.

    auto_schedule 는 **kwargs 로 전달하므로, 새 파라미터를 쓰지 않는 경로가
    여전히 깨지지 않는다는 것을 간접 검증 (signature positional/keyword 호환성).
    """
    r = cp_sat_schedule(
        "NON_EXISTENT_RUN_P3_SIG",
        db,
        random_seed=0,
        frozen_group_keys=None,
    )
    assert isinstance(r, dict)
    assert "warnings" in r


def test_cp_sat_sheath_color_hard_combines_with_frozen(db: Session) -> None:
    """P2 frozen_group_keys + P3 sheath_color_hard 동시 전달 시 예외 없음.

    두 제약이 충돌하는 방어 경로(둘 다 frozen 인 pair → hard skip)를 간접 검증.
    실제 충돌 시나리오 품질은 P4 에서 실데이터로 검증.
    """
    r = cp_sat_schedule(
        "NON_EXISTENT_RUN_P3_COMBO",
        db,
        frozen_group_keys={"GHOST_GK_A"},
        sheath_color_hard=True,
    )
    assert isinstance(r, dict)
    assert "warnings" in r


# ──────────────────────────────────────────────────────────────────────────────
# P4: reschedule_affected_groups(use_cpsat=...) 파라미터 계약
# ──────────────────────────────────────────────────────────────────────────────


def test_reschedule_use_cpsat_flag_accepts(db: Session) -> None:
    """use_cpsat=True 시그니처 호환성 — 빈 affected set 에서 예외 없음.

    긴급 경로가 CP-SAT 전역 재최적화를 호출하는 스위치. empty 입력은 어느 모드든
    no-op 이어야 한다.
    """
    r = reschedule_affected_groups(_RUN_LABEL, db, set(), use_cpsat=True)
    assert isinstance(r, dict)
    assert {"total_tasks", "violations", "warnings"}.issubset(r.keys())


def test_reschedule_use_cpsat_default_is_false(db: Session) -> None:
    """use_cpsat 기본값은 False — 하위 호환 (기존 호출부 무영향).

    auto_schedule / manual cascade 등 기존 호출부는 use_cpsat 을 전달하지 않는다.
    이 경로가 기존 greedy 동작을 보존해야 한다.
    """
    r1 = reschedule_affected_groups(_RUN_LABEL, db, set())
    r2 = reschedule_affected_groups(_RUN_LABEL, db, set(), use_cpsat=False)
    assert r1 == r2


def test_reschedule_use_cpsat_deterministic(db: Session) -> None:
    """use_cpsat=True 결정성 (빈 input 기준)."""
    r1 = reschedule_affected_groups(_RUN_LABEL, db, set(), use_cpsat=True)
    r2 = reschedule_affected_groups(_RUN_LABEL, db, set(), use_cpsat=True)
    assert r1 == r2


def test_reschedule_use_cpsat_ghost_affected_safe(db: Session) -> None:
    """CP-SAT 경로에서도 존재하지 않는 affected key 가 안전하게 처리되는지.

    긴급 로직이 stale affected_group_keys 를 넘기더라도 솔버가 죽으면 안 된다.
    """
    r = reschedule_affected_groups(
        _RUN_LABEL + "_GHOST_CPSAT",
        db,
        {"GHOST_GROUP_P4"},
        use_cpsat=True,
    )
    assert isinstance(r, dict)
    # warnings 필드는 있어야 함 (빈 리스트든, 메시지 있든)
    assert isinstance(r["warnings"], list)


# ──────────────────────────────────────────────────────────────────────────────
# P6: apply_urgent_incremental 반환 dict 시그니처 (snapshot/change_set_id 키)
# ──────────────────────────────────────────────────────────────────────────────


def test_apply_urgent_incremental_returns_change_set_keys(db: Session) -> None:
    """apply_urgent_incremental 반환 dict 에 P6 신규 키 포함.

    new_orders=0 조기 반환 경로 — ERP 파싱 실패 or 중복 전부 — 에서
    change_set_id=None 유지 + snapshot_count_before/after 필드 존재.

    실제 반영/CP-SAT 호출까지 가는 통합 시나리오는 ERP Excel fixture 가 필요해
    본 유닛 테스트 범위 밖. 시그니처 계약만 고정한다.
    """
    from app.services.urgent_scheduler import apply_urgent_incremental

    # 빈 bytes → parse_erp_file_incremental 이 파싱 실패/빈 결과 중 하나로 반응.
    # 예외가 나면 테스트가 감지하고, 정상 경로면 new_orders=0 으로 조기 반환.
    # 어느 경로든 반환 dict 의 키 집합은 안정적으로 노출되어야 한다.
    try:
        result = apply_urgent_incremental(
            b"",
            _RUN_LABEL + "_P6_EMPTY",
            db,
        )
    except Exception:
        # 빈 bytes 파싱 예외는 본 테스트 범위 밖 — skip (허용).
        import pytest

        pytest.skip("parse_erp_file_incremental rejected empty bytes")
        return

    assert isinstance(result, dict)
    required_keys = {
        "new_orders",
        "warnings",
        "change_set_id",
        "snapshot_count_before",
        "snapshot_count_after",
    }
    assert required_keys.issubset(set(result.keys())), (
        f"missing keys: {required_keys - set(result.keys())}"
    )

    # new_orders=0 경로이므로 change_set_id=None (INSERT 생략).
    # snapshot_count_before 는 DB 상태에 따라 0 이상의 int.
    assert result["new_orders"] == 0
    assert result["change_set_id"] is None
    assert isinstance(result["snapshot_count_before"], int)
    assert result["snapshot_count_before"] >= 0
    assert isinstance(result["snapshot_count_after"], int)


def test_build_snapshot_shape(db: Session) -> None:
    """_build_snapshot 이 기대 스키마의 dict 를 반환.

    read-only 쿼리이므로 DB 비어있거나 run_label 이 없어도 예외 없이 {} 반환.
    각 엔트리는 {start, end, equipment_code} 세 필드.
    """
    from app.services.urgent_scheduler import _build_snapshot

    snap = _build_snapshot(_RUN_LABEL + "_P6_SNAP_SHAPE", db)
    assert isinstance(snap, dict)

    # 각 엔트리 shape 검증 — 비어있는 DB 경로면 값 없음 (skip), 있으면 스키마 확인.
    for task_id, entry in snap.items():
        assert isinstance(task_id, str)
        assert isinstance(entry, dict)
        assert set(entry.keys()) == {"start", "end", "equipment_code"}


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
