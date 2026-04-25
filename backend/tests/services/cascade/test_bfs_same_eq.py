from datetime import datetime
from app.application.cascade.snap import Snap, SnapTask
from app.application.cascade.bfs import same_equipment_overlapping, same_eq_prev_end


def _mk(task_id, eq, sh, eh, sol=1):
    return SnapTask(
        task_id,
        eq,
        datetime(2026, 4, 20, sh, 0),
        datetime(2026, 4, 20, eh, 0),
        batch_id=f"B-{task_id}",
        sales_order_id="SO-1",
        sales_order_line=sol,
        due_date=datetime(2026, 4, 30),
    )


def test_overlapping_forward():
    """T 의 뒤에 있으면서 T 와 겹치는 task 는 반환."""
    t = _mk("T", "EQ", 10, 14)
    n = _mk("N", "EQ", 12, 15)
    snap = Snap(by_id={"T": t, "N": n})
    assert [x.task_id for x in same_equipment_overlapping(t, snap)] == ["N"]


def test_overlapping_backward_on_equipment_change():
    """T10 (v1 B2 수정): 설비 변경 드래그 시 새 설비의 '앞' task 가 T 시작 이전에 끝나지 않고 T 와 겹치면 반환."""
    t = _mk("T", "EQ", 10, 14)  # T 가 새로 EQ 로 이동했다고 가정
    prev = _mk("P", "EQ", 8, 12)  # P.end(12) > T.start(10) → 겹침
    snap = Snap(by_id={"T": t, "P": prev})
    result = [x.task_id for x in same_equipment_overlapping(t, snap)]
    assert "P" in result


def test_no_overlap_when_adjacent():
    """P.end == T.start 는 겹침 아님 (경계 비포함)."""
    t = _mk("T", "EQ", 10, 14)
    p = _mk("P", "EQ", 8, 10)  # 12 → 10 으로 정확히 맞닿음
    snap = Snap(by_id={"T": t, "P": p})
    assert same_equipment_overlapping(t, snap) == []


def test_no_overlap_different_equipment():
    t = _mk("T", "EQ1", 10, 14)
    other = _mk("O", "EQ2", 10, 14)
    snap = Snap(by_id={"T": t, "O": other})
    assert same_equipment_overlapping(t, snap) == []


def test_excludes_self():
    t = _mk("T", "EQ", 10, 14)
    snap = Snap(by_id={"T": t})
    assert same_equipment_overlapping(t, snap) == []


def test_same_eq_prev_end_returns_latest_before():
    target = _mk("T", "EQ", 14, 18)
    a = _mk("A", "EQ", 8, 10)
    b = _mk("B", "EQ", 10, 13)  # B 의 end 가 target.start(14) 보다 가까움
    snap = Snap(by_id={"T": target, "A": a, "B": b})
    assert same_eq_prev_end(target, snap) == datetime(2026, 4, 20, 13, 0)


def test_same_eq_prev_end_none_when_first():
    t = _mk("T", "EQ", 9, 12)
    snap = Snap(by_id={"T": t})
    assert same_eq_prev_end(t, snap) is None


def test_same_eq_prev_end_includes_exact_boundary():
    """P.end == T.start 인 이웃도 prev 로 인정 (가장 가까운 이전)."""
    t = _mk("T", "EQ", 14, 18)
    p = _mk("P", "EQ", 10, 14)
    snap = Snap(by_id={"T": t, "P": p})
    assert same_eq_prev_end(t, snap) == datetime(2026, 4, 20, 14, 0)
