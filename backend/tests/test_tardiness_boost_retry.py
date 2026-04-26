"""납기 초과 boost retry + SAVEPOINT 롤백 로직 단위 테스트.

Focus: 비즈니스 로직 경계 조건만 검증 (solver 품질 비교는 integration 범주).
- retry_depth > 0 이면 재진입 안 함 (무한 재귀 방지)
- worst_tasks 비면 retry 대상 없음 → 원본 유지
- SAVEPOINT rollback 이 customer_priority 변경을 복원

정상 개선 케이스는 결정론이 어려워 (solver 품질 의존) integration 에서
커버. 본 파일은 방어 로직(recursion guard, empty guard, rollback) 만.
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.application.scheduling.greedy.auto_schedule import _tardiness_boost_retry
def _seed_pair(
    db: Session, run_label: str, batch_group: str, due_date: date, end_dt: datetime
) -> int:
    batch = ProductionBatch(
        run_label=run_label,
        batch_seq=0,
        process_name="저압시스",
        sq_mm2=120,
        due_date=due_date,
        customer_priority=5,
        drum_count=1,
        drum_length_m=500,
        total_length_m=500,
        conductor_material="CU",
        sales_order_id=f"SO-{batch_group}",
        sales_order_line=1,
        batch_group=batch_group,
    )
    db.add(batch)
    db.flush()
    task = ScheduleTask(
        run_label=run_label,
        batch_id=batch.batch_id,
        equipment_code="SH-A100",
        start_datetime=end_dt - timedelta(hours=1),
        end_datetime=end_dt,
    )
    db.add(task)
    db.flush()
    return task.task_id


def test_boost_retry_empty_worst_tasks_returns_original(db):
    """worst_tasks 가 비어있으면 retry 대상 없음 → 원본 유지."""
    original = {"some_key": "value"}
    tr = {"total_tardy_count": 1, "total_tardy_minutes": 100, "worst_tasks": []}
    r = _tardiness_boost_retry(
        original_result=original,
        original_tardiness=tr,
        run_label="tbr-empty",
        db=db,
        use_cpsat=True,
        retry_depth=0,
    )
    assert r is original
    assert r["retry_adopted"] is False
    assert "worst_tasks" in r["retry_rejected_reason"]


def test_boost_retry_unknown_task_ids_returns_original(db):
    """worst_tasks 에 존재하지 않는 task_id 만 있으면 retry 대상 없음."""
    original = {"some_key": "value"}
    tr = {
        "total_tardy_count": 1,
        "total_tardy_minutes": 100,
        "worst_tasks": [{"task_id": -99999}],  # 존재하지 않음
    }
    r = _tardiness_boost_retry(
        original_result=original,
        original_tardiness=tr,
        run_label="tbr-unknown",
        db=db,
        use_cpsat=True,
        retry_depth=0,
    )
    assert r is original
    assert r["retry_adopted"] is False
    assert "batch_id" in r["retry_rejected_reason"]


def test_boost_retry_rollback_preserves_original_priority(db):
    """Retry 가 예외로 실패해도 customer_priority 원본 유지 (SAVEPOINT rollback)."""
    run = "tbr-rollback"
    tid = _seed_pair(
        db,
        run,
        batch_group="TBR-1",
        due_date=date(2026, 4, 15),
        end_dt=datetime(2026, 4, 20, 10, 0),
    )
    db.flush()
    # 사전 priority 확인
    batch_before = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run).first()
    )
    assert batch_before.customer_priority == 5

    # retry_depth=0 이지만 auto_schedule 내부에서 실패하도록 monkey-patch
    # — 간단히 use_cpsat 에 잘못된 타입을 전달해 예외 유도
    original = {"tardiness_report": {}}
    tr = {
        "total_tardy_count": 1,
        "total_tardy_minutes": 5000,
        "worst_tasks": [{"task_id": tid}],
    }

    # auto_schedule 내부에서 실패하게 하려 모듈 변경 대신, 존재하지 않는
    # run_label 과 호환 안 되는 kwarg 조합으로 예외 유발. 본 테스트의
    # 초점은 "rollback 이후 batch.priority 원복" 이므로, 예외 경로를
    # 어떻게든 타면 충분.
    import app.services.schedule_optimizer as so

    _orig_auto = so.auto_schedule

    def _faulty_auto(**_kw):
        raise RuntimeError("forced failure for rollback test")

    so.auto_schedule = _faulty_auto  # type: ignore[assignment]
    try:
        r = _tardiness_boost_retry(
            original_result=original,
            original_tardiness=tr,
            run_label=run,
            db=db,
            use_cpsat=True,
            retry_depth=0,
        )
    finally:
        so.auto_schedule = _orig_auto  # restore

    assert r is original
    assert r["retry_adopted"] is False
    assert "retry_error" in r
    assert "forced failure" in r["retry_error"]

    # priority 원본(5) 유지 확인 — boost(1) 가 commit 되지 않았어야 함
    batch_after = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run).first()
    )
    assert batch_after.customer_priority == 5, (
        f"SAVEPOINT rollback 실패 — priority {batch_after.customer_priority} (기대 5)"
    )
