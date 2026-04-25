"""스케줄링 cross-cutting DB 헬퍼.

solver / greedy 양쪽이 공통으로 호출하는 DB 정리 작업을 모은다 — 현재는
ScheduleTask 안전 삭제 한 가지지만, 향후 batch 상태 reset 등 비슷한 헬퍼가
추가되면 같은 모듈에 둔다.

기존 위치: app.application.scheduling.cp_sat.orchestrator (Week 3 Task 3A.1 이전 분리됨,
Phase 1 step 3 에서 application/_shared/ 로 이동).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.infrastructure.models.schedule_task import ScheduleTask

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _delete_task_safely(db: "Session", task: ScheduleTask) -> int:
    """ScheduleTask 삭제 전에 참조 FK 들을 안전하게 해제한다.

    왜: 동일 트랜잭션에서
      - `auto_assign` audit_log 가 task_id 로 이 행을 참조 (audit_log_task_id_fkey)
      - 다른 schedule_task 의 predecessor_task_id 가 이 행을 참조 (self-FK)
    둘 다 살아있으면 db.delete(task) 가 ForeignKeyViolation 으로 실패한다.
    audit 이력은 run_label/batch_id 로 추적 가능하므로 task_id 만 NULL 로 끊는다.
    predecessor 체인은 단방향 공정 순서이므로 NULL 허용 (선행 미상으로 표시).

    Returns:
        삭제된 task.task_id — 호출부에서 in-memory map(예: predecessor_map) 정리용.
    """
    from app.infrastructure.models.audit_log import AuditLog

    deleted_id = task.task_id
    db.query(AuditLog).filter(AuditLog.task_id == deleted_id).update(
        {"task_id": None}, synchronize_session=False
    )
    db.query(ScheduleTask).filter(
        ScheduleTask.predecessor_task_id == deleted_id
    ).update({"predecessor_task_id": None}, synchronize_session=False)
    db.delete(task)
    return deleted_id
