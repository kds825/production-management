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
class TaskView:
    """build_snapshot 가 기대하는 최소 duck-type. ORM 엔티티를 래핑할 때 사용.

    task_id 는 str (snap 전역 계약). batch 는 ProductionBatch 또는 None.
    """

    task_id: str
    equipment_code: str
    start_datetime: datetime
    end_datetime: datetime
    batch_id: int | None
    batch: object | None  # ProductionBatch 혹은 None; duck-typed


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

    def apply(
        self,
        task_id: str,
        new_start: datetime,
        new_end: datetime,
        new_equipment_code: str | None = None,
    ) -> None:
        # NOTE: cascade BFS 는 snap 하나에 대해 단일 스레드 동기 실행을 전제.
        # 아래 None-guard 는 race 안전하지 않으므로 동시 호출 금지.
        t = self.by_id[task_id]
        # Fail-fast: cascade BFS 는 실제 변경이 있을 때만 apply 를 호출해야 함.
        # no-op 호출은 버그 — old_* 가 설정되기 전에 차단 (원본 보존 guard 앞).
        if (
            t.start == new_start
            and t.end == new_end
            and (new_equipment_code is None or new_equipment_code == t.equipment_code)
        ):
            raise ValueError(
                f"apply() called with no-op change on task_id={task_id}; "
                f"cascade BFS should only apply real changes."
            )
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
    """ORM `ScheduleTask` iterable 을 SnapTask 로 변환해 Snap 생성.

    `tasks` 는 SQLAlchemy query 결과 또는 동등한 duck-typed 객체. 각 항목은
    `task_id`, `equipment_code`, `start_datetime`, `end_datetime`, `batch_id`,
    `batch` (relationship: sales_order_id, sales_order_line, due_date) 를 노출해야 한다.
    `batch` 가 None 이거나 필드가 없으면 해당 값은 None 으로 세팅.

    NOTE: 현재 ORM `ScheduleTask` 는 `batch` SQLAlchemy relationship 을 선언하지 않아
    caller 가 ProductionBatch 를 사전 조회해 duck-typed 로 주입하거나, Task 10 DB wrapper
    (plan_cascade_preview) 에서 batch 를 lookup 하여 주입하는 방식을 사용한다.
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
            sales_order_id=getattr(batch, "sales_order_id", None) if batch else None,
            sales_order_line=getattr(batch, "sales_order_line", None)
            if batch
            else None,
            due_date=getattr(batch, "due_date", None) if batch else None,
        )
    return Snap(by_id=by_id)
