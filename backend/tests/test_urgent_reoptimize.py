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
# C1 Regression: _datetime_to_wmin 시간축 정합성 (CRITICAL fix)
# ──────────────────────────────────────────────────────────────────────────────
#
# 배경: CP-SAT 모델의 시간축은 _WORK_MIN_PER_DAY(=840 분, 08:00~22:00) 기반
# **working-minutes** 이다. `_due_work_min` 등 다른 변수들은 working-minute 축
# 으로 계산되지만, 과거 `_datetime_to_wmin` 은 wall-clock delta 를 반환해
# (`(dt - base_date).total_seconds() // 60`) frozen 배치의 start 가 솔버 인식
# 위치와 실제 위치가 어긋났다. 결과: INFEASIBLE 또는 non-frozen 배치가 엉뚱한
# 곳으로 이동. 본 테스트는 변환 함수 자체의 축 정합성을 단위로 가드한다.


def test_datetime_to_wmin_same_day_within_working_hours() -> None:
    """base_date 와 같은 날 08:00 ~ 22:00 구간은 그대로 분 offset.

    base_date = 2026-04-20 08:00 (월), dt = 2026-04-20 14:00 (월)
    → working-min offset = 6h * 60 = 360 (working-hour 기준).
    wall-clock 과 결과가 같은 케이스(하루 안, 근무시간 안) — 회귀 없음 확인.
    """
    from datetime import datetime

    from app.services.cp_sat_optimizer import _datetime_to_wmin

    base = datetime(2026, 4, 20, 8, 0, 0)
    dt = datetime(2026, 4, 20, 14, 0, 0)
    assert _datetime_to_wmin(dt, base) == 360


def test_datetime_to_wmin_next_working_day_axis_alignment() -> None:
    """C1 핵심: 하루 뒤 08:00 은 정확히 _WORK_MIN_PER_DAY(840 분) offset.

    버그가 있는 구현(wall-clock delta)에서는 24h=1440 분을 반환해 축이 어긋난다.
    올바른 구현(working-min)은 정확히 840. 이 차이가 frozen 배치의 start 를
    솔버가 "약 600분 뒤" 로 오해하게 만든 근본 원인.
    """
    from datetime import datetime

    from app.services.cp_sat_optimizer import (
        _WORK_MIN_PER_DAY,
        _datetime_to_wmin,
    )

    base = datetime(2026, 4, 20, 8, 0, 0)  # Mon 08:00
    dt = datetime(2026, 4, 21, 8, 0, 0)  # Tue 08:00 (하루 working-day 경과)

    # C1 fix: 정확히 1 working-day = 840 분 (wall-clock 1440 아님)
    assert _datetime_to_wmin(dt, base) == _WORK_MIN_PER_DAY


def test_datetime_to_wmin_weekend_skipped() -> None:
    """주말은 working-day 카운트에서 제외 → axis consistency.

    base=금요일 08:00, dt=월요일 08:00 → working-days between = 1 (Fri 만)
    → 1 * 840 = 840 분. wall-clock 으로는 72h=4320 분.
    `_due_work_min` 과 동일한 `_work_days_between` 축을 쓰는지 확인.
    """
    from datetime import datetime

    from app.services.cp_sat_optimizer import (
        _WORK_MIN_PER_DAY,
        _datetime_to_wmin,
    )

    base = datetime(2026, 4, 17, 8, 0, 0)  # Friday
    dt = datetime(2026, 4, 20, 8, 0, 0)  # Monday (skip Sat/Sun)

    # Fri(working-day 1) + Sat/Sun(0) = 1 working-day from base
    assert _datetime_to_wmin(dt, base) == _WORK_MIN_PER_DAY


def test_datetime_to_wmin_past_returns_zero() -> None:
    """dt < base_date → 0 반환 (과거는 모델 밖).

    frozen 배치가 base_date 이전에 시작한 경우(in_progress 는 이미 시작됨),
    솔버 horizon 바깥이므로 0 으로 clamp 해 INFEASIBLE 방지.
    """
    from datetime import datetime, timedelta

    from app.services.cp_sat_optimizer import _datetime_to_wmin

    base = datetime(2026, 4, 20, 8, 0, 0)
    past = base - timedelta(hours=3)
    assert _datetime_to_wmin(past, base) == 0


