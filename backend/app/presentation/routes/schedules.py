import copy
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.feature_flags import is_cascade_v2_enabled
from app.domain.entities import ScheduleTask, TaskPriority, TaskStatus
from app.infrastructure.database import get_db
from app.infrastructure.memory_store import store
from app.observability.cascade_logging import log_cascade_request
from app.observability.metrics import (
    cascade_feature_flag_state,
    cascade_preview_duration_seconds,
    cascade_revert_total,
    cascade_unresolved_total,
)
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
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.presentation.schemas.cascade import (
    BulkUpdateErrorCode,
    BulkUpdateRequestV2,
    BulkUpdateSuccess,
    CascadePreviewRequest as CascadePreviewRequestV2,
    CascadePreviewResponse as CascadePreviewResponseV2,
)
from app.services.batch_grouping import format_spec_display, extract_sq
from app.services.cascade import plan_cascade_preview, UnresolvedReason
from app.services.cascade.snap import build_snapshot
from app.services.schedule_optimizer import PREDECESSOR_PROCESS
from app.services.schedule_validators import (
    find_due_date_violation,
    find_predecessor_violation,
    find_same_eq_overlap,
)

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
    lot_count: int | None = None,
    spec_list: list[str] | None = None,
) -> ScheduleTaskResponse:
    """DB schedule_task + production_batch 레코드를 프론트엔드 응답 형태로 변환.

    group_volume_m: batch_group 전체 합산 길이 (None이면 단일 배치 길이 사용)
    group_order_count: 그룹 내 개별 행 수
    spec_list: 시스(SH-*) 블록에서 같은 batch_group 에 묶인 SQ 목록
               (예: ['50SQ','100SQ']). 비시스 task 는 None.
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
        batch_id=batch.batch_id,
        created_at=task.created_at,
        sq_mm2=sq_mm2 if sq_mm2 else None,
        lot_count=lot_count,
        spec_list=spec_list,
        # WIP 매칭 FK를 그대로 노출 — 프론트 ContextMenu "미배정으로 이동"
        # disabled 판정에 사용 (Task 5.2). None 이면 일반 생산 배치.
        wip_matched_id=batch.wip_matched_id,
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

    # batch_group별 volume 계산:
    # 헤더(batch_seq=-1)가 있으면 헤더의 total_length_m = 실제 생산지시(틀단위) 수량
    # 없으면 개별 수주(batch_seq>=0) 합산
    header_rows = (
        db.query(
            ProductionBatchModel.batch_group,
            ProductionBatchModel.total_length_m,
            ProductionBatchModel.drum_count,
        )
        .filter(
            ProductionBatchModel.batch_group.isnot(None),
            ProductionBatchModel.batch_seq == -1,
        )
        .all()
    )
    header_volumes = {bg: float(vol or 0) for bg, vol, _ in header_rows}
    header_drum_counts: dict[str, int] = {bg: int(dc or 1) for bg, _, dc in header_rows}

    order_stats_rows = (
        db.query(
            ProductionBatchModel.batch_group,
            func.sum(ProductionBatchModel.total_length_m),
            func.count(ProductionBatchModel.batch_id),
        )
        .filter(
            ProductionBatchModel.batch_group.isnot(None),
            ProductionBatchModel.batch_seq >= 0,
        )
        .group_by(ProductionBatchModel.batch_group)
        .all()
    )
    # 헤더가 있으면 헤더 값, 없으면 수주 합산
    group_volumes = {
        bg: header_volumes.get(bg, float(vol or 0)) for bg, vol, _ in order_stats_rows
    }
    # 헤더만 있고 order가 없는 그룹도 포함
    for bg, vol in header_volumes.items():
        if bg not in group_volumes:
            group_volumes[bg] = vol
    group_counts = {bg: int(cnt or 1) for bg, _, cnt in order_stats_rows}

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

    # spec_list 계산 — 시스 batch_group 에 묶인 SQ 규격 목록 (오름차순, 중복 제거)
    # 왜 bulk 1-query: task 별 N+1 쿼리 방지. 시스 batch_group 만 대상이므로
    # 응답에 포함된 시스 태스크의 batch_group 집합만 스캔한다.
    sheath_groups: set[str] = {
        t.batch_group
        for t, _ in db_tasks
        if t.batch_group and (t.equipment_code or "").startswith("SH-")
    }
    group_spec_list: dict[str, list[str]] = {}
    if sheath_groups:
        sq_rows = (
            db.query(
                ProductionBatchModel.batch_group,
                ProductionBatchModel.sq_mm2,
            )
            .filter(
                ProductionBatchModel.batch_group.in_(sheath_groups),
                ProductionBatchModel.sq_mm2.isnot(None),
                # batch_seq >= 0: 헤더(-1) 제외 — 헤더는 대표 SQ 만 갖고 있어
                # 실제 묶인 수주 규격 목록을 왜곡한다.
                ProductionBatchModel.batch_seq >= 0,
            )
            .all()
        )
        tmp: dict[str, set[int]] = {}
        for bg, sq in sq_rows:
            if sq is None:
                continue
            tmp.setdefault(bg, set()).add(int(sq))
        group_spec_list = {
            bg: [f"{sq}SQ" for sq in sorted(sqs)] for bg, sqs in tmp.items()
        }

    def _spec_list_for(task: ScheduleTaskModel) -> list[str] | None:
        """시스 task 에만 spec_list 부여, 비시스는 None."""
        if not (task.equipment_code or "").startswith("SH-"):
            return None
        return group_spec_list.get(task.batch_group) or []

    return [
        _db_task_to_response(
            task,
            batch,
            group_volume_m=group_volumes.get(task.batch_group),
            group_order_count=group_counts.get(task.batch_group, 1),
            color_change_min=color_change_map.get(task.task_id, 0),
            lot_count=header_drum_counts.get(task.batch_group),
            spec_list=_spec_list_for(task),
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
# Endpoint: POST /api/schedules/cascade-preview-legacy (v1, legacy)
#
# v2 전환(2026-04-18 Task 11): 본 엔드포인트는 `AffectedTask/CascadeConflict` 기반 v1
# 계약을 유지하는 레거시 경로. 신규 v2 (`/cascade-preview`) 는 Pydantic 스키마
# (`schemas.cascade`) + 헤더 가드 + FEATURE_FLAG 게이팅 을 적용. 프론트 마이그레이션이
# 완료되면 제거 대상.
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

        # DB 통합 wrapper 호출 — 내부에서 snapshot 구성 + BFS + validators 수행.
        result = plan_cascade_preview(
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


# ---------------------------------------------------------------------------
# Endpoint: POST /api/schedules/revert/{change_set_id}
#
# Task 14: Undo — 가장 최근 change_set 1건만 되돌림.
#
# 계약:
#   - 404: 알 수 없는 change_set_id.
#   - 409: 이 change_set 이후 더 최근 change_set 이 존재 (freshness 실패).
#          PoC 단계에서는 최근 1건만 undo 스코프 — 중간 revert 는 일관성을 깰 수 있어 거부.
#   - 200: snapshot_before 로 task.start/end/equipment_code 복구 → change_set 삭제 후 커밋.
# ---------------------------------------------------------------------------


@router.post("/revert/{change_set_id}")
def revert(change_set_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """change_set 한 건을 undo — snapshot_before 값을 schedule_task 에 재적용.

    왜 "최신 1건만": 여러 change_set 을 역순으로 뒤로 감는 full history 는 스냅샷
    간 교차 의존성(예: 두 change_set 이 같은 task 를 덮어쓴 경우) 을 해소해야 해서
    비용이 크다. PoC 는 직전 1건만 안전하게 되돌리는 계약으로 단순화.
    """
    # Task 23: 구조화 로그 + revert counter by status.
    # 404/409/200 경로 각각 status label 로 집계 — 대시보드에서 바로 retry 비율 측정.
    request_id = str(uuid.uuid4())
    with log_cascade_request(
        request_id, f"/revert/{change_set_id}", change_set_id=change_set_id
    ) as extra:
        cs = db.get(ScheduleChangeSet, change_set_id)
        if cs is None:
            cascade_revert_total.labels(status="not_found").inc()
            extra["revert_status"] = "not_found"
            raise HTTPException(status_code=404, detail="change_set_id not found")

        # Freshness 검증 — 이 change_set 이후 새 change_set 이 있으면 undo 거부.
        newer = (
            db.query(ScheduleChangeSet)
            .filter(ScheduleChangeSet.created_at > cs.created_at)
            .order_by(ScheduleChangeSet.created_at.asc())
            .first()
        )
        if newer is not None:
            cascade_revert_total.labels(status="conflict").inc()
            extra["revert_status"] = "conflict"
            extra["newer_change_set_id"] = newer.change_set_id
            raise HTTPException(
                status_code=409,
                detail=(
                    f"newer change_set exists: {newer.change_set_id} "
                    f"(created_at={newer.created_at.isoformat()})"
                ),
            )

        # snapshot_before 로 복구 — task_id 키는 bulk_update_v2 가 str 로 저장.
        # ScheduleTask.task_id 는 Integer PK 이므로 isdigit 이면 int 캐스팅.
        restored_n = 0
        for task_id_str, snap in (cs.snapshot_before or {}).items():
            task_pk: Any = int(task_id_str) if task_id_str.isdigit() else task_id_str
            t = db.get(ScheduleTaskModel, task_pk)
            if t is None:
                # 극단적 race — task 가 삭제된 경우 skip (409 보다 관대하게).
                continue
            t.start_datetime = datetime.fromisoformat(snap["start"])
            t.end_datetime = datetime.fromisoformat(snap["end"])
            if snap.get("equipment_code"):
                t.equipment_code = snap["equipment_code"]
            restored_n += 1

        # change_set 삭제 — 같은 id 로 재revert 방지 (멱등성 대신 1회 소비 선택).
        db.delete(cs)
        db.commit()

        cascade_revert_total.labels(status="success").inc()
        extra["revert_status"] = "success"
        extra["restored_n"] = restored_n
        return {"reverted": True, "change_set_id": change_set_id}


# ---------------------------------------------------------------------------
# Endpoint: GET /api/schedules/change-sets/{change_set_id}/diff
#
# 긴급수주 등 change_set 1건의 snapshot_before/after 를 비교해 "어떤 task 가 어떻게
# 바뀌었는지" 를 분류 반환. 프론트의 diff panel / side-by-side Gantt 데이터 소스.
#
# 분류 규칙:
#   - moved   : before/after 양쪽 존재 + start|end|equipment_code 중 하나라도 상이
#   - added   : after 에만 존재 (before 에 없음)
#   - removed : before 에만 존재 (after 에 없음, 정상 흐름에선 드뭄)
#   - unchanged: 완전 동일 (task_id 리스트만 반환 — payload 부피 축소)
#
# 시간 포맷: snapshot 의 start/end 는 ISO8601 문자열 그대로 유지. delta_hours 는
# float 로 별도 계산해 제공 (프론트가 raw parse 부담 없이 정렬/필터링 가능).
# ---------------------------------------------------------------------------


def _parse_iso_or_none(value: Any) -> datetime | None:
    """snapshot 내 ISO8601 문자열을 datetime 으로 변환. 잘못된 값이면 None.

    snapshot 은 JSONB 이므로 스키마가 강제되지 않는다 — 과거 레코드나 손상된
    데이터가 섞여 있을 수 있어 방어적으로 파싱한다 (fail-fast 대신 partial).
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _delta_hours(old_iso: Any, new_iso: Any) -> float | None:
    """두 ISO8601 문자열의 시간 차이를 시간 단위 float 로 반환.

    둘 중 하나라도 파싱 실패 시 None — 프론트가 '계산 불가' 상태를 표시할 수 있게.
    round(2) 로 소수점 2자리까지 (1분 해상도).
    """
    old_dt = _parse_iso_or_none(old_iso)
    new_dt = _parse_iso_or_none(new_iso)
    if old_dt is None or new_dt is None:
        return None
    return round((new_dt - old_dt).total_seconds() / 3600, 2)


