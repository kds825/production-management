"""schedules/bulk_update.py — bulk update v1/v2 엔드포인트.

핸들러:
- PATCH /tasks/bulk-update-legacy   → bulk_update_tasks_legacy (v1)
- POST /tasks/bulk-update           → bulk_update_v2 (v2)

v1 은 단순 일괄 시간 갱신, v2 는 Snap 기반 재검증 + ScheduleChangeSet 저장
(Task 14 revert 의 Undo 앵커).

`__init__.py` 가 본 모듈 router 를 prefix="/schedules" 패키지 router 에
include 한다.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.feature_flags import is_cascade_v2_enabled
from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import (
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.schedule_task import (
    ScheduleTask as ScheduleTaskModel,
)
from app.observability.cascade_logging import log_cascade_request
from app.presentation.routes.schedules._shared import _parse_task_id
from app.presentation.schemas.cascade import (
    BulkUpdateErrorCode,
    BulkUpdateRequestV2,
    BulkUpdateSuccess,
)
from app.application.cascade.snap import build_snapshot
from app.application.validation.schedule_validators import (
    find_due_date_violation,
    find_predecessor_violation,
    find_same_eq_overlap,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Bulk Update v1 — Pydantic 스키마 (legacy)
# ---------------------------------------------------------------------------


class BulkTaskUpdate(BaseModel):
    task_id: str
    new_start: datetime
    new_end: datetime


class BulkUpdateRequest(BaseModel):
    updates: list[BulkTaskUpdate]


# ---------------------------------------------------------------------------
# v2 내부 헬퍼
# ---------------------------------------------------------------------------


def _task_serializable(snap_task) -> dict:
    """SnapTask 를 JSON-safe dict 로 변환 — snapshot_before/after 저장 포맷."""
    return {
        # naive datetime → ISO 문자열. revert 시 fromisoformat 으로 복원.
        "start": snap_task.start.isoformat(),
        "end": snap_task.end.isoformat(),
        "equipment_code": snap_task.equipment_code,
    }


def _coerce_task_id(raw: str):
    """문자열 task_id 를 DB PK 타입에 맞춰 변환.

    ScheduleTask.task_id 는 Integer 지만 snap 은 str 로 비교한다. 프론트가
    'TASK-123' 또는 '123' 을 보낼 수 있어 prefix 제거 후 int 캐스팅.
    """
    prefix = "TASK-"
    core = raw[len(prefix) :] if raw.startswith(prefix) else raw
    if core.isdigit():
        return int(core)
    return core


def _feature_disabled_error(first_task_id: str) -> HTTPException:
    """FEATURE_FLAG off + multi-change 조합에 쓰는 422 생성기 — 테스트도 이 경로 공유."""
    return HTTPException(
        status_code=422,
        detail={
            "error_code": BulkUpdateErrorCode.FEATURE_DISABLED.value,
            "offending_task_id": first_task_id,
            "detail": "cascade v2 disabled (only single-task updates allowed)",
            "can_retry": False,
        },
    )


# ---------------------------------------------------------------------------
# Endpoint: PATCH /api/schedules/tasks/bulk-update-legacy (v1, legacy)
#
# v2 전환(2026-04-18 Task 13): 본 PATCH 는 legacy 프론트(scheduleStore.ts) 가
# `{ updates: [...] }` body 로 호출하는 기존 경로. 프론트 마이그레이션이 끝나면 제거.
# 새 `POST /tasks/bulk-update` 는 아래 v2 엔드포인트로 구현되어 있다.
# ---------------------------------------------------------------------------


@router.patch("/tasks/bulk-update-legacy")
def bulk_update_tasks_legacy(
    body: BulkUpdateRequest,
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """(legacy v1) 여러 태스크의 시작/종료 시간을 단일 트랜잭션으로 일괄 갱신.

    v2 와 달리 validator / change_set 저장이 없고 단순 적용만 한다.
    cascade-preview 결과를 프론트엔드에서 확정한 뒤 한 번에 반영할 때 사용.
    """
    if not body.updates:
        return {"updated": 0}

    for upd in body.updates:
        numeric_id = _parse_task_id(upd.task_id)
        task = (
            db.query(ScheduleTaskModel)
            .filter(ScheduleTaskModel.task_id == numeric_id)
            .first()
        )
        if task is None:
            raise HTTPException(
                status_code=404,
                detail=f"작업 '{upd.task_id}'를 찾을 수 없습니다.",
            )
        task.start_datetime = upd.new_start
        task.end_datetime = upd.new_end

    db.commit()
    return {"updated": len(body.updates)}


# ---------------------------------------------------------------------------
# Endpoint: POST /api/schedules/tasks/bulk-update (v2)
#
# Task 13: v2 계약.
#   - `FEATURE_FLAG_CASCADE_V2` off 시 single-task 변경만 허용 (legacy 호환),
#     multi-change 는 `FEATURE_DISABLED` 로 422.
#   - Snap 기반 재검증 3 종 (same-eq overlap → predecessor → due-date) 순서대로,
#     먼저 발견된 한 건만 error_code 로 422 반환.
#   - 성공 시 `schedule_change_sets` 에 snapshot_before/after INSERT → change_set_id 반환
#     (Task 14 revert 의 Undo 앵커).
# ---------------------------------------------------------------------------


@router.post("/tasks/bulk-update", response_model=BulkUpdateSuccess)
def bulk_update_v2(
    body: BulkUpdateRequestV2,
    db: Session = Depends(get_db),
) -> BulkUpdateSuccess:
    """v2 — cascade 적용 트랜잭션.

    실행 순서:
      1. FEATURE_FLAG off + multi-change 인 경우 422(`FEATURE_DISABLED`).
      2. DB 에서 ScheduleTask + ProductionBatch 를 읽어 Snap 구성.
      3. no-op change 필터링 (Snap.apply 가 no-op 을 reject 하므로).
      4. 각 change 를 snap 에 apply.
      5. 재검증 순서: same_eq overlap → predecessor → due_date.
         먼저 걸리는 위반을 422 로 반환 (error_code + offending_task_id + can_retry).
      6. ScheduleTask 갱신 + ScheduleChangeSet INSERT 를 단일 commit 으로 마감.

    실패 시 rollback (db 세션 레벨) — 위반은 422 이전에 DB 를 건드리지 않으므로 자연스러운
    no-commit 경로가 된다.
    """
    # Task 23: 구조화 로그 래핑 — 요청별 request_id 로 correlate. with-block 내부 raise
    # 는 log_cascade_request 가 status=error 로 마감하며 그대로 전파한다.
    request_id = str(uuid.uuid4())
    with log_cascade_request(
        request_id,
        "/tasks/bulk-update",
        n_changes=len(body.changes),
        expected_cascade_request_id=body.expected_cascade_request_id,
    ) as extra:
        # ---- 1) Feature flag 가드 ----------------------------------------------
        if not body.changes:
            # 빈 요청은 no-op 성공 — change_set 생성은 skip (INSERT 빈 snapshot 낭비 방지).
            extra["result"] = "noop_empty"
            return BulkUpdateSuccess(change_set_id="", updated_task_ids=[])
        if not is_cascade_v2_enabled() and len(body.changes) > 1:
            extra["feature_disabled"] = True
            raise _feature_disabled_error(body.changes[0].task_id)

        # ---- 2) Snap 구성 --------------------------------------------------------
        # plan_cascade_preview 와 동일한 duck-typed 결합 뷰를 사용 — build_snapshot 이
        # batch relationship 을 기대하기 때문. N+1 방지 위해 batch 를 사전 조회.
        tasks = db.query(ScheduleTaskModel).all()
        batch_ids = {t.batch_id for t in tasks if t.batch_id is not None}
        batches_by_id = (
            {
                b.batch_id: b
                for b in db.query(ProductionBatchModel)
                .filter(ProductionBatchModel.batch_id.in_(batch_ids))
                .all()
            }
            if batch_ids
            else {}
        )

        class _TaskView:
            __slots__ = (
                "task_id",
                "equipment_code",
                "start_datetime",
                "end_datetime",
                "batch_id",
                "batch",
            )

            def __init__(self, t, batch):
                # task_id 를 str 화 — Snap 이 dict key 로 str 비교를 가정.
                self.task_id = str(t.task_id)
                self.equipment_code = t.equipment_code
                self.start_datetime = t.start_datetime
                self.end_datetime = t.end_datetime
                self.batch_id = t.batch_id
                self.batch = batch

        views = [_TaskView(t, batches_by_id.get(t.batch_id)) for t in tasks]
        snap = build_snapshot(views)

        # ---- 3) 변경 대상 존재 확인 + no-op 필터 -------------------------------
        # 왜 no-op 필터: Snap.apply 가 no-op 호출을 ValueError 로 거부 (원본 보존 규칙).
        # 프론트가 cascade-preview 결과를 그대로 재전송했을 때 변경 없는 항목이 섞여 있을 수
        # 있으므로, 서버 측에서 관대하게 걸러낸다.
        real_changes = []
        missing = [c.task_id for c in body.changes if c.task_id not in snap.by_id]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"task not found: {missing[0]}",
            )
        for c in body.changes:
            cur = snap.by_id[c.task_id]
            same_time = cur.start == c.new_start and cur.end == c.new_end
            same_eq = c.new_equipment_code is None or (
                c.new_equipment_code == cur.equipment_code
            )
            if same_time and same_eq:
                continue  # no-op
            real_changes.append(c)

        # snapshot_before 는 "실제 변경 대상" 만 캡처 — revert 시 되돌릴 대상과 일치시킴.
        snapshot_before = {
            c.task_id: _task_serializable(snap.by_id[c.task_id]) for c in real_changes
        }

        # ---- 4) Apply --------------------------------------------------------------
        for c in real_changes:
            snap.apply(c.task_id, c.new_start, c.new_end, c.new_equipment_code)

        # ---- 5) 재검증 ------------------------------------------------------------
        # 순서 중요: same-eq overlap 이 가장 치명적 (설비 이중 점유). predecessor > due_date
        # 순으로 얕은 위반부터 탐지하는 구조.
        for check_fn, code in [
            (
                find_same_eq_overlap,
                BulkUpdateErrorCode.VALIDATION_OVERLAP_SAME_EQUIPMENT,
            ),
            (
                find_predecessor_violation,
                BulkUpdateErrorCode.VALIDATION_PREDECESSOR_VIOLATION,
            ),
            (
                find_due_date_violation,
                BulkUpdateErrorCode.VALIDATION_DUE_DATE_VIOLATION,
            ),
        ]:
            offending = check_fn(snap)
            if offending is not None:
                extra["validation_error"] = code.value
                extra["offending_task_id"] = str(offending.task_id)
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error_code": code.value,
                        "offending_task_id": str(offending.task_id),
                        "detail": (
                            f"{code.value}: end={offending.end.isoformat()} "
                            f"equipment={offending.equipment_code}"
                        ),
                        # can_retry=True — preview 를 다시 돌리면 해소 가능할 수 있음.
                        "can_retry": True,
                    },
                )

        # ---- 6) Commit -----------------------------------------------------------
        snapshot_after = {
            c.task_id: _task_serializable(snap.by_id[c.task_id]) for c in real_changes
        }
        change_set_id = str(uuid.uuid4())
        updated_ids: list[str] = []

        for c in real_changes:
            task_pk = _coerce_task_id(c.task_id)
            t = db.get(ScheduleTaskModel, task_pk)
            if t is None:
                # snap 에 있었지만 DB 조회에서 빠진 경우 — 극히 드물지만 race 가드.
                continue
            t.start_datetime = c.new_start
            t.end_datetime = c.new_end
            if c.new_equipment_code:
                t.equipment_code = c.new_equipment_code
            updated_ids.append(c.task_id)

        if real_changes:
            cs = ScheduleChangeSet(
                change_set_id=change_set_id,
                preview_request_id=body.expected_cascade_request_id,
                snapshot_before=snapshot_before,
                snapshot_after=snapshot_after,
            )
            db.add(cs)
        db.commit()

        # 관측 필드: 실제 반영된 변경 수 + change_set_id (undo 상관관계).
        extra["real_changes_n"] = len(real_changes)
        extra["updated_task_ids_n"] = len(updated_ids)
        extra["change_set_id"] = change_set_id if real_changes else ""

        # real_changes 가 없으면 change_set_id 는 빈 문자열 (클라이언트는 undo 대상 없음으로 해석).
        return BulkUpdateSuccess(
            change_set_id=change_set_id if real_changes else "",
            updated_task_ids=updated_ids,
        )