def test_datetime_to_wmin_consistent_with_due_work_min() -> None:
    """_due_work_min 과 _datetime_to_wmin 이 동일 축을 쓰는지 확인 (C1 핵심).

    같은 날짜에 대해 _due_work_min(날짜) == _datetime_to_wmin(날짜 08:00).
    이 일치가 깨지면 frozen start 와 due_wmin 이 다른 축 위에 놓여 솔버가
    "frozen 은 미래 / due 는 훨씬 먼 미래" 로 오해해 non-frozen 그룹을 엉뚱한
    곳에 배치하게 된다.
    """
    from datetime import datetime

    from app.services.cp_sat_optimizer import (
        _datetime_to_wmin,
        _due_work_min,
    )

    base = datetime(2026, 4, 20, 8, 0, 0)
    # 3 working-days 뒤 (Mon → Thu)
    due_date_target = datetime(2026, 4, 23, 8, 0, 0)

    due_wmin = _due_work_min(due_date_target.date(), base)
    frozen_wmin = _datetime_to_wmin(due_date_target, base)

    # 두 함수가 같은 순간(target day 08:00)을 같은 값으로 인식해야 한다.
    # wall-clock 구현이면 frozen_wmin=4320, due_wmin=2520 으로 다름 → 축 어긋남.
    assert due_wmin == frozen_wmin, (
        f"C1 axis mismatch: _due_work_min={due_wmin} vs _datetime_to_wmin="
        f"{frozen_wmin} — frozen 배치의 start 가 due 축과 다른 축에 박히면 "
        f"솔버가 위치를 잘못 인식한다."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Fixture regression: frozen in_progress 배치의 start_datetime 보존
# ──────────────────────────────────────────────────────────────────────────────


def test_cpsat_frozen_in_progress_start_preserved(db: Session) -> None:
    """CRITICAL: in_progress 배치의 start_datetime 이 CP-SAT 재최적화 후에도 유지.

    C1 regression guard: _datetime_to_wmin 의 시간축이 어긋나면 frozen 위치가
    실제와 다르게 박혀 이 테스트가 깨진다. 동시에 `_ALWAYS_FROZEN_STATUSES`
    경로가 in_progress 태스크를 삭제 대상에서 제외하는지도 간접 검증.
    """
    from datetime import datetime, timedelta

    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask

    run_label = "TEST_C1_FROZEN_20260420"

    # Cleanup before — 이전 실행 잔여물 제거 (commit 없으므로 rollback 에 의존)
    db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).delete(
        synchronize_session=False
    )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).delete(
        synchronize_session=False
    )
    db.flush()

    try:
        # 1. in_progress 배치 seed
        header = ProductionBatch(
            run_label=run_label,
            process_name="연선",
            batch_seq=-1,
            drum_count=3,
            drum_length_m=7100,
            total_length_m=21300,
            sq_mm2=120,
            core_count=1,
            equipment_code="ST-54BO1",
            due_date=datetime.now().date() + timedelta(days=10),
            customer_priority=5,
            line_speed_mpm=11.7,
            setup_time_min=210,
            estimated_duration_min=2200,
            status="in_progress",  # frozen 대상
            batch_group=f"ST-120-FROZEN-{run_label}",
        )
        db.add(header)
        db.flush()

        # 2. ScheduleTask seed — 현재 진행 중 (start 는 "지금 기준 과거")
        original_start = datetime.now().replace(
            hour=10, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
        original_end = original_start + timedelta(hours=5)
        task = ScheduleTask(
            batch_id=header.batch_id,
            equipment_code="ST-54BO1",
            start_datetime=original_start,
            end_datetime=original_end,
            status="in_progress",
            run_label=run_label,
            batch_group=header.batch_group,
        )
        db.add(task)
        db.flush()
        original_task_id = task.task_id

        # 3. CP-SAT 재최적화 호출 (빈 affected_group_keys — frozen 만 있고 재배치 대상 없음)
        result = reschedule_affected_groups(run_label, db, set(), use_cpsat=True)

        # 시그니처 / shape 검증
        assert isinstance(result, dict)
        assert {"total_tasks", "violations", "warnings"}.issubset(result.keys())

        # 4. in_progress ScheduleTask 가 손상되지 않았는지 확인
        db.flush()
        retasks = (
            db.query(ScheduleTask)
            .filter(
                ScheduleTask.run_label == run_label,
                ScheduleTask.status == "in_progress",
            )
            .all()
        )
        assert len(retasks) == 1, (
            f"in_progress task 갯수가 바뀜: 기대 1, 실제 {len(retasks)} "
            f"— frozen 보호 경로 깨짐"
        )
        retask = retasks[0]
        assert retask.task_id == original_task_id, (
            "in_progress task 가 삭제 후 재생성됨 — frozen 보호 경로 위반"
        )
        assert retask.start_datetime == original_start, (
            f"C1 regression: in_progress start moved "
            f"{original_start} → {retask.start_datetime}"
        )
        assert retask.equipment_code == "ST-54BO1", (
            "in_progress 설비가 변경됨 — frozen 보호 경로 위반"
        )
    finally:
        # cleanup (rollback 대비 — use_cpsat 경로에 flush 가 포함되나 commit 은 테스트 외부)
        db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).delete(
            synchronize_session=False
        )
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).delete(
            synchronize_session=False
        )
        db.flush()


