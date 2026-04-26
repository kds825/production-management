"""겹침 자동 재최적화 + SchedulerOverlapError 테스트."""

import pytest

from sqlalchemy.orm import Session

from app.exceptions import SchedulerOverlapError
from app.infrastructure.models.production_batch import ProductionBatch


def _seed_minimal(db: Session, run_label: str):
    """스케줄러가 구동할 최소 배치 1건."""
    db.add(
        ProductionBatch(
            run_label=run_label,
            batch_seq=0,
            process_name="저압절연",
            sq_mm2=50,
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            sales_order_id="SO-RET-1",
            sales_order_line=1,
            batch_group="",
        )
    )
    db.flush()


def test_overlap_retry_returns_ok_when_no_overlap(db):
    """No overlap → result has overlap_alert=False."""
    from app.application.scheduling.greedy.auto_schedule import auto_schedule
    _seed_minimal(db, run_label="test-retry-ok")
    result = auto_schedule(run_label="test-retry-ok", db=db)
    assert result.get("overlap_alert") is False


def test_overlap_persist_raises(db, monkeypatch):
    """2 retries 후에도 겹침 지속 → SchedulerOverlapError."""
    from app.services import schedule_optimizer

    # _run_optimization_once 를 항상 overlap 이 생기는 결과로 mock
    def _always_overlap(run_label, db, **kwargs):
        return {"warnings": [], "tasks_created": []}

    monkeypatch.setattr(schedule_optimizer, "_run_optimization_once", _always_overlap)

    # validate_all 이 overlap 을 보고하도록 mock
    from app.application.validation import constraint_checker

    def _fake_validate(run_label, db):
        return [{"constraint_id": "overlap", "detail": "mock overlap"}]

    # Phase 1 개선: 재시도 루프가 validate_overlap_only 를 호출하므로 이쪽을 monkeypatch.
    # 기존 validate_all 은 최종 전체 검증용으로만 남아있음 (run_stage2 경로).
    monkeypatch.setattr(constraint_checker, "validate_overlap_only", _fake_validate)

    _seed_minimal(db, run_label="test-retry-persist")

    with pytest.raises(SchedulerOverlapError) as exc_info:
        schedule_optimizer.auto_schedule(run_label="test-retry-persist", db=db)
    assert exc_info.value.attempts == 3  # 초기 + 2회 재시도 = 3 전체 시도
    assert len(exc_info.value.violations) >= 1


def test_overlap_retry_succeeds_on_second_attempt(db, monkeypatch):
    """첫 시도 overlap, 두번째 성공 → 예외 없이 반환."""
    from app.services import schedule_optimizer
    from app.application.validation import constraint_checker

    call_count = {"validate": 0, "run": 0}

    def _sometimes_overlap(run_label, db):
        call_count["validate"] += 1
        if call_count["validate"] == 1:
            return [{"constraint_id": "overlap", "detail": "first"}]
        return []

    def _fake_run(run_label, db, **kwargs):
        call_count["run"] += 1
        return {"warnings": [], "tasks_created": []}

    monkeypatch.setattr(schedule_optimizer, "_run_optimization_once", _fake_run)
    # 재시도 경로는 validate_overlap_only 를 호출하도록 Phase 1 에서 변경됨.
    monkeypatch.setattr(constraint_checker, "validate_overlap_only", _sometimes_overlap)

    _seed_minimal(db, run_label="test-retry-success")
    result = schedule_optimizer.auto_schedule(run_label="test-retry-success", db=db)
    assert result.get("overlap_alert") is False
    assert call_count["run"] == 2  # 1회 실패 + 1회 성공


