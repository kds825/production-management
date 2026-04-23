from datetime import datetime
from collections import defaultdict

from app.services.cascade.snap import Snap, SnapTask
from app.services.cascade.validators import (
    validate_due_date,
    validate_horizon,
    validate_cycles,
)
from app.services.cascade.reasons import UnresolvedReason


def _mk(task_id, sh, eh, due, sol=1):
    t = SnapTask(
        task_id,
        "EQ",
        datetime(2026, 4, 20, sh, 0),
        datetime(2026, 4, 20, eh, 0),
        batch_id=f"B-{task_id}",
        sales_order_id="SO",
        sales_order_line=sol,
        due_date=due,
    )
    # 변경 이력 세팅 (apply 된 것처럼)
    t.old_start = datetime(2026, 4, 20, sh - 1, 0)
    t.old_end = datetime(2026, 4, 20, eh - 1, 0)
    return t


def test_validate_due_date_detects_violation():
    t = _mk("T", 10, 14, due=datetime(2026, 4, 20, 12, 0))
    snap = Snap(by_id={"T": t})
    result = validate_due_date(snap)
    assert len(result) == 1
    assert result[0]["reason"] == UnresolvedReason.due_date_violation
    assert result[0]["task_id"] == "T"


def test_validate_due_date_ignores_unchanged():
    t = _mk("T", 10, 14, due=datetime(2026, 4, 20, 12, 0))
    # 변경 이력 제거 (apply 없음)
    t.old_start = None
    t.old_end = None
    snap = Snap(by_id={"T": t})
    assert validate_due_date(snap) == []


def test_validate_due_date_ignores_none_due():
    t = _mk("T", 10, 14, due=None)
    snap = Snap(by_id={"T": t})
    assert validate_due_date(snap) == []


def test_validate_due_date_within_deadline():
    t = _mk("T", 10, 14, due=datetime(2026, 4, 30))
    snap = Snap(by_id={"T": t})
    assert validate_due_date(snap) == []


def test_validate_horizon_exceeds():
    t = _mk("T", 10, 14, due=datetime(2026, 4, 30))
    snap = Snap(by_id={"T": t})
    result = validate_horizon(snap, horizon_end=datetime(2026, 4, 20, 11, 0))
    assert len(result) == 1
    assert result[0]["reason"] == UnresolvedReason.no_space_forward


def test_validate_horizon_within_bounds():
    t = _mk("T", 10, 14, due=datetime(2026, 4, 30))
    snap = Snap(by_id={"T": t})
    result = validate_horizon(snap, horizon_end=datetime(2026, 5, 1))
    assert result == []


def test_validate_horizon_ignores_unchanged():
    t = _mk("T", 10, 14, due=datetime(2026, 4, 30))
    t.old_start = None
    t.old_end = None
    snap = Snap(by_id={"T": t})
    assert validate_horizon(snap, horizon_end=datetime(2026, 4, 20, 11, 0)) == []


def test_validate_cycles_hits_max_waves():
    push_count = defaultdict(int, {"A": 5})
    result = validate_cycles(push_count, max_waves=4)
    assert len(result) == 1
    assert result[0]["reason"] == UnresolvedReason.cycle_detected
    assert result[0]["task_id"] == "A"


def test_validate_cycles_under_limit():
    push_count = defaultdict(int, {"A": 2})
    assert validate_cycles(push_count, max_waves=4) == []


def test_validate_cycles_exactly_at_limit():
    """정확히 max_waves 는 허용 (경계)."""
    push_count = defaultdict(int, {"A": 4})
    assert validate_cycles(push_count, max_waves=4) == []


def test_validate_cycles_multiple_offenders():
    push_count = defaultdict(int, {"A": 5, "B": 6, "C": 1})
    result = validate_cycles(push_count, max_waves=4)
    offender_ids = {r["task_id"] for r in result}
    assert offender_ids == {"A", "B"}
