"""auto_schedule re-invocation 회귀 가드 (Phase 6 step 8, 2026-05).

PoC live test 에서 발견된 회귀:
    동일 run_label 로 auto_schedule 을 두 번 호출하면 두 번째 호출이
    예상치 못하게 greedy_fallback 으로 빠진다.

근본 원인:
    첫 호출이 ProductionBatch.status='scheduled' 로 마킹 → _load_inputs.py:73
    의 filter ``status=='planned'`` 가 두 번째 호출에서 0 batch 반환 →
    solver_status='UNKNOWN' (or 빈 결과) → not in {OPTIMAL, FEASIBLE} →
    auto_schedule 이 greedy_fallback 으로 전환.

본 테스트는 auto_schedule 진입부의 _purge 가드가 두 번째 호출 직전에
schedule_task 를 정리하고 production_batch.status 를 'planned' 로 reset
하는지 확인한다 (frozen 배치는 보존).

monkeypatch 로 cp_sat_schedule 의 비용을 회피 — 두 번째 호출 진입 시점에
ProductionBatch.status 가 'planned' 로 reset 되어 있는지만 검증.
"""

from __future__ import annotations

import importlib
from datetime import datetime  # noqa: F401 (formatter 가 unused 로 오인 — 본문 함수에서 사용)

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


def _seed_batches(db: Session, run_label: str, n: int = 3) -> None:
    """planned 상태 배치 n 건 seed."""
    for i in range(n):
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="저압절연",
                sq_mm2=50,
                drum_count=1,
                drum_length_m=500,
                total_length_m=500,
                conductor_material="CU",
                sales_order_id=f"SO-REINV-{i}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
    db.flush()


def _simulate_first_call_persisted_state(db: Session, run_label: str) -> None:
    """첫 호출 결과가 commit 된 상태를 시뮬레이트.

    실제 cp_sat 호출 비용 없이: 모든 batch 의 status='scheduled' 로 변경 +
    schedule_task 1건씩 INSERT. 두 번째 auto_schedule 호출의 진입 가드가
    이 누적 상태를 인식하고 reset 하는지 검증.
    """
    batches = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).all()
    )
    assert batches, "seed batches missing"
    _dummy_start = datetime(2026, 5, 18, 8, 0, 0)
    _dummy_end = datetime(2026, 5, 18, 12, 0, 0)
    for b in batches:
        b.status = "scheduled"
        b.equipment_code = "EX-B100"
        db.add(
            ScheduleTask(
                batch_id=b.batch_id,
                run_label=run_label,
                equipment_code="EX-B100",
                start_datetime=_dummy_start,
                end_datetime=_dummy_end,
                status="scheduled",
            )
        )
    db.flush()


def test_reinvocation_resets_status_when_existing_tasks(db, monkeypatch):
    """첫 호출 잔존 상태에서 두 번째 진입 시 status='planned' 로 reset."""
    run_label = "test-reinvoke-reset"
    _seed_batches(db, run_label, n=3)
    _simulate_first_call_persisted_state(db, run_label)

    # 두 번째 호출 진입 _직전_ DB 상태: 모두 'scheduled', task 3건
    scheduled_before = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "scheduled",
        )
        .count()
    )
    assert scheduled_before == 3, "precondition: 모두 scheduled 상태"
    tasks_before = (
        db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).count()
    )
    assert tasks_before == 3, "precondition: schedule_task 3건 존재"

    # auto_schedule 의 attempt loop 진입 직전에서 빠르게 종료 — 가드 진입만 확인.
    schedule_optimizer = importlib.import_module(
        "app.application.scheduling.greedy.auto_schedule"
    )

    def _fast_exit(run_label, db, **kwargs):
        # _run_optimization_once 가 호출되는 시점에는 이미 진입 가드가
        # 동작했어야 한다.
        return {"total_tasks": 0, "warnings": [], "engine": "greedy"}

    monkeypatch.setattr(schedule_optimizer, "_run_optimization_once", _fast_exit)
    # overlap 검증은 통과시켜 retry 진입 금지
    from app.application.validation import constraint_checker

    monkeypatch.setattr(constraint_checker, "validate_overlap_only", lambda *a, **k: [])

    schedule_optimizer.auto_schedule(run_label=run_label, db=db, use_cpsat=False)

    # 가드가 동작해 status='planned' 로 reset 되었어야 한다.
    planned_after = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "planned",
        )
        .count()
    )
    assert planned_after == 3, (
        "재호출 진입 가드 실패 — status='planned' 로 reset 되지 않음. "
        "회귀 재발 시 두 번째 cp_sat 호출이 0 batch 로 fallback 으로 빠진다."
    )
    # schedule_task 도 정리되었어야 한다.
    tasks_after = (
        db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).count()
    )
    assert tasks_after == 0, "schedule_task 가 _purge_run_tasks 로 정리되어야 한다"


