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

    # ❶ Why — phrasing.adequacy_line 호출. audit pass 룰 + equipment 적합성
    why_lines = _build_why_lines(batch, equipment, audit_rows, phrasing)

    # ❷ Impact — phrasing.impact_line + severity 결정
    impact = _build_impact_block(batch, audit_rows, phrasing)

    # ❹ Gantt — 본 batch 의 같은 설비 + 같은 날짜 row 묶음
    equipment_day_rows = _build_equipment_day(db, batch, task, gantt_builder)
    sort_label = gantt_builder.sort_label()

    # ❺ Bundle compare — post-hoc rebuild from cluster_sort_key. orchestrator
    # 손대지 않음 (Engineer review blocker — main-parity 회귀 0 자명).
    chosen_metric, alt_metrics = _post_hoc_bundle_metrics(db, batch, task, audit_rows)
    bundle_rows = compare_bundles(batch, chosen_metric, alt_metrics, top_n=5)

    # ❻ Alternatives — audit_log.action_type='filter_out' row 사용
    alternatives = _build_alternatives(audit_rows, phrasing)

    # 헤더 verdict_summary 1줄 (CEO R2)
    verdict = _safe_verdict_summary(
        phrasing, batch=batch, audit_rows=audit_rows, solver_run=solver_run, task=task
    )

    placement_text = _format_placement_text(equipment, task)
    placement_calc = _build_placement_calc(task, audit_rows)

    card = DecisionCard(
        batch_id=batch.batch_id,
        task_id=task.task_id if task else None,
        run_label=batch.run_label or "",
        process_key=process_key,  # type: ignore[arg-type]
        process_label=_process_label(batch),
        sub_chip=_sub_chip(batch, equipment),
        customer_name=batch.customer_name or "",
        customer_priority=int(batch.customer_priority or 99),
        due_date=None,  # batch.due_date 는 date — pydantic datetime 으로 변환은 별도 spec
        placement_text=placement_text,
        placement_calc=placement_calc,
        verdict_summary=verdict,
        why=why_lines,
        impact=impact,
        handoff=handoff,
        wip_match=wip_match,
        outsource_handoff=outsource_handoff,
        equipment_day=equipment_day_rows,
        equipment_day_sort_label=sort_label,
        bundle_compare=[__dict_for_bundle(r) for r in bundle_rows],
        alternatives=alternatives,
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


# ── ❶ Why lines — phrasing.adequacy_line N개 ──────────────────────────────


def _build_why_lines(
    batch: ProductionBatch,
    equipment: Optional[EquipmentMaster],
    audit_rows: list,
    phrasing,
) -> list:
    """audit pass 룰 + equipment 적합성 룰을 자연어로 합성.

    audit_rows 의 constraints_applied 에서 result='pass' 룰을 anchor 로 사용.
    equipment 가 있으면 5-1 (SQ 적합성) + 10-3 (재질) + 3-3 (색상묶음) 도 추가.
    """
    from app.presentation.schemas.decision_card import DecisionLine

    lines: list[DecisionLine] = []

    # Equipment 기반 ❶ 자연어 (sheath / stranding / insulation)
    if equipment is not None:
        eq_name = equipment.equipment_name or equipment.equipment_code
        sq = int(float(batch.sq_mm2 or 0))

        # 5-1: SQ 작업범위
        if equipment.range_min and equipment.range_max:
            text = phrasing.adequacy_line(
                anchor="5-1",
                params={
                    "equipment": eq_name,
                    "min": int(equipment.range_min),
                    "max": int(equipment.range_max),
                    "sq": sq,
                },
            )
            if not text.startswith("["):  # 폴백 마커 제외
                lines.append(
                    DecisionLine(
                        anchor="why_line_5_1",
                        natural=text,
                        constraint_id="#5-1",
                        severity="ok",
                    )
                )

        # 10-3: 시스재질 (sheath only — Stranding/Insulation 은 다른 anchor)
        if (
            phrasing.process_key == "sheath"
            and equipment.material_limit
            and equipment.material_limit != "ALL"
        ):
            text = phrasing.adequacy_line(
                anchor="10-3",
                params={
                    "equipment": eq_name,
                    "allowed": equipment.material_limit,
                    "current": batch.conductor_material or "CU",
                },
            )
            if not text.startswith("["):
                lines.append(
                    DecisionLine(
                        anchor="why_line_10_3",
                        natural=text,
                        constraint_id="#10-3",
                        severity="ok",
                    )
                )

        # 3-3: 색상묶음 (sheath only)
        if phrasing.process_key == "sheath" and equipment.color_group:
            text = phrasing.adequacy_line(
                anchor="3-3",
                params={
                    "equipment": eq_name,
                    "eq_category": equipment.color_group,
                    "color": batch.sheath_color or "",
                },
            )
            if not text.startswith("["):
                lines.append(
                    DecisionLine(
                        anchor="why_line_3_3",
                        natural=text,
                        constraint_id="#3-3",
                        severity="ok",
                    )
                )

    # 4-2: 색상교체 — audit row 의 constraint_id='4-2' 보고
    for r in audit_rows:
        cs = r.constraints_applied or []
        for c in cs:
            cid = c.get("id") if isinstance(c, dict) else None
            if cid != "4-2":
                continue
            params = c.get("params") or {}
            minutes = int(params.get("minutes", 0) or 0)
            if minutes == 0:
                anchor_key = "4-2_save"
                anchor_params = {"color": batch.sheath_color or ""}
            else:
                anchor_key = "4-2_warn"
                anchor_params = {
                    "prev_color": params.get("prev_color", "?"),
                    "minutes": minutes,
                }
            text = phrasing.adequacy_line(anchor=anchor_key, params=anchor_params)
            if not text.startswith("["):
                lines.append(
                    DecisionLine(
                        anchor="why_line_4_2",
                        natural=text,
                        constraint_id="#4-2",
                        severity=phrasing.severity_for(
                            kind="color_change", value=float(minutes)
                        ),
                    )
                )

    return lines


# ── ❷ Impact block ───────────────────────────────────────────────────────


def _build_impact_block(batch: ProductionBatch, audit_rows: list, phrasing):
    """audit row 에서 metric 추출 → ImpactCell N개 + severity."""
    from app.presentation.schemas.decision_card import ImpactBlock, ImpactCell

    cells: list[ImpactCell] = []

    # 표준 4 metric 시도 — audit row 의 constraints_applied 에서
    metric_extractors = [
        ("color_change", "🎨 색상교체"),
        ("spec_change", "📏 규격교체"),
        ("predecessor_gap", "⏱️ 전공정과 갭"),
        ("due_slack_days", "📅 납기 여유"),
    ]
    for kind, label in metric_extractors:
        value = _extract_metric(audit_rows, kind)
        if value is None:
            continue
        sev = phrasing.severity_for(kind=kind, value=float(value))
        text = phrasing.impact_line(kind=kind, params={"value": value})
        cells.append(
            ImpactCell(
                label=label,
                value=text,
                severity=sev,
                kind=kind,
            )
        )

    # 소요시간 분해 — Step 3c-2 wire-up: setup + production + gap 합산
    duration_breakdown: list[dict] = []
    if batch.estimated_duration_min:
        duration_breakdown.append(
            {
                "label": "본 작업",
                "minutes": int(float(batch.estimated_duration_min) or 0),
                "source": "batch.estimated_duration_min",
            }
        )

    return ImpactBlock(cells=cells, duration_breakdown=duration_breakdown)


def _extract_metric(audit_rows: list, kind: str):
    """audit_log.constraints_applied 에서 kind 에 해당하는 value 추출."""
    for r in audit_rows:
        cs = r.constraints_applied or []
        for c in cs:
            if not isinstance(c, dict):
                continue
            params = c.get("params") or {}
            if kind == "color_change" and c.get("id") == "4-2":
                return params.get("minutes", 0)
            if kind == "spec_change" and c.get("id") in ("5-2-spec", "spec-change"):
                return params.get("minutes", 0)
            if kind == "predecessor_gap" and c.get("id") == "predecessor_gap":
                return params.get("minutes", 0)
            if kind == "due_slack_days" and c.get("id") == "due_slack":
                return params.get("days", 0.0)
    return None


# ── ❹ Equipment-day Gantt rows ────────────────────────────────────────────


def _build_equipment_day(
    db: Session,
    batch: ProductionBatch,
    task: Optional[ScheduleTask],
    gantt_builder,
) -> list:
    """본 batch 의 같은 설비 + 같은 날짜의 ScheduleTask row 집계 + 정렬."""
    from app.presentation.schemas.decision_card import GanttRow

    if task is None or not task.equipment_code or not task.start_datetime:
        return []

    same_day_start = task.start_datetime.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_day = same_day_start.replace(hour=23, minute=59, second=59)
    same_day_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.equipment_code == task.equipment_code,
            ScheduleTask.start_datetime >= same_day_start,
            ScheduleTask.start_datetime <= next_day,
            ScheduleTask.run_label == batch.run_label,
        )
        .all()
    )

    # gantt_builder.sort_key 로 정렬 (sheath 의 경우 cluster_sort_key 미러)
    rows_view: list = []
    for t in same_day_tasks:
        rows_view.append(
            GanttRow(
                task_id=t.task_id,
                batch_id=t.batch_id,
                batch_group=t.batch_group or "",
                label=t.batch_group or f"task-{t.task_id}",
                start_at=t.start_datetime,
                end_at=t.end_datetime,
                is_self=(t.batch_id == batch.batch_id),
            )
        )
    # 시각 단순 정렬 — 더 정교한 cluster_sort_key 미러는 build_card 가 cluster_meta
    # 를 가지지 않으므로 시간 순으로. 정렬 라벨은 sheath 의 경우 memory 표기.
    rows_view.sort(key=lambda r: r.start_at)
    return rows_view


