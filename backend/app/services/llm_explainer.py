"""LLM 기반 스케줄링 결정 설명 생성 — audit trail JSON을 자연어로 변환"""

import json
import os
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.equipment_master import EquipmentMaster

# PwC GenAI Gateway 또는 직접 Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = os.getenv(
    "ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"
)
MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")


SYSTEM_PROMPT = """당신은 전선 제조 공장의 생산계획 AI 어시스턴트입니다.
스케줄링 시스템이 내린 결정의 근거를 공장 관리자가 이해할 수 있는 한국어로 설명합니다.

규칙:
- 전문 용어는 공장에서 쓰는 표현 그대로 사용 (SQ, 틀단위, 연선, 시스 등)
- "~에 배치했습니다" 형식의 서술형 문장 사용
- 적용된 제약조건을 하나씩 나열하되, 이유를 함께 설명
- 대안이 있었다면 왜 기각했는지도 설명
- 납기 준수 여부를 명확히 언급
- 3~5문장으로 간결하게"""


async def explain_decision(
    batch_id: int,
    db: Session,
    task_id: Optional[int] = None,
) -> dict:
    """특정 배치/작업의 스케줄링 결정을 자연어로 설명"""

    # Gather context
    batch = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_id == batch_id).first()
    )
    if not batch:
        return {
            "explanation": f"배치 {batch_id}를 찾을 수 없습니다.",
            "source": "error",
        }

    equipment = None
    if batch.equipment_code:
        equipment = (
            db.query(EquipmentMaster)
            .filter(EquipmentMaster.equipment_code == batch.equipment_code)
            .first()
        )

    # Get audit logs for this batch
    query = db.query(AuditLog).filter(AuditLog.batch_id == batch_id)
    if task_id:
        query = query.filter(AuditLog.task_id == task_id)
    logs = query.order_by(AuditLog.created_at.asc()).all()

    # Build context for LLM
    context = _build_context(batch, equipment, logs)

    # Try LLM explanation
    if ANTHROPIC_API_KEY:
        explanation = await _call_llm(context)
        if explanation:
            return {"explanation": explanation, "source": "llm", "batch_id": batch_id}

    # Fallback: template-based explanation
    explanation = _template_explanation(batch, equipment, logs)
    return {"explanation": explanation, "source": "template", "batch_id": batch_id}


def explain_decision_sync(
    batch_id: int,
    db: Session,
    task_id: Optional[int] = None,
) -> dict:
    """동기 버전 — LLM 없이 템플릿 기반 설명"""
    batch = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_id == batch_id).first()
    )
    if not batch:
        return {
            "explanation": f"배치 {batch_id}를 찾을 수 없습니다.",
            "source": "error",
        }

    equipment = None
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

    explanation = _template_explanation(batch, equipment, logs)
    return {"explanation": explanation, "source": "template", "batch_id": batch_id}


def _build_context(batch, equipment, logs) -> str:
    """LLM에 전달할 컨텍스트 문자열 구성"""
    parts = []

    parts.append("## 배치 정보")
    parts.append(f"- 제품군: {batch.product_group}")
    parts.append(f"- 규격: {batch.core_count}C x {batch.sq_mm2}SQ")
    parts.append(f"- 색상: {batch.sheath_color or batch.core_colors or '미지정'}")
    parts.append(
        f"- 거래처: {batch.customer_name} (우선순위 P{batch.customer_priority})"
    )
    parts.append(f"- 납기일: {batch.due_date}")
    parts.append(f"- 공정: {batch.process_name}")
    parts.append(
        f"- 총 길이: {batch.total_length_m}m (여척 {batch.extra_length_m}m 포함)"
    )
    parts.append(f"- 선속: {batch.line_speed_mpm} m/min")
    parts.append(f"- 예상 소요: {batch.estimated_duration_min}분")
    parts.append(f"- 재질: {batch.conductor_material}")

    if equipment:
        parts.append("\n## 배정 설비")
        parts.append(f"- 설비: {equipment.equipment_name} ({equipment.equipment_code})")
        parts.append(f"- 공정: {equipment.process_name}")
        parts.append(f"- 재질 제한: {equipment.material_limit or 'ALL'}")
        parts.append(
            f"- 작업 범위: {equipment.range_min}~{equipment.range_max} {equipment.range_unit}"
        )
        if equipment.color_group:
            parts.append(f"- 색상 그룹: {equipment.color_group}")

    if logs:
        parts.append("\n## 적용된 제약조건")
        for log in logs:
            parts.append(f"- [{log.action_type}] {log.decision_reason}")
            if log.constraints_applied:
                for c in log.constraints_applied:
                    result = c.get("result", "?")
                    parts.append(
                        f"  - {c.get('id', '?')} {c.get('name', '?')}: {result} — {c.get('detail', '')}"
                    )
            if log.alternatives_considered:
                parts.append(
                    f"  - 대안 검토: {json.dumps(log.alternatives_considered, ensure_ascii=False)}"
                )

    return "\n".join(parts)


async def _call_llm(context: str) -> Optional[str]:
    """Anthropic Claude API 호출"""
    if not ANTHROPIC_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL,
                    "max_tokens": 500,
                    "system": SYSTEM_PROMPT,
                    "messages": [
                        {
                            "role": "user",
                            "content": f"다음 스케줄링 결정의 근거를 공장 관리자에게 설명해주세요:\n\n{context}",
                        }
                    ],
                },
            )
            if response.status_code == 200:
                data = response.json()
                return data["content"][0]["text"]
    except Exception:
        pass

    return None


def _template_explanation(batch, equipment, logs) -> str:
    """LLM 없이 구조화된 템플릿 기반 설명 생성"""
    parts = []

    # Opening
    eq_name = equipment.equipment_name if equipment else "미배정"
    parts.append(
        f"이 작업({batch.product_group} {batch.core_count}C x {batch.sq_mm2}SQ)은 "
        f"설비 [{eq_name}]에 배치되었습니다."
    )

    # Equipment selection reason
    if equipment:
        reasons = []
        if equipment.range_min and equipment.range_max:
            reasons.append(
                f"SQ {batch.sq_mm2}이 설비 작업범위 "
                f"{equipment.range_min}~{equipment.range_max}{equipment.range_unit} 내"
            )
        if equipment.material_limit and equipment.material_limit != "ALL":
            reasons.append(
                f"재질 {batch.conductor_material}이 {equipment.material_limit} 전용 설비에 적합"
            )
        if equipment.color_group:
            reasons.append(
                f"색상 {batch.sheath_color or '미지정'}이 색상그룹 '{equipment.color_group}'에 적합"
            )
        if reasons:
            parts.append("선택 이유: " + ", ".join(reasons) + ".")

    # Constraint application
    if logs:
        constraint_names = set()
        for log in logs:
            if log.constraints_applied:
                for c in log.constraints_applied:
                    name = c.get("name", "")
                    result = c.get("result", "")
                    if name and result == "pass":
                        constraint_names.add(name)
        if constraint_names:
            parts.append(f"적용된 제약조건: {', '.join(sorted(constraint_names))}.")

    # Due date
    if batch.due_date:
        parts.append(f"납기일: {batch.due_date}.")
        parts.append(
            f"거래처 우선순위: P{batch.customer_priority} ({batch.customer_name})."
        )

    # Duration
    if batch.estimated_duration_min:
        hours = batch.estimated_duration_min / 60
        parts.append(
            f"예상 소요시간: {batch.estimated_duration_min:.0f}분 ({hours:.1f}시간), "
            f"선속 {batch.line_speed_mpm} m/min 기준."
        )

    return " ".join(parts)