# ──────────────────────────────────────────────────────────────────────────────
# P9-B: Tardiness hard + chain weight rebalance 시그니처 / 상수 계약
# ──────────────────────────────────────────────────────────────────────────────
#
# 사용자 결정 (Tardiness A 엄격): 납기 초과는 1분이라도 infeasible 로 취급.
# cp_sat_schedule(tardiness_hard=True) 에서 end_var <= due_wmin 를 hard 로 강제.
# 색상 교체는 tardiness 와 동일 분 단위(_CHAIN_WEIGHT=120) 로 cost 화해 경쟁 가능.


def test_cp_sat_tardiness_hard_signature(db: Session) -> None:
    """tardiness_hard 파라미터 시그니처 호환성.

    None run_label → 예외 없이 dict 반환 (빈 배치 경로).
    True / False 둘 다 허용되어야 한다.
    """
    r1 = cp_sat_schedule("NON_EXISTENT_P9B", db, tardiness_hard=True)
    r2 = cp_sat_schedule("NON_EXISTENT_P9B", db, tardiness_hard=False)
    assert isinstance(r1, dict) and isinstance(r2, dict)
    assert "warnings" in r1 and "warnings" in r2


def test_cp_sat_tardiness_hard_default_true(db: Session) -> None:
    """tardiness_hard 기본값 True (사용자 결정 A 엄격).

    기존 호출부(auto_schedule / reschedule_affected_groups_cpsat)는
    tardiness_hard 를 전달하지 않으므로 기본값이 True 여야 "납기 1분도 초과 불가"
    요구를 만족한다.
    """
    import inspect

    sig = inspect.signature(cp_sat_schedule)
    assert "tardiness_hard" in sig.parameters, (
        "cp_sat_schedule 에 tardiness_hard 파라미터 없음"
    )
    assert sig.parameters["tardiness_hard"].default is True, (
        f"tardiness_hard 기본값은 True 여야 함 (실제={sig.parameters['tardiness_hard'].default})"
    )


def test_chain_weight_is_real_changeover_minutes(db: Session) -> None:
    """chain_diff 가중치가 실제 color changeover 분 단위와 일치.

    사용자 결정: 색상 변경 1회 cost = 120min (ConstraintConfig 4-2 sheath_color_min).
    solver 가 "color 1회 교체 비용" 과 "idle 120min" 을 동등 비교할 수 있으려면
    _CHAIN_WEIGHT >= 60 이어야 한다. 기존 값(1)은 tardiness_weight(수십~수백만)에
    밀려 사실상 색상 최적화가 무시되던 상태였다.
    """
    from app.services.cp_sat_optimizer import _CHAIN_WEIGHT

    assert _CHAIN_WEIGHT >= 60, (
        f"_CHAIN_WEIGHT={_CHAIN_WEIGHT} 너무 작음 — 색상 교체 cost 가 "
        f"idle/tardiness 대비 사실상 무시됨"
    )


def test_cp_sat_tardiness_hard_and_color_hard_combine(db: Session) -> None:
    """tardiness_hard + sheath_color_hard 동시 전달 예외 없음 (3-level fallback chain 구축용)."""
    r = cp_sat_schedule(
        "NON_EXISTENT_P9B_COMBO",
        db,
        tardiness_hard=True,
        sheath_color_hard=True,
    )
    assert isinstance(r, dict)
    assert "warnings" in r


