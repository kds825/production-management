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

# 미배정 가능한 배치 상태 — 스케줄러가 'scheduled'를 default로 쓰던 레거시와
# neutral spec의 'planned'를 모두 허용. 의미상 둘 다 "아직 시작 안 함"이므로
# 미배정 가능. 향후 스케줄러 vocabulary 통일 시 'scheduled' 제거로 되돌림.
UNASSIGNABLE_STATUSES: tuple[str, ...] = ("planned", "scheduled")

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

    # 상태 검증 — planned/scheduled 허용 (scheduled는 스케줄러 레거시 default;
    # 의미상 동일하게 "아직 시작 안 함"이므로 둘 다 미배정 가능).
    # in_progress/completed는 진행중/완료이므로 소급 미배정 불가.
    invalid = [b for b in batches if b.status not in UNASSIGNABLE_STATUSES]
    if invalid:
        raise BatchGroupStatusError(
            f"planned/scheduled 외 상태 포함: "
            f"{[(b.batch_id, b.status) for b in invalid]}"
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


from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class TaskPosition:
    """restore-at 계산 결과의 단일 task 위치 제안."""

    task_id: int
    batch_id: int
    process_name: str
    new_equipment_code: str
    new_start: datetime
    new_end: datetime
    is_anchor: bool


@dataclass
class RestoreAtPlanResult:
    """compute_restore_at_plan 반환 — no mutation, preview 전용.

    cascade-preview-v2 응답 shape와 호환 (pushes/pulls/unresolved/request_id).
    """

    batch_group: str
    task_positions: list[TaskPosition]
    pushes: list[dict] = field(default_factory=list)
    pulls: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    request_id: str = ""
    can_auto_resolve: bool = True
    iter_count: int = 0
    truncated: bool = False


def compute_restore_at_plan(
    db: Session,
    batch_group: str,
    anchor_equipment_code: str,
    anchor_start: datetime,
) -> RestoreAtPlanResult:
    """unassigned batch_group 을 앵커 기준으로 재배치하는 preview 계산 (no mutation).

    - 모든 task가 status='unassigned' 이어야 함 (아니면 BatchGroupStatusError).
    - anchor task = batch_seq가 가장 작은 ProductionBatch 의 ScheduleTask.
    - Anchor는 anchor_equipment_code + anchor_start 로 배치, duration 보존.
    - Downstream: batch_seq 순회, 직전 task new_end를 start로 (기존 equipment_code 유지).
    - snap에 모든 task (unassigned 포함) 를 넣고 우리 batch_group task들에만 apply.
      CRITICAL: snap.apply()는 no-op(값 변경 없음) 시 ValueError raise — 변경 시에만 호출.
    - cascade-preview-on-snap으로 다른 planned task 와의 충돌 pushes/pulls/unresolved 수집.
    - DB mutation 없음 (preview only).

    Raises:
        BatchGroupNotFoundError: batch_group 이 DB 에 없음.
        BatchGroupStatusError: unassigned 외 상태가 혼재.
    """
    import uuid

    from app.services.cascade.snap import build_snapshot
    from app.services.cascade.service import plan_cascade_preview_on_snap
    from app.services.calendar_engine import (
        reverse_advance as _calendar_reverse_advance,
    )

    batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == batch_group)
        .order_by(ProductionBatch.batch_seq)
        .all()
    )
    if not batches:
        raise BatchGroupNotFoundError(f"batch_group '{batch_group}' 없음")
    if not all(b.status == "unassigned" for b in batches):
        raise BatchGroupStatusError(
            f"unassigned 외 상태 포함: {[(b.batch_id, b.status) for b in batches]}"
        )

    batch_ids = [b.batch_id for b in batches]
    tasks = db.query(ScheduleTask).filter(ScheduleTask.batch_id.in_(batch_ids)).all()
    batch_by_id = {b.batch_id: b for b in batches}
    # batch_seq → ScheduleTask 매핑 (anchor = 가장 작은 batch_seq)
    task_by_batch_seq: dict[int, ScheduleTask] = {}
    for t in tasks:
        b = batch_by_id.get(t.batch_id)
        if b is None:
            continue
        task_by_batch_seq[b.batch_seq] = t

    if not task_by_batch_seq:
        return RestoreAtPlanResult(
            batch_group=batch_group,
            task_positions=[],
            request_id=str(uuid.uuid4()),
        )

    # 1) anchor + downstream 위치 계산 (pure in-memory — no DB write)
    ordered_seqs = sorted(task_by_batch_seq.keys())
    positions: list[TaskPosition] = []
    last_end = anchor_start
    for idx, seq in enumerate(ordered_seqs):
        t = task_by_batch_seq[seq]
        duration = t.end_datetime - t.start_datetime
        if idx == 0:
            # anchor: 지정 설비 + 지정 시작 시각
            new_equip = anchor_equipment_code
            new_start = anchor_start
        else:
            # downstream: 기존 equipment_code 유지, 직전 task new_end 이후
            # TODO(v2b): working-time advance 시 calendar_engine.advance 주입.
            new_equip = t.equipment_code
            new_start = last_end
        new_end = new_start + duration
        positions.append(
            TaskPosition(
                task_id=t.task_id,
                batch_id=t.batch_id,
                process_name=batch_by_id[t.batch_id].process_name or "",
                new_equipment_code=new_equip,
                new_start=new_start,
                new_end=new_end,
                is_anchor=(idx == 0),
            )
        )
        last_end = new_end

    # 2) cascade-preview-on-snap — snap에는 모든 task (unassigned 포함) 가 들어감
    # ScheduleTask.task_id 는 int (ORM Integer) — snap 키도 int 그대로 사용.
    schedule_tasks = db.query(ScheduleTask).all()
    batch_ids_all = {t.batch_id for t in schedule_tasks if t.batch_id is not None}
    batches_all = {
        b.batch_id: b
        for b in db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id.in_(batch_ids_all))
        .all()
    }

    class _TaskView:
        """duck-typed ScheduleTask + batch 결합 뷰 — build_snapshot 계약을 충족."""

        __slots__ = (
            "task_id",
            "equipment_code",
            "start_datetime",
            "end_datetime",
            "batch_id",
            "batch",
        )

        def __init__(self, t, batch):
            self.task_id = t.task_id
            self.equipment_code = t.equipment_code
            self.start_datetime = t.start_datetime
            self.end_datetime = t.end_datetime
            self.batch_id = t.batch_id
            self.batch = batch

    views = [_TaskView(t, batches_all.get(t.batch_id)) for t in schedule_tasks]
    snap = build_snapshot(views)

    # apply — snap.apply()는 no-op(값 변경 없음) 시 ValueError raise.
    # 따라서 실제 변경이 있는 경우에만 호출 (예: anchor가 이미 그 자리에 있으면 skip).
    # snap 키는 ORM int task_id와 동일 타입으로 접근.
    for pos in positions:
        t = task_by_batch_seq[batch_by_id[pos.batch_id].batch_seq]
        snap_task = snap.by_id[t.task_id]
        if (
            snap_task.start != pos.new_start
            or snap_task.end != pos.new_end
            or snap_task.equipment_code != pos.new_equipment_code
        ):
            snap.apply(
                t.task_id,
                pos.new_start,
                pos.new_end,
                pos.new_equipment_code,
            )

    horizon_end = max(
        (tv.end for tv in snap.by_id.values()),
        default=anchor_start + timedelta(days=30),
    ) + timedelta(days=7)

    anchor_pos = positions[0]
    anchor_task = task_by_batch_seq[ordered_seqs[0]]
    cascade_result = plan_cascade_preview_on_snap(
        snap=snap,
        changed_task_id=anchor_task.task_id,  # int — consistent with snap keys
        advance_fn=lambda dt, *_a, **_k: dt,  # identity — Task 1.3 에서 재검토
        reverse_advance_fn=_calendar_reverse_advance,
        horizon_end=horizon_end,
    )

    return RestoreAtPlanResult(
        batch_group=batch_group,
        task_positions=positions,
        pushes=cascade_result.pushes,
        pulls=cascade_result.pulls,
        unresolved=cascade_result.unresolved,
        request_id=cascade_result.request_id,
        can_auto_resolve=cascade_result.can_auto_resolve,
        iter_count=cascade_result.iter_count,
        truncated=cascade_result.truncated,
    )


