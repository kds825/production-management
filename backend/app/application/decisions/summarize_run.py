"""run_label 전체 배치의 AI 요약 생성.

직전 위치: `services/llm_explainer.py::generate_batch_summary_sync`. Phase 2
step 2 에서 분리. 동작 동일 (LLM 호출 + JSON 파싱 폴백 + 통계 템플릿).

추가 (Phase 2): LLM 응답의 한국어 명사를 catalog 와 비교해 환각이 발견되면
템플릿 결과로 fallback. catalog 는 본 run 의 batch.process_name /
customer_name / 공정/설비 등 도메인 식별자 union.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.application.decisions._llm_client import call_llm_sync
from app.application.decisions.narrator import detect_hallucinations
from app.application.decisions.risk_detector import _detect_rule_based_risks
from app.infrastructure.models.production_batch import ProductionBatch


SUMMARY_SYSTEM_PROMPT = """당신은 전선 제조 공장의 생산계획 AI 어시스턴트입니다.
배치 목록을 분석하여 공장 관리자에게 핵심 인사이트를 요약합니다.

규칙:
- 전문 용어는 공장에서 쓰는 표현 그대로 사용
- 리스크(납기 촉박, 설비 과부하, 재질 혼용 등)를 우선 보고
- 3~5개 핵심 포인트를 bullet으로 반환
- JSON 형식으로 응답: {"highlights": ["...", "..."], "riskCount": N}"""


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
            "totalGroups": 0,
            "totalProductionM": 0.0,
            "riskCount": 0,
            "highlights": ["해당 run_label의 배치 데이터가 없습니다."],
            "insights": [],
            "source": "rule-based",
        }

    total_m = sum(float(b.total_length_m or 0) for b in batches)
    # 고유 배치 그룹 수 (간트 블록 단위)
    total_groups = len({b.batch_group for b in batches if b.batch_group})

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

    # --- 규칙 기반 리스크 감지 (LLM 성공/실패 무관하게 항상 실행) ---
    rule_risk_count, rule_risk_highlights = _detect_rule_based_risks(batches)

    # --- LLM 호출 ---
    llm_raw = call_llm_sync(
        context,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=f"다음 생산 배치 현황을 분석하여 핵심 인사이트를 JSON으로 요약해주세요:\n\n{context}",
    )
    llm_succeeded = False
    llm_risk_count = 0
    llm_highlights: list[str] = []

    if llm_raw:
        # 환각 검증 (Phase 2): catalog 외 한국어 명사가 등장하면 LLM 결과 거부.
        catalog = _build_summary_catalog(batches, by_process, by_customer)
        if not detect_hallucinations(llm_raw, catalog):
            try:
                # LLM이 JSON 블록을 마크다운 코드펜스로 감쌀 수 있으므로 추출
                raw = llm_raw.strip()
                if "```" in raw:
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                parsed = json.loads(raw.strip())
                llm_highlights = parsed.get("highlights", [])
                llm_risk_count = int(parsed.get("riskCount", 0))
                if llm_highlights:
                    llm_succeeded = True
            except Exception:
                # JSON 파싱 실패 시 텍스트를 그대로 bullet으로 사용
                llm_highlights = [
                    line.strip("- •").strip()
                    for line in llm_raw.splitlines()
                    if line.strip()
                ]
                if llm_highlights:
                    llm_succeeded = True

    # --- LLM 성공 시: LLM 인사이트 + 규칙 기반 리스크 병합 ---
    if llm_succeeded:
        # LLM highlights 뒤에 규칙 기반 리스크 추가 (중복 방지)
        merged_highlights = list(llm_highlights)
        for rh in rule_risk_highlights:
            if rh not in merged_highlights:
                merged_highlights.append(rh)
        merged_risk = max(llm_risk_count, rule_risk_count)
        source = "llm"
    else:
        # LLM 실패 시: 규칙 기반 결과 + 통계 템플릿 폴백
        merged_highlights: list[str] = []
        merged_highlights.append(
            f"총 {total_batches}건의 배치, {total_m:,.0f}m 생산 계획"
        )
        merged_highlights.append(f"공정 구성: {process_summary}")
        if earliest and latest:
            merged_highlights.append(f"납기일 범위: {earliest} ~ {latest}")
        top_customer = max(by_customer.items(), key=lambda x: x[1], default=None)
        if top_customer:
            merged_highlights.append(
                f"최다 수주 거래처: {top_customer[0]} ({top_customer[1]}건)"
            )
        # 규칙 기반 리스크 항목 추가
        for rh in rule_risk_highlights:
            if rh not in merged_highlights:
                merged_highlights.append(rh)
        merged_risk = rule_risk_count
        source = "rule-based"

    # LLM 마크다운 볼드(**) 제거 — UI에 직접 표시되므로
    merged_highlights = [h.replace("**", "") for h in merged_highlights]

    return {
        "totalBatches": total_batches,
        "totalGroups": total_groups,
        "totalProductionM": round(total_m, 1),
        "riskCount": merged_risk,
        "highlights": merged_highlights,
        "insights": [],
        "source": source,
    }


def _build_summary_catalog(
    batches: list,
    by_process: dict[str, int],
    by_customer: dict[str, int],
) -> set[str]:
    """LLM 응답 검증용 catalog 구성.

    본 run 의 모든 batch 에 대한 process_name + customer_name + 색상 + 재질
    union 을 모은다. 추가로 통계 컨텍스트가 LLM 에 전달되었으므로 일반화 명사
    (m, 공정, 배치, 그룹 등) 도 포함.
    """
    catalog: set[str] = set(by_process.keys()) | set(by_customer.keys())
    for b in batches:
        if b.process_name:
            catalog.add(b.process_name)
        if b.customer_name:
            catalog.add(b.customer_name)
        if b.sheath_color:
            catalog.add(b.sheath_color)
        if b.conductor_material:
            catalog.add(b.conductor_material)
        if b.product_group:
            catalog.add(b.product_group)
    # 일반화: 본 use-case 가 다루는 도메인 일반 명사
    catalog.update(
        {
            "총",
            "건",
            "공정",
            "구성",
            "범위",
            "거래처",
            "수주",
            "생산",
            "계획",
            "최다",
            "납기일",
            "리스크",
            "긴급",
            "병목",
            "교체",
            "손실",
            "재공",
            "활용도",
            "매칭",
            "미활용",
            "평균",
            "기타",
            "미지정",
        }
    )
    return catalog