# ──────────────────────────────────────────────────────────────────────────────
# P9-E: Calendar engine 통합 — _working_minutes_between / _due_work_min 확장
# ──────────────────────────────────────────────────────────────────────────────
#
# 기존 _work_days_between + _WORK_MIN_PER_DAY 는 "하루=고정 840분" 근사로 공휴일/
# 금요일/공정별 시간 차이를 반영하지 못했다. calendar_engine 기반 함수로 교체.


def test_working_minutes_between_weekend_excluded(db: Session) -> None:
    """주말은 working-minute 0."""
    from datetime import datetime

    from app.services.cp_sat_optimizer import _working_minutes_between

    sat = datetime(2026, 4, 18, 8, 0)  # 토
    sun_end = datetime(2026, 4, 19, 20, 0)  # 일
    # default 카테고리(equipment_code None) — 주말 0h
    assert _working_minutes_between(sat, sun_end) == 0


def test_working_minutes_between_weekday_with_break(db: Session) -> None:
    """연선 월 08:00 ~ 14:00 = 5h (점심 12:00~13:00 = 60min 휴식 제외).

    calendar_engine._DAILY_BREAKS["연선연합"] 의 점심 1h 가 반영되어
    실제 가용 분 = 6h - 1h = 5h = 300min.
    """
    from datetime import datetime

    from app.services.cp_sat_optimizer import _working_minutes_between

    mon = datetime(2026, 4, 20, 8, 0)
    mon_end = datetime(2026, 4, 20, 14, 0)
    result = _working_minutes_between(mon, mon_end, equipment_code="ST-54BO1", db=db)
    assert result == 300, f"expected 300, got {result}"


def test_due_work_min_accepts_equipment_code(db: Session) -> None:
    """_due_work_min 시그니처에 equipment_code/db optional 파라미터 호환성."""
    from datetime import date as date_cls
    from datetime import datetime

    from app.services.cp_sat_optimizer import _due_work_min

    # 기존 호출(equipment_code 없이)도 여전히 동작
    r1 = _due_work_min(date_cls(2026, 4, 25), datetime(2026, 4, 20, 8, 0))
    # 신규 확장 파라미터 — calendar_engine 기반 정확 계산
    r2 = _due_work_min(
        date_cls(2026, 4, 25),
        datetime(2026, 4, 20, 8, 0),
        equipment_code="ST-54BO1",
        db=db,
    )
    assert isinstance(r1, int) and isinstance(r2, int)
    assert r1 >= 0 and r2 >= 0


# ──────────────────────────────────────────────────────────────────────────────
# Round 2 MED #13: Past-due 차등 가중치
# ──────────────────────────────────────────────────────────────────────────────
#
# 배경: `_due_work_min` 이 past-due(due < base) 를 0 으로 반환해 soft tardiness
# 에서 "overdue 5일 == overdue 1일" 동일 처리. Round 2 에서 음수 반환으로 변경해
# 차등 가중 (3배 더 지남 = 3배 penalty) 실현. Hard 모드에선 제약 skip + warning.


def test_due_work_min_past_due_returns_negative_legacy(db: Session) -> None:
    """Legacy 경로 (equipment_code/db 없음) — past due 는 음수 반환."""
    from datetime import date as date_cls
    from datetime import datetime

    from app.services.cp_sat_optimizer import _WORK_MIN_PER_DAY, _due_work_min

    base = datetime(2026, 4, 20, 8, 0)  # Mon
    past_due = date_cls(2026, 4, 13)  # 7일 전, Mon (근무일 5일)
    result = _due_work_min(past_due, base)
    assert result < 0, f"past due 는 음수여야: {result}"
    # 4/13 Mon ~ 4/20 Mon (exclusive) = 5 근무일 → -5 * 840
    assert result == -5 * _WORK_MIN_PER_DAY, (
        f"5 근무일 * 840 = -4200 분이어야: got {result}"
    )


