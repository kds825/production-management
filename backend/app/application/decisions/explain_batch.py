"""단일 배치의 스케줄링 결정을 자연어로 설명한다.

직전 위치: `services/llm_explainer.py::explain_decision_sync` + 보조함수
(`_build_context`, `_template_explanation`). Phase 2 step 3 에서 분리.
Phase 6 (decision_card) Step 3c-1: 본문 facts 를 phrasing.py 재사용으로
변경 (2nd opinion §3 정합성). LLM 은 1-line tagline 만 책임.

흐름:
  1. ProductionBatch + EquipmentMaster + AuditLog 로드
  2. **본문 facts** 는 phrasing.py adequacy/impact/handoff 합성 (deterministic)
  3. LLM tagline 1줄 호출 — 환각 검증 narrator 적용. 실패/환각 시 phrasing.
     verdict_summary 폴백
  4. 응답: {explanation, tagline, facts, source, batch_id} — explanation
     은 tagline + 빈줄 + facts 로 합성 (호환성 유지)

decision_card vs explain_batch 두 자연어 경로의 facts 가 정확히 같은
phrasing module 을 통과하므로 정합성 보장.
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from app.application.decisions._llm_client import call_llm_sync
from app.application.decisions.narrator import detect_hallucinations
from app.application.decisions.phrasing import (
    get_phrasing_provider,
    register_phrasing_provider,
)
from app.application.decisions.phrasing_providers import (
    DefaultPhrasingProvider,
    InsulationPhrasingProvider,
    OutsourcePhrasingProvider,
    SheathPhrasingProvider,
    StrandingPhrasingProvider,
)
from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


_TAGLINE_SYSTEM_PROMPT = """당신은 전선 제조 공장의 생산계획 AI 어시스턴트입니다.
배치 스케줄링 결과를 한 문장(40자 이내)으로 요약해 주세요.

