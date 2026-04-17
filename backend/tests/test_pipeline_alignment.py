"""End-alignment helper 단위 테스트 — DB 없이 순수 함수 시나리오.

의도: calculate_start_datetime/calculate_end_datetime/_find_available_slot 은
DB·캘린더에 의존하므로 이 테스트는 *helper 동작의 논리적 계약*만 검증한다.
실제 스케줄러 경로 테스트는 test_pipeline_sync.py 참조.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock


def _mock_db():
    """calculate_start/end 가 호출되면 canonical 24h/일 캘린더처럼 동작하는 mock."""
    return MagicMock()


def test_returns_input_when_no_predecessor_end_recorded(monkeypatch):
    """process_end_by_sq 에 예상 선행공정 종료가 없으면 start/end 변경 없음."""
    from app.services import schedule_optimizer

    current_start = datetime(2026, 4, 10, 8, 0)
    current_end = datetime(2026, 4, 15, 8, 0)

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="고압시스",
        pred_proc="고압절연",
        group_sqs={300},
        process_end_by_sq={},  # 비어있음
        current_start=current_start,
        current_end=current_end,
        duration_min=5 * 24 * 60,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A150",
    )

    assert aligned_start == current_start
    assert aligned_end == current_end


def test_returns_input_when_already_aligned(monkeypatch):
    """현재 end >= pred_end_latest 이면 변경 없음 (reverse_start ≤ current_start)."""
    from app.services import schedule_optimizer

    # calculate_start_datetime(pred_end=4/15, duration=5일)=4/10 → current_start(=4/10)와 같음
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_start_datetime",
        lambda end, dur, db, eq: end - timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_end_datetime",
        lambda start, dur, db, eq: start + timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "_find_available_slot",
        lambda earliest, dur, slots, db, eq: earliest,
    )

    current_start = datetime(2026, 4, 10, 8, 0)
    current_end = datetime(2026, 4, 15, 8, 0)

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="고압시스",
        pred_proc="고압절연",
        group_sqs={300},
        process_end_by_sq={("고압절연", 300): datetime(2026, 4, 15, 8, 0)},
        current_start=current_start,
        current_end=current_end,
        duration_min=5 * 24 * 60,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A150",
    )

    assert aligned_start == current_start
    assert aligned_end == current_end


def test_delays_start_when_predecessor_ends_later(monkeypatch):
    """pred_end > current_end 이면 reverse_start = pred_end - duration, start 지연.
    불변식: aligned_end == pred_end (블록 폭 유지)."""
    from app.services import schedule_optimizer

    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_start_datetime",
        lambda end, dur, db, eq: end - timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_end_datetime",
        lambda start, dur, db, eq: start + timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "_find_available_slot",
        lambda earliest, dur, slots, db, eq: earliest,
    )

    current_start = datetime(2026, 4, 9, 8, 0)
    duration_min = 7 * 24 * 60  # 7일
    current_end = current_start + timedelta(minutes=duration_min)  # 4/16 08:00

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="고압시스",
        pred_proc="고압절연",
        group_sqs={300},
        process_end_by_sq={("고압절연", 300): datetime(2026, 4, 17, 8, 0)},
        current_start=current_start,
        current_end=current_end,
        duration_min=duration_min,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A150",
    )

    # pred_end=4/17, reverse_start = 4/17 - 7일 = 4/10 08:00
    assert aligned_start == datetime(2026, 4, 10, 8, 0)
    # 블록 폭 유지: end - start == duration_min
    assert (aligned_end - aligned_start).total_seconds() / 60 == duration_min
    # 불변식: end >= pred_end
    assert aligned_end == datetime(2026, 4, 17, 8, 0)


def test_mixed_sq_uses_max_pred_end(monkeypatch):
    """혼합 SQ 그룹(고압시스 색상별) — 모든 SQ의 pred_end 중 최대값 사용."""
    from app.services import schedule_optimizer

    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_start_datetime",
        lambda end, dur, db, eq: end - timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_end_datetime",
        lambda start, dur, db, eq: start + timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "_find_available_slot",
        lambda earliest, dur, slots, db, eq: earliest,
    )

    duration_min = 24 * 60
    current_start = datetime(2026, 4, 9, 8, 0)
    current_end = current_start + timedelta(minutes=duration_min)

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="고압시스",
        pred_proc="고압절연",
        group_sqs={240, 300},
        process_end_by_sq={
            ("고압절연", 240): datetime(2026, 4, 15, 8, 0),
            ("고압절연", 300): datetime(2026, 4, 17, 8, 0),  # 더 늦음
        },
        current_start=current_start,
        current_end=current_end,
        duration_min=duration_min,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A150",
    )

    # 최대값인 4/17 기준으로 정렬
    assert aligned_end == datetime(2026, 4, 17, 8, 0)
    assert aligned_start == datetime(2026, 4, 16, 8, 0)


def test_sheath_also_checks_assembly_end(monkeypatch):
    """시스(저압/고압)는 절연 외에 연합 종료도 선행으로 고려."""
    from app.services import schedule_optimizer

    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_start_datetime",
        lambda end, dur, db, eq: end - timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_end_datetime",
        lambda start, dur, db, eq: start + timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "_find_available_slot",
        lambda earliest, dur, slots, db, eq: earliest,
    )

    duration_min = 24 * 60
    current_start = datetime(2026, 4, 9, 8, 0)
    current_end = current_start + timedelta(minutes=duration_min)

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="저압시스",
        pred_proc="저압절연",
        group_sqs={50},
        process_end_by_sq={
            ("저압절연", 50): datetime(2026, 4, 12, 8, 0),
            ("연합", 50): datetime(2026, 4, 14, 8, 0),  # 연합이 더 늦음
        },
        current_start=current_start,
        current_end=current_end,
        duration_min=duration_min,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A100",
    )

    assert aligned_end == datetime(2026, 4, 14, 8, 0)


# ---- 통합 테스트 (실제 스케줄러 + DB) ----


def test_high_voltage_sheath_ends_after_high_voltage_insulation(db):
    """고압시스 최종 종료가 고압절연 최종 종료 이상 (멀티설비 분배 경로).

    재현: 2026-04-18 자동배열에서 고압시스(A150+B100)가 고압절연(CV#1+CV#2)
    보다 하루 먼저 끝나던 버그. _schedule_multi_equipment 에 end-alignment
    역산이 없어서 발생.
    """
    from datetime import date

    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask
    from app.services.schedule_optimizer import auto_schedule

    run_label = "test-hv-align"

    # 고압절연 2드럼 (CV#1+CV#2 분배 발동 조건: drums≥2, eligible≥2)
    # 고압시스 2드럼 (A150+B100 분배 발동)
    for i in range(2):
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="고압절연",
                sq_mm2=300,
                drum_count=1,
                drum_length_m=8000,  # 길이 크게 → 절연 duration 길어짐
                total_length_m=8000,
                conductor_material="CU",
                voltage="22.9kV",
                sales_order_id=f"SO-HV-{i + 1}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="고압시스",
                sheath_color="흑",
                sq_mm2=300,
                due_date=date(2026, 4, 30),
                drum_count=1,
                drum_length_m=2000,  # 길이 작게 → 시스 duration 짧음 (버그 조건)
                total_length_m=2000,
                conductor_material="CU",
                voltage="22.9kV",
                sales_order_id=f"SO-HV-{i + 1}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
    db.flush()

    auto_schedule(run_label=run_label, db=db)

    insul_tasks = (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(
            ScheduleTask.run_label == run_label,
            ProductionBatch.process_name == "고압절연",
        )
        .all()
    )
    sheath_tasks = (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(
            ScheduleTask.run_label == run_label,
            ProductionBatch.process_name == "고압시스",
        )
        .all()
    )
    assert insul_tasks, "고압절연 task 생성 실패 (시드 부족?)"
    assert sheath_tasks, "고압시스 task 생성 실패 (시드 부족?)"

    insul_last_end = max(t.end_datetime for t in insul_tasks)
    sheath_last_end = max(t.end_datetime for t in sheath_tasks)

    # 불변식: 고압시스 최종 종료 >= 고압절연 최종 종료
    assert sheath_last_end >= insul_last_end, (
        f"고압시스 최종 종료 {sheath_last_end} < 고압절연 최종 종료 {insul_last_end} "
        f"— end-alignment 실패 (버그 회귀)"
    )


def test_high_voltage_sheath_block_width_preserved(db):
    """멀티설비 경로 end-alignment 후에도 블록 폭(end-start)이 커지지 않음."""
    from datetime import date

    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask
    from app.services.schedule_optimizer import auto_schedule

    run_label = "test-hv-width"
    for i in range(2):
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="고압절연",
                sq_mm2=300,
                drum_count=1,
                drum_length_m=8000,
                total_length_m=8000,
                conductor_material="CU",
                voltage="22.9kV",
                sales_order_id=f"SO-W-{i + 1}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="고압시스",
                sheath_color="흑",
                sq_mm2=300,
                due_date=date(2026, 4, 30),
                drum_count=1,
                drum_length_m=2000,
                total_length_m=2000,
                conductor_material="CU",
                voltage="22.9kV",
                sales_order_id=f"SO-W-{i + 1}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
    db.flush()

    auto_schedule(run_label=run_label, db=db)

    sheath_tasks = (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(
            ScheduleTask.run_label == run_label,
            ProductionBatch.process_name == "고압시스",
        )
        .all()
    )
    assert sheath_tasks

    for t in sheath_tasks:
        width_min = (t.end_datetime - t.start_datetime).total_seconds() / 60
        # 블록 폭이 24h(= 1일) 이내 — 2000m는 시스 선속상 수 시간 내 작업. 극단적 확장 방지.
        assert width_min <= 24 * 60, (
            f"고압시스 블록 폭 {width_min}분 (> 24h). 확장 버그 회귀 의심."
        )
