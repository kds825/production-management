"""decision_card payload 합성 — phrasing / section / gantt / bundle 통합.

Phase 6 (decision_card) Step 3c-1 skeleton. 본 모듈은 `presentation/routes/
decision_card.py` 가 호출하는 단일 진입점 `build_decision_card`. Step 3c-2
에서 wire-up snapshot fixture 와 함께 본문 채워질 것 — 본 skeleton 은:

- 입력 (batch_id, db, debug=bool) → 출력 DecisionCard pydantic
- DB 로드 (ProductionBatch + ScheduleTask + EquipmentMaster + AuditLog +
  SolverDecision) — N+1 없이 단일 쿼리 패턴
- phrasing/section/gantt provider 호출 → ❶~❻ 섹션 합성
- debug=True 면 DebugBlock 채움 (S2/S3 schema 정합 — ScheduleTask + raw
  details_json)
- 미데이터 (cold start, 빈 audit) 안전 폴백 — 기본 EMPTY 카드
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

from app.application.decisions.bundle_alternative import compare_bundles
from app.application.decisions.equipment_day_gantt import (
    get_gantt_builder,
)
from app.application.decisions.phrasing import (
    _resolve_key,
    get_phrasing_provider,
)
from app.application.decisions.section_builder import (
    HandoffBlock as DCHandoffBlock,
    OutsourceHandoffBlock as DCOutsourceBlock,
    WipMatchBlock as DCWipMatchBlock,
    get_section_builder,
)
from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.solver_decision import SolverDecision
from app.infrastructure.models.solver_run import SolverRun
from app.presentation.schemas.decision_card import (
    DebugBlock,
    DecisionCard,
    HandoffBlock,
    OutsourceHandoffBlock,
    WipMatchBlock,
)

if TYPE_CHECKING:
    pass


def build_decision_card(
    batch_id: int, db: Session, *, debug: bool = False
) -> DecisionCard:
    """`GET /api/scheduler/{run_label}/decision-card/{batch_id}` 의 페이로드 합성.

    Args:
      batch_id: 카드 주체 batch.
      db: SQLAlchemy Session.
      debug: True 면 DebugBlock 채워서 반환. 운영자 role 은 항상 False
        (route 에서 결정). 백엔드 1차 게이트는 라우트 측 — 본 함수는
        debug 인자 그대로 따른다.
    """
    batch, task, equipment, audit_rows, solver_run, solver_rows = _load_context(
        db, batch_id
    )

    if batch is None:
        # 폴백 — DecisionCardEmpty 컴포넌트가 본 응답 처리
        return _empty_card(batch_id)

    process_key = _resolve_key(batch)
    phrasing = get_phrasing_provider(batch)
    section_builder = get_section_builder(process_key)
    gantt_builder = get_gantt_builder(process_key)

    # ❸ section_builder 가 union 의 적절한 dataclass 반환
    section_block = section_builder.build_handoff_section(
        batch=batch,
        audit_rows=audit_rows,
        solver_rows=[task] if task is not None else [],
        phrasing=phrasing,
    )

    handoff: Optional[HandoffBlock] = None
    wip_match: Optional[WipMatchBlock] = None
    outsource_handoff: Optional[OutsourceHandoffBlock] = None
    if isinstance(section_block, DCHandoffBlock):
        handoff = HandoffBlock(**vars(section_block))
    elif isinstance(section_block, DCWipMatchBlock):
        wip_match = WipMatchBlock(**vars(section_block))
    elif isinstance(section_block, DCOutsourceBlock):
        outsource_handoff = OutsourceHandoffBlock(**vars(section_block))

    # ❹ Gantt — 본 batch 의 같은 설비/같은 날짜 row (skeleton: 빈 리스트.
    # Step 3c-2 wire-up 에서 ScheduleTask 같은 설비 필터)
    equipment_day_rows: list = []
    sort_label = gantt_builder.sort_label()

    # ❺ Bundle compare (skeleton: 빈 리스트. Step 3c-2 에서 post-hoc rebuild)
    chosen_metric = None
    alt_metrics: list = []
    bundle_rows = compare_bundles(batch, chosen_metric, alt_metrics)

    # 헤더 verdict_summary 1줄 (CEO R2)
    verdict = _safe_verdict_summary(
        phrasing, batch=batch, audit_rows=audit_rows, solver_run=solver_run, task=task
    )

    placement_text = _format_placement_text(equipment, task)

    card = DecisionCard(
        batch_id=batch.batch_id,
        task_id=task.task_id if task else None,
        run_label=batch.run_label or "",
        process_key=process_key,  # type: ignore[arg-type]
        process_label=_process_label(batch),
        sub_chip=_sub_chip(batch, equipment),
        customer_name=batch.customer_name or "",
        customer_priority=int(batch.customer_priority or 99),
        due_date=(
            batch.due_date  # type: ignore[arg-type]
            if hasattr(batch.due_date, "tzinfo")
            else None
        ),
        placement_text=placement_text,
        placement_calc={},  # Step 3c-2 wire-up
        verdict_summary=verdict,
        why=[],  # Step 3c-2: phrasing.adequacy_line 호출 N개
        # impact: default ImpactBlock
        handoff=handoff,
        wip_match=wip_match,
        outsource_handoff=outsource_handoff,
        equipment_day=equipment_day_rows,
        equipment_day_sort_label=sort_label,
        bundle_compare=[
            # BundleAlternativeRow → schema BundleAlternative
            # frozen dataclass 라 dict 변환 후 unpack
            __dict_for_bundle(r)  # type: ignore[arg-type]
            for r in bundle_rows
        ],
        alternatives=[],  # Step 3c-2: filter_out audit row → phrasing.filter_out_reason
        section_default_expanded=section_builder.default_expanded_for(
            _scenario_key_hint(batch, audit_rows)
        ),
        source="rule-based",
        debug=_build_debug_block(
            solver_run=solver_run, solver_rows=solver_rows, task=task
        )
        if debug
        else None,
    )
    return card


# ── DB 로드 — N+1 없이 단일 쿼리 ──────────────────────────────────────────


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


# ── helpers ───────────────────────────────────────────────────────────────


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


def _scenario_key_hint(batch: ProductionBatch, audit_rows: list[AuditLog]) -> str:
    """간단한 휴리스틱 — Step 3c-2 에서 더 정교한 로직.

    A: 정상 / B: 색상교체 / C: 규격교체 / D: D-day / E: 외주 /
    F: 연선 재공 / G: TFR-GV / H: 묶음 비교
    """
    pg_upper = (batch.product_group or "").upper()
    if "TFR-GV" in pg_upper and float(batch.sq_mm2 or 0) > 25:
        return "G"
    if batch.process_name == "연선":
        return "F"
    return "A"


def _safe_verdict_summary(
    phrasing,
    *,
    batch: ProductionBatch,
    audit_rows: list[AuditLog],
    solver_run: Optional[SolverRun],
    task: Optional[ScheduleTask],
) -> str:
    """phrasing.verdict_summary 안전 호출 — 미데이터 시 폴백."""
    try:
        return phrasing.verdict_summary(
            batch=batch,
            audit=_collect_audit_metrics(audit_rows),
            solver=solver_run,
            schedule_task=_task_view(task),
        )
    except Exception as e:  # noqa: BLE001 — 안전 폴백 우선
        return f"ⓘ verdict_summary 합성 실패 ({e!s})"


def _collect_audit_metrics(audit_rows: list[AuditLog]):
    """audit_rows 에서 verdict_summary 가 보는 metric snapshot 추출.

    Step 3c-2 wire-up 에서 정교화. skeleton 단계는 SimpleNamespace 폴백.
    """
    from types import SimpleNamespace

    color_min = 0
    spec_min = 0
    slack = 0.0
    loss_pct = 0.0
    for r in audit_rows:
        if r.action_type == "color_change" and r.constraints_applied:
            for c in r.constraints_applied:
                color_min = int(c.get("minutes", color_min) or color_min)
        if r.action_type == "spec_change" and r.constraints_applied:
            for c in r.constraints_applied:
                spec_min = int(c.get("minutes", spec_min) or spec_min)
    return SimpleNamespace(
        color_change_min=color_min,
        spec_change_min=spec_min,
        due_slack_days=slack,
        wip_loss_pct=loss_pct,
        outsource_lead_days=0,
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
