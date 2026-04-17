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
