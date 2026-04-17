"""파이프라인 동기화 공식 테스트 — 연선→절연 유휴 최소 역산."""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


def test_insulation_end_aligns_with_stranding_end(db: Session):
    """절연 선속이 연선의 2배라도 절연 끝이 연선 끝보다 빠르면 안 됨.
    역산 공식 적용 후: 절연 끝이 연선 끝에 정렬 (±1시간 이내)."""
    from app.services.schedule_optimizer import auto_schedule

    _seed_stranding_then_insulation(
        db,
        run_label="test-pipeline-1",
        stranding_total_min=600,
        insulation_total_min=300,
    )
    auto_schedule(run_label="test-pipeline-1", db=db)

    stranding = _get_task_by_process(db, "test-pipeline-1", "연선")
    insulation = _get_task_by_process(db, "test-pipeline-1", "저압절연")
    assert stranding is not None, "연선 task not created"
    assert insulation is not None, "저압절연 task not created"

    # 불변식: 절연 끝 ≥ 연선 끝
    assert insulation.end_datetime >= stranding.end_datetime, (
        f"절연 끝 {insulation.end_datetime} < 연선 끝 {stranding.end_datetime}"
    )
    # 유휴 최소: 끝 차이 ≤ 1시간 (캘린더 시간 올림 허용)
    gap_min = (insulation.end_datetime - stranding.end_datetime).total_seconds() / 60
    assert gap_min <= 60, f"끝 정렬 어긋남 {gap_min}분"


def test_insulation_start_not_before_first_drum(db: Session):
    """절연 시작 ≥ 연선 첫 드럼 완료 시각."""
    from app.services.schedule_optimizer import auto_schedule

    _seed_stranding_multi_drum(
        db,
        run_label="test-pipeline-2",
        drums=3,
        stranding_per_drum_min=200,
        insulation_total_min=100,
    )
    auto_schedule(run_label="test-pipeline-2", db=db)

    stranding = _get_task_by_process(db, "test-pipeline-2", "연선")
    insulation = _get_task_by_process(db, "test-pipeline-2", "저압절연")
    assert stranding and insulation

    stranding_span = (
        stranding.end_datetime - stranding.start_datetime
    ).total_seconds() / 60
    first_drum_end = stranding.start_datetime + timedelta(minutes=stranding_span / 3)
    # 절연 시작이 첫 드럼 완료 이후여야 함 (±1분 허용)
    delta = (insulation.start_datetime - first_drum_end).total_seconds()
    assert delta >= -60, f"절연이 첫 드럼 완료 전에 시작: 차이 {delta}초"


def test_cp_sat_pipeline_end_constraint(db):
    """CP-SAT solver: pred_end <= succ_end 하드 제약."""
    from app.services.cp_sat_optimizer import cp_sat_schedule

    _seed_stranding_then_insulation(
        db, run_label="test-cpsat-1", stranding_total_min=600, insulation_total_min=300
    )
    result = cp_sat_schedule(run_label="test-cpsat-1", db=db)
    assert result.get("solver_status") in ("OPTIMAL", "FEASIBLE"), (
        f"Solver failed: {result.get('solver_status')}"
    )

    stranding = _get_task_by_process(db, "test-cpsat-1", "연선")
    insulation = _get_task_by_process(db, "test-cpsat-1", "저압절연")
    assert stranding and insulation
    assert insulation.end_datetime >= stranding.end_datetime, (
        f"CP-SAT: 절연 끝 {insulation.end_datetime} < 연선 끝 {stranding.end_datetime}"
    )


def test_insulation_block_width_unchanged(db: Session):
    """블록 width 는 선속 기반 고정 — 역산 공식 적용해도 width 늘어나지 않음."""
    from app.services.schedule_optimizer import auto_schedule

    _seed_stranding_then_insulation(
        db,
        run_label="test-pipeline-3",
        stranding_total_min=600,
        insulation_total_min=300,
    )
    auto_schedule(run_label="test-pipeline-3", db=db)
    insulation = _get_task_by_process(db, "test-pipeline-3", "저압절연")
    assert insulation

    actual_dur = (
        insulation.end_datetime - insulation.start_datetime
    ).total_seconds() / 60
    # 최대 허용: 시간 단위 올림 + 캘린더 휴식 스킵으로 약간 변동 가능. 2배 초과는 회귀.
    assert actual_dur <= 600, f"블록이 과하게 커짐: {actual_dur}분 (기대 ≤ 600)"


# ---- 헬퍼 ----


def _seed_stranding_then_insulation(
    db, run_label, stranding_total_min, insulation_total_min
):
    """단일 드럼 연선 + 절연 배치 시드. 필수 not-null 컬럼 채움.

    실제 SpeedMaster 값:
      - ST-T6B0 + SQ=50: 13.3 m/min  (연선)
      - EX-B100 + SQ=50: 55 m/min    (저압절연)
    의도: 절연 선속 >> 연선 선속 → 절연 블록 width가 훨씬 작음.
    역산 공식이 없으면 절연이 연선보다 훨씬 먼저 끝난다.
    """
    # 길이를 min 단위에 맞춰 부여 (SpeedMaster 조회값과 무관한 상대 비율로 충분).
    # stranding_total_min*10 = 6000m → ST-T6B0에서 6000/13.3 ≈ 451min
    # insulation_total_min*20 = 6000m → EX-B100에서 6000/55 ≈ 109min
    # 연선 duration >> 절연 duration 이 보장되어 버그 시나리오 재현 가능.
    batches = [
        ProductionBatch(
            run_label=run_label,
            batch_seq=0,
            process_name="연선",
            sq_mm2=50,
            drum_count=1,
            drum_length_m=stranding_total_min * 10,
            total_length_m=stranding_total_min * 10,
            sales_order_id="SO-TEST-1",
            sales_order_line=1,
            batch_group="",
            status="planned",
            conductor_material="CU",
        ),
        ProductionBatch(
            run_label=run_label,
            batch_seq=0,
            process_name="저압절연",
            sq_mm2=50,
            drum_count=1,
            drum_length_m=insulation_total_min * 20,
            total_length_m=insulation_total_min * 20,
            sales_order_id="SO-TEST-1",
            sales_order_line=1,
            batch_group="",
            status="planned",
            conductor_material="CU",
        ),
    ]
    db.add_all(batches)
    db.flush()


def _seed_stranding_multi_drum(
    db, run_label, drums, stranding_per_drum_min, insulation_total_min
):
    header = ProductionBatch(
        run_label=run_label,
        batch_seq=-1,
        process_name="연선",
        sq_mm2=50,
        drum_count=drums,
        drum_length_m=stranding_per_drum_min * 10,
        total_length_m=stranding_per_drum_min * drums * 10,
        sales_order_id="SO-TEST-2",
        sales_order_line=1,
        batch_group="",
        status="planned",
        conductor_material="CU",
    )
    insul = ProductionBatch(
        run_label=run_label,
        batch_seq=0,
        process_name="저압절연",
        sq_mm2=50,
        drum_count=1,
        drum_length_m=insulation_total_min * 20,
        total_length_m=insulation_total_min * 20,
        sales_order_id="SO-TEST-2",
        sales_order_line=1,
        batch_group="",
        status="planned",
        conductor_material="CU",
    )
    db.add_all([header, insul])
    db.flush()


def _get_task_by_process(db, run_label, process_name):
    return (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(
            ScheduleTask.run_label == run_label,
            ProductionBatch.process_name == process_name,
        )
        .first()
    )
