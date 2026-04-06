import copy
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.entities import ScheduleTask, TaskPriority, TaskStatus
from app.infrastructure.database import get_db
from app.infrastructure.memory_store import store
from app.infrastructure.models.equipment_master import (
    EquipmentMaster as EquipmentMasterModel,
)
from app.infrastructure.models.production_batch import (
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_task import (
    ScheduleTask as ScheduleTaskModel,
)
from app.presentation.schemas import (
    ScheduleTaskCreate,
    ScheduleTaskResponse,
    ScheduleTaskUpdate,
)
from app.services.batch_grouping import format_spec_display, extract_sq
from app.services.schedule_optimizer import PREDECESSOR_PROCESS

router = APIRouter(prefix="/schedules", tags=["스케줄"])

# ---------------------------------------------------------------------------
# 인메모리 버전 스토어 — PoC 단계용 단순 리스트
# ---------------------------------------------------------------------------
_versions: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Pydantic 스키마 (버전 관련)
# ---------------------------------------------------------------------------


class VersionSaveRequest(BaseModel):
    label: str = ""
    tasks: list[dict[str, Any]]
    created_at: datetime | None = None


class VersionSummaryResponse(BaseModel):
    id: str
    label: str
    created_at: datetime
    task_count: int


class VersionDetailResponse(BaseModel):
    id: str
    label: str
    created_at: datetime
    tasks: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _to_response(task: ScheduleTask) -> ScheduleTaskResponse:
    return ScheduleTaskResponse(
        id=task.id,
        order_id=task.order_id,
        equipment_id=task.equipment_id,
        product=task.product,
        spec=task.spec,
        core_count=task.core_count,
        color=task.color,
        start=task.start,
        end=task.end,
        volume_m=task.volume_m,
        line_speed_m_per_min=task.line_speed_m_per_min,
        priority=task.priority,
        status=task.status,
        delivery_date=task.delivery_date,
        process_step=task.process_step,
        predecessors=task.predecessors,
        notes=task.notes,
        changeover_min=task.changeover_min,
        duration_hours=task.duration_hours,
        customer=getattr(task, "customer", None),
    )


def _db_task_to_response(
    task: ScheduleTaskModel,
    batch: ProductionBatchModel,
    group_volume_m: float | None = None,
    group_order_count: int = 1,
    color_change_min: int = 0,
) -> ScheduleTaskResponse:
    """DB schedule_task + production_batch 레코드를 프론트엔드 응답 형태로 변환.

    group_volume_m: batch_group 전체 합산 길이 (None이면 단일 배치 길이 사용)
    group_order_count: 그룹 내 개별 행 수
    """
    # customer_priority(int) → TaskPriority
    cp = batch.customer_priority or 99
    if cp <= 3:
        priority = TaskPriority.CRITICAL
    elif cp <= 7:
        priority = TaskPriority.URGENT
    else:
        priority = TaskPriority.NORMAL

    # spec: 고압이면 "1C x 4/0AWG" / "1C x 500KCMIL", 일반이면 "1C x 633SQ"
    core_count = batch.core_count or 1
    sq_mm2 = float(batch.sq_mm2 or 0)
    spec = format_spec_display(getattr(batch, "spec_raw", None), core_count, sq_mm2)

    # color: sheath_color 우선, 없으면 core_colors
    color = batch.sheath_color or batch.core_colors or ""

    # status: DB 값을 TaskStatus enum으로 안전하게 파싱 (알 수 없는 값은 PLANNED)
    try:
        status = TaskStatus(task.status)
    except (ValueError, KeyError):
        status = TaskStatus.PLANNED

    # duration_hours: start~end 차이 (ScheduleTask 도메인 프로퍼티와 동일 계산)
    duration_hours = (task.end_datetime - task.start_datetime).total_seconds() / 3600

    return ScheduleTaskResponse(
        id=f"TASK-{task.task_id}",
        order_id=batch.sales_order_id or "",
        equipment_id=task.equipment_code,
        product=batch.product_group or "",
        spec=spec,
        core_count=core_count,
        color=color,
        start=task.start_datetime,
        end=task.end_datetime,
        volume_m=group_volume_m
        if group_volume_m is not None
        else float(batch.total_length_m or 0),
        line_speed_m_per_min=float(batch.line_speed_mpm or 0),
        priority=priority,
        status=status,
        delivery_date=datetime(
            batch.due_date.year,
            batch.due_date.month,
            batch.due_date.day,
        )
        if batch.due_date
        else None,
        process_step=batch.batch_seq,
        predecessors=[f"TASK-{task.predecessor_task_id}"]
        if task.predecessor_task_id
        else [],
        notes=batch.remarks or "",
        changeover_min=int(task.setup_time_min or 0),
        setup_time_min=int(task.setup_time_min or 0),
        color_change_min=color_change_min,
        duration_hours=duration_hours,
        customer=batch.customer_name or "",
        batch_group=task.batch_group or "",
        material=batch.conductor_material or None,
    )


# ---------------------------------------------------------------------------
# 작업(Task) CRUD 엔드포인트
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=list[ScheduleTaskResponse])
def list_tasks(
    date_from: str | None = Query(None, description="시작일 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="종료일 YYYY-MM-DD"),
    process_type: str | None = Query(
        None, description="공정 필터 (연선,저압절연,저압시스 등)"
    ),
    equipment_id: str | None = Query(None, description="설비 필터"),
    voltage: str | None = Query(None, description="전압 필터 (저압/고압)"),
    db: Session = Depends(get_db),
) -> list[ScheduleTaskResponse]:
    """스케줄 작업 목록 조회 (시작 시간 오름차순).

    Stage 2 auto-scheduling 결과를 PostgreSQL에서 읽어 반환한다.
    date_from/date_to/process_type/equipment_id/voltage 쿼리 파라미터로
    Gantt 뷰에 필요한 구간만 필터링하여 전송량을 줄인다.
    DB에 schedule_task 레코드가 없을 경우 인메모리 store로 폴백하여
    개발 초기 샘플 데이터도 계속 볼 수 있다.
    """
    q = db.query(ScheduleTaskModel, ProductionBatchModel).join(
        ProductionBatchModel,
        ScheduleTaskModel.batch_id == ProductionBatchModel.batch_id,
    )

    # WIP 완료 배치는 간트에 미표시 — 재고로 대체된 공정이므로 스케줄 불필요
    q = q.filter(ProductionBatchModel.status != "wip_complete")

    # 날짜 범위 필터 — 태스크 시간대가 윈도우와 겹치는 것 포함
    # (start < window_end AND end > window_start 조건으로 부분 겹침도 포함)
    if date_from:
        dt_from = datetime.fromisoformat(date_from)
        q = q.filter(ScheduleTaskModel.end_datetime > dt_from)
    if date_to:
        # "YYYY-MM-DD" 형식이면 해당 날 끝까지 포함 (23:59:59)
        dt_to_str = date_to if "T" in date_to else f"{date_to}T23:59:59"
        dt_to = datetime.fromisoformat(dt_to_str)
        q = q.filter(ScheduleTaskModel.start_datetime < dt_to)

    # 공정명 필터
    if process_type:
        q = q.filter(ProductionBatchModel.process_name == process_type)

    # 설비 코드 필터
    if equipment_id:
        q = q.filter(ScheduleTaskModel.equipment_code == equipment_id)

    # 전압 필터 — 저압: 0.6kV 계열, 고압: 22.9kV / 35kV 계열
    if voltage == "저압":
        q = q.filter(ProductionBatchModel.voltage.contains("0.6"))
    elif voltage == "고압":
        q = q.filter(
            ProductionBatchModel.voltage.contains("22.9")
            | ProductionBatchModel.voltage.contains("35")
        )

    db_tasks = q.order_by(ScheduleTaskModel.start_datetime).all()

    # batch_group별 합산 volume 계산 — 단일 그룹 쿼리로 N+1 해소
    group_stats_rows = (
        db.query(
            ProductionBatchModel.batch_group,
            func.sum(ProductionBatchModel.total_length_m),
            func.count(ProductionBatchModel.batch_id),
        )
        .filter(ProductionBatchModel.batch_group.isnot(None))
        .group_by(ProductionBatchModel.batch_group)
        .all()
    )
    group_volumes = {bg: float(vol or 0) for bg, vol, _ in group_stats_rows}
    group_counts = {bg: int(cnt or 1) for bg, _, cnt in group_stats_rows}

    # 색상교체 시간 계산을 위해 같은 설비의 직전 배치 sheath_color 조회
    prev_colors: dict[str, str] = {}  # equipment_code → 직전 batch sheath_color
    color_change_map: dict[int, int] = {}  # task_id → color_change_min
    for task_row, batch_row in db_tasks:
        eq = task_row.equipment_code
        curr_color = (batch_row.sheath_color or "").strip()
        if batch_row.process_name in ("저압시스", "고압시스", "HFCO시스"):
            prev_color = prev_colors.get(eq, "")
            if prev_color and curr_color and prev_color != curr_color:
                color_change_map[task_row.task_id] = (
                    120  # default, could query SpeedMaster
                )
        prev_colors[eq] = curr_color

    return [
        _db_task_to_response(
            task,
            batch,
            group_volume_m=group_volumes.get(task.batch_group),
            group_order_count=group_counts.get(task.batch_group, 1),
            color_change_min=color_change_map.get(task.task_id, 0),
        )
        for task, batch in db_tasks
    ]


@router.post("/tasks", response_model=ScheduleTaskResponse, status_code=201)
def create_task(body: ScheduleTaskCreate) -> ScheduleTaskResponse:
    """새 스케줄 작업 등록 (D&D 수동 배치용 — 인메모리 store 사용)"""
    # 설비 존재 확인
    equipment = store.get_equipment(body.equipment_id)
    if equipment is None:
        raise HTTPException(
            status_code=404,
            detail=f"설비 '{body.equipment_id}'를 찾을 수 없습니다.",
        )

    # 수주 존재 확인 및 스케줄 완료 플래그 처리
    order = store.get_order(body.order_id)
    if order is None:
        raise HTTPException(
            status_code=404,
            detail=f"수주 '{body.order_id}'를 찾을 수 없습니다.",
        )

    task = ScheduleTask(
        id=f"TASK-{uuid.uuid4().hex[:8].upper()}",
        order_id=body.order_id,
        equipment_id=body.equipment_id,
        product=body.product,
        spec=body.spec,
        core_count=body.core_count,
        color=body.color,
        start=body.start,
        end=body.end,
        volume_m=body.volume_m,
        line_speed_m_per_min=body.line_speed_m_per_min,
        priority=body.priority,
        status=TaskStatus.PLANNED,
        delivery_date=body.delivery_date,
        process_step=body.process_step,
        predecessors=body.predecessors,
        notes=body.notes,
        changeover_min=body.changeover_min,
    )

    created = store.create_task(task)
    store.mark_order_scheduled(body.order_id)
    return _to_response(created)


@router.put("/tasks/{task_id}", response_model=ScheduleTaskResponse)
def update_task(
    task_id: str,
    body: ScheduleTaskUpdate,
    db: Session = Depends(get_db),
) -> ScheduleTaskResponse:
    """스케줄 작업 수정 (부분 업데이트 — D&D용).

    DB-based 태스크(Stage 2)와 인메모리 태스크(수동 배치) 두 경로를 지원한다.
    설비 변경 시 SQ 범위 및 재질 적합성을 검증하여 부적합 배정을 사전 차단한다.
    """
    # ------------------------------------------------------------------
    # 1) DB-based path: task_id가 "TASK-<숫자>" 형태이면 DB에서 조회
    # ------------------------------------------------------------------
    db_numeric_id: int | None = None
    prefix = "TASK-"
    if task_id.startswith(prefix):
        suffix = task_id[len(prefix) :]
        if suffix.isdigit():
            db_numeric_id = int(suffix)

    if db_numeric_id is not None:
        task = (
            db.query(ScheduleTaskModel)
            .filter(ScheduleTaskModel.task_id == db_numeric_id)
            .first()
        )
        if task is None:
            raise HTTPException(
                status_code=404,
                detail=f"작업 '{task_id}'를 찾을 수 없습니다.",
            )

        # 설비 변경 시 유효성 검증
        if body.equipment_id is not None:
            target_equip = (
                db.query(EquipmentMasterModel)
                .filter(EquipmentMasterModel.equipment_code == body.equipment_id)
                .first()
            )
            if not target_equip:
                raise HTTPException(
                    status_code=404,
                    detail=f"설비 '{body.equipment_id}'를 찾을 수 없습니다.",
                )

            # 배치 정보 조회 — SQ/재질 검증에 필요
            batch = (
                db.query(ProductionBatchModel)
                .filter(ProductionBatchModel.batch_id == task.batch_id)
                .first()
            )

            # SQ 범위 검증
            if (
                batch
                and target_equip.range_min is not None
                and target_equip.range_max is not None
            ):
                sq = float(batch.sq_mm2 or 0)
                if sq < float(target_equip.range_min) or sq > float(
                    target_equip.range_max
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"규격 {int(sq)}SQ가 설비 "
                            f"{target_equip.equipment_name} "
                            f"범위({int(target_equip.range_min)}"
                            f"~{int(target_equip.range_max)})를 "
                            f"초과합니다"
                        ),
                    )

            # 재질 검증
            if (
                batch
                and target_equip.material_limit
                and target_equip.material_limit != "ALL"
            ):
                if (
                    batch.conductor_material
                    and batch.conductor_material != target_equip.material_limit
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"재질 {batch.conductor_material}은 설비 "
                            f"{target_equip.equipment_name}"
                            f"({target_equip.material_limit} 전용)에서 "
                            f"생산 불가합니다"
                        ),
                    )

            task.equipment_code = body.equipment_id

        # 나머지 필드 업데이트
        if body.start is not None:
            task.start_datetime = body.start
        if body.end is not None:
            task.end_datetime = body.end
        if body.status is not None:
            task.status = body.status.value
        db.commit()
        db.refresh(task)

        batch_for_resp = (
            db.query(ProductionBatchModel)
            .filter(ProductionBatchModel.batch_id == task.batch_id)
            .first()
        )
        return _db_task_to_response(task, batch_for_resp)

    # ------------------------------------------------------------------
    # 2) 인메모리 path: 수동 배치(D&D) 태스크
    # ------------------------------------------------------------------
    existing = store.get_task(task_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"작업 '{task_id}'를 찾을 수 없습니다.",
        )

    # 설비 변경 시 유효성 검증
    if body.equipment_id is not None:
        if store.get_equipment(body.equipment_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"설비 '{body.equipment_id}'를 찾을 수 없습니다.",
            )

        # DB에서 설비 마스터 조회하여 SQ/재질 검증 수행
        target_equip = (
            db.query(EquipmentMasterModel)
            .filter(EquipmentMasterModel.equipment_code == body.equipment_id)
            .first()
        )
        if target_equip:
            # 인메모리 태스크의 spec에서 SQ 값 파싱 (SQ/AWG/KCMIL 모두 처리)
            sq_val = extract_sq(existing.spec or "")

            # SQ 범위 검증
            if (
                sq_val is not None
                and target_equip.range_min is not None
                and target_equip.range_max is not None
            ):
                if sq_val < float(target_equip.range_min) or sq_val > float(
                    target_equip.range_max
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"규격 {int(sq_val)}SQ가 설비 "
                            f"{target_equip.equipment_name} "
                            f"범위({int(target_equip.range_min)}"
                            f"~{int(target_equip.range_max)})를 "
                            f"초과합니다"
                        ),
                    )

            # 재질 검증 — 인메모리 태스크는 order_id로 배치 역추적
            if target_equip.material_limit and target_equip.material_limit != "ALL":
                batch = (
                    db.query(ProductionBatchModel)
                    .filter(ProductionBatchModel.sales_order_id == existing.order_id)
                    .first()
                )
                if (
                    batch
                    and batch.conductor_material
                    and batch.conductor_material != target_equip.material_limit
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"재질 {batch.conductor_material}은 설비 "
                            f"{target_equip.equipment_name}"
                            f"({target_equip.material_limit} 전용)에서 "
                            f"생산 불가합니다"
                        ),
                    )

    updates = body.model_dump(exclude_none=True)
    updated = store.update_task(task_id, updates)
    return _to_response(updated)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str) -> None:
    """스케줄 작업 삭제"""
    if not store.delete_task(task_id):
        raise HTTPException(
            status_code=404,
            detail=f"작업 '{task_id}'를 찾을 수 없습니다.",
        )