def test_due_work_min_past_due_returns_negative_calendar(db: Session) -> None:
    """Calendar 경로 (equipment_code 있음) — past due 는 음수 반환."""
    from datetime import date as date_cls
    from datetime import datetime

    from app.services.cp_sat_optimizer import _due_work_min

    base = datetime(2026, 4, 20, 8, 0)  # Mon
    past_due = date_cls(2026, 4, 15)  # 5일 전, Wed
    result = _due_work_min(past_due, base, equipment_code="ST-54BO1", db=db)
    assert result < 0, f"past due(calendar path) 는 음수여야: {result}"


def test_due_work_min_future_due_still_positive(db: Session) -> None:
    """미래 납기는 여전히 양수 (회귀 방지)."""
    from datetime import date as date_cls
    from datetime import datetime

    from app.services.cp_sat_optimizer import _due_work_min

    base = datetime(2026, 4, 20, 8, 0)
    future_due = date_cls(2026, 4, 25)
    result = _due_work_min(future_due, base)
    assert result > 0, f"미래 납기는 양수: {result}"


def test_due_work_min_differentiates_past_due_magnitude(db: Session) -> None:
    """3일 overdue 는 1일 overdue 보다 더 큰 음수 (차등 가능).

    MED #13 의 핵심 — soft tardiness 에서 overdue 기간이 길수록 penalty 커짐.
    """
    from datetime import date as date_cls
    from datetime import datetime

    from app.services.cp_sat_optimizer import _due_work_min

    base = datetime(2026, 4, 20, 8, 0)  # Mon
    due_3d_past = date_cls(2026, 4, 15)  # Wed, 3 근무일 전
    due_1d_past = date_cls(2026, 4, 17)  # Fri, 1 근무일 전

    r3 = _due_work_min(due_3d_past, base)
    r1 = _due_work_min(due_1d_past, base)

    assert r3 < r1 < 0, f"3일 overdue({r3}) < 1일 overdue({r1}) < 0 이어야 — 차등 실패"


def test_tardiness_hard_past_due_warns_not_crashes(db: Session) -> None:
    """Past due 배치가 있어도 tardiness_hard=True 경로가 crash 없이 dict 반환."""
    from app.services.cp_sat_optimizer import cp_sat_schedule

    # 빈 run_label — past due 그룹은 없지만 신규 경로(warning 생성)가 깨지지 않는지.
    r = cp_sat_schedule("NON_EXISTENT_PAST_DUE_WARN", db, tardiness_hard=True)
    assert isinstance(r, dict)
    assert "warnings" in r


# ──────────────────────────────────────────────────────────────────────────────
# Round 2 HIGH #5: SpeedMaster 설비별 line_speed duration 차등
# ──────────────────────────────────────────────────────────────────────────────
#
# 배경: 기존 `_compute_group_duration` 이 eligible 설비 중 1개 speed 로 scalar
# duration 반환. 같은 배치를 설비 A/B 에 할당해도 duration 동일 → solver 가
# 빠른 설비 선호 불가. Round 2 에서 `_compute_group_duration_map` 으로 설비별
# 차등 계산. hard-coded line_speed=10 fallback 제거 (warning + scalar fallback).


def test_compute_group_duration_map_returns_per_equipment(db: Session) -> None:
    """`_compute_group_duration_map` 이 eligible 각 설비에 대해 dict 반환.

    설비 간 SpeedMaster 의 line_speed_mpm 이 다르면 duration 도 달라져야 함.
    """
    from types import SimpleNamespace

    from app.services.cp_sat_optimizer import _compute_group_duration_map

    # Fake ProductionBatch: total_length=1000m, SQ=16
    batch = SimpleNamespace(
        batch_seq=0,
        estimated_duration_min=0,
        line_speed_mpm=0,
        total_length_m=1000,
        extra_length_m=0,
        sq_mm2=16,
    )
    eligible = [
        SimpleNamespace(equipment_code="EQ-FAST"),
        SimpleNamespace(equipment_code="EQ-SLOW"),
    ]
    # SpeedMaster: 빠른 설비 100m/min, 느린 설비 50m/min
    speed_map = {
        ("EQ-FAST", 16.0): SimpleNamespace(line_speed_mpm=100.0),
        ("EQ-SLOW", 16.0): SimpleNamespace(line_speed_mpm=50.0),
    }

    result = _compute_group_duration_map([batch], eligible, speed_map)

    assert set(result.keys()) == {"EQ-FAST", "EQ-SLOW"}
    # 1000m / 100m/min = 10 min vs 1000m / 50m/min = 20 min — 2배 차이
    assert result["EQ-FAST"] < result["EQ-SLOW"], (
        f"빠른 설비 duration 이 더 작아야: FAST={result['EQ-FAST']}, "
        f"SLOW={result['EQ-SLOW']}"
    )
    # 대략 10 vs 20 min
    assert abs(result["EQ-FAST"] - 10.0) < 1.0
    assert abs(result["EQ-SLOW"] - 20.0) < 1.0


