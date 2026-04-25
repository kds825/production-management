from datetime import datetime
from app.application.cascade.snap import Snap, SnapTask
from app.application.cascade.bfs import successor_tasks


def _mk(task_id, eq, sh, eh, so="SO-1", sol=1):
    return SnapTask(
        task_id,
        eq,
        datetime(2026, 4, 20, sh, 0),
        datetime(2026, 4, 20, eh, 0),
        batch_id=f"B-{task_id}",
        sales_order_id=so,
        sales_order_line=sol,
        due_date=datetime(2026, 4, 30),
    )


def test_successor_is_same_so_and_later_start():
    t = _mk("T", "EQ1", 10, 14, so="A", sol=1)
    s = _mk("S", "EQ2", 14, 18, so="A", sol=1)
    other = _mk("O", "EQ2", 14, 18, so="B", sol=1)  # 다른 수주
    snap = Snap(by_id={"T": t, "S": s, "O": other})
    assert [x.task_id for x in successor_tasks(t, snap)] == ["S"]


def test_ignore_earlier_task():
    t = _mk("T", "EQ1", 10, 14)
    earlier = _mk("E", "EQ2", 6, 9)  # 같은 수주지만 이전
    snap = Snap(by_id={"T": t, "E": earlier})
    assert successor_tasks(t, snap) == []


def test_successor_sorted_by_start():
    t = _mk("T", "EQ1", 10, 14)
    s2 = _mk("S2", "EQ3", 18, 20)
    s1 = _mk("S1", "EQ2", 14, 18)
    snap = Snap(by_id={"T": t, "S1": s1, "S2": s2})
    assert [x.task_id for x in successor_tasks(t, snap)] == ["S1", "S2"]


def test_excludes_self():
    t = _mk("T", "EQ1", 10, 14)
    snap = Snap(by_id={"T": t})
    assert successor_tasks(t, snap) == []


def test_no_successor_when_missing_so_or_line():
    """sales_order_id 혹은 sales_order_line 이 None 이면 successor 추적 불가."""
    t = SnapTask(
        "T",
        "EQ1",
        datetime(2026, 4, 20, 10, 0),
        datetime(2026, 4, 20, 14, 0),
        batch_id="B-T",
        sales_order_id=None,
        sales_order_line=1,
        due_date=datetime(2026, 4, 30),
    )
    s = SnapTask(
        "S",
        "EQ2",
        datetime(2026, 4, 20, 14, 0),
        datetime(2026, 4, 20, 18, 0),
        batch_id="B-S",
        sales_order_id=None,
        sales_order_line=1,
        due_date=datetime(2026, 4, 30),
    )
    snap = Snap(by_id={"T": t, "S": s})
    assert successor_tasks(t, snap) == []


def test_different_sales_order_line_not_successor():
    t = _mk("T", "EQ1", 10, 14, sol=1)
    other_line = _mk("O", "EQ2", 14, 18, sol=2)  # 다른 라인
    snap = Snap(by_id={"T": t, "O": other_line})
    assert successor_tasks(t, snap) == []
