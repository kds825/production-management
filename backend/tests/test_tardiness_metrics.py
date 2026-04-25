"""tardiness_metrics.count_tardiness 단위 테스트.

- Empty run_label → zero 집계
- On-time 만 → zero 집계
- Past-due 혼합 → 정확 집계 (total/priority/process/worst)
"""

from datetime import date, datetime, timedelta


from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.domain.tardiness import count_tardiness


def _seed_batch_and_task(
    db,
    run_label: str,
    process_name: str,
    due_date: date,
    end_datetime: datetime,
    equipment_code: str = "SH-A100",
    customer_priority: int | None = None,
    batch_group: str = "TEST_BG",
    sq_mm2: int = 120,
) -> None:
    """배치와 스케줄 태스크 한 쌍을 시드."""
    batch = ProductionBatch(
        run_label=run_label,
        batch_seq=0,
        process_name=process_name,
        sq_mm2=sq_mm2,
        due_date=due_date,
        customer_priority=customer_priority,
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
        equipment_code=equipment_code,
        start_datetime=end_datetime - timedelta(hours=1),
        end_datetime=end_datetime,
    )
    db.add(task)
    db.flush()


def test_count_tardiness_empty_run_label(db):
    """빈 run_label 또는 None → zero 집계 반환."""
    r = count_tardiness("", db)
    assert r["total_tardy_count"] == 0
    assert r["total_tardy_minutes"] == 0
    assert r["worst_tasks"] == []

    r2 = count_tardiness(None, db)  # type: ignore[arg-type]
    assert r2["total_tardy_count"] == 0


def test_count_tardiness_all_on_time(db):
    """모든 task 가 납기 이내 → zero 집계."""
    run = "tm-ontime"
    _seed_batch_and_task(
        db,
        run,
        "저압시스",
        due_date=date(2026, 4, 20),
        end_datetime=datetime(2026, 4, 19, 15, 0),
        batch_group="BG-1",
    )
    _seed_batch_and_task(
        db,
        run,
        "저압절연",
        due_date=date(2026, 4, 25),
        end_datetime=datetime(2026, 4, 25, 23, 0),  # 당일 마감 직전
        batch_group="BG-2",
    )
    db.flush()
    r = count_tardiness(run, db)
    assert r["total_tardy_count"] == 0
    assert r["total_tardy_minutes"] == 0


def test_count_tardiness_mixed_counts_only_tardy(db):
    """on-time 1 + 초과 2 → count=2, minutes 합 정확, worst 내림차순."""
    run = "tm-mixed"
    # on-time
    _seed_batch_and_task(
        db,
        run,
        "저압시스",
        due_date=date(2026, 4, 20),
        end_datetime=datetime(2026, 4, 20, 10, 0),
        batch_group="ON-1",
    )
    # 초과 1: 납기 4/17, 종료 4/20 10:00 → 약 2일+ 초과
    _seed_batch_and_task(
        db,
        run,
        "저압시스",
        due_date=date(2026, 4, 17),
        end_datetime=datetime(2026, 4, 20, 10, 0),
        batch_group="LATE-1",
        customer_priority=5,  # urgent
    )
    # 초과 2: 납기 4/15, 종료 4/20 10:00 → 약 5일+ 초과
    _seed_batch_and_task(
        db,
        run,
        "저압절연",
        due_date=date(2026, 4, 15),
        end_datetime=datetime(2026, 4, 20, 10, 0),
        batch_group="LATE-2",
        customer_priority=1,  # critical
    )
    db.flush()

    r = count_tardiness(run, db)
    assert r["total_tardy_count"] == 2
    assert r["total_tardy_minutes"] > 0
    assert r["max_tardy_days"] > 4.0  # 4/15 → 4/20 종료는 약 5일

    # priority 분포
    assert r["per_priority"]["critical"]["count"] == 1
    assert r["per_priority"]["urgent"]["count"] == 1
    assert r["per_priority"]["normal"]["count"] == 0

    # process 분포
    assert "저압시스" in r["per_process"]
    assert "저압절연" in r["per_process"]
    assert r["per_process"]["저압시스"]["count"] == 1
    assert r["per_process"]["저압절연"]["count"] == 1

    # worst_tasks 내림차순 (4/15 납기 → 5+일 vs 4/17 → 3+일)
    assert len(r["worst_tasks"]) == 2
    assert r["worst_tasks"][0]["tardy_days"] >= r["worst_tasks"][1]["tardy_days"]
    assert r["worst_tasks"][0]["batch_group"] == "LATE-2"  # 4/15 납기가 제일 심함


def test_count_tardiness_skips_none_due(db):
    """due_date 가 None 인 task 는 집계 제외 (끝난 task 여도 판정 불가).

    end_datetime NULL 은 DB schema 상 불가이므로 WHERE 절에서 이미 필터됨 —
    여기서는 due None 경우만 검증.
    """
    run = "tm-nulls"
    batch_null_due = ProductionBatch(
        run_label=run,
        batch_seq=0,
        process_name="저압시스",
        sq_mm2=120,
        due_date=None,  # no due
        drum_count=1,
        drum_length_m=500,
        total_length_m=500,
        conductor_material="CU",
        sales_order_id="SO-NULL-DUE",
        sales_order_line=1,
        batch_group="NULL-DUE",
    )
    db.add(batch_null_due)
    db.flush()
    db.add(
        ScheduleTask(
            run_label=run,
            batch_id=batch_null_due.batch_id,
            equipment_code="SH-A100",
            start_datetime=datetime(2026, 4, 20, 8, 0),
            end_datetime=datetime(2026, 4, 20, 10, 0),
        )
    )
    db.flush()

    r = count_tardiness(run, db)
    assert r["total_tardy_count"] == 0
    assert r["total_tardy_minutes"] == 0
