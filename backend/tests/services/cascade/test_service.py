"""Unit tests for `app.application.cascade.service.plan_cascade_preview_on_snap`.

service 는 wave 기반 BFS cascade orchestrator 로,
Snap 을 주입받아 pushes/pulls/unresolved 를 산출한다. DB/calendar 의존은
advance_fn/reverse_advance_fn 으로 주입해서 순수 단위 테스트 가능하게 설계.

여기선 identity calendar (주말/근무시간 skip 무시) 로 T1 T2 T3 T11 시나리오를 검증.
"""

from datetime import datetime

from app.application.cascade.snap import Snap, SnapTask
from app.application.cascade.service import plan_cascade_preview_on_snap, MAX_WAVES
from app.application.cascade.reasons import PushReason, UnresolvedReason


def _mk(task_id, eq, sh, eh, sol=1, due_h=30):
    return SnapTask(
        task_id,
        eq,
        datetime(2026, 4, 20, sh, 0),
        datetime(2026, 4, 20, eh, 0),
        batch_id=f"B-{task_id}",
        sales_order_id="SO-1",
        sales_order_line=sol,
        due_date=datetime(2026, 4, 30, due_h % 24),
    )


def _advance_identity(dt):
    """테스트용 캘린더: 그대로 리턴 (주말/근무시간 skip 무시)."""
    return dt


def _reverse_identity(dt, dur):
    return dt - dur


def _run(snap, changed_id, horizon_end=None):
    return plan_cascade_preview_on_snap(
        snap,
        changed_id,
        advance_fn=_advance_identity,
        reverse_advance_fn=_reverse_identity,
        horizon_end=horizon_end or datetime(2026, 5, 1),
    )


def test_t1_duration_increase_pushes_next_same_eq():
    """T1: A 가 EQ1 에서 3h 연장되어 뒤의 B 와 충돌 → B push."""
    A = _mk("A", "EQ1", 9, 12)
    B = _mk("B", "EQ1", 12, 15)
    snap = Snap(by_id={"A": A, "B": B})
    snap.apply("A", datetime(2026, 4, 20, 9, 0), datetime(2026, 4, 20, 15, 0))
    res = _run(snap, "A")
    pushed_ids = {p["task_id"] for p in res.pushes}
    assert "B" in pushed_ids
    b_push = [p for p in res.pushes if p["task_id"] == "B"][0]
    assert b_push["new_start"] == datetime(2026, 4, 20, 15, 0)
    assert b_push["reason"] == PushReason.same_equipment_conflict


def test_t2_successor_chain_push():
    """T2: 연선 A 연장 → 절연 B push → 시스 C 도 밀림."""
    A = _mk("A", "EQ1", 9, 12, sol=1)  # 연선
    B = _mk("B", "EQ2", 12, 15, sol=1)  # 절연
    C = _mk("C", "EQ3", 15, 18, sol=1)  # 시스
    snap = Snap(by_id={"A": A, "B": B, "C": C})
    snap.apply(
        "A", datetime(2026, 4, 20, 9, 0), datetime(2026, 4, 20, 14, 0)
    )  # 2h 연장
    res = _run(snap, "A")
    ids = {p["task_id"]: p for p in res.pushes}
    assert "B" in ids and ids["B"]["new_start"] == datetime(2026, 4, 20, 14, 0)
    assert "C" in ids  # B 의 후속이 또 밀림


def test_t3_cross_equipment_conflict():
    """T3: successor B 가 새 시작 시점에서 자기 설비의 다른 수주 X 와 충돌 → X 도 push."""
    A = _mk("A", "EQ1", 9, 12, sol=1)  # 연선
    B = _mk("B", "EQ2", 12, 15, sol=1)  # 절연
    X = _mk("X", "EQ2", 14, 16, sol=99)  # 다른 수주
    snap = Snap(by_id={"A": A, "B": B, "X": X})
    snap.apply("A", datetime(2026, 4, 20, 9, 0), datetime(2026, 4, 20, 14, 0))
    res = _run(snap, "A")
    ids = {p["task_id"]: p for p in res.pushes}
    assert "X" in ids
    assert (
        ids["X"]["reason"] == PushReason.same_equipment_conflict
    )  # X 입장에서 B 와 same-eq overlap


def test_t11_wave_dedup():
    """T11: B 가 A 의 same-equipment 이자 successor — 한 번만 push."""
    A = _mk("A", "EQ1", 9, 12, sol=1)
    B = _mk("B", "EQ1", 12, 15, sol=1)  # 같은 설비의 다음 task 이자 successor
    snap = Snap(by_id={"A": A, "B": B})
    snap.apply("A", datetime(2026, 4, 20, 9, 0), datetime(2026, 4, 20, 13, 0))
    res = _run(snap, "A")
    b_entries = [p for p in res.pushes if p["task_id"] == "B"]
    assert len(b_entries) == 1


def test_no_conflict_returns_empty_lists():
    """변경이 기존 배치와 충돌하지 않으면 pushes/pulls/unresolved 모두 빈 리스트."""
    A = _mk("A", "EQ1", 9, 12)
    B = _mk("B", "EQ1", 20, 22)  # 충분히 떨어짐
    snap = Snap(by_id={"A": A, "B": B})
    snap.apply("A", datetime(2026, 4, 20, 9, 0), datetime(2026, 4, 20, 13, 0))
    res = _run(snap, "A")
    assert res.pushes == []
    assert res.pulls == []
    assert res.unresolved == []
    assert res.can_auto_resolve is True


def test_due_date_violation_yields_unresolved():
    A = _mk("A", "EQ1", 9, 12)
    snap = Snap(by_id={"A": A})
    # due date 가 4월 20일 15시 인데 end 가 20시 → 위반
    A.due_date = datetime(2026, 4, 20, 15, 0)
    snap.apply("A", datetime(2026, 4, 20, 9, 0), datetime(2026, 4, 20, 20, 0))
    res = _run(snap, "A")
    assert any(
        u["reason"] == UnresolvedReason.due_date_violation for u in res.unresolved
    )
    assert res.can_auto_resolve is False


def test_max_waves_constant_is_4():
    assert MAX_WAVES == 4


def test_result_has_request_id_and_iter_count():
    A = _mk("A", "EQ1", 9, 12)
    snap = Snap(by_id={"A": A})
    snap.apply("A", datetime(2026, 4, 20, 9, 0), datetime(2026, 4, 20, 13, 0))
    res = _run(snap, "A")
    assert res.request_id  # uuid-ish, 빈 문자열 아님
    assert res.iter_count >= 0
    assert res.truncated is False
    assert isinstance(res.summary, str)