# ── ❺ Post-hoc bundle metrics ────────────────────────────────────────────


def _post_hoc_bundle_metrics(
    db: Session,
    batch: ProductionBatch,
    task: Optional[ScheduleTask],
    audit_rows: list,
) -> tuple[Optional[dict], list[dict]]:
    """orchestrator 손대지 않고 본 묶음 + 인접 cluster N=5 score 합성.

    chosen 은 본 batch_group 의 metric, alternatives 는 같은 run_label 안의
    다른 batch_group 들. 솔버 결과 위에서 phrasing-only rebuild.
    """
    if not batch.batch_group:
        return None, []

    chosen = {
        "label": f"본 묶음 ({batch.batch_group})",
        "color_change_min": _extract_metric(audit_rows, "color_change") or 0,
        "spec_change_min": _extract_metric(audit_rows, "spec_change") or 0,
        "duration_min": int(float(batch.estimated_duration_min or 0)),
        "score": 0.0,  # chosen 의 score 는 표시용 — alternative 들의 비교 기준
    }

    # 인접 cluster — 같은 run_label, 다른 batch_group 의 ScheduleTask
    same_run_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == batch.run_label,
            ScheduleTask.batch_group != batch.batch_group,
        )
        .limit(20)  # 한도 — 50ms 보장
        .all()
    )
    seen_groups: set = set()
    alts: list[dict] = []
    for t in same_run_tasks:
        if t.batch_group in seen_groups or not t.batch_group:
            continue
        seen_groups.add(t.batch_group)
        # 단순 score — duration 차이를 score 로 (실제 phrasing-only post-hoc)
        dur_min = int(
            (t.end_datetime - t.start_datetime).total_seconds() / 60
            if t.end_datetime and t.start_datetime
            else 0
        )
        alts.append(
            {
                "label": f"대안 묶음 {t.batch_group}",
                "color_change_min": 0,
                "spec_change_min": 0,
                "duration_min": dur_min,
                "score": float(dur_min) - chosen["duration_min"],
            }
        )
        if len(alts) >= 5:
            break
    return chosen, alts


