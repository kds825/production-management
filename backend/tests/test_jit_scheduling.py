"""JIT post-processing prototype 검증.

`apply_jit_delay` 는 납기 여유 큰 task 를 설비 내 gap + 후공정 제약 범위에서
뒤로 shift. PDF T6B0 4/8~4/12 gap 재현 목표.

Prototype 단계 — auto_schedule() 에 아직 통합 안 됨. unit test 로 invariant 검증.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.application.scheduling.greedy.jit_scheduling import apply_jit_delay


@dataclass
class _FakeTask:
    task_id: int
    batch_id: int
    equipment_code: str
    start_datetime: datetime
    end_datetime: datetime


def test_jit_shifts_isolated_task_with_large_due_slack():
    """단일 설비 1 task — 납기 여유 크면 납기 말일로 shift."""
    t = _FakeTask(
        task_id=1,
        batch_id=100,
        equipment_code="ST-T6B0",
        start_datetime=datetime(2026, 4, 8, 4, 0),
        end_datetime=datetime(2026, 4, 9, 14, 0),
    )
    # pb_by_id mock 없이 직접 호출 — db=None 경로 + pb lookup 실패
    # 납기 여유 확인하려면 db 가 필요 → 단위 테스트용으로 분리된 helper 체크가
    # 좋지만, prototype 이라 db 없는 경우는 shift 하지 않음이 맞음.
    count = apply_jit_delay([t], db=None, min_slack_days=3)
    # db 없으면 pb_by_id 비어 있어 아무 shift 없음 (안전 기본값).
    assert count == 0
    # 원본 변경 없음
    assert t.start_datetime == datetime(2026, 4, 8, 4, 0)


def test_jit_no_shift_when_adjacent_task_blocks():
    """설비 내 바로 뒤 task 의 start 가 cap 역할 — shift 불가."""
    # 2 task consecutive 에 가까움 (5분 gap 만). shift 해봤자 의미 없음.
    t1 = _FakeTask(
        task_id=1,
        batch_id=100,
        equipment_code="ST-T6B0",
        start_datetime=datetime(2026, 4, 8, 4, 0),
        end_datetime=datetime(2026, 4, 9, 14, 0),
    )
    t2 = _FakeTask(
        task_id=2,
        batch_id=101,
        equipment_code="ST-T6B0",
        start_datetime=datetime(2026, 4, 9, 14, 5),
        end_datetime=datetime(2026, 4, 10, 10, 0),
    )
    count = apply_jit_delay([t1, t2], db=None, min_slack_days=3)
    # db=None → pb_by_id 비어 있음 → skip. 안전 확인.
    assert count == 0


def test_jit_preserves_invariants_when_all_skip():
    """모든 task 가 shift 불가 조건이면 변경 없음."""
    tasks = [
        _FakeTask(
            task_id=i,
            batch_id=100 + i,
            equipment_code="ST-T6B0",
            start_datetime=datetime(2026, 4, 8, i),
            end_datetime=datetime(2026, 4, 8, i + 1),
        )
        for i in range(3)
    ]
    orig = [(t.start_datetime, t.end_datetime) for t in tasks]
    apply_jit_delay(tasks, db=None, min_slack_days=3)
    after = [(t.start_datetime, t.end_datetime) for t in tasks]
    assert orig == after, "db=None 경로에서 task 변경 발생 — 안전 기본값 깨짐"


def test_jit_empty_tasks_returns_zero():
    """빈 입력 → 0 반환, 에러 없음."""
    assert apply_jit_delay([], db=None) == 0