def test_retry_real_run_resets_batch_status_and_audit(db, monkeypatch):
    """Real integration: 실제 _run_optimization_once 경로에서 재시도 시
    배치 상태가 'planned' 로 복원되고 audit_log FK 참조가 정리되는지 검증.

    BUG 1 (silent empty schedule) + BUG 2 (audit_log FK violation) 방지용.
    기존 테스트는 _run_optimization_once 자체를 monkey-patch 해서
    실제 경로를 커버하지 못했음.
    """
    from app.services import schedule_optimizer
    from app.application.validation import constraint_checker
    from app.infrastructure.models.schedule_task import ScheduleTask
    from app.infrastructure.models.audit_log import AuditLog

    run_label = "test-retry-real"
    _seed_minimal(db, run_label=run_label)

    # 첫 호출만 overlap 보고, 두번째부터는 실제 검증 경로.
    # Phase 1 개선: 재시도 루프가 validate_overlap_only 를 호출하므로 이쪽을 patch.
    call_count = {"validate": 0}
    real_overlap_only = constraint_checker.validate_overlap_only

    def _flaky_validate(rlabel, dbs):
        call_count["validate"] += 1
        if call_count["validate"] == 1:
            return [{"constraint_id": "overlap", "detail": "mock", "task_id": -1}]
        return real_overlap_only(rlabel, dbs)

    monkeypatch.setattr(constraint_checker, "validate_overlap_only", _flaky_validate)

    # _run_optimization_once 는 monkey-patch 하지 않음 — 실제 경로 실행
    result = schedule_optimizer.auto_schedule(run_label=run_label, db=db)

    # 재시도 성공 → overlap_alert=False
    assert result.get("overlap_alert") is False, (
        f"Retry should succeed on real path: {result}"
    )

    # 재시도 2회차가 실제로 태스크를 생성했는지 — 빈 스케줄이면 BUG 1 재발
    tasks = db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    assert len(tasks) >= 1, (
        "Retry produced empty schedule (silent failure) — BUG 1 still present."
    )

    # FK 무결성: 삭제된 task_id 를 참조하는 orphan audit row 가 없어야 함
    audit_task_ids = {
        a.task_id
        for a in db.query(AuditLog)
        .filter(AuditLog.run_label == run_label, AuditLog.task_id.isnot(None))
        .all()
        if a.task_id
    }
    current_task_ids = {t.task_id for t in tasks}
    orphans = audit_task_ids - current_task_ids
    assert not orphans, (
        f"Orphan audit_log rows reference deleted tasks (BUG 2): {orphans}"
    )


def test_cpsat_path_also_retries_on_overlap(db, monkeypatch):
    """CP-SAT 경로(use_cpsat=True)도 overlap 감지 시 재시도.

    Fix P0-4A: 기존에는 plan_pipeline 이 cp_sat_schedule 을 직접 호출하여
    retry+validate 래퍼를 우회했음. auto_schedule(use_cpsat=True) 로 통합된
    이후 CP-SAT 경로도 greedy 와 동일한 안전망(validate → overlap 감지 →
    random_seed 변동 재시도)을 공유하는지 확인한다.
    """
    from app.services import schedule_optimizer
    from app.application.validation import constraint_checker
    from app.infrastructure.models.production_batch import ProductionBatch

    db.add(
        ProductionBatch(
            run_label="test-cpsat-retry",
            batch_seq=0,
            process_name="저압절연",
            sq_mm2=50,
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            sales_order_id="SO-CP-RETRY",
            sales_order_line=1,
            batch_group="",
            status="planned",
        )
    )
    db.flush()

    calls = {"validate": 0}
    # Phase 1 개선 반영: 재시도 루프가 validate_overlap_only 만 호출.
    real_overlap_only = constraint_checker.validate_overlap_only

    def _flaky(rlabel, dbs):
        calls["validate"] += 1
        if calls["validate"] == 1:
            return [{"constraint_id": "overlap", "detail": "mock", "task_id": -1}]
        return real_overlap_only(rlabel, dbs)

    monkeypatch.setattr(constraint_checker, "validate_overlap_only", _flaky)
    result = schedule_optimizer.auto_schedule(
        run_label="test-cpsat-retry", db=db, use_cpsat=True
    )
    assert result.get("overlap_alert") is False
    assert calls["validate"] >= 2  # 최소 1회 겹침 감지 + 1회 재검증
