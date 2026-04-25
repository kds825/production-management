"""Phase C1: constraint_checker._check_setup_time false positive 회귀 방지.

schedule_optimizer 는 setup 을 task.setup_time_min 내부에 저장 (duration 포함).
- single-equipment 경로: next.setup_time_min 에 기록
- multi-equipment 경로: curr.setup_time_min 에 기록 (first-batch-in-group)

따라서 checker 는 gap + curr.setup + next.setup 합산으로 봐야 "SQ 교체 setup 이
어디엔가 기록됐는가" 를 정확히 본다. gap 만 보면 false positive 대량 발생.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.database import SessionLocal
from app.application.validation.constraint_checker import validate_all


# run_stage2_direct.py 로 생성된 고정 run. 이 테스트가 실행 시점에 스케줄이 변하면
# 수치는 달라질 수 있으나 roll-forward 구조상 0 건 이상이면 안 됨.
RUN_LABEL = "20260417_212139"


@pytest.fixture(scope="module")
def violations():
    s: Session = SessionLocal()
    try:
        yield validate_all(RUN_LABEL, s)
    finally:
        s.close()


def test_setup_time_violations_should_be_zero(violations):
    """schedule_optimizer 가 setup 을 task 내부에 담는 한, 4-1 violation 은 0 건이어야 한다."""
    setup_violations = [v for v in violations if v.get("constraint_id") == "4-1"]
    if setup_violations:
        sample = [v["detail"] for v in setup_violations[:5]]
        pytest.fail(
            f"4-1 violations = {len(setup_violations)} (expected 0)\n"
            f"샘플 5건:\n  " + "\n  ".join(sample)
        )
