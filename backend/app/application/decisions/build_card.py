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
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.presentation.schemas.decision_card import (
    DecisionCard,
    HandoffBlock,
    OutsourceHandoffBlock,
    WipMatchBlock,
)

# Phase 1 Task 1.8/1.9 (B-5.1/B-5.2) — helper 함수 sub-module 분할.
from app.application.decisions._card_helpers import (
    _load_context,
    _empty_card,
    _process_label,
    _sub_chip,
    _format_placement_text,
    _build_debug_block,
    __dict_for_bundle,
)
from app.application.decisions._card_why import (
    _build_why_lines,
    _extract_metric,
    _scenario_key_hint,
    _safe_verdict_summary,
)
from app.application.decisions._card_impact import (
    _build_impact_block,
    _build_equipment_day,
    _post_hoc_bundle_metrics,
    _build_alternatives,
    _build_placement_calc,
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
