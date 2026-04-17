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
    from app.services.schedule_optimizer import auto_schedule

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
    from app.services import constraint_checker

    def _fake_validate(run_label, db):
        return [{"constraint_id": "overlap", "detail": "mock overlap"}]

    monkeypatch.setattr(constraint_checker, "validate_all", _fake_validate)

    _seed_minimal(db, run_label="test-retry-persist")

    with pytest.raises(SchedulerOverlapError) as exc_info:
        schedule_optimizer.auto_schedule(run_label="test-retry-persist", db=db)
    assert exc_info.value.attempts == 3  # 초기 + 2회 재시도 = 3 전체 시도
    assert len(exc_info.value.violations) >= 1


def test_overlap_retry_succeeds_on_second_attempt(db, monkeypatch):
    """첫 시도 overlap, 두번째 성공 → 예외 없이 반환."""
    from app.services import schedule_optimizer
    from app.services import constraint_checker

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
    monkeypatch.setattr(constraint_checker, "validate_all", _sometimes_overlap)

    _seed_minimal(db, run_label="test-retry-success")
    result = schedule_optimizer.auto_schedule(run_label="test-retry-success", db=db)
    assert result.get("overlap_alert") is False
    assert call_count["run"] == 2  # 1회 실패 + 1회 성공