@router.get("/change-sets/{change_set_id}/diff")
def get_change_set_diff(
    change_set_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """change_set 1건의 snapshot_before/after 를 비교해 변경 내역을 분류 반환.

    긴급수주 반영 후 "기존 계획 대비 어떤 배치가 어떻게 바뀌었는지" 를 UI 에
    노출하기 위한 읽기 전용 API. revert 와 달리 DB 를 수정하지 않는다.
    """
    cs = db.get(ScheduleChangeSet, change_set_id)
    if cs is None:
        raise HTTPException(
            status_code=404,
            detail=f"change_set_id '{change_set_id}' not found",
        )

    before = cs.snapshot_before or {}
    after = cs.snapshot_after or {}

    # JSONB 는 정상 경로에서 dict 로 역직렬화되지만, 과거 데이터/수동 INSERT 로 인해
    # str (직렬화 누락) 이 들어올 가능성을 방어. 파싱 실패는 500 으로 올려 원인 가시화.
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise HTTPException(
            status_code=500,
            detail="snapshot_before / snapshot_after must be JSON objects",
        )

    before_ids = set(before.keys())
    after_ids = set(after.keys())

    common_ids = before_ids & after_ids
    added_ids = after_ids - before_ids
    removed_ids = before_ids - after_ids

    moved_tasks: list[dict[str, Any]] = []
    unchanged_task_ids: list[str] = []

    for task_id in sorted(common_ids):
        b = before.get(task_id) or {}
        a = after.get(task_id) or {}
        if not isinstance(b, dict) or not isinstance(a, dict):
            # 개별 task 엔트리 손상 시 moved 로 간주 (보수적) — 진단 로그 대체.
            continue

        old_start = b.get("start")
        old_end = b.get("end")
        old_eq = b.get("equipment_code")
        new_start = a.get("start")
        new_end = a.get("end")
        new_eq = a.get("equipment_code")

        start_changed = old_start != new_start
        end_changed = old_end != new_end
        eq_changed = old_eq != new_eq

        if not (start_changed or end_changed or eq_changed):
            unchanged_task_ids.append(task_id)
            continue

        moved_tasks.append(
            {
                "task_id": task_id,
                "old_start": old_start,
                "old_end": old_end,
                "old_equipment": old_eq,
                "new_start": new_start,
                "new_end": new_end,
                "new_equipment": new_eq,
                "start_delta_hours": _delta_hours(old_start, new_start),
                "end_delta_hours": _delta_hours(old_end, new_end),
                "equipment_changed": eq_changed,
            }
        )

    added_tasks: list[dict[str, Any]] = []
    for task_id in sorted(added_ids):
        a = after.get(task_id) or {}
        if not isinstance(a, dict):
            continue
        added_tasks.append(
            {
                "task_id": task_id,
                "start": a.get("start"),
                "end": a.get("end"),
                "equipment": a.get("equipment_code"),
            }
        )

    removed_tasks: list[dict[str, Any]] = []
    for task_id in sorted(removed_ids):
        b = before.get(task_id) or {}
        if not isinstance(b, dict):
            continue
        removed_tasks.append(
            {
                "task_id": task_id,
                "start": b.get("start"),
                "end": b.get("end"),
                "equipment": b.get("equipment_code"),
            }
        )

    # kind 컬럼이 없는 구 스키마 환경(migration 미적용) 에서도 안전하게 동작하도록
    # getattr 로 접근 — 없으면 기본값 "cascade" (model 의 default 와 동일).
    kind_value = getattr(cs, "kind", None) or "cascade"

    return {
        "change_set_id": cs.change_set_id,
        "kind": kind_value,
        "created_at": cs.created_at.isoformat() if cs.created_at else None,
        "preview_request_id": cs.preview_request_id,
        "summary": {
            "moved": len(moved_tasks),
            "added": len(added_tasks),
            "removed": len(removed_tasks),
            "unchanged": len(unchanged_task_ids),
            "total_before": len(before_ids),
            "total_after": len(after_ids),
        },
        "moved_tasks": moved_tasks,
        "added_tasks": added_tasks,
        "removed_tasks": removed_tasks,
        "unchanged_task_ids": unchanged_task_ids,
    }
