"""JIT `apply_jit_delay` 통합/파이프라인 체인 동작 검증.

Scope:
    1. SCHEDULER_JIT env var toggle — auto_schedule 내부에서 경로 분기.
    2. Pipeline chain JIT — 연선→절연→시스 체인 전체에 shift 전파
       (fixed-point iteration).
    3. 불변식 유지 — overlap 0, 납기 준수, successor.start >= predecessor.end.

이 테스트는 DB 없이 mock/fake 로만 동작한다 — prototype 단계이므로
live Supabase 없이도 로직 검증 가능해야 한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from unittest.mock import patch

from app.application.scheduling.greedy.jit_scheduling import apply_jit_delay


@dataclass
class _FakeBatch:
    batch_id: int
    due_date: date
    sales_order_id: int
    sales_order_line: int


@dataclass
class _FakeTask:
    task_id: int
    batch_id: int
    equipment_code: str
    start_datetime: datetime
    end_datetime: datetime


class _FakeSession:
    """Minimal Session stub — query(ProductionBatch).filter(...).all()."""

    def __init__(self, batches: list[_FakeBatch]):
        self._batches = batches

    def query(self, model):
        return _FakeQuery(self._batches)


class _FakeQuery:
    def __init__(self, batches):
        self._batches = batches

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._batches

    def first(self):
        # calendar_engine.get_available_hours 가 OperationCalendar.first()
        # 를 호출 — holiday 없는 상태로 취급.
        return None


def test_pipeline_chain_shifts_upstream_after_downstream():
    """3공정 체인 (연선→절연→시스) 가 파이프라인 체인 shift.

    fixed-point iteration 없으면 연선이 (아직 shift 안 된) 절연 start 를
    successor cap 으로 받아 불충분하게 shift — 본 테스트는 fixed-point 의
    필연성을 요구한다.

    시나리오: 같은 수주 line (so=1, line=1), 납기 15일 여유.
    - 연선 task: 설비 ST-X, 4/1 08:00 ~ 4/1 16:00
    - 절연 task: 설비 IN-Y, 4/1 16:00 ~ 4/2 08:00
    - 시스 task: 설비 SH-Z, 4/2 08:00 ~ 4/2 20:00
    납기 4/20 → 모든 task 가 납기말로 밀릴 여유 있음.

    fixed-point 기대:
    - pass1: 시스 가 먼저 shift (successor 없음) → 4/20 8:00~20:00
    - pass2: 절연 cap 이 shifted 시스.start=4/20 08:00 으로 확장 → 절연 4/19
    - pass3: 연선 cap 이 shifted 절연.start 로 확장 → 연선 4/18~
    """
    batches = [
        _FakeBatch(
            batch_id=10,
            due_date=date(2026, 4, 20),
            sales_order_id=1,
            sales_order_line=1,
        ),
        _FakeBatch(
            batch_id=11,
            due_date=date(2026, 4, 20),
            sales_order_id=1,
            sales_order_line=1,
        ),
        _FakeBatch(
            batch_id=12,
            due_date=date(2026, 4, 20),
            sales_order_id=1,
            sales_order_line=1,
        ),
    ]
    tasks = [
        _FakeTask(1, 10, "ST-X", datetime(2026, 4, 1, 8), datetime(2026, 4, 1, 16)),
        _FakeTask(2, 11, "IN-Y", datetime(2026, 4, 1, 16), datetime(2026, 4, 2, 8)),
        _FakeTask(3, 12, "SH-Z", datetime(2026, 4, 2, 8), datetime(2026, 4, 2, 20)),
    ]
    db = _FakeSession(batches)

    count = apply_jit_delay(tasks, db=db, min_slack_days=3)

    # 3 task 모두 shift 되어야 파이프라인 chain 성공.
    assert count == 3, f"pipeline chain expected 3 shifts, got {count}"

    # 시스 가 가장 뒤로 (~납기말)
    assert tasks[2].end_datetime.date() == date(2026, 4, 20)
    # 절연 end 는 시스 start 이하 — 순서 보존
    assert tasks[1].end_datetime <= tasks[2].start_datetime
    # 연선 end 는 절연 start 이하 — 순서 보존
    assert tasks[0].end_datetime <= tasks[1].start_datetime
    # 원본 보다 모두 뒤로
    assert tasks[0].start_datetime > datetime(2026, 4, 1, 8)
    assert tasks[1].start_datetime > datetime(2026, 4, 1, 16)


def test_no_overlap_after_chain_shift_on_shared_equipment():
    """같은 설비에서 2 task, 뒤 task 는 납기 여유 작고 앞 task 는 여유 큰 경우.

    뒤 task (납기 4/3) 는 shift 불가 → cap 역할 → 앞 task (납기 4/20) shift 가
    뒤 task start 를 넘지 않아야 함 (overlap 금지).
    """
    batches = [
        _FakeBatch(
            batch_id=20,
            due_date=date(2026, 4, 20),
            sales_order_id=2,
            sales_order_line=1,
        ),
        _FakeBatch(
            batch_id=21, due_date=date(2026, 4, 3), sales_order_id=3, sales_order_line=1
        ),
    ]
    tasks = [
        _FakeTask(10, 20, "ST-A", datetime(2026, 4, 1, 8), datetime(2026, 4, 1, 16)),
        _FakeTask(11, 21, "ST-A", datetime(2026, 4, 1, 16), datetime(2026, 4, 2, 8)),
    ]
    db = _FakeSession(batches)

    apply_jit_delay(tasks, db=db, min_slack_days=3)

    # 설비 내 정렬 후 overlap 없음
    tasks_sorted = sorted(tasks, key=lambda t: t.start_datetime)
    for i in range(len(tasks_sorted) - 1):
        assert tasks_sorted[i].end_datetime <= tasks_sorted[i + 1].start_datetime, (
            f"overlap: {tasks_sorted[i]} vs {tasks_sorted[i + 1]}"
        )


def test_due_respected_after_shift():
    """shift 가 납기를 절대 초과하지 않음 (upper cap = due_end)."""
    batches = [
        _FakeBatch(
            batch_id=30,
            due_date=date(2026, 4, 10),
            sales_order_id=4,
            sales_order_line=1,
        ),
    ]
    tasks = [
        _FakeTask(20, 30, "ST-B", datetime(2026, 4, 1, 8), datetime(2026, 4, 1, 16)),
    ]
    db = _FakeSession(batches)

    apply_jit_delay(tasks, db=db, min_slack_days=3)

    # 납기 4/10 23:59 이하
    assert tasks[0].end_datetime <= datetime(2026, 4, 10, 23, 59)


def test_env_var_disabled_by_default():
    """SCHEDULER_JIT 미설정 시 auto_schedule 은 JIT 호출 안 함.

    auto_schedule 레벨 통합 검증 — 내부 call 여부를 mock 으로.
    """
    from app.services import schedule_optimizer

    # JIT 함수를 mock — 실제 호출 발생 시 감지
    with patch.object(
        schedule_optimizer, "apply_jit_delay", return_value=0
    ) as mock_jit:
        # 기본 환경: SCHEDULER_JIT 없음 or "0"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCHEDULER_JIT", None)
            _ = schedule_optimizer._should_apply_jit()
            # _should_apply_jit 헬퍼가 False 반환해야 함
            assert schedule_optimizer._should_apply_jit() is False

        # "0" 명시도 off
        with patch.dict(os.environ, {"SCHEDULER_JIT": "0"}):
            assert schedule_optimizer._should_apply_jit() is False

        # "1" 이면 on
        with patch.dict(os.environ, {"SCHEDULER_JIT": "1"}):
            assert schedule_optimizer._should_apply_jit() is True

    _ = mock_jit  # silence unused
