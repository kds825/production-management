"""LLM 기반 스케줄링 결정 설명 생성 — audit trail JSON을 자연어로 변환"""

import json
import os
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy.orm import Session

# .env 파일에서 환경변수 로드 (서버 프로세스에서도 동작하도록)
_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask

# Provider selection: "openai" | "anthropic" (default: "openai")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# gpt-4.1은 2025년 기준 최신 안정 모델
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

# Anthropic (PwC GenAI Gateway 또는 직접 Anthropic API)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = os.getenv(
    "ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"
)
ANTHROPIC_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")


SYSTEM_PROMPT = """당신은 전선 제조 공장의 생산계획 AI 어시스턴트입니다.
스케줄링 시스템이 내린 결정의 근거를 공장 관리자가 이해할 수 있는 한국어로 설명합니다.

규칙:
- 전문 용어는 공장에서 쓰는 표현 그대로 사용 (SQ, 틀단위, 연선, 시스 등)
- "~에 배치했습니다" 형식의 서술형 문장 사용
- 적용된 제약조건을 하나씩 나열하되, 이유를 함께 설명
- 대안이 있었다면 왜 기각했는지도 설명
- 납기 준수 여부를 명확히 언급
- 3~5문장으로 간결하게"""

SUMMARY_SYSTEM_PROMPT = """당신은 전선 제조 공장의 생산계획 AI 어시스턴트입니다.
배치 목록을 분석하여 공장 관리자에게 핵심 인사이트를 요약합니다.

규칙:
- 전문 용어는 공장에서 쓰는 표현 그대로 사용
- 리스크(납기 촉박, 설비 과부하, 재질 혼용 등)를 우선 보고
- 3~5개 핵심 포인트를 bullet으로 반환
- JSON 형식으로 응답: {"highlights": ["...", "..."], "riskCount": N}"""


async def explain_decision(
    batch_id: int,
    db: Session,
    task_id: Optional[int] = None,
) -> dict:
    """특정 배치/작업의 스케줄링 결정을 자연어로 설명"""

    # Gather context — batch_id가 실제로 schedule_task.task_id일 수 있으므로 먼저 확인
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

    # Get audit logs for this batch
    query = db.query(AuditLog).filter(AuditLog.batch_id == batch_id)
    if task_id:
        query = query.filter(AuditLog.task_id == task_id)
    logs = query.order_by(AuditLog.created_at.asc()).all()

    # Build context for LLM
    context = _build_context(batch, equipment, logs)

    # Try LLM explanation — configured provider first, then fallback to template
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
    """동기 버전 — LLM 동기 호출 시도 후 실패 시 템플릿 폴백"""
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
    llm_result = _call_llm_sync(context)
    if llm_result:
        return {"explanation": llm_result, "source": "llm", "batch_id": batch_id}

    explanation = _template_explanation(batch, equipment, logs)
    return {"explanation": explanation, "source": "template", "batch_id": batch_id}


