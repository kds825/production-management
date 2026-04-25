"""schedules/cascade.py — cascade preview v1/v2 엔드포인트.

핸들러:
- POST /cascade-preview-legacy   → cascade_preview_legacy (v1)
- POST /cascade-preview          → cascade_preview_v2 (v2, 헤더 게이트)

`__init__.py` 가 본 모듈 router 를 prefix="/schedules" 패키지 router 에
include 한다.

`plan_cascade_preview` 호출 시 패키지(`app.presentation.routes.schedules`) 의
attribute 로 우회 참조하는 이유: 기존 테스트 (`tests/test_cascade_preview_v2.py`,
`tests/test_observability_metrics.py`) 가 `unittest.mock.patch(
"app.presentation.routes.schedules.plan_cascade_preview", ...)` 로 패치하기
때문에, 이 submodule 안에서 직접 `from app.application.cascade import
plan_cascade_preview` 후 호출하면 patch 가 적용되지 않는다. 호출 시점에
패키지 attribute 를 lookup 하면 monkey-patched 함수가 사용된다.
"""

import uuid
from collections import deque
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.feature_flags import is_cascade_v2_enabled
from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import (
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_task import (
    ScheduleTask as ScheduleTaskModel,
)
from app.observability.cascade_logging import log_cascade_request
from app.observability.metrics import (
    cascade_feature_flag_state,
    cascade_preview_duration_seconds,
    cascade_unresolved_total,
)
from app.presentation.routes.schedules._shared import _parse_task_id
from app.presentation.schemas.cascade import (
    CascadePreviewRequest as CascadePreviewRequestV2,
    CascadePreviewResponse as CascadePreviewResponseV2,
)
from app.application.cascade import UnresolvedReason
from app.services.schedule_optimizer import PREDECESSOR_PROCESS


router = APIRouter()


# ---------------------------------------------------------------------------
# 공정 간 선행/후행 관계 — cascade preview 에서 사용
# PREDECESSOR_PROCESS 는 schedule_optimizer 에서 import 한 단일 진실 공급원
# 역매핑: 선행 → [후행, ...] (예: "연선" → ["저압절연", "고압절연", "연합"])
# ---------------------------------------------------------------------------

_SUCCESSOR_PROCESSES: dict[str, list[str]] = {}
for _succ, _pred in PREDECESSOR_PROCESS.items():
    _SUCCESSOR_PROCESSES.setdefault(_pred, []).append(_succ)


def _collect_all_successors(process_name: str) -> set[str]:
    """BFS 로 process_name 의 모든 전이적(transitive) 후행 공정을 수집한다.

    예: "연선" → {"저압절연", "고압절연", "연합", "저압시스", "고압시스"}
    """
    visited: set[str] = set()
    queue: deque[str] = deque()
    queue.append(process_name)
    while queue:
        current = queue.popleft()
        for succ in _SUCCESSOR_PROCESSES.get(current, []):
            if succ not in visited:
                visited.add(succ)
                queue.append(succ)
    return visited


# ---------------------------------------------------------------------------
# Cascade Preview v1 — Pydantic 스키마 (legacy)
# ---------------------------------------------------------------------------


class CascadePreviewRequest(BaseModel):
    task_id: str
    new_start: datetime
    new_end: datetime


class AffectedTask(BaseModel):
    task_id: str
    old_start: datetime
    old_end: datetime
    new_start: datetime
    new_end: datetime
    process: str
    equipment: str
    reason: str


class CascadeConflict(BaseModel):
    task_id: str
    conflict_with: str
    equipment: str
    overlap_min: int
    resolution: str  # "push_forward"


class CascadePreviewResponse(BaseModel):
    affected_tasks: list[AffectedTask]
    conflicts: list[CascadeConflict]
    can_auto_resolve: bool


# ---------------------------------------------------------------------------
# v2 내부 헬퍼
# ---------------------------------------------------------------------------


def _reason_to_str(value) -> str:
    """Enum / str 혼재 reason 을 일관된 문자열로 직렬화 (프론트 파싱 단순화)."""
    # service 레이어는 PushReason/UnresolvedReason enum 을 사용하지만, 테스트/확장을
    # 위해 plain str 도 허용. 둘 다 .value 로 정규화.
    return value.value if hasattr(value, "value") else str(value)


def _push_to_schema(d: dict) -> dict:
    """service 의 push/pull dict → PushEntry 호환 dict 로 reason 정규화."""
    out = {**d}
    out["reason"] = _reason_to_str(d["reason"])
    return out


def _unres_to_schema(d: dict) -> dict:
    """service 의 unresolved dict → UnresolvedEntry 호환 dict 로 reason 정규화."""
    out = {**d}
    out["reason"] = _reason_to_str(d["reason"])
    return out


# ---------------------------------------------------------------------------
# Endpoint: POST /api/schedules/cascade-preview-legacy (v1, legacy)
#
# v2 전환(2026-04-18 Task 11): 본 엔드포인트는 `AffectedTask/CascadeConflict` 기반 v1
# 계약을 유지하는 레거시 경로. 신규 v2 (`/cascade-preview`) 는 Pydantic 스키마
# (`schemas.cascade`) + 헤더 가드 + FEATURE_FLAG 게이팅 을 적용. 프론트 마이그레이션이
# 완료되면 제거 대상.
# ---------------------------------------------------------------------------


@router.post("/cascade-preview-legacy", response_model=CascadePreviewResponse)
def cascade_preview_legacy(
    body: CascadePreviewRequest,
    db: Session = Depends(get_db),
) -> CascadePreviewResponse:
    """(legacy v1) 이동된 태스크의 후행 공정에 대한 연쇄(cascade) 변경 미리보기.

    같은 수주(sales_order_id)에 속하는 모든 전이적 후행 공정 태스크를 찾고,
    이동 delta 만큼 시간을 밀어낸 뒤 같은 설비의 다른 태스크와 겹침(충돌)을 감지한다.
    """
    numeric_id = _parse_task_id(body.task_id)

    # 1. 이동 대상 태스크 + 배치 조회
    row = (
        db.query(ScheduleTaskModel, ProductionBatchModel)
        .join(
            ProductionBatchModel,
            ScheduleTaskModel.batch_id == ProductionBatchModel.batch_id,
        )
        .filter(ScheduleTaskModel.task_id == numeric_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"작업 '{body.task_id}'를 찾을 수 없습니다.",
        )
    moved_task, moved_batch = row

    # 2. delta 계산
    delta: timedelta = body.new_start - moved_task.start_datetime

    # 3. 전이적 후행 공정 목록
    successor_processes = _collect_all_successors(moved_batch.process_name)
    if not successor_processes:
        return CascadePreviewResponse(
            affected_tasks=[],
            conflicts=[],
            can_auto_resolve=True,
        )

    # 4. 같은 수주의 후행 공정 태스크 조회
    successor_rows = (
        db.query(ScheduleTaskModel, ProductionBatchModel)
        .join(
            ProductionBatchModel,
            ScheduleTaskModel.batch_id == ProductionBatchModel.batch_id,
        )
        .filter(
            ProductionBatchModel.sales_order_id == moved_batch.sales_order_id,
            ProductionBatchModel.process_name.in_(successor_processes),
        )
        .all()
    )

    affected_tasks: list[AffectedTask] = []
    affected_ids: set[int] = set()  # 충돌 검사 시 제외용

    for stask, sbatch in successor_rows:
        new_start = stask.start_datetime + delta
        new_end = stask.end_datetime + delta
        affected_tasks.append(
            AffectedTask(
                task_id=f"TASK-{stask.task_id}",
                old_start=stask.start_datetime,
                old_end=stask.end_datetime,
                new_start=new_start,
                new_end=new_end,
                process=sbatch.process_name,
                equipment=stask.equipment_code,
                reason=f"선행공정 '{moved_batch.process_name}' 이동에 의한 cascade",
            )
        )
        affected_ids.add(stask.task_id)

    # 5. 충돌(overlap) 감지 — 각 affected task 의 새 시간대가 같은 설비의
    #    다른 태스크(이동 대상·영향 대상 제외)와 겹치는지 확인
    conflicts: list[CascadeConflict] = []

    # 설비별 기존 태스크를 사전 조회하여 N+1 방지
    equipment_codes = {at.equipment for at in affected_tasks}
    if equipment_codes:
        other_tasks = (
            db.query(ScheduleTaskModel)
            .filter(
                ScheduleTaskModel.equipment_code.in_(equipment_codes),
                ScheduleTaskModel.task_id != numeric_id,
                ~ScheduleTaskModel.task_id.in_(affected_ids) if affected_ids else True,
            )
            .order_by(ScheduleTaskModel.start_datetime)
            .all()
        )
    else:
        other_tasks = []

    # 설비별 인덱스 구축
    others_by_equip: dict[str, list[ScheduleTaskModel]] = {}
    for ot in other_tasks:
        others_by_equip.setdefault(ot.equipment_code, []).append(ot)

    for at in affected_tasks:
        for ot in others_by_equip.get(at.equipment, []):
            # 겹침 판정: A_start < B_end AND A_end > B_start
            if at.new_start < ot.end_datetime and at.new_end > ot.start_datetime:
                overlap_seconds = (
                    min(at.new_end, ot.end_datetime)
                    - max(at.new_start, ot.start_datetime)
                ).total_seconds()
                overlap_min = max(1, int(overlap_seconds / 60))
                conflicts.append(
                    CascadeConflict(
                        task_id=at.task_id,
                        conflict_with=f"TASK-{ot.task_id}",
                        equipment=at.equipment,
                        overlap_min=overlap_min,
                        resolution="push_forward",
                    )
                )

    # 모든 충돌이 push_forward 로 해소 가능하면 auto-resolve 허용
    can_auto_resolve = all(c.resolution == "push_forward" for c in conflicts)

    return CascadePreviewResponse(
        affected_tasks=affected_tasks,
        conflicts=conflicts,
        can_auto_resolve=can_auto_resolve,
    )


# ---------------------------------------------------------------------------
# Endpoint: POST /api/schedules/cascade-preview (v2)
#
# Task 11: v2 계약 — 헤더 게이트 + FEATURE_FLAG off 경로 + 새 Pydantic 스키마.
# - `X-Cascade-API-Version: 2` 헤더 필수 (400 otherwise).
# - `FEATURE_FLAG_CASCADE_V2` off 시 단일 `invalid_equipment` unresolved 로 응답
#   (프론트가 legacy 로 fallback 하거나 사용자에게 비활성화 메시지를 노출).
# - DB 통합 wrapper (Task 10 `plan_cascade_preview`) 를 그대로 호출.
# ---------------------------------------------------------------------------


@router.post("/cascade-preview", response_model=CascadePreviewResponseV2)
def cascade_preview_v2(
    body: CascadePreviewRequestV2,
    x_cascade_api_version: str | None = Header(None, alias="X-Cascade-API-Version"),
    db: Session = Depends(get_db),
) -> CascadePreviewResponseV2:
    """v2 cascade preview — 블록 duration 변경의 파급 영향을 계산.

    실행 계약:
      - 헤더 `X-Cascade-API-Version: 2` 미일치 → 400.
      - request body 의 `new_start/new_end` 는 naive KST datetime (Pydantic validator
        가 tz-aware 를 422 로 reject).
      - `FEATURE_FLAG_CASCADE_V2` off → 단일 invalid_equipment unresolved 로 응답해
        프론트가 비활성화 상태를 인지하고 legacy fallback 또는 UX 메시지를 표시.
      - on → `plan_cascade_preview` (Task 10 DB wrapper) 호출 → 결과를 Pydantic
        스키마로 직렬화.
    """
    # 헤더 게이트: 계약 버전 불일치는 즉시 400 — Pydantic validation 이전 단계.
    # 관측성 전: 잘못된 헤더는 422/400 레이턴시 측정 대상이 아니므로 metric/log 전 단계에서 차단.
    if x_cascade_api_version != "2":
        raise HTTPException(
            status_code=400,
            detail="X-Cascade-API-Version: 2 header required",
        )

    # Task 23: feature flag snapshot + 구조화 로그 + Histogram 측정.
    # feature_flag gauge 는 요청마다 갱신 → 런타임 ON/OFF 를 대시보드가 즉시 반영.
    request_id = str(uuid.uuid4())
    flag_on = is_cascade_v2_enabled()
    cascade_feature_flag_state.set(1 if flag_on else 0)

    with (
        cascade_preview_duration_seconds.time(),
        log_cascade_request(
            request_id,
            "/cascade-preview",
            task_id=body.task_id,
            new_start=body.new_start.isoformat(),
            new_end=body.new_end.isoformat(),
            new_equipment_code=body.new_equipment_code,
        ) as extra,
    ):
        # Feature flag off: 계약은 유지하되 실제 cascade 계산은 skip.
        # invalid_equipment reason 을 선택한 이유 — "기능 자체가 꺼져 있어 해소 불가" 를
        # 프론트가 동일한 unresolved UI 로 처리할 수 있게 하기 위함.
        if not flag_on:
            extra["feature_disabled"] = True
            cascade_unresolved_total.labels(
                reason=UnresolvedReason.invalid_equipment.value
            ).inc()
            return CascadePreviewResponseV2(
                request_id=request_id,
                summary="cascade v2 disabled",
                pushes=[],
                pulls=[],
                unresolved=[
                    {
                        "task_id": body.task_id,
                        "equipment_code": "",
                        "batch_label": "",
                        "reason": UnresolvedReason.invalid_equipment.value,
                        "detail": "cascade v2 disabled",
                    }
                ],
                can_auto_resolve=False,
                iter_count=0,
                truncated=False,
            )

        # DB 통합 wrapper 호출 — 패키지(`schedules`) attribute 로 lookup 해서
        # tests/test_cascade_preview_v2.py 가 `patch("app.presentation.routes
        # .schedules.plan_cascade_preview", ...)` 로 갈아끼운 mock 이 적용되도록.
        # 직접 import 하면 이 모듈 namespace 가 원본 함수를 잡아버려 patch 무력화.
        from app.presentation.routes import schedules as _schedules_pkg

        result = _schedules_pkg.plan_cascade_preview(
            body.task_id,
            body.new_start,
            body.new_end,
            body.new_equipment_code,
            db,
        )

        # 로그 관측 필드 — reason histogram 으로 어떤 push 가 주도적인지 파악.
        extra["pushes_n"] = len(result.pushes)
        extra["pulls_n"] = len(result.pulls)
        extra["unresolved_n"] = len(result.unresolved)
        extra["wave_used"] = result.iter_count
        extra["truncated"] = result.truncated
        reason_hist: dict[str, int] = {}
        for p in result.pushes:
            r = _reason_to_str(p["reason"])
            reason_hist[r] = reason_hist.get(r, 0) + 1
        extra["reason_histogram"] = reason_hist

        # unresolved counter 증가 — reason 라벨별 집계.
        for u in result.unresolved:
            cascade_unresolved_total.labels(reason=_reason_to_str(u["reason"])).inc()

        # service 가 반환한 request_id 대신 logged request_id 로 일관성 유지.
        # (프론트는 이 값을 bulk-update 의 expected_cascade_request_id 로 echo back.)
        return CascadePreviewResponseV2(
            request_id=request_id,
            summary=result.summary,
            pushes=[_push_to_schema(p) for p in result.pushes],
            pulls=[_push_to_schema(p) for p in result.pulls],
            unresolved=[_unres_to_schema(u) for u in result.unresolved],
            can_auto_resolve=result.can_auto_resolve,
            iter_count=result.iter_count,
            truncated=result.truncated,
        )
