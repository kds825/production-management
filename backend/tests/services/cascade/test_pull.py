from datetime import datetime
from app.services.cascade.snap import Snap, SnapTask
from app.services.cascade.pull import propose_for_successors
from app.services.cascade.reasons import PullReason


def _mk(task_id, eq, sh, eh, so="SO-1", sol=1, old_sh=None, old_eh=None):
    t = SnapTask(
        task_id,
        eq,
        datetime(2026, 4, 20, sh, 0),
        datetime(2026, 4, 20, eh, 0),
        batch_id=f"B-{task_id}",
        sales_order_id=so,
        sales_order_line=sol,
        due_date=datetime(2026, 4, 30),
    )
    if old_sh is not None:
        t.old_start = datetime(2026, 4, 20, old_sh, 0)
        t.old_end = datetime(2026, 4, 20, old_eh, 0)
    return t


def fake_reverse_advance(dt, duration):
    """테스트용 — 캘린더 skip 무시, 단순 `dt - duration`."""
    return dt - duration


def test_t6_slack_propagates():
    """변경 task T 가 3h 짧아져 후속 S 앞 slack 이 생기면 Pull."""
    T = _mk("T", "EQ1", sh=9, eh=12, old_sh=9, old_eh=15)  # 3h 짧아짐
    S = _mk("S", "EQ2", sh=15, eh=18)  # 원래 15시 시작
    snap = Snap(by_id={"T": T, "S": S})
    pulls = propose_for_successors("T", snap, fake_reverse_advance)
    assert len(pulls) == 1
    assert pulls[0]["task_id"] == "S"
    assert pulls[0]["new_start"] == datetime(2026, 4, 20, 12, 0)  # 3h 당김
    assert pulls[0]["new_end"] == datetime(2026, 4, 20, 15, 0)
    assert pulls[0]["reason"] == PullReason.successor_slack_available


def test_t7_same_eq_prev_blocks_full_slack():
    """S 의 같은 설비 앞 task P.end=13 이 있어 slack 이 제한됨."""
    T = _mk("T", "EQ1", sh=9, eh=12, old_sh=9, old_eh=15)
    S = _mk("S", "EQ2", sh=15, eh=18)
    P = _mk("P", "EQ2", sh=10, eh=13, sol=99)  # S 같은 설비 앞 task (다른 수주)
    snap = Snap(by_id={"T": T, "S": S, "P": P})
    pulls = propose_for_successors("T", snap, fake_reverse_advance)
    # earliest = max(T.end=12, P.end=13) = 13
    # slack = S.old_start(15) - 13 = 2h
    # proposed_start = 15 - 2 = 13
    assert len(pulls) == 1
    assert pulls[0]["task_id"] == "S"
    assert pulls[0]["new_start"] == datetime(2026, 4, 20, 13, 0)
    assert pulls[0]["new_end"] == datetime(2026, 4, 20, 16, 0)


def test_t12_no_pull_when_prev_blocks_entirely():
    """P.end 가 S.start 와 동일 → slack = 0 → Pull 없음."""
    T = _mk("T", "EQ1", sh=9, eh=12, old_sh=9, old_eh=15)
    S = _mk("S", "EQ2", sh=15, eh=18)
    P = _mk("P", "EQ2", sh=10, eh=15, sol=99)
    snap = Snap(by_id={"T": T, "S": S, "P": P})
    pulls = propose_for_successors("T", snap, fake_reverse_advance)
    assert pulls == []


def test_no_pull_when_changed_task_not_shortened():
    """T 가 변경되지 않음 → Pull 제안 없음."""
    T = _mk("T", "EQ1", sh=9, eh=12)  # old_* = None
    S = _mk("S", "EQ2", sh=12, eh=15)
    snap = Snap(by_id={"T": T, "S": S})
    assert propose_for_successors("T", snap, fake_reverse_advance) == []


def test_no_pull_when_lengthened():
    """T 가 오히려 늘어났으면 Pull 없음."""
    T = _mk("T", "EQ1", sh=9, eh=15, old_sh=9, old_eh=12)  # 늘어남
    S = _mk("S", "EQ2", sh=12, eh=15)
    snap = Snap(by_id={"T": T, "S": S})
    assert propose_for_successors("T", snap, fake_reverse_advance) == []


def test_pull_propagates_to_chain():
    """T 축소 → S1 당김 → S2 는 S1 과 맞닿아 있으면 추가 slack 없음."""
    T = _mk("T", "EQ1", sh=9, eh=12, old_sh=9, old_eh=15)
    S1 = _mk("S1", "EQ2", sh=15, eh=18)
    S2 = _mk("S2", "EQ3", sh=18, eh=20)
    snap = Snap(by_id={"T": T, "S1": S1, "S2": S2})
    pulls = propose_for_successors("T", snap, fake_reverse_advance)
    # S1: slack 3 → proposed end = 18 - 3 + 3 = 18 (pull 된 start 12)
    # Wait: S1.old_start=15, slack=15-12=3, proposed_start=15-3=12, proposed_end=12+3=15
    # Then prev_end=15 (S1 new_end). S2.old_start=18, slack=18-15=3.
    # proposed S2 start = 18-3 = 15, end = 15+2 = 17.
    ids_new_start = {p["task_id"]: p["new_start"] for p in pulls}
    assert ids_new_start["S1"] == datetime(2026, 4, 20, 12, 0)
    assert ids_new_start["S2"] == datetime(2026, 4, 20, 15, 0)


def test_no_successor_no_pull():
    T = _mk("T", "EQ1", sh=9, eh=12, old_sh=9, old_eh=15)
    snap = Snap(by_id={"T": T})
    assert propose_for_successors("T", snap, fake_reverse_advance) == []
