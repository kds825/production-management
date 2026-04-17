"""납기 hard constraint 테스트 — 사용자 요구 '납기는 반드시 지켜져야함'.

현재(2026-04-17) 기준 미래 납기만 사용해서 '피지블'한 케이스에서
스케줄러가 실제로 납기를 지키는지 검증한다.
"""

from datetime import date


from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


def _seed_feasible_due(db, run_label: str):
    """납기 내 완료 가능한 배치 — 정상 케이스."""
    # 5/11 (월) 납기, 500m 물량 (짧음) → 여유 (시스템 기준일 2026-04-17)
    db.add(
        ProductionBatch(
            run_label=run_label,
            batch_seq=0,
            process_name="저압시스",
            sheath_color="흑",
            sq_mm2=50,
            due_date=date(2026, 5, 11),
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            sales_order_id="SO-DUE-OK",
            sales_order_line=1,
            batch_group="",
        )
    )
    db.flush()


def test_schedule_meets_due_date_when_feasible(db):
    """피지블한 경우 모든 task 의 end.date() <= batch.due_date 여야."""
    from app.services.schedule_optimizer import auto_schedule

    _seed_feasible_due(db, "test-due-1")
    auto_schedule(run_label="test-due-1", db=db)

    tasks = db.query(ScheduleTask).filter(ScheduleTask.run_label == "test-due-1").all()
    assert tasks, "스케줄 생성 실패"
    for t in tasks:
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_id == t.batch_id)
            .first()
        )
        if batch and batch.due_date:
            assert t.end_datetime.date() <= batch.due_date, (
                f"납기 위반: task end={t.end_datetime.date()} > due={batch.due_date}"
            )


def test_color_chain_does_not_violate_due_date(db):
    """장기 색상 체인이 납기 빠른 수주를 뒤로 밀지 않음.

    시나리오 (기준일 2026-04-17):
      흑 납기 5/11 (여유), 흑 납기 6/30 (먼 납기), 청 납기 5/12 (여유).
    Color-first 순수 구현이면 흑·흑·청 → 청이 흑(6/30) 뒤로 밀림 → 위반 위험이 있으나
    각 배치가 1일 미만이므로 피지블. 납기 hard 가 동작하면 모두 납기 내 완료.
    """
    from app.services.schedule_optimizer import auto_schedule

    rows = [
        ("흑", 50, date(2026, 5, 11), "SO-DUE-A"),
        ("흑", 50, date(2026, 6, 30), "SO-DUE-B"),
        ("청", 50, date(2026, 5, 12), "SO-DUE-C"),
    ]
    for color, sq, due, so in rows:
        db.add(
            ProductionBatch(
                run_label="test-due-chain",
                batch_seq=0,
                process_name="저압시스",
                sheath_color=color,
                sq_mm2=sq,
                due_date=due,
                drum_count=1,
                drum_length_m=500,
                total_length_m=500,
                conductor_material="CU",
                sales_order_id=so,
                sales_order_line=1,
                batch_group="",
            )
        )
    db.flush()

    auto_schedule(run_label="test-due-chain", db=db)
    tasks = (
        db.query(ScheduleTask).filter(ScheduleTask.run_label == "test-due-chain").all()
    )
    assert tasks

    violations = []
    for t in tasks:
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_id == t.batch_id)
            .first()
        )
        if batch and batch.due_date and t.end_datetime.date() > batch.due_date:
            violations.append(
                (batch.sales_order_id, batch.due_date, t.end_datetime.date())
            )

    assert not violations, f"납기 위반 {len(violations)}건: {violations}"


def test_delivery_violation_reported_as_error(db):
    """피지블하지 않은 납기는 constraint_checker 가 'error' severity 로 보고.

    사용자 요구: "납기는 반드시 맞춰야하는거야" — warning 이 아닌 error 로 격상.
    """
    from app.services.schedule_optimizer import auto_schedule
    from app.services.constraint_checker import validate_all

    # 이미 지난 납기 → 강제 위반. 스케줄러가 어떻게든 배치하고 검증기가 error 반환해야.
    db.add(
        ProductionBatch(
            run_label="test-due-past",
            batch_seq=0,
            process_name="저압시스",
            sheath_color="흑",
            sq_mm2=50,
            due_date=date(2026, 1, 1),  # 과거 납기 — 반드시 위반됨
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            sales_order_id="SO-PAST",
            sales_order_line=1,
            batch_group="",
        )
    )
    db.flush()

    auto_schedule(run_label="test-due-past", db=db)
    violations = validate_all(run_label="test-due-past", db=db)

    delivery_errors = [
        v
        for v in violations
        if v.get("constraint_id") == "1-1"
        and v.get("severity") == "error"
        and "납기" in v.get("detail", "")
    ]
    assert delivery_errors, (
        f"과거 납기 케이스에서 severity=error 인 납기 위반이 반드시 보고되어야 함. "
        f"전체 위반: {violations}"
    )
