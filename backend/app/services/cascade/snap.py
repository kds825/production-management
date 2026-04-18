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
    """ORM ScheduleTask 들을 SnapTask 로 복사하여 Snap 생성.

    현재 (Task 4) 는 interface 만 선언. 실구현과 대응 test 는 Task 10 service.py 통합 단계에서
    DB fixture 와 함께 추가 예정.
    """
    raise NotImplementedError("build_snapshot: Task 10 DB 통합에서 구현 예정")
