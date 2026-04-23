"""설비 내 overlap 회귀 방지 test.

목적:
- 현재 baseline run `20260417_215558` 에 SH-A100 에서 19분 overlap 존재
  (task 28942 A100_갈_2026W16H2_400SQ × task 28966 A100_회_2026W18H1_120SQ).
- 본 test 는 새 run 이 생성될 때마다 같은 문제 없는지 검증.
- 현재 baseline 은 expected FAIL (Phase D 이월 예정). xfail 로 marking.
"""

from __future__ import annotations

from collections import defaultdict

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.database import SessionLocal
from app.infrastructure.models import ScheduleTask


_BASELINE_RUN = "20260417_215558"


@pytest.fixture(scope="module")
def db_session():
    s: Session = SessionLocal()
    yield s
    s.close()


def _find_overlaps(tasks: list[ScheduleTask]) -> list[tuple]:
    by_eq: dict[str, list[ScheduleTask]] = defaultdict(list)
    for t in tasks:
        by_eq[t.equipment_code].append(t)
    pairs = []
    for eq, ts in by_eq.items():
        ts.sort(key=lambda t: t.start_datetime)
        for i in range(len(ts) - 1):
            c, n = ts[i], ts[i + 1]
            if n.start_datetime < c.end_datetime:
                pairs.append(
                    (
                        eq,
                        c.task_id,
                        n.task_id,
                        c.end_datetime,
                        n.start_datetime,
                    )
                )
    return pairs


@pytest.mark.xfail(
    reason=(
        "Phase C Task 3 이월 (Phase D) — SH-A100 에서 다른 색상 group 간 "
        "_find_available_slot 이 기존 timeline slot 을 놓치는 root cause 아직 확정 "
        "안 됨. 현재 baseline run 20260417_215558 는 19분 overlap 1건 보유. "
        "Phase D 에서 수정 후 이 xfail 제거 예정."
    ),
    strict=False,
)
def test_no_overlap_in_baseline_run(db_session: Session):
    tasks = (
        db_session.query(ScheduleTask)
        .filter(ScheduleTask.run_label == _BASELINE_RUN)
        .all()
    )
    if not tasks:
        pytest.skip(f"run={_BASELINE_RUN} 에 schedule_task 없음")
    pairs = _find_overlaps(tasks)
    assert not pairs, f"설비 내 overlap {len(pairs)}건: {pairs[:3]}"


def test_baseline_overlap_is_exactly_the_known_pair(db_session: Session):
    """현재 baseline 의 overlap 이 문서화된 1건 (SH-A100 task 28942×28966) 맞는지.

    만약 overlap 이 사라지거나 다른 쌍으로 바뀌면 이 test 가 실패 → Phase D 진행
    시 기준점 변경을 감지.
    """
    tasks = (
        db_session.query(ScheduleTask)
        .filter(ScheduleTask.run_label == _BASELINE_RUN)
        .all()
    )
    if not tasks:
        pytest.skip(f"run={_BASELINE_RUN} 에 schedule_task 없음")
    pairs = _find_overlaps(tasks)
    # 정확히 1건 + SH-A100 설비 + 19분 overlap 확인
    assert len(pairs) == 1, f"overlap 쌍 개수가 1이 아님: {len(pairs)} → {pairs}"
    eq, cid, nid, c_end, n_start = pairs[0]
    assert eq == "SH-A100"
    overlap_min = (c_end - n_start).total_seconds() / 60
    assert 15 <= overlap_min <= 25, f"overlap 분 값 범위 벗어남: {overlap_min}"


# Phase D 재실행 run — ceiling fix 적용 후 + JIT post-processing 적용된 검증용 run.
# 이 run 에는 설비 내 overlap 이 단 1건도 없어야 한다.
_VERIFIED_RUN = "20260418_jit_run"


def test_no_overlap_in_verified_run(db_session: Session):
    """ceiling fix + JIT 적용 후 생성된 run 에 overlap 0 — Phase D 성공 criterion."""
    tasks = (
        db_session.query(ScheduleTask)
        .filter(ScheduleTask.run_label == _VERIFIED_RUN)
        .all()
    )
    if not tasks:
        pytest.skip(
            f"run={_VERIFIED_RUN} 에 schedule_task 없음 — "
            "scripts/run_stage2_direct.py 로 재실행 필요"
        )
    pairs = _find_overlaps(tasks)
    assert not pairs, (
        f"Phase D fix 후에도 설비 내 overlap {len(pairs)}건 잔존: {pairs[:3]}"
    )
