"""In-memory schedule snapshot (pure functions, no DB).

cascade 파이프라인(BFS/Pull/Validators)이 공유하는 가벼운 스냅샷 자료구조.
원본 ORM 엔티티를 건드리지 않고, 제안(preview) 단계에서만 in-memory로 이동/수정한 뒤
최종 `apply` 가 결정되면 서비스 레이어에서 DB 반영한다.

설계 원칙:
- `SnapTask.old_*` 는 "최초 원본" 을 보존한다. apply 재진입 호출이 있더라도 첫 apply
  이전 값만 기록하여, downstream 에서 "원본 대비 변경" 판별을 단일 기준으로 할 수 있게 함.
- `by_equipment` 는 항상 start 오름차순으로 반환하여 호출자가 추가 정렬을 하지 않아도 되게 함
  (validators 의 overlap 검사, BFS 의 영향 체인 탐색이 시간순 가정을 공유).
"""

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
        t = self.by_id[task_id]
        # 최초 원본은 한 번만 보존 (재진입 호출 시 갱신 금지).
        # validators 가 "원본 대비 지연 여부"를 판단할 때 기준점을 단일화하기 위함.
        if t.old_start is None:
            t.old_start = t.start
            t.old_end = t.end
            t.old_equipment_code = t.equipment_code
        t.start = new_start
        t.end = new_end
        if new_equipment_code is not None:
            t.equipment_code = new_equipment_code

    def get(self, task_id) -> SnapTask:
        return self.by_id[task_id]

    def by_equipment(self, code) -> list[SnapTask]:
        return sorted(
            (t for t in self.by_id.values() if t.equipment_code == code),
            key=lambda t: t.start,
        )

    def changed_tasks(self) -> list[SnapTask]:
        """apply 가 한 번이라도 일어난 Task 만 반환 (validators.py 가 소비)."""
        return [t for t in self.by_id.values() if t.old_start is not None]


def build_snapshot(tasks: Iterable) -> Snap:
    """ORM ScheduleTask 들을 SnapTask 로 deepcopy 하여 Snap 생성.

    주의: Task 4 시점에서는 ORM 통합을 아직 하지 않음 — Task 10 에서 완성.
    현재는 interface stub 으로 동작만 검증 가능하게 둔다.
    """
    by_id: dict[str, SnapTask] = {}
    for t in tasks:
        batch = getattr(t, "batch", None)
        by_id[t.task_id] = SnapTask(
            task_id=t.task_id,
            equipment_code=t.equipment_code,
            start=t.start_datetime,
            end=t.end_datetime,
            batch_id=t.batch_id,
            sales_order_id=getattr(batch, "sales_order_id", None),
            sales_order_line=getattr(batch, "sales_order_line", None),
            due_date=getattr(batch, "due_date", None),
        )
    return Snap(by_id=by_id)
