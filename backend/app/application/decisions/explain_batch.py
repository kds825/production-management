"""단일 배치의 스케줄링 결정을 자연어로 설명한다.

직전 위치: `services/llm_explainer.py::explain_decision_sync` + 보조함수
(`_build_context`, `_template_explanation`). Phase 2 step 3 에서 분리.

흐름:
  1. ProductionBatch + EquipmentMaster + AuditLog 로드 → context 문자열 구성
  2. LLM 호출 (`_llm_client.call_llm_sync`) — 실패 시 None 반환
  3. LLM 성공 시 narrator.detect_hallucinations 로 한국어 명사 사후 검증.
     환각 발견 시 템플릿 폴백.
  4. LLM 미설정/모두 실패 시 `_template_explanation` 폴백.
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from app.application.decisions._llm_client import call_llm_sync
from app.application.decisions.narrator import detect_hallucinations
from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


def explain_decision_sync(
    batch_id: int,
    db: Session,
    task_id: Optional[int] = None,
) -> dict:
    """동기 버전 — LLM 동기 호출 시도 후 실패 시 템플릿 폴백.

    환각 검증 (Phase 2): LLM 결과의 한국어 명사 중 본 batch + audit logs 의
    constraint 명/설비명/공정명 catalog 에 없는 것이 발견되면 템플릿 폴백.
    """
    batch = (
        db.query(ProductionBatch).filter(ProductionBatch.batch_id == batch_id).first()
    )
    if not batch:
        # 간트가 TASK-{task_id} 형식으로 보내는 경우: schedule_task.task_id로 재조회
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == batch_id).first()
        if task:
            batch_id = task.batch_id
            batch = (
                db.query(ProductionBatch)
                .filter(ProductionBatch.batch_id == batch_id)
                .first()
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

    # Try sync LLM call first
    context = _build_context(batch, equipment, logs)
    llm_result = call_llm_sync(context)
    if llm_result:
        catalog = _build_korean_catalog(batch, equipment, logs)
        if not detect_hallucinations(llm_result, catalog):
            return {"explanation": llm_result, "source": "llm", "batch_id": batch_id}
        # LLM 환각 감지 → 템플릿 폴백 + source 표기

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
        f"- 총 길이: {batch.total_length_m}m (5% 불량 버퍼 포함) + 색상교체 여척 {batch.extra_length_m}m"
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


def _build_korean_catalog(batch, equipment, logs) -> set[str]:
    """LLM 결과 검증용 한국어 명사 catalog 구성.

    포함:
      - batch 의 공정명 / 거래처명 / 색상 / 재질
      - 설비명 / 색상그룹
      - audit log 의 제약조건 한국어 이름
    """
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
