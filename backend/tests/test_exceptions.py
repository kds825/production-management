"""SchedulerOverlapError 테스트."""

import pytest

from app.exceptions import SchedulerOverlapError


def test_scheduler_overlap_error_attributes():
    exc = SchedulerOverlapError(
        "overlap persists",
        run_label="test-run",
        violations=[{"constraint_id": "overlap", "detail": "dummy"}],
        attempts=3,
    )
    assert exc.run_label == "test-run"
    assert exc.attempts == 3
    assert exc.violations == [{"constraint_id": "overlap", "detail": "dummy"}]
    assert str(exc) == "overlap persists"


def test_scheduler_overlap_error_is_exception():
    with pytest.raises(SchedulerOverlapError) as exc_info:
        raise SchedulerOverlapError("x", run_label="r", violations=[], attempts=1)
    assert exc_info.value.run_label == "r"