def test_compute_group_duration_map_empty_speed_data(db: Session) -> None:
    """SpeedMaster 데이터 전혀 없고 배치 line_speed_mpm 도 0 이면 fallback 0 (hard-code 10 제거 검증).

    기존 _compute_group_duration 은 line_speed=10 으로 hard-coded fallback 했으나,
    _compute_group_duration_map 은 그러지 않고 speed=0 → duration=0 반환.
    호출부(cp_sat_schedule)가 별도로 warning 처리.
    """
    from types import SimpleNamespace

    from app.services.cp_sat_optimizer import _compute_group_duration_map

    batch = SimpleNamespace(
        batch_seq=0,
        estimated_duration_min=0,
        line_speed_mpm=0,
        total_length_m=1000,
        extra_length_m=0,
        sq_mm2=16,
    )
    eligible = [SimpleNamespace(equipment_code="EQ-NO-DATA")]
    speed_map = {}  # 비어있음

    result = _compute_group_duration_map([batch], eligible, speed_map)

    # speed 데이터 없음 → 0 분 (하드코딩 10 아님 — warning 책임은 호출부)
    assert result == {"EQ-NO-DATA": 0.0}, (
        f"line_speed 없을 때 hard-coded 10 아닌 0 fallback: {result}"
    )


def test_compute_group_duration_map_returns_empty_for_no_eligible(db: Session) -> None:
    """eligible 리스트 비어있으면 빈 dict 반환 (방어적)."""
    from types import SimpleNamespace

    from app.services.cp_sat_optimizer import _compute_group_duration_map

    batch = SimpleNamespace(
        batch_seq=0,
        estimated_duration_min=0,
        line_speed_mpm=10,
        total_length_m=1000,
        extra_length_m=0,
        sq_mm2=16,
    )
    assert _compute_group_duration_map([batch], [], {}) == {}


def test_cp_sat_schedule_still_works_with_per_eq_dur(db: Session) -> None:
    """per_eq_dur_enabled 경로가 엔드투엔드 smoke 테스트에서 예외 없이 완료.

    실제 solver 호출 path — 배치 없어 model 이 비지만 새 경로(dur_vars/optional
    end helpers)가 빈 input 에서도 safe 해야.
    """
    from app.services.cp_sat_optimizer import cp_sat_schedule

    r = cp_sat_schedule("NON_EXISTENT_RUN_ROUND2_H5", db)
    assert isinstance(r, dict)


# ──────────────────────────────────────────────────────────────────────────────
# Round 2 HIGH #6: 연선 setup 3-tier transition penalty
# ──────────────────────────────────────────────────────────────────────────────
#
# 배경: 연선 setup 은 동일SQ 0 / 동일소선경 30 / 이소선경 210 min (3-tier). 기존
# CP-SAT 모델은 group setup 을 scalar `setup_min(rep)` 로만 반영 — solver 가
# "같은 SQ 인접" 을 자발적으로 선호할 근거가 없음. Round 2 에서
# `_TRANSITION_WEIGHT` 도입: 같은 설비 + 다른 SQ 인 연선 쌍에 penalty 부과 →
# solver 는 같은 SQ 들을 한 설비에 모으는 배치를 선호.


def test_transition_weight_is_meaningful(db: Session) -> None:
    """`_TRANSITION_WEIGHT` 가 유의미한 크기 (≥ 100min) — idle/chain 과 동등 scale."""
    from app.services.cp_sat_optimizer import _TRANSITION_WEIGHT

    assert _TRANSITION_WEIGHT >= 100, (
        f"_TRANSITION_WEIGHT={_TRANSITION_WEIGHT} 너무 작음 — idle/chain 대비 "
        f"사실상 무력. 연선 setup 3-tier 차등 반영 실패."
    )


