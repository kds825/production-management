"""
인메모리 데이터 스토어 — PoC 단계에서 DB 없이 seed_data를 메모리에 로드
단일 인스턴스 패턴으로 애플리케이션 수명 동안 상태 유지
"""

import copy
import uuid

from app.domain.entities import Equipment, Order, ProcessRoute, ScheduleTask
from seed_data import (
    EQUIPMENT,
    LINE_SPEEDS,
    PROCESS_ROUTES,
    SAMPLE_ORDERS,
    SAMPLE_TASKS,
)


class MemoryStore:
    """모든 도메인 객체의 인메모리 저장소"""

    def __init__(self) -> None:
        # 설비 — 변경 없는 마스터 데이터
        self._equipment: dict[str, Equipment] = {eq.id: eq for eq in EQUIPMENT}

        # 수주 — 수정 가능 (is_scheduled 플래그)
        self._orders: dict[str, Order] = {o.id: copy.deepcopy(o) for o in SAMPLE_ORDERS}

        # 공정 경로 — 변경 없는 마스터 데이터
        self._routes: dict[str, ProcessRoute] = {r.id: r for r in PROCESS_ROUTES}

        # 선속도 테이블
        self._line_speeds: dict[str, dict[str, float]] = LINE_SPEEDS

        # 스케줄 작업 — CRUD 대상
        self._tasks: dict[str, ScheduleTask] = {
            t.id: copy.deepcopy(t) for t in SAMPLE_TASKS
        }

    # ── Equipment ────────────────────────────────────────────────────────────

    def list_equipment(self) -> list[Equipment]:
        return list(self._equipment.values())

    def get_equipment(self, equipment_id: str) -> Equipment | None:
        return self._equipment.get(equipment_id)

    # ── Orders ───────────────────────────────────────────────────────────────

    def list_orders(self) -> list[Order]:
        return list(self._orders.values())

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def mark_order_scheduled(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order:
            order.is_scheduled = True
            return True
        return False

    # ── Process Routes ───────────────────────────────────────────────────────

    def list_routes(self) -> list[ProcessRoute]:
        return list(self._routes.values())

    def get_route(self, route_id: str) -> ProcessRoute | None:
        return self._routes.get(route_id)

    # ── Line Speeds ──────────────────────────────────────────────────────────

    def get_line_speeds(self) -> dict[str, dict[str, float]]:
        return self._line_speeds

    # ── Schedule Tasks ───────────────────────────────────────────────────────

    def list_tasks(self) -> list[ScheduleTask]:
        """시작 시간 오름차순 정렬로 반환"""
        return sorted(self._tasks.values(), key=lambda t: t.start)

    def get_task(self, task_id: str) -> ScheduleTask | None:
        return self._tasks.get(task_id)

    def create_task(self, task: ScheduleTask) -> ScheduleTask:
        """새 작업 저장 — ID 자동 생성"""
        if not task.id:
            task.id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        self._tasks[task.id] = task
        return task

    def update_task(self, task_id: str, updates: dict) -> ScheduleTask | None:
        """필드 부분 업데이트 — None 값은 무시"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        for field, value in updates.items():
            if value is not None and hasattr(task, field):
                setattr(task, field, value)
        return task

    def delete_task(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False

    def tasks_for_equipment(self, equipment_id: str) -> list[ScheduleTask]:
        """특정 설비에 배정된 작업 목록"""
        return [t for t in self._tasks.values() if t.equipment_id == equipment_id]


# 애플리케이션 싱글턴 스토어
store = MemoryStore()
