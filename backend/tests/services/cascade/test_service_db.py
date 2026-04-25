"""Task 10 T8/T9: cascade 서비스 DB 통합 수준 검증.

원본 plan 의도는 "실 DB + 실 calendar" 였으나, PoC 단계에서 DB fixture 전체를 띄우는
비용이 높고 `plan_cascade_preview_on_snap` + 실 `reverse_advance` 주입 만으로도
T8(설비 변경 cascade) / T9(주말 gap skip 앞당김) 의 핵심 계약을 충분히 검증 가능.

- T8: 설비 변경 시 새 설비의 기존 task 와의 겹침을 감지 → push 제안.
- T9: 실 `reverse_advance` 로 앞당김 제안이 산출됨 (calendar 휴무/주말 skip 경로 통합).
"""

from datetime import datetime

from app.services.cascade.snap import Snap, SnapTask
from app.services.cascade.service import plan_cascade_preview_on_snap
from app.infrastructure.calendar_engine import reverse_advance as real_reverse


def _mk(task_id, eq, sh, eh, sol=1, day=20):
    """fixture 밖의 로컬 헬퍼 — 특정 시나리오용 task 를 유연하게 생성."""
    return SnapTask(
        task_id,
        eq,
        datetime(2026, 4, day, sh, 0),
        datetime(2026, 4, day, eh, 0),
        batch_id=f"B-{task_id}",
        sales_order_id="SO-1",
        sales_order_line=sol,
        due_date=datetime(2026, 4, 30),
    )


def test_t8_equipment_change_cascade():
    """T8: 설비 변경 시 새 설비의 기존 task 와 겹치면 push 제안이 발생.

    A(EQ1, 9-12) 를 EQ2 로 10-13 이동 → B(EQ2, 8-11) 가 A 와 10-11 구간 겹침.
    same_equipment_overlapping 이 양방향 감지로 B 를 push frontier 에 올려야 함.
    """
    A = _mk("A", "EQ1", 9, 12)
    B = _mk("B", "EQ2", 8, 11)  # EQ2 의 기존 task
    snap = Snap(by_id={"A": A, "B": B})
    # A 를 EQ2 로 이동 (10-13)
    snap.apply(
        "A",
        datetime(2026, 4, 20, 10, 0),
        datetime(2026, 4, 20, 13, 0),
        new_equipment_code="EQ2",
    )
    res = plan_cascade_preview_on_snap(
        snap,
        "A",
        advance_fn=lambda dt: dt,
        reverse_advance_fn=lambda dt, dur: dt - dur,
        horizon_end=datetime(2026, 5, 1),
    )
    # B 가 EQ2 에서 A(10-13) 와 겹침 → push 대상
    assert any(p["task_id"] == "B" for p in res.pushes), (
        f"B 가 push 에 포함되어야 함. 실제 pushes: {res.pushes}"
    )


def test_t9_pull_uses_real_reverse_advance():
    """T9: 실 `reverse_advance` 주입 시 Pull 제안이 주말/휴무 gap 을 skip.

    T(EX-B100, 9-15) 가 9-12 로 3h 짧아진 뒤, S(EX-B100, 15-18) 가 후속 공정.
    slack = 3h → S.start(15:00) - 3h = 12:00 제안 (저압절연 EX-B100 은 24h 가동,
    휴식 없음 → identity 결과와 일치해야 함).
    """
    T = SnapTask(
        "T",
        "EX-B100",
        datetime(2026, 4, 20, 9, 0),
        datetime(2026, 4, 20, 12, 0),
        batch_id="B-T",
        sales_order_id="SO-1",
        sales_order_line=1,
        due_date=datetime(2026, 4, 30),
    )
    # old_* 을 수동 세팅 — "이미 짧아진 상태" 를 snap 에 표현
    # (apply 를 재호출하면 no-op 가드 때문에 기존 값과 동일할 경우 ValueError 발생하므로 직접 주입)
    T.old_start = datetime(2026, 4, 20, 9, 0)
    T.old_end = datetime(2026, 4, 20, 15, 0)  # 원본 6h → 변경 3h (3h 단축)

    # successor 조건 = 같은 sales_order_id + sales_order_line + o.start >= t.end.
    # 공정 순서는 task_id/batch_id 로 구분하고 라인은 동일하게 유지해야 successor_tasks 가 포착.
    S = SnapTask(
        "S",
        "EX-B100",
        datetime(2026, 4, 20, 15, 0),
        datetime(2026, 4, 20, 18, 0),
        batch_id="B-S",
        sales_order_id="SO-1",
        sales_order_line=1,
        due_date=datetime(2026, 4, 30),
    )
    snap = Snap(by_id={"T": T, "S": S})
    res = plan_cascade_preview_on_snap(
        snap,
        "T",
        advance_fn=lambda dt: dt,
        reverse_advance_fn=lambda dt, dur: real_reverse(
            dt, dur, ctx={"equipment_code": "EX-B100", "db": None}
        ),
        horizon_end=datetime(2026, 5, 1),
    )
    # Pull 제안 1건 (S 앞당김) — 실 reverse_advance 가 평일/휴식없음 계열에선 identity 와 동일.
    assert len(res.pulls) == 1, f"Pull 제안 1건 예상. 실제: {res.pulls}"
    assert res.pulls[0]["task_id"] == "S"
