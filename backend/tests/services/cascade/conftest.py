"""Task 10 fixture: cascade 서비스 통합 테스트용 seed 스케줄.

외부 DB 의존 없이 `plan_cascade_preview_on_snap` 에 직접 주입할 수 있는 최소 Snap 을
제공. T8/T9 등 단위 통합 테스트가 seed 와 실 calendar `reverse_advance` 를 조합해
동작을 검증할 수 있게 설계.
"""

from datetime import datetime

import pytest

from app.application.cascade.snap import Snap, SnapTask


@pytest.fixture
def seed_schedule() -> Snap:
    """단순 2 equipment × 2 task seed. 외부 DB 없이 Snap 으로 직접 공급.

    - EQ1 / EQ2 에 각 1건 — 설비 변경 cascade (T8) 검증 기반.
    - 동일 SO-1 라인이지만 서로 다른 sales_order_line 으로 독립 공정.
    """
    tasks = {
        "A": SnapTask(
            "A",
            "EQ1",
            datetime(2026, 4, 20, 9, 0),
            datetime(2026, 4, 20, 12, 0),
            batch_id="B-A",
            sales_order_id="SO-1",
            sales_order_line=1,
            due_date=datetime(2026, 4, 30),
        ),
        "B": SnapTask(
            "B",
            "EQ2",
            datetime(2026, 4, 20, 8, 0),
            datetime(2026, 4, 20, 11, 0),
            batch_id="B-B",
            sales_order_id="SO-2",
            sales_order_line=1,
            due_date=datetime(2026, 4, 30),
        ),
    }
    return Snap(by_id=tasks)