# ---------------------------------------------------------------------------
# 버전(Version) 엔드포인트 — 스케줄 스냅샷 저장/조회
# ---------------------------------------------------------------------------


@router.post("/versions", response_model=VersionSummaryResponse, status_code=201)
def save_version(body: VersionSaveRequest) -> VersionSummaryResponse:
    """현재 스케줄을 새 버전으로 저장 (타임스탬프 자동 생성)"""
    now = body.created_at or datetime.now()
    version_id = f"VER-{uuid.uuid4().hex[:8].upper()}"
    label = body.label or f"버전 {now.strftime('%Y-%m-%d %H:%M')}"

    version: dict[str, Any] = {
        "id": version_id,
        "label": label,
        "created_at": now,
        # 깊은 복사로 스냅샷 보존
        "tasks": copy.deepcopy(body.tasks),
    }
    _versions.append(version)

    return VersionSummaryResponse(
        id=version_id,
        label=label,
        created_at=now,
        task_count=len(body.tasks),
    )


@router.get("/versions", response_model=list[VersionSummaryResponse])
def list_versions() -> list[VersionSummaryResponse]:
    """저장된 모든 버전 목록 조회 (최신 순)"""
    return [
        VersionSummaryResponse(
            id=v["id"],
            label=v["label"],
            created_at=v["created_at"],
            task_count=len(v["tasks"]),
        )
        for v in reversed(_versions)
    ]