def test_reinvocation_skip_when_no_existing_tasks(db, monkeypatch):
    """첫 호출 (existing task 없음) 진입 시에는 가드가 no-op (불필요 작업 없음)."""
    run_label = "test-reinvoke-skip"
    _seed_batches(db, run_label, n=2)
    # schedule_task 없음 → 가드는 skip 되어야 함.

    schedule_optimizer = importlib.import_module(
        "app.application.scheduling.greedy.auto_schedule"
    )

    purge_calls = {"n": 0}
    orig_purge = schedule_optimizer._purge_run_tasks

    def _counting_purge(db, run_label):
        purge_calls["n"] += 1
        return orig_purge(db, run_label)

    monkeypatch.setattr(schedule_optimizer, "_purge_run_tasks", _counting_purge)
    monkeypatch.setattr(
        schedule_optimizer,
        "_run_optimization_once",
        lambda run_label, db, **kw: {"total_tasks": 0, "warnings": []},
    )
    from app.application.validation import constraint_checker

    monkeypatch.setattr(constraint_checker, "validate_overlap_only", lambda *a, **k: [])

    schedule_optimizer.auto_schedule(run_label=run_label, db=db, use_cpsat=False)

    assert purge_calls["n"] == 0, (
        "existing task 없는 첫 호출에서 _purge_run_tasks 가 호출되었다 — "
        "no-op 진입 가드가 깨졌다."
    )


def test_reinvocation_skip_with_frozen_group_keys(db, monkeypatch):
    """frozen_group_keys 명시 시 가드 skip (cascade reschedule 시나리오 보호)."""
    run_label = "test-reinvoke-frozen"
    _seed_batches(db, run_label, n=2)
    _simulate_first_call_persisted_state(db, run_label)

    schedule_optimizer = importlib.import_module(
        "app.application.scheduling.greedy.auto_schedule"
    )

    purge_calls = {"n": 0}
    orig_purge = schedule_optimizer._purge_run_tasks

    def _counting_purge(db, run_label):
        purge_calls["n"] += 1
        return orig_purge(db, run_label)

    monkeypatch.setattr(schedule_optimizer, "_purge_run_tasks", _counting_purge)
    monkeypatch.setattr(
        schedule_optimizer,
        "_run_optimization_once",
        lambda run_label, db, **kw: {"total_tasks": 0, "warnings": []},
    )
    from app.application.validation import constraint_checker

    monkeypatch.setattr(constraint_checker, "validate_overlap_only", lambda *a, **k: [])

    schedule_optimizer.auto_schedule(
        run_label=run_label,
        db=db,
        use_cpsat=False,
        frozen_group_keys={"some-group"},
    )

    assert purge_calls["n"] == 0, (
        "frozen_group_keys 가 명시되었는데 진입 가드가 _purge_run_tasks 를 호출했다 — "
        "cascade reschedule 시나리오의 frozen 보존 의도가 깨질 수 있다."
    )