규칙:
- 정확히 한 문장 (40자 이내).
- 도메인 용어만 사용 (시스/연선/SQ/색상교체/규격교체/설비명/거래처명).
- 중립적 표현 — 추측이나 의견 제외.
- 본 카드의 facts 와 모순되지 않게 — facts 는 별도 본문에 포함됨.
"""


def explain_decision_sync(
    batch_id: int,
    db: Session,
    task_id: Optional[int] = None,
) -> dict:
    """동기 버전 — LLM 1-line tagline + phrasing 본문 합성.

    Returns:
      {
        "explanation": "<tagline>\\n\\n<facts>",  # 호환성
        "tagline": "<1줄 LLM/template>",
        "facts": "<phrasing 합성 본문>",
        "source": "llm" | "template",  # tagline 의 source
        "batch_id": int,
      }
    """
    batch, equipment, logs = _load_context(db, batch_id, task_id)
    if batch is None:
        return {
            "explanation": f"배치 {batch_id}를 찾을 수 없습니다.",
            "tagline": "",
            "facts": "",
            "source": "error",
            "batch_id": batch_id,
        }

    # ── 본문 facts: phrasing.py deterministic 합성 (decision_card 와 정합성) ──
    facts = _build_facts_via_phrasing(batch, equipment, logs)

    # ── tagline: LLM 1-line + 환각 검증 → 실패 시 verdict_summary 폴백 ──
    tagline, source = _make_tagline(batch, equipment, logs, facts)

    explanation = f"{tagline}\n\n{facts}".strip()
    return {
        "explanation": explanation,
        "tagline": tagline,
        "facts": facts,
        "source": source,
        "batch_id": batch.batch_id,
    }


# ── DB 로드 (TASK-{task_id} 형식 대응 — 기존 동작 유지) ───────────────────


def _load_context(
    db: Session, batch_id: int, task_id: Optional[int]
) -> tuple[Optional[ProductionBatch], Optional[EquipmentMaster], list[AuditLog]]:
    batch = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_id == batch_id).first()
    )
    if not batch:
        # 간트가 TASK-{task_id} 형식으로 보내는 경우: schedule_task 로 재조회
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == batch_id).first()
        if task:
            batch_id = task.batch_id
            batch = (
                db.query(ProductionBatch)
                .filter(ProductionBatch.batch_id == batch_id)
                .first()
            )
        if not batch:
            return None, None, []

    equipment: Optional[EquipmentMaster] = None
    if batch.equipment_code:
        equipment = (
            db.query(EquipmentMaster)
            .filter(EquipmentMaster.equipment_code == batch.equipment_code)
            .first()
        )

    query = db.query(AuditLog).filter(AuditLog.batch_id == batch_id)
    if task_id:
        query = query.filter(AuditLog.task_id == task_id)
    logs = query.order_by(AuditLog.created_at.asc()).all()
    return batch, equipment, logs


# ── 본문 facts — phrasing.py 재사용 (decision_card 와 동일 경로) ─────────


def _build_facts_via_phrasing(
    batch: ProductionBatch,
    equipment: Optional[EquipmentMaster],
    logs: list[AuditLog],
) -> str:
    """phrasing.adequacy_line / handoff_line / impact_line 합성.

    phrasing 가 lifespan startup 에 register 안 된 환경 (단위 테스트 등) 을
    위해 본 함수에서 즉석 등록. 이미 등록되었으면 idempotent.
    """
    _ensure_phrasing_registered()
    phrasing = get_phrasing_provider(batch)

    parts: list[str] = []
    eq_name = (
        equipment.equipment_name
        if equipment and equipment.equipment_name
        else (batch.equipment_code or "미배정")
    )

    # ❶ adequacy — equipment + SQ 자연어
    parts.append(
        phrasing.adequacy_line(
            anchor="5-1",
            params={
                "equipment": eq_name,
                "min": int(equipment.range_min)
                if equipment and equipment.range_min
                else 0,
                "max": int(equipment.range_max)
                if equipment and equipment.range_max
                else 0,
                "sq": int(float(batch.sq_mm2 or 0)),
            },
        )
    )

    # ❸ handoff — 시스/외주 변형 (TFR-GV / 단선접지선 분기는 phrasing 안에서)
    pg_upper = (batch.product_group or "").upper()
    parts.append(
        phrasing.handoff_line(
            params={
                "is_tfrgv": "TFR-GV" in pg_upper and float(batch.sq_mm2 or 0) > 25,
                "is_bare_ground": "TFR-GV" in pg_upper
                and float(batch.sq_mm2 or 0) <= 25,
                "predecessor": "절연",
                "matched_sm_id": batch.wip_matched_id,
                "remainder_pct": 0.0,
                "vendor_name": "외주",
                "lead_days": 0,
            }
        )
    )

    # 적용된 제약조건 — audit log 의 자연어 줄 (있으면)
    constraint_names: list[str] = []
    for log in logs:
        if log.constraints_applied:
            for c in log.constraints_applied:
                name = c.get("name") if isinstance(c, dict) else None
                result = c.get("result") if isinstance(c, dict) else None
                if name and result == "pass":
                    constraint_names.append(name)
    if constraint_names:
        parts.append(f"적용된 제약조건: {', '.join(sorted(set(constraint_names)))}.")

    # 납기 + 거래처 + 소요시간 (도메인 facts — phrasing impact_line 가 아닌
    # 단순 리포팅. phrasing 으로 합성하면 verdict_summary 와 중복)
    if batch.due_date:
        parts.append(f"납기일: {batch.due_date}.")
    if batch.customer_name:
        parts.append(
            f"거래처 우선순위: P{batch.customer_priority} ({batch.customer_name})."
        )
    if batch.estimated_duration_min:
        hours = float(batch.estimated_duration_min) / 60
        parts.append(
            f"예상 소요시간: {batch.estimated_duration_min:.0f}분 ({hours:.1f}시간), "
            f"선속 {batch.line_speed_mpm} m/min 기준."
        )

    return " ".join(p for p in parts if p)


def _ensure_phrasing_registered() -> None:
    """phrasing registry 미등록 환경 (단위 테스트 등) 에서 즉석 register.

    main.py lifespan startup 이 정식 등록 경로 — 본 함수는 fail-soft 보강.
    이미 등록되었으면 register 가 idempotent (같은 key 덮어쓰기).
    """
    from app.application.decisions.phrasing import _REGISTRY  # noqa: PLC0415

    if not _REGISTRY:
        for p in (
            DefaultPhrasingProvider(),
            SheathPhrasingProvider(),
            StrandingPhrasingProvider(),
            InsulationPhrasingProvider(),
            OutsourcePhrasingProvider(),
        ):
            register_phrasing_provider(p)


# ── tagline — LLM 1-line + 환각 검증 → 폴백 ──────────────────────────────


def _make_tagline(
    batch: ProductionBatch,
    equipment: Optional[EquipmentMaster],
    logs: list[AuditLog],
    facts: str,
) -> tuple[str, str]:
    """LLM 1줄 호출 → 환각 통과 시 LLM tagline / 실패 시 phrasing.verdict_summary.

    Returns: (tagline, source)  source ∈ {'llm', 'template'}.
    """
    catalog = _build_korean_catalog(batch, equipment, logs)

    user_prompt = (
        "다음 배치 정보를 한 문장(40자 이내)으로 요약해 주세요. "
        "facts 와 모순되지 않게:\n\n"
        f"facts:\n{facts}"
    )
    raw = call_llm_sync(
        facts,  # legacy `context` positional — 실제로는 user_prompt 가 우선
        system_prompt=_TAGLINE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    if raw:
        # 첫 줄만 — LLM 이 길게 응답해도 1줄로 자른다
        tagline = raw.strip().splitlines()[0] if raw.strip() else ""
        # 따옴표/마크다운 제거
        tagline = re.sub(r"^[\"'`]|[\"'`]$", "", tagline.strip()).strip()
        if tagline and not detect_hallucinations(tagline, catalog):
            return tagline, "llm"

    # 폴백 — phrasing.verdict_summary (deterministic)
    phrasing = get_phrasing_provider(batch)
    try:
        from types import SimpleNamespace

        audit = SimpleNamespace(
            color_change_min=0, spec_change_min=0, due_slack_days=0.0
        )
        tagline = phrasing.verdict_summary(
            batch=batch, audit=audit, solver=None, schedule_task=None
        )
    except Exception:  # noqa: BLE001 — 안전 폴백 우선
        tagline = f"배치 {batch.batch_id} — {batch.process_name or ''} 처리"
    return tagline, "template"


def _build_korean_catalog(batch, equipment, logs) -> set[str]:
    """LLM tagline 검증용 catalog. tagline 만 검증 — facts 는 deterministic 이라 검증 불요."""
    catalog: set[str] = set()
    if batch.process_name:
        catalog.add(batch.process_name)
    if batch.customer_name:
        catalog.add(batch.customer_name)
    if batch.sheath_color:
        catalog.add(batch.sheath_color)
    if batch.conductor_material:
        catalog.add(batch.conductor_material)
    if batch.product_group:
        catalog.add(batch.product_group)
    if equipment:
        if equipment.equipment_name:
            catalog.add(equipment.equipment_name)
        if equipment.process_name:
            catalog.add(equipment.process_name)
        if equipment.color_group:
            catalog.add(equipment.color_group)
    for log in logs:
        if log.constraints_applied:
            for c in log.constraints_applied:
                name = c.get("name") if isinstance(c, dict) else None
                if name:
                    catalog.add(name)
    return catalog