def generate_batch_summary_sync(run_label: str, db: Session) -> dict:
    """run_label 전체 배치를 분석하여 AI 요약을 생성한다.

    LLM 호출에 성공하면 highlights/riskCount를 LLM 결과에서 가져오고,
    실패하면 통계 기반 템플릿으로 폴백한다.
    """
    batches = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).all()
    )

    total_batches = len(batches)
    if total_batches == 0:
        return {
            "totalBatches": 0,
            "totalProductionM": 0.0,
            "riskCount": 0,
            "highlights": ["해당 run_label의 배치 데이터가 없습니다."],
            "insights": [],
        }

    total_m = sum(float(b.total_length_m or 0) for b in batches)

    # 공정별 집계
    by_process: dict[str, int] = {}
    for b in batches:
        p = b.process_name or "기타"
        by_process[p] = by_process.get(p, 0) + 1

    # 거래처별 집계
    by_customer: dict[str, int] = {}
    for b in batches:
        c = b.customer_name or "미지정"
        by_customer[c] = by_customer.get(c, 0) + 1

    # 납기일 범위
    due_dates = [b.due_date for b in batches if b.due_date]
    earliest = min(due_dates) if due_dates else None
    latest = max(due_dates) if due_dates else None

    # LLM용 컨텍스트 구성
    process_summary = ", ".join(f"{p}:{n}건" for p, n in by_process.items())
    customer_summary = ", ".join(
        f"{c}:{n}건" for c, n in sorted(by_customer.items(), key=lambda x: -x[1])[:5]
    )
    context = (
        f"총 배치 수: {total_batches}건\n"
        f"총 생산량: {total_m:,.0f}m\n"
        f"공정별: {process_summary}\n"
        f"거래처별(상위5): {customer_summary}\n"
        f"납기일 범위: {earliest} ~ {latest}"
    )

    # LLM 호출
    llm_raw = _call_llm_sync(
        context,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=f"다음 생산 배치 현황을 분석하여 핵심 인사이트를 JSON으로 요약해주세요:\n\n{context}",
    )
    risk_count = 0
    highlights: list[str] = []

    if llm_raw:
        try:
            # LLM이 JSON 블록을 마크다운 코드펜스로 감쌀 수 있으므로 추출
            raw = llm_raw.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw.strip())
            highlights = parsed.get("highlights", [])
            risk_count = int(parsed.get("riskCount", 0))
        except Exception:
            # JSON 파싱 실패 시 텍스트를 그대로 bullet으로 사용
            highlights = [
                line.strip("- •").strip()
                for line in llm_raw.splitlines()
                if line.strip()
            ]

    # 템플릿 폴백 — LLM 결과가 없거나 highlights가 비어 있으면 통계 기반 생성
    if not highlights:
        highlights = []
        highlights.append(f"총 {total_batches}건의 배치, {total_m:,.0f}m 생산 계획")
        highlights.append(f"공정 구성: {process_summary}")
        if earliest and latest:
            highlights.append(f"납기일 범위: {earliest} ~ {latest}")
        top_customer = max(by_customer.items(), key=lambda x: x[1], default=None)
        if top_customer:
            highlights.append(
                f"최다 수주 거래처: {top_customer[0]} ({top_customer[1]}건)"
            )

    return {
        "totalBatches": total_batches,
        "totalProductionM": round(total_m, 1),
        "riskCount": risk_count,
        "highlights": highlights,
        "insights": [],
    }


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
    """설정된 provider로 LLM 호출. 실패 시 None 반환 → 템플릿 fallback."""
    if LLM_PROVIDER == "openai" and OPENAI_API_KEY:
        return await _call_openai(context)
    elif LLM_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        return await _call_anthropic(context)
    return None


async def _call_openai(context: str) -> Optional[str]:
    """OpenAI Chat Completions API 호출"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"다음 스케줄링 결정의 근거를 공장 관리자에게 설명해주세요:\n\n{context}",
                        },
                    ],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
    except Exception:
        pass
    return None


async def _call_anthropic(context: str) -> Optional[str]:
    """Anthropic Claude API 호출 (PwC GenAI Gateway 또는 직접 호출)"""
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
                    "model": ANTHROPIC_MODEL,
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


def _call_llm_sync(
    context: str,
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
) -> Optional[str]:
    """동기 LLM 호출 — httpx 동기 클라이언트 사용.

    system_prompt를 명시하지 않으면 기본 SYSTEM_PROMPT를 사용한다.
    user_prompt를 명시하지 않으면 기본 스케줄링 설명 요청 문구를 사용한다.
    """
    effective_system = system_prompt if system_prompt is not None else SYSTEM_PROMPT
    effective_user = (
        user_prompt
        if user_prompt is not None
        else f"다음 스케줄링 결정의 근거를 공장 관리자에게 설명해주세요:\n\n{context}"
    )
    if LLM_PROVIDER == "openai" and OPENAI_API_KEY:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": OPENAI_MODEL,
                        "messages": [
                            {"role": "system", "content": effective_system},
                            {"role": "user", "content": effective_user},
                        ],
                        "max_completion_tokens": 500,
                        "temperature": 0.3,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
        except Exception:
            pass
    elif LLM_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    ANTHROPIC_API_URL,
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": ANTHROPIC_MODEL,
                        "max_tokens": 500,
                        "system": effective_system,
                        "messages": [{"role": "user", "content": effective_user}],
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
