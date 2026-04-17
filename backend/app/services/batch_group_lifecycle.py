"""batch_group 단위 미배정/복원 서비스 (Task 2.1).

Eng Critical #1: 이 모듈의 함수는 절대 db.commit()을 호출하지 않는다.
호출자(라우트, Task 2.3)가 단일 트랜잭션 내에서 audit_log INSERT 후 commit한다.
→ 서비스가 commit하면 audit_log 쓰기가 별도 트랜잭션이 되어 원자성이 깨진다.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


# status 리터럴 — 신규 spec 기준. schedule_task 레거시 default('scheduled')와 병존하나
# 본 모듈은 미배정 전이에만 관여하므로 unassigned 로의 단방향 세팅만 다룬다.
TaskStatus = Literal["planned", "in_progress", "completed", "unassigned"]

# 미배정 사유 — VARCHAR(32). '기타'가 기본값.
UnassignReason = Literal["자재지연", "설비고장", "납기재협상", "기타"]

VALID_REASONS: frozenset[str] = frozenset(
    ["자재지연", "설비고장", "납기재협상", "기타"]
)

# 기본 reason — spec에 따라 reason 누락 시 '기타'로 저장 (DB 레거시 NULL과 구분)
_DEFAULT_REASON: str = "기타"


class BatchGroupStatusError(ValueError):
    """planned 외 상태가 포함된 batch_group에 대한 미배정 시도."""


class BatchGroupNotFoundError(LookupError):
    """요청한 batch_group이 DB에 없음."""


class BatchGroupWipMatchedError(ValueError):
    """WIP 매칭된 batch_group — 미배정 차단 (Spec Q7).

    왜: WIP 반제품이 이미 할당된 배치를 미배정하면 재고 상태 불일치.
    """


class BatchGroupReasonError(ValueError):
    """유효하지 않은 unassign reason (허용 목록 외 값)."""


def unassign_batch_group(
    db: Session,
    batch_group: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """batch_group에 속한 모든 batch/task를 status='unassigned'로 soft-delete.

    계약:
        - flush만 수행, commit은 호출자 책임 (Eng Critical #1).
        - 시간·설비 등 복원에 필요한 메타데이터는 유지 (status만 전이).
        - 모든 batch가 이미 unassigned면 no-op — 원래 reason 유지 (멱등).

    Args:
        db: SQLAlchemy Session. 호출자가 commit/rollback 주도.
        batch_group: 대상 batch_group 식별자.
        reason: 미배정 사유. None이면 '기타'로 저장. 허용 목록 외 값은 예외.

    Returns:
        {
            "batch_group": str,
            "affected_batches": list[int],  # batch_id
            "affected_tasks": list[int],    # task_id
            "reason": str,                  # 저장된 사유
            "idempotent": bool,             # 이미 unassigned였나
        }

    Raises:
        BatchGroupReasonError: reason이 허용 목록 외 값.
        BatchGroupNotFoundError: batch_group이 DB에 없음.
        BatchGroupStatusError: planned 외 상태가 포함됨.
        BatchGroupWipMatchedError: WIP 매칭된 배치가 포함됨.
    """
    # Fail-fast: reason 검증은 DB 조회 전에 (쓸데없는 쿼리 방지)
    effective_reason = reason if reason is not None else _DEFAULT_REASON
    if effective_reason not in VALID_REASONS:
        raise BatchGroupReasonError(
            f"유효하지 않은 reason '{reason}'. 허용: {sorted(VALID_REASONS)}"
        )

    batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == batch_group)
        .with_for_update()
        .all()
    )
    if not batches:
        raise BatchGroupNotFoundError(f"batch_group '{batch_group}' 없음")

    # 멱등: 모두 unassigned면 no-op. 원래 저장된 reason을 돌려줘야 UI가 일관되게 표시.
    if all(b.status == "unassigned" for b in batches):
        return {
            "batch_group": batch_group,
            "affected_batches": [],
            "affected_tasks": [],
            "reason": batches[0].unassign_reason,
            "idempotent": True,
        }

    # 상태 검증 — planned만 허용 (in_progress/completed는 소급 미배정 불가)
    invalid = [b for b in batches if b.status != "planned"]
    if invalid:
        raise BatchGroupStatusError(
            f"planned 외 상태 포함: {[(b.batch_id, b.status) for b in invalid]}"
        )

    # WIP 매칭 검증 (Spec Q7) — 재고 상태 무결성 유지
    if any(b.wip_matched_id is not None for b in batches):
        raise BatchGroupWipMatchedError(
            f"WIP 매칭된 batch_group '{batch_group}'은 미배정할 수 없습니다"
        )

    batch_ids = [b.batch_id for b in batches]
    tasks = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.batch_id.in_(batch_ids))
        .with_for_update()
        .all()
    )

    # 상태 전이: 시간/설비 필드는 보존 — 복원(Task 2.2) 시 재사용.
    for t in tasks:
        t.status = "unassigned"
    for b in batches:
        b.status = "unassigned"
        b.unassign_reason = effective_reason

    db.flush()  # commit 아님 — 호출자(라우트)가 audit_log 쓰고 commit

    return {
        "batch_group": batch_group,
        "affected_batches": batch_ids,
        "affected_tasks": [t.task_id for t in tasks],
        "reason": effective_reason,
        "idempotent": False,
    }