def restore_batch_group(db: Session, batch_group: str) -> dict[str, Any]:
    """unassigned batch_group을 원래 자리로 복원 (status flip).

    계약 (Eng Critical #2):
        - status만 전이: unassigned → planned.
        - equipment_code / start_datetime / end_datetime 은 그대로 — unassign 시
          보존했으므로 재계산 불필요.
        - unassign_reason 초기화 (None).
        - 원래 자리에 이미 다른 planned task가 있으면 conflicts 반환 + 무변경.
        - 멱등: 이미 planned면 no-op.
        - flush만, commit은 호출자.

    Returns:
        {
            "batch_group": str,
            "restored_tasks": list[int],   # 성공 시 task_id 배열
            "conflicts": list[dict],       # 점유 충돌 상세
            "idempotent": bool,
        }

    Raises:
        BatchGroupNotFoundError: batch_group 없음.
        BatchGroupStatusError: unassigned 외 상태가 혼재.
    """
    batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == batch_group)
        .with_for_update()
        .all()
    )
    if not batches:
        raise BatchGroupNotFoundError(f"batch_group '{batch_group}' 없음")

    # 멱등: 이미 모두 planned면 no-op
    if all(b.status == "planned" for b in batches):
        return {
            "batch_group": batch_group,
            "restored_tasks": [],
            "conflicts": [],
            "idempotent": True,
        }

    # 모두 unassigned여야 복원 가능
    if not all(b.status == "unassigned" for b in batches):
        raise BatchGroupStatusError(
            f"unassigned 외 상태 포함: {[(b.batch_id, b.status) for b in batches]}"
        )

    batch_ids = [b.batch_id for b in batches]
    tasks = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.batch_id.in_(batch_ids))
        .with_for_update()
        .all()
    )

    # 원래 자리 점유 검사 — N+1이나 v1 트래픽상 수용 (Eng Critical #4는 v2 이관)
    # 자기 자신(task_id 동일)은 제외. 다른 planned task와 시간 겹치면 conflict.
    conflicts: list[dict[str, Any]] = []
    for t in tasks:
        overlap = (
            db.query(ScheduleTask)
            .filter(
                ScheduleTask.equipment_code == t.equipment_code,
                ScheduleTask.status == "planned",
                ScheduleTask.start_datetime < t.end_datetime,
                ScheduleTask.end_datetime > t.start_datetime,
                ScheduleTask.task_id != t.task_id,
            )
            .all()
        )
        if overlap:
            conflicts.append(
                {
                    "task_id": t.task_id,
                    "equipment_code": t.equipment_code,
                    "start": t.start_datetime.isoformat() if t.start_datetime else None,
                    "end": t.end_datetime.isoformat() if t.end_datetime else None,
                    "overlap_with": [o.task_id for o in overlap],
                }
            )

    if conflicts:
        # 아무것도 건드리지 않고 반환. 호출자(라우트)가 409 매핑.
        return {
            "batch_group": batch_group,
            "restored_tasks": [],
            "conflicts": conflicts,
            "idempotent": False,
        }

    for t in tasks:
        t.status = "planned"
    for b in batches:
        b.status = "planned"
        b.unassign_reason = None

    db.flush()

    return {
        "batch_group": batch_group,
        "restored_tasks": [t.task_id for t in tasks],
        "conflicts": [],
        "idempotent": False,
    }