@router.get("/versions/{version_id}", response_model=VersionDetailResponse)
def get_version(version_id: str) -> VersionDetailResponse:
    """특정 버전의 전체 작업 목록 조회"""
    version = next((v for v in _versions if v["id"] == version_id), None)
    if version is None:
        raise HTTPException(
            status_code=404,
            detail=f"버전 '{version_id}'를 찾을 수 없습니다.",
        )
    return VersionDetailResponse(
        id=version["id"],
        label=version["label"],
        created_at=version["created_at"],
        tasks=version["tasks"],
    )


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
# Cascade Preview — Pydantic 스키마
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
# Bulk Update — Pydantic 스키마
# ---------------------------------------------------------------------------


class BulkTaskUpdate(BaseModel):
    task_id: str
    new_start: datetime
    new_end: datetime


class BulkUpdateRequest(BaseModel):
    updates: list[BulkTaskUpdate]


# ---------------------------------------------------------------------------
# Endpoint: POST /api/schedules/cascade-preview
# ---------------------------------------------------------------------------


def _parse_task_id(raw_id: str) -> int:
    """'TASK-123' → 123. 숫자만 들어온 경우도 허용."""
    prefix = "TASK-"
    suffix = raw_id[len(prefix) :] if raw_id.startswith(prefix) else raw_id
    if not suffix.isdigit():
        raise HTTPException(
            status_code=400,
            detail=f"task_id 형식이 올바르지 않습니다: '{raw_id}'",
        )
    return int(suffix)


@router.post("/cascade-preview", response_model=CascadePreviewResponse)
def cascade_preview(
    body: CascadePreviewRequest,
    db: Session = Depends(get_db),
) -> CascadePreviewResponse:
    """이동된 태스크의 후행 공정에 대한 연쇄(cascade) 변경 미리보기.

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
# Endpoint: PATCH /api/schedules/tasks/bulk-update
# ---------------------------------------------------------------------------


@router.patch("/tasks/bulk-update")
def bulk_update_tasks(
    body: BulkUpdateRequest,
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """여러 태스크의 시작/종료 시간을 단일 트랜잭션으로 일괄 갱신한다.

    cascade-preview 결과를 프론트엔드에서 확정한 뒤 한 번에 반영할 때 사용한다.
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
