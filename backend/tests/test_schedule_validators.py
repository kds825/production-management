"""Task 13: schedule_validators 단위 테스트.

DB 없이 Snap/SnapTask 만으로 세 validator 의 탐지 규칙을 검증. 서비스 단위 테스트이므로
라우트/DB fixture 에 의존하지 않는다.
"""

from datetime import datetime

from app.application.cascade.snap import Snap, SnapTask
from app.application.validation.schedule_validators import (
    find_due_date_violation,
    find_predecessor_violation,
    find_same_eq_overlap,
)


def _mk(
    tid: str,
    eq: str,
    sh: int,
    eh: int,
    due: datetime | None = datetime(2026, 4, 30),
    sol: int = 1,
) -> SnapTask:
    """SnapTask 빌더. apply 된 것처럼 보이게 old_* 를 세팅해 changed_tasks() 가 잡게 함.

    changed_tasks() 필터 규칙: `old_start is not None` → apply 가 한 번이라도 일어난 것으로
    간주. validator 들은 이 필터를 통과한 task 만 검사하므로 테스트 데이터에선 old_* 를
    1시간 앞으로 offset 해 "실제 이동된 것처럼" 세팅.
    """
    t = SnapTask(
        task_id=tid,
        equipment_code=eq,
        start=datetime(2026, 4, 20, sh, 0),
        end=datetime(2026, 4, 20, eh, 0),
        batch_id=f"B-{tid}",
        sales_order_id="SO-1",
        sales_order_line=sol,
        due_date=due,
    )
    # apply 시뮬레이션 — 원본은 1시간 앞이었다고 가정.
    t.old_start = datetime(2026, 4, 20, max(sh - 1, 0), 0)
    t.old_end = datetime(2026, 4, 20, max(eh - 1, 0), 0)
    t.old_equipment_code = eq
    return t


class TestFindSameEqOverlap:
    def test_overlap_detected(self):
        snap = Snap(
            by_id={
                "A": _mk("A", "EQ", 10, 14),
                "B": _mk("B", "EQ", 12, 16),  # overlap (12 < 14)
            }
        )
        off = find_same_eq_overlap(snap)
        assert off is not None
        assert off.task_id in {"A", "B"}

    def test_no_overlap_when_adjacent(self):
        """경계 접촉(end == start) 은 overlap 아님 — same_equipment_overlapping 규약."""
        snap = Snap(
            by_id={
                "A": _mk("A", "EQ", 10, 12),
                "B": _mk("B", "EQ", 12, 14),
            }
        )
        assert find_same_eq_overlap(snap) is None

    def test_different_equipment_no_overlap(self):
        snap = Snap(
            by_id={
                "A": _mk("A", "EQ1", 10, 14),
                "B": _mk("B", "EQ2", 12, 16),
            }
        )
        assert find_same_eq_overlap(snap) is None


class TestFindDueDateViolation:
    def test_due_date_violation_detected(self):
        t = _mk("T", "EQ", 10, 14, due=datetime(2026, 4, 20, 13, 0))
        snap = Snap(by_id={"T": t})
        off = find_due_date_violation(snap)
        assert off is not None
        assert off.task_id == "T"

    def test_due_date_none_skipped(self):
        """due_date None 이면 위반 대상 아님 (납기 미지정)."""
        t = _mk("T", "EQ", 10, 14, due=None)
        snap = Snap(by_id={"T": t})
        assert find_due_date_violation(snap) is None

    def test_end_before_due_date_ok(self):
        t = _mk("T", "EQ", 10, 14, due=datetime(2026, 4, 21, 0, 0))
        snap = Snap(by_id={"T": t})
        assert find_due_date_violation(snap) is None


class TestFindPredecessorViolation:
    def test_predecessor_overlap_detected(self):
        """선행(P) end=14, 후행(S) start=12 → P 가 S 시작 시점에도 진행 중 → 위반."""
        pred = _mk("P", "EQ1", 10, 14, sol=1)
        succ = _mk("S", "EQ2", 12, 16, sol=1)
        snap = Snap(by_id={"P": pred, "S": succ})
        off = find_predecessor_violation(snap)
        assert off is not None
        assert off.task_id == "S"

    def test_proper_order_ok(self):
        """P(10~14) → S(14~16) 공백 없음 — 위반 아님."""
        pred = _mk("P", "EQ1", 10, 14, sol=1)
        succ = _mk("S", "EQ2", 14, 16, sol=1)
        snap = Snap(by_id={"P": pred, "S": succ})
        assert find_predecessor_violation(snap) is None

    def test_different_so_line_skipped(self):
        """다른 sales_order_line 은 chain 관계 없음."""
        pred = _mk("P", "EQ1", 10, 14, sol=1)
        succ = _mk("S", "EQ2", 12, 16, sol=2)
        snap = Snap(by_id={"P": pred, "S": succ})
        assert find_predecessor_violation(snap) is None
