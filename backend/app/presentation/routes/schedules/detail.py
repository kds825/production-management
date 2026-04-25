"""schedules/detail.py — 단건 task / 버전 작성·수정·삭제 엔드포인트.

핸들러:
- POST /tasks                  → create_task
- PUT /tasks/{task_id}         → update_task
- DELETE /tasks/{task_id}      → delete_task
- POST /versions               → save_version

`__init__.py` 가 본 모듈 router 를 prefix="/schedules" 패키지 router 에
include 한다.
"""

import copy
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.domain.entities import ScheduleTask, TaskStatus
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
from app.presentation.routes.schedules._shared import (
    VersionSaveRequest,
    VersionSummaryResponse,
    _db_task_to_response,
    _to_response,
    _versions,
)
from app.presentation.schemas import (
    ScheduleTaskCreate,
    ScheduleTaskResponse,
    ScheduleTaskUpdate,
)
from app.application.ingest import extract_sq


router = APIRouter()


@router.post("/tasks", response_model=ScheduleTaskResponse, status_code=201)
def create_task(body: ScheduleTaskCreate) -> ScheduleTaskResponse:
    """새 스케줄 작업 등록 (D&D 수동 배치용 — 인메모리 store 사용)."""
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
        # Why 'M' 접두: update_task 라우팅은 "TASK-" 뒤가 전부 숫자면 DB 경로로
        # 보냄. uuid.hex 는 ~6% 확률로 all-digit 이 되어 in-memory task 가 DB 조회
        # 경로를 타고 404. 'M'(manual) 고정 prefix 로 충돌 제거.
        id=f"TASK-M{uuid.uuid4().hex[:7].upper()}",
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
    """스케줄 작업 삭제."""
    if not store.delete_task(task_id):
        raise HTTPException(
            status_code=404,
            detail=f"작업 '{task_id}'를 찾을 수 없습니다.",
        )


@router.post("/versions", response_model=VersionSummaryResponse, status_code=201)
def save_version(body: VersionSaveRequest) -> VersionSummaryResponse:
    """현재 스케줄을 새 버전으로 저장 (타임스탬프 자동 생성)."""
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
