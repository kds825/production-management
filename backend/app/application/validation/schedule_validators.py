"""Bulk-update 재검증 유틸 — cascade-preview 와 commit 사이 race 방어.

Task 13: bulk-update v2 가 preview 와 동일한 BFS/validator 로직을 재실행하지 않더라도
최소한의 "위반 여부" 만 즉시 검출하도록 snap 기반 3 종 검사를 제공한다.

각 함수는 snap.changed_tasks() — 즉 apply() 가 호출된 task 만 — 을 대상으로 하여,
불필요한 전체 스캔을 피하고 preview 에서 이미 검증된 미변경 task 는 건드리지 않는다.

반환 규약:
- 위반이 여러 건이라도 "먼저 발견된 한 건" 만 반환 (호출자가 하나의 error_code 로
  422 응답을 구성하는 단순한 흐름을 위함). 여러 건 누적이 필요해지면 → list 반환형으로
  확장하되, 호출자 트리거 매핑도 같이 바꿔야 함.
- 없으면 None.
"""

from app.application.cascade.bfs import same_equipment_overlapping
from app.application.cascade.snap import Snap, SnapTask


def find_same_eq_overlap(snap: Snap) -> SnapTask | None:
    """Apply 된 task 중 같은 설비 내 겹침 발견 시 해당 task 반환.

    preview → commit 사이에 다른 요청이 같은 설비에 task 를 배치했을 수 있음.
    snap 자체는 최신 DB 스냅샷으로 구성되므로 해당 race 를 즉시 탐지.
    """
    for t in snap.changed_tasks():
        if same_equipment_overlapping(t, snap):
            return t
    return None


def find_due_date_violation(snap: Snap) -> SnapTask | None:
    """Apply 된 task 중 납기(due_date) 초과를 반환.

    due_date 가 None 이면 납기 미지정 → 위반 아님.
    """
    for t in snap.changed_tasks():
        if t.due_date is not None and t.end > t.due_date:
            return t
    return None


def find_predecessor_violation(snap: Snap) -> SnapTask | None:
    """같은 sales_order_line 에서 successor 가 predecessor.end 이전에 시작하면 위반.

    판정 규칙:
      apply 된 task t 를 successor 로 가정하고, 같은 SO-line 내 다른 task o 중
      `o.start < t.start and o.end > t.start` 인 경우 — 즉 o 는 t 보다 먼저 시작했는데
      t 시작 시점에도 아직 끝나지 않음 → 선행/후행 순서가 깨진 상태로 판정.

    주의:
    - sales_order_id / sales_order_line 이 None 이면 chain 추적 불가 → 스킵.
    - 엄격한 "o.end <= t.start" 가 아니라 "o.end > t.start" 로 하는 이유는 겹침 자체가
      선행 위반의 신호이므로 경계도 위반으로 간주. 엄격 등호(0분 공백) 는 허용.
    """
    for t in snap.changed_tasks():
        if t.sales_order_id is None or t.sales_order_line is None:
            continue
        for o in snap.by_id.values():
            if o.task_id == t.task_id:
                continue
            if (
                o.sales_order_id == t.sales_order_id
                and o.sales_order_line == t.sales_order_line
                and o.start < t.start
                and o.end > t.start
            ):
                return t
    return None
