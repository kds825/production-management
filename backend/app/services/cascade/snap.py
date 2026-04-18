"""In-memory schedule snapshot (pure functions, no DB)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class SnapTask:
    task_id: str
    equipment_code: str
    start: datetime
    end: datetime
    batch_id: str
    sales_order_id: str | None
    sales_order_line: int | None
    due_date: datetime | None
    old_start: datetime | None = None
    old_end: datetime | None = None
    old_equipment_code: str | None = None


@dataclass
class Snap:
    by_id: dict[str, SnapTask] = field(default_factory=dict)

    def apply(self, task_id, new_start, new_end, new_equipment_code=None):
        raise NotImplementedError

    def get(self, task_id) -> SnapTask:
        raise NotImplementedError

    def by_equipment(self, code) -> list[SnapTask]:
        raise NotImplementedError


def build_snapshot(tasks: Iterable) -> Snap:
    raise NotImplementedError
