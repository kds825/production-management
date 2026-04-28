"""decision_card helper 함수 — context 로드 / 빈 카드 / sub-chip / debug block.

build_card.py 분할 (Task 1.8, B-5.1).
원본 import path 보존 — build_card 가 본 모듈에서 re-export.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.solver_decision import SolverDecision
from app.infrastructure.models.solver_run import SolverRun
from app.presentation.schemas.decision_card import DebugBlock, DecisionCard


def _load_context(
    db: Session, batch_id: int
) -> tuple[
    Optional[ProductionBatch],
    Optional[ScheduleTask],
    Optional[EquipmentMaster],
    list[AuditLog],
    Optional[SolverRun],
    list[SolverDecision],
]:
    batch = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id == batch_id)
        .one_or_none()
    )
    if batch is None:
        return None, None, [], [], None, []

    task = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.batch_id == batch_id)
        .order_by(ScheduleTask.created_at.desc())
        .first()
    )

    equipment: Optional[EquipmentMaster] = None
    if task is not None:
        equipment = (
            db.query(EquipmentMaster)
            .filter(EquipmentMaster.equipment_code == task.equipment_code)
            .one_or_none()
        )
    elif batch.equipment_code:
        equipment = (
            db.query(EquipmentMaster)
            .filter(EquipmentMaster.equipment_code == batch.equipment_code)
            .one_or_none()
        )

    audit_rows = (
        db.query(AuditLog)
        .filter(AuditLog.batch_id == batch_id)
        .order_by(AuditLog.created_at.asc())
        .all()
    )

    solver_run: Optional[SolverRun] = None
    solver_rows: list[SolverDecision] = []
    if batch.run_label:
        solver_run = (
            db.query(SolverRun)
            .filter(SolverRun.run_label == batch.run_label)
            .order_by(SolverRun.started_at.desc())
            .first()
        )
        if solver_run is not None:
            solver_rows = (
                db.query(SolverDecision)
                .filter(SolverDecision.run_id == solver_run.run_id)
                .order_by(SolverDecision.constraint_id)
                .all()
            )

    return batch, task, equipment, audit_rows, solver_run, solver_rows


def _empty_card(batch_id: int) -> DecisionCard:
    """batch 미발견 / cold start fallback."""
    return DecisionCard(
        batch_id=batch_id,
        run_label="",
        process_key="default",
        process_label="배치 정보 없음",
        placement_text=f"배치 {batch_id} 를 찾을 수 없습니다.",
        verdict_summary="ⓘ 배치 정보 없음 — ERP 에서 확인",
    )


def _process_label(batch: ProductionBatch) -> str:
    pn = batch.process_name or "미상"
    if "TFR-GV" in (batch.product_group or "").upper():
        if float(batch.sq_mm2 or 0) <= 25:
            return f"{pn} (단선 접지선)"
        return f"{pn} (TFR-GV)"
    return pn


def _sub_chip(
    batch: ProductionBatch, equipment: Optional[EquipmentMaster]
) -> Optional[str]:
    if not equipment:
        return None
    # A120 / A100 분기 — equipment.equipment_code 또는 color_group 으로 추정
    if equipment.color_group:
        return f"{equipment.color_group} 묶음"
    return None


def _format_placement_text(
    equipment: Optional[EquipmentMaster], task: Optional[ScheduleTask]
) -> str:
    if equipment is None or task is None:
        return "미배정"
    eq_name = equipment.equipment_name or equipment.equipment_code
    return (
        f"{eq_name} 에 {task.start_datetime:%m-%d %H:%M} ~ "
        f"{task.end_datetime:%H:%M} 배치"
    )


def _task_view(task: Optional[ScheduleTask]):
    """ScheduleTask → phrasing 호환 view (start_at/end_at 별칭).

    S4: phrasing.verdict_summary 시그니처는 schedule_task 인자를 받는다.
    실제 컬럼은 start_datetime/end_datetime 이지만 phrasing 는 start_at/
    end_at 으로 접근 — 본 view 가 어댑터.
    """
    if task is None:
        return None
    from types import SimpleNamespace

    return SimpleNamespace(
        start_at=task.start_datetime,
        end_at=task.end_datetime,
        equipment_code=task.equipment_code,
        cluster_id=task.batch_group or "",
    )


def _build_debug_block(
    *,
    solver_run: Optional[SolverRun],
    solver_rows: list[SolverDecision],
    task: Optional[ScheduleTask],
) -> DebugBlock:
    objective: dict = {}
    rows_dump: list[dict] = []
    for r in solver_rows:
        cid = r.constraint_id
        objective[cid] = float(r.penalty_value) if r.penalty_value is not None else 0.0
        rows_dump.append(
            {
                "constraint_id": cid,
                "applied": bool(r.applied),
                "hard_literal_value": r.hard_literal_value,
                "penalty_value": (
                    float(r.penalty_value) if r.penalty_value is not None else None
                ),
                "details_json": r.details_json or {},
            }
        )

    schedule_task_row: dict = {}
    if task is not None:
        # S2: 시작/종료 시각 source-of-truth 는 ScheduleTask
        schedule_task_row = {
            "task_id": task.task_id,
            "batch_id": task.batch_id,
            "equipment_code": task.equipment_code,
            "start_at": (
                task.start_datetime.isoformat() if task.start_datetime else None
            ),
            "end_at": task.end_datetime.isoformat() if task.end_datetime else None,
            "batch_group": task.batch_group,
        }

    return DebugBlock(
        engine="cpsat",  # Step 3c-2 wire-up 에서 solver_run.engine 등 사용
        solver_status=(solver_run.solver_status if solver_run else ""),
        solve_time_ms=0.0,
        objective_breakdown=objective,
        audit_anchors=[],
        schedule_task_row=schedule_task_row,
        solver_decision_rows=rows_dump,
        constraint_config_version="",  # Step 3c-2 wire-up
    )


def __dict_for_bundle(row) -> dict:
    """BundleAlternativeRow (frozen dataclass) → schema BundleAlternative dict."""
    return {
        "label": row.label,
        "color_change_min": row.color_change_min,
        "spec_change_min": row.spec_change_min,
        "duration_min": row.duration_min,
        "score": row.score,
        "is_chosen": row.is_chosen,
        "rationale": row.rationale,
    }
