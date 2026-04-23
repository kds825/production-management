"""audit_log.task_id FK 에 ON DELETE CASCADE 추가

Revision ID: f5d8a1c09e21
Revises: e4f7a9c21b30
Create Date: 2026-04-20 15:00:00.000000

왜 CASCADE 가 필요한가:
  schedule_optimizer._purge_run_tasks 가 재시도마다 실행하는 3단계 쿼리
  (DELETE AuditLog → DELETE ScheduleTask → UPDATE ProductionBatch) 중
  첫 두 단계는 FK 무결성 때문에 순서가 강제되어 왔다. audit_log.task_id
  가 schedule_task.task_id 를 참조하지만 CASCADE 옵션이 없어, ScheduleTask
  를 먼저 지우면 FK violation 이 발생한다.

  ON DELETE CASCADE 를 추가하면:
    - ScheduleTask DELETE 시 관련 AuditLog 행도 자동 삭제
    - _purge_run_tasks 의 첫 단계 AuditLog DELETE 를 생략 가능
    - 원격 Supabase 왕복 1회 절감 × 재시도 횟수 → Stage2 자동배열 수 초 단축

호환성:
  기존 audit_log 행 중 orphan (이미 삭제된 task 참조) 은 없다고 가정.
  만약 있으면 CASCADE 추가 자체는 기존 데이터에 영향을 주지 않으며,
  이후 ScheduleTask 삭제 시점에 orphan 정리 기회를 얻는다.

downgrade:
  FK 를 drop 하고 CASCADE 없이 재생성. 기존 _purge_run_tasks 3단계 경로
  와 호환.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f5d8a1c09e21"
down_revision: Union[str, Sequence[str], None] = "e4f7a9c21b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FK_NAME = "audit_log_task_id_fkey"
_TABLE = "audit_log"
_LOCAL_COL = "task_id"
_REF_TABLE = "schedule_task"
_REF_COL = "task_id"


def upgrade() -> None:
    """audit_log.task_id FK 를 ON DELETE CASCADE 로 재생성."""
    op.drop_constraint(_FK_NAME, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _FK_NAME,
        _TABLE,
        _REF_TABLE,
        [_LOCAL_COL],
        [_REF_COL],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """CASCADE 없이 기본 FK 복원."""
    op.drop_constraint(_FK_NAME, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _FK_NAME,
        _TABLE,
        _REF_TABLE,
        [_LOCAL_COL],
        [_REF_COL],
    )
