"""색상 전환 metric 테스트 (Phase 9 baseline).

Why: P9-B 가중치 재조정 후 시스 색상 전환 회수가 감소하는지 비교 기준이
필요. 현재 CP-SAT 가 갈-호-small-회-갈 같은 패턴으로 120 분짜리 색상
교체를 빈번히 발생시키고 있음 (사용자 보고). count_color_transitions 헬퍼는
설비별로 인접 시스 task 의 색상 전환을 세어 baseline 수치를 제공.

본 파일은 헬퍼의 정의역(빈 run, 단색, 교차색, 다설비 분리, 비시스 무시)을
좁게 검증한다. 실제 CP-SAT 결과의 baseline 측정은 별도 스크립트/노트북에서
이 헬퍼를 호출해 수행.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.services.sheath_cluster import count_color_transitions


# ---------------------------------------------------------------------------
# 시드 헬퍼
# ---------------------------------------------------------------------------


def _cleanup(db: Session, run_label: str) -> None:
    """이전 run 잔여물 제거 — schedule_task → production_batch 순 (FK)."""
    batch_ids = [
        bid
        for (bid,) in db.query(ProductionBatch.batch_id)
        .filter(ProductionBatch.run_label == run_label)
        .all()
    ]
    if batch_ids:
        db.query(ScheduleTask).filter(ScheduleTask.batch_id.in_(batch_ids)).delete(
            synchronize_session=False
        )
    db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).delete(
        synchronize_session=False
    )
    db.flush()


def _seed_sheath_task(
    db: Session,
    run_label: str,
    *,
    color: str | None,
    equipment_code: str,
    start: datetime,
    duration_min: int = 60,
    process_name: str = "저압시스",
    sq: int = 120,
) -> ScheduleTask:
    """시스 batch + 1 task 시드. color=None 으로 호출하면 누락 케이스 검증."""
    batch = ProductionBatch(
        run_label=run_label,
        batch_seq=0,
        process_name=process_name,
        sheath_color=color,
        sq_mm2=sq,
        drum_count=1,
        drum_length_m=500,
        total_length_m=500,
        conductor_material="CU",
        sales_order_id=f"SO-P9-{int(start.timestamp()) % 10_000_000}",
        sales_order_line=1,
        batch_group=f"{equipment_code}_{color or 'NA'}_W00",
        equipment_code=equipment_code,
    )
    db.add(batch)
    db.flush()
    task = ScheduleTask(
        batch_id=batch.batch_id,
        equipment_code=equipment_code,
        start_datetime=start,
        end_datetime=start + timedelta(minutes=duration_min),
        run_label=run_label,
        batch_group=batch.batch_group,
        status="scheduled",
    )
    db.add(task)
    db.flush()
    return task


def _seed_non_sheath_task(
    db: Session,
    run_label: str,
    *,
    equipment_code: str,
    start: datetime,
    process_name: str = "연선",
) -> ScheduleTask:
    """비시스 공정 task — count_color_transitions 가 무시해야 하는 케이스."""
    batch = ProductionBatch(
        run_label=run_label,
        batch_seq=0,
        process_name=process_name,
        sq_mm2=95,
        drum_count=1,
        drum_length_m=500,
        total_length_m=500,
        conductor_material="CU",
        sales_order_id=f"SO-P9NS-{int(start.timestamp()) % 10_000_000}",
        sales_order_line=1,
        batch_group=f"{equipment_code}_NS_W00",
        equipment_code=equipment_code,
    )
    db.add(batch)
    db.flush()
    task = ScheduleTask(
        batch_id=batch.batch_id,
        equipment_code=equipment_code,
        start_datetime=start,
        end_datetime=start + timedelta(minutes=60),
        run_label=run_label,
        batch_group=batch.batch_group,
        status="scheduled",
    )
    db.add(task)
    db.flush()
    return task


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------


def test_empty_run_returns_zero_transitions(db: Session) -> None:
    """존재하지 않는 run_label → 빈 결과."""
    result = count_color_transitions("NON_EXISTENT_P9_BASELINE_RUN", db)
    assert result["total_transitions"] == 0
    assert result["per_equipment"] == {}
    assert result["categories"]["total_changeover_min"] == 0


def test_single_color_sequence_zero_transitions(db: Session) -> None:
    """같은 색 연속 3 task → 전환 0."""
    run = "TEST_TRANS_SINGLE_COLOR"
    _cleanup(db, run)
    base = datetime(2026, 4, 20, 8, 0, 0)
    for i in range(3):
        _seed_sheath_task(
            db,
            run,
            color="갈",
            equipment_code="SH-A100",
            start=base + timedelta(hours=i * 2),
        )

    result = count_color_transitions(run, db)
    assert result["total_transitions"] == 0
    assert result["per_equipment"]["SH-A100"]["transitions"] == 0
    assert result["per_equipment"]["SH-A100"]["sequence"] == ["갈", "갈", "갈"]
    assert result["per_equipment"]["SH-A100"]["unique_colors"] == 1
    assert result["per_equipment"]["SH-A100"]["task_count"] == 3
    assert result["categories"]["total_changeover_min"] == 0


def test_alternating_colors_counts_transitions(db: Session) -> None:
    """갈 → 호 → 갈 → 전환 2 (240 분 소요)."""
    run = "TEST_TRANS_ALT"
    _cleanup(db, run)
    base = datetime(2026, 4, 20, 8, 0, 0)
    for i, color in enumerate(["갈", "호", "갈"]):
        _seed_sheath_task(
            db,
            run,
            color=color,
            equipment_code="SH-A100",
            start=base + timedelta(hours=i * 2),
        )

    result = count_color_transitions(run, db)
    assert result["total_transitions"] == 2
    assert result["per_equipment"]["SH-A100"]["transitions"] == 2
    assert result["per_equipment"]["SH-A100"]["sequence"] == ["갈", "호", "갈"]
    assert result["per_equipment"]["SH-A100"]["unique_colors"] == 2
    assert result["categories"]["total_changeover_min"] == 240


def test_same_color_on_different_equipment_no_transition(db: Session) -> None:
    """다른 설비 간 동색은 전환 아님 — 설비별 독립 sequence."""
    run = "TEST_TRANS_MULTI_EQUIP"
    _cleanup(db, run)
    base = datetime(2026, 4, 20, 8, 0, 0)
    _seed_sheath_task(db, run, color="갈", equipment_code="SH-A100", start=base)
    _seed_sheath_task(
        db,
        run,
        color="갈",
        equipment_code="SH-A120",
        start=base + timedelta(hours=1),
    )

    result = count_color_transitions(run, db)
    assert result["total_transitions"] == 0
    assert set(result["per_equipment"].keys()) == {"SH-A100", "SH-A120"}
    assert result["per_equipment"]["SH-A100"]["transitions"] == 0
    assert result["per_equipment"]["SH-A120"]["transitions"] == 0


def test_skip_non_sheath_processes(db: Session) -> None:
    """시스 아닌 공정 task 는 무시. 시스 색상 전환만 카운트."""
    run = "TEST_TRANS_SKIP_NON_SHEATH"
    _cleanup(db, run)
    base = datetime(2026, 4, 20, 8, 0, 0)
    # 연선 task 가 중간에 끼어도 무시돼야 함 (다른 공정용 설비여도, 같은
    # 설비여도 시스가 아니면 sequence 에 안 들어가야 함)
    _seed_non_sheath_task(db, run, equipment_code="ST-54BO1", start=base)
    _seed_sheath_task(
        db,
        run,
        color="갈",
        equipment_code="SH-A100",
        start=base + timedelta(hours=1),
    )
    _seed_sheath_task(
        db,
        run,
        color="호",
        equipment_code="SH-A100",
        start=base + timedelta(hours=2),
    )

    result = count_color_transitions(run, db)
    # 시스만 카운트: 갈 → 호 (전환 1)
    assert result["total_transitions"] == 1
    # 비시스 설비는 결과에 등장 X
    assert "ST-54BO1" not in result["per_equipment"]
    assert result["per_equipment"]["SH-A100"]["sequence"] == ["갈", "호"]
    assert result["categories"]["total_changeover_min"] == 120


def test_none_color_is_skipped(db: Session) -> None:
    """sheath_color 가 None 인 시스 task → sequence 에서 skip.

    Why: 색상 정보가 없는 task 를 별도 색으로 취급하면 노이즈 전환이 발생.
    spec 에 따라 'None은 별도 색으로 취급 안 함 — skip' 정책.
    """
    run = "TEST_TRANS_NONE_SKIP"
    _cleanup(db, run)
    base = datetime(2026, 4, 20, 8, 0, 0)
    _seed_sheath_task(db, run, color="갈", equipment_code="SH-A100", start=base)
    _seed_sheath_task(
        db,
        run,
        color=None,
        equipment_code="SH-A100",
        start=base + timedelta(hours=1),
    )
    _seed_sheath_task(
        db,
        run,
        color="갈",
        equipment_code="SH-A100",
        start=base + timedelta(hours=2),
    )

    result = count_color_transitions(run, db)
    # None 이 skip 되면 [갈, 갈] → 전환 0
    assert result["total_transitions"] == 0
    assert result["per_equipment"]["SH-A100"]["sequence"] == ["갈", "갈"]