# ── ❻ Alternatives — filter_out audit row ─────────────────────────────────


def _build_alternatives(audit_rows: list, phrasing) -> list:
    """audit_log.action_type='filter_out' row → phrasing.filter_out_reason."""
    from app.presentation.schemas.decision_card import Alternative

    out: list[Alternative] = []
    for r in audit_rows:
        if r.action_type != "filter_out":
            continue
        cs = r.constraints_applied or []
        for c in cs:
            if not isinstance(c, dict):
                continue
            code = c.get("id", "")
            params = (c.get("params") or {}) | {"detail": c.get("detail", "")}
            text = phrasing.filter_out_reason(code=code, params=params)
            out.append(
                Alternative(
                    candidate_label=str(params.get("equipment", code)),
                    rejected_reason=text,
                    severity="fail",
                    code=code,
                )
            )
    return out


# ── placement_calc — 시작·종료 시각 계산 근거 popover ─────────────────────


def _build_placement_calc(task: Optional[ScheduleTask], audit_rows: list) -> dict:
    """⓵ '시작 = max(예정ready, 직전묶음끝+셋업, 캘린더가용)' popover 데이터."""
    if task is None:
        return {}
    return {
        "start_at": (task.start_datetime.isoformat() if task.start_datetime else None),
        "end_at": task.end_datetime.isoformat() if task.end_datetime else None,
        "duration_minutes": (
            int((task.end_datetime - task.start_datetime).total_seconds() / 60)
            if (task.start_datetime and task.end_datetime)
            else 0
        ),
        "rationale": (
            "시작 = max(예정ready, 직전묶음끝+셋업, 캘린더가용) — "
            "ScheduleTask.start_datetime source."
        ),
    }


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