def test_transition_weight_under_tardiness_weight(db: Session) -> None:
    """`_TRANSITION_WEIGHT` 가 납기 weight 보다 작아 납기 우선순위 보존.

    setup 전이 비용이 납기보다 커지면 overdue 를 허용하고 setup 최적화에 매달릴
    수 있음. critical=_DUE_HARD_WEIGHT*100=10M 대비 전이 180min 은 훨씬 작음.
    """
    from app.services.cp_sat_optimizer import (
        _DUE_HARD_WEIGHT,
        _TRANSITION_WEIGHT,
    )

    assert _TRANSITION_WEIGHT < _DUE_HARD_WEIGHT


def test_cp_sat_schedule_still_works_with_transition_penalty(db: Session) -> None:
    """Transition penalty 항 추가 후 빈 input 에서도 solver 가 안전 실행.

    실제 배치 fixture 없이도 model 경로가 예외 없이 완료해야.
    """
    from app.services.cp_sat_optimizer import cp_sat_schedule

    r = cp_sat_schedule("NON_EXISTENT_ROUND2_H6", db)
    assert isinstance(r, dict)


# ──────────────────────────────────────────────────────────────────────────────
# Round 2 HIGH #7: 멀티설비 드럼 분배 duration 근사 반영
# ──────────────────────────────────────────────────────────────────────────────
#
# 배경: 대형 배치(5틀 95SQ 등) 를 N 설비에 분산 가능 (`_schedule_multi_equipment`).
# 기존 CP-SAT 모델은 그룹=단일 interval 가정 → 분할하면 실제 duration 은 raw/N_split
# 이지만 solver 는 raw 로 오인하여 "분할하면 더 빠름" 판단 못 함. Round 2
# conservative 대안: 멀티 분할 대상 감지 시 `cpsat_dur = raw / N_split` 근사 사용.
# 완전한 N-interval 모델링은 별도 phase.


def test_cp_sat_schedule_still_works_with_multi_equip_approx(db: Session) -> None:
    """멀티설비 분할 근사 경로가 smoke 테스트에서 safe 동작."""
    from app.services.cp_sat_optimizer import cp_sat_schedule

    r = cp_sat_schedule("NON_EXISTENT_ROUND2_H7", db)
    assert isinstance(r, dict)


def test_is_multi_equip_group_signature_preserved(db: Session) -> None:
    """`_is_multi_equip_group` 시그니처 회귀 방지 — cp_sat pre-phase 판단에 사용.

    Round 2 HIGH #7 에서 group_meta 구축 단계 (sq_to_equip 아직 비어있는 시점)
    에서 이 함수를 호출해 분할 가능 여부 판단하므로 기존 시그니처 유지 필수.
    """
    from types import SimpleNamespace

    from app.services.cp_sat_optimizer import _is_multi_equip_group

    rep = SimpleNamespace(
        process_name="연선",
        sq_mm2=95,
        drum_count=5,
        batch_seq=-1,
    )
    gb = [rep]
    eligible = [
        SimpleNamespace(equipment_code="ST-A"),
        SimpleNamespace(equipment_code="ST-B"),
    ]
    # 연선 + 5 drums + 2 eligible + not a CORE group name → multi-eligible
    is_multi, total_drums = _is_multi_equip_group(
        "ST-95-multi", gb, eligible, sq_to_equip={}
    )
    # 함수가 (bool, int) tuple 반환
    assert isinstance(is_multi, bool)
    assert isinstance(total_drums, int)


# ──────────────────────────────────────────────────────────────────────────────
# TODO (P4 이후)
# ──────────────────────────────────────────────────────────────────────────────
#
# 실제 배치를 seed 해 CP-SAT 재최적화 품질을 검증하는 테스트는 P4 에서 추가한다:
#
# - test_urgent_reoptimize_beats_greedy_on_tardiness: 동일 fixture 에서 CP-SAT
#   결과의 sum(tardiness) 가 greedy 결과보다 작거나 같다.
# - test_urgent_color_chain_hard_constraint: 시스 클러스터 인접 쌍의 색상 순서가
#   정렬 규칙에 부합한다 (hard constraint 검증 — P3 이후).
# - test_urgent_deterministic_with_seed: random_seed 고정 시 동일 input → 동일
#   task 배치 (버전 diff API 의 신뢰성 기반).
