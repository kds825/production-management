"""Unit tests for `app.application.cascade.snap`.

Snap 은 스케줄 스냅샷(in-memory)을 비즈니스 로직에서 변경/조회하기 위한 pure helper.
Phase 1 의 cascade 제안 파이프라인 (BFS/Pull/Validators) 이 공유하는 자료구조이므로,
apply 재진입 시 최초 원본(old_*) 보존, equipment 조회 시 start 오름차순 정렬 등
계약을 테스트로 고정한다.
"""

from datetime import datetime

import pytest

from app.application.cascade.snap import Snap, SnapTask


def _mk(task_id, eq, start_h, end_h, sol=1):
    """헬퍼: 고정 날짜(2026-04-20) 위에 시간만 다른 SnapTask 를 생성."""
    return SnapTask(
        task_id,
        eq,
        datetime(2026, 4, 20, start_h, 0),
        datetime(2026, 4, 20, end_h, 0),
        batch_id=f"B-{task_id}",
        sales_order_id="SO-1",
        sales_order_line=sol,
        due_date=datetime(2026, 4, 30),
    )


def test_apply_records_old_values():
    t = _mk("T1", "A", 9, 12)
    snap = Snap(by_id={"T1": t})
    snap.apply("T1", datetime(2026, 4, 20, 10, 0), datetime(2026, 4, 20, 13, 0))
    assert snap.get("T1").start == datetime(2026, 4, 20, 10, 0)
    assert snap.get("T1").end == datetime(2026, 4, 20, 13, 0)
    assert snap.get("T1").old_start == datetime(2026, 4, 20, 9, 0)
    assert snap.get("T1").old_end == datetime(2026, 4, 20, 12, 0)


def test_apply_second_call_preserves_original_old_values():
    """apply 2회 호출 시 old_* 는 첫 apply 이전 값으로 고정 (최초 원본 보존)."""
    t = _mk("T1", "A", 9, 12)
    snap = Snap(by_id={"T1": t})
    snap.apply("T1", datetime(2026, 4, 20, 10, 0), datetime(2026, 4, 20, 13, 0))
    snap.apply("T1", datetime(2026, 4, 20, 11, 0), datetime(2026, 4, 20, 14, 0))
    assert snap.get("T1").old_start == datetime(2026, 4, 20, 9, 0)  # 최초 원본
    assert snap.get("T1").old_end == datetime(2026, 4, 20, 12, 0)
    assert snap.get("T1").start == datetime(2026, 4, 20, 11, 0)  # 마지막 apply 반영
    assert snap.get("T1").end == datetime(2026, 4, 20, 14, 0)


def test_by_equipment_sorted():
    snap = Snap(
        by_id={
            "A": _mk("A", "EQ1", 14, 16),
            "B": _mk("B", "EQ1", 9, 12),
            "C": _mk("C", "EQ2", 8, 10),
        }
    )
    result = snap.by_equipment("EQ1")
    assert [t.task_id for t in result] == ["B", "A"]


def test_by_equipment_empty_when_no_match():
    snap = Snap(by_id={"T": _mk("T", "EQ1", 9, 12)})
    assert snap.by_equipment("NONEXISTENT") == []


def test_apply_changes_equipment():
    snap = Snap(by_id={"T1": _mk("T1", "A", 9, 12)})
    snap.apply(
        "T1",
        datetime(2026, 4, 20, 9, 0),
        datetime(2026, 4, 20, 12, 0),
        new_equipment_code="B",
    )
    assert snap.get("T1").equipment_code == "B"
    assert snap.get("T1").old_equipment_code == "A"
    assert snap.by_equipment("A") == []
    assert len(snap.by_equipment("B")) == 1


def test_changed_tasks_returns_only_modified():
    a = _mk("A", "EQ", 9, 12)
    b = _mk("B", "EQ", 14, 18)
    snap = Snap(by_id={"A": a, "B": b})
    snap.apply("A", datetime(2026, 4, 20, 10, 0), datetime(2026, 4, 20, 13, 0))
    changed = snap.changed_tasks()
    assert [t.task_id for t in changed] == ["A"]


def test_apply_raises_on_no_op():
    t = _mk("T1", "A", 9, 12)
    snap = Snap(by_id={"T1": t})
    with pytest.raises(ValueError, match="no-op change"):
        snap.apply("T1", datetime(2026, 4, 20, 9, 0), datetime(2026, 4, 20, 12, 0))


def test_apply_raises_on_no_op_with_same_equipment():
    t = _mk("T1", "A", 9, 12)
    snap = Snap(by_id={"T1": t})
    with pytest.raises(ValueError, match="no-op change"):
        snap.apply(
            "T1",
            datetime(2026, 4, 20, 9, 0),
            datetime(2026, 4, 20, 12, 0),
            new_equipment_code="A",
        )


class _FakeBatch:
    """Duck-typed 대체 — ScheduleTask.batch relationship 이 없으므로 caller가
    사전 조회한 ProductionBatch 를 주입하는 패턴을 테스트로 고정."""

    def __init__(self, sales_order_id=None, sales_order_line=None, due_date=None):
        self.sales_order_id = sales_order_id
        self.sales_order_line = sales_order_line
        self.due_date = due_date


class _FakeTask:
    """Duck-typed ScheduleTask 대체. SQLAlchemy 의존 없이 build_snapshot 계약을 검증."""

    def __init__(self, task_id, equipment_code, start, end, batch_id, batch):
        self.task_id = task_id
        self.equipment_code = equipment_code
        self.start_datetime = start
        self.end_datetime = end
        self.batch_id = batch_id
        self.batch = batch


def test_build_snapshot_from_duck_typed_rows():
    from app.application.cascade.snap import build_snapshot

    b = _FakeBatch(
        sales_order_id="SO-1",
        sales_order_line=2,
        due_date=datetime(2026, 4, 30),
    )
    row = _FakeTask(
        "T1",
        "EQ1",
        datetime(2026, 4, 20, 9, 0),
        datetime(2026, 4, 20, 12, 0),
        "B-1",
        b,
    )
    snap = build_snapshot([row])
    t = snap.get("T1")
    assert t.equipment_code == "EQ1"
    assert t.sales_order_id == "SO-1"
    assert t.sales_order_line == 2
    assert t.due_date == datetime(2026, 4, 30)


def test_build_snapshot_handles_none_batch():
    """batch 가 None 이면 SO/due_date 필드는 모두 None — caller 가 batch 를 조회하지 못한 경우."""
    from app.application.cascade.snap import build_snapshot

    row = _FakeTask(
        "T1",
        "EQ1",
        datetime(2026, 4, 20, 9, 0),
        datetime(2026, 4, 20, 12, 0),
        "B-1",
        None,
    )
    snap = build_snapshot([row])
    t = snap.get("T1")
    assert t.sales_order_id is None
    assert t.sales_order_line is None
    assert t.due_date is None
