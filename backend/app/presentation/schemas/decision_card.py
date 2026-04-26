"""decision_card pydantic schema — `GET /api/scheduler/{run_label}/decision-card/{batch_id}` 응답.

Phase 6 (decision_card) Step 3c-1. application/decisions/build_card.py 의
DecisionCard dataclass 미러. 운영자는 자연어 + 액션 1개, 개발자(admin role)
는 같은 payload + DebugBlock.

핵심 (2nd opinion blocker — role-based debug omit):
- 운영자 role 요청 → response.debug == None (백엔드에서 omit, frontend 분기
  렌더링 의존 X)
- admin role 요청 → response.debug 채움
- role 결정은 Step 3c-1 단계에서 X-User-Role 헤더 (PoC 단순화). 정식 JWT
  는 Step 5 / 별도 spec.

도메인 정확성:
- HandoffBlock | WipMatchBlock | OutsourceHandoffBlock 의 union 은 pydantic
  Optional 3개로 표현 — 정확히 1개만 채움 (process_key 에 따라).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── DecisionLine — ❶ / ❷ / ❹ / ❺ / ❻ 자연어 줄 row ────────────────────────


class DecisionLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor: str = Field(..., max_length=50)
    """피드백 line_anchor 매칭 키 — 'why_line_3' 등."""

    natural: str
    """운영자 표시 문구 (한국어 자연어, deterministic phrasing)."""

    constraint_id: Optional[str] = Field(default=None, max_length=10)
    """디버그 view + line_anchor → constraint_id 추적용 (#4-2 등)."""

    severity: Literal["ok", "save", "warn", "fail"] = "ok"

    detail: dict = Field(default_factory=dict)
    """tier 2 popover 풀텍스트."""


# ── ❷ ImpactBlock ───────────────────────────────────────────────────────


class ImpactCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str  # 예: '📅 납기 여유'
    value: str  # 예: '+1.2일'
    severity: Literal["ok", "save", "warn", "fail"] = "ok"
    kind: str = ""  # 'color_change' / 'spec_change' / 'due_slack_days' 등


class ImpactBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cells: list[ImpactCell] = Field(default_factory=list)
    duration_breakdown: list[dict] = Field(default_factory=list)
    """❷ 펼침 토글 '소요시간이 어떻게 산정됐나요?' — 분 단위 분해표."""


# ── ❸ Hand-off / Wip-match / Outsource (union 표현) ─────────────────────


class HandoffBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predecessor_label: str
    predecessor_end_at: Optional[datetime] = None
    gap_minutes: int = 0
    self_start_at: Optional[datetime] = None
    self_end_at: Optional[datetime] = None
    successor_label: Optional[str] = None
    successor_first_slot_at: Optional[datetime] = None


class WipMatchBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched_wip_id: Optional[int] = None
    wip_total_m: float = 0.0
    wip_used_m: float = 0.0
    remainder_m: float = 0.0
    loss_pct: float = 0.0
    candidates: list[dict] = Field(default_factory=list)


class OutsourceHandoffBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_name: str
    order_at: Optional[datetime] = None
    outsource_start_at: Optional[datetime] = None
    outsource_end_at: Optional[datetime] = None
    inbound_at: Optional[datetime] = None
    successor_label: Optional[str] = None
    successor_first_slot_at: Optional[datetime] = None
    lead_days: int = 0


# ── ❹ Equipment day Gantt row ────────────────────────────────────────────


class GanttRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: int
    batch_id: int
    batch_group: str = ""
    label: str
    start_at: datetime
    end_at: datetime
    is_self: bool = False
    """본 카드의 batch 인지 — frontend 가 highlight."""


# ── ❺ Bundle compare row ─────────────────────────────────────────────────


class BundleAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    color_change_min: int = 0
    spec_change_min: int = 0
    duration_min: int = 0
    score: float = 0.0
    is_chosen: bool = False
    rationale: str = ""


# ── ❻ Alternative (탈락 사유) ────────────────────────────────────────────


class Alternative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_label: str  # '시스 1호기' / '04-29 08:00 시작'
    rejected_reason: str  # phrasing.filter_out_reason 결과
    severity: Literal["ok", "save", "warn", "fail"] = "fail"
    code: str = ""  # constraint id (#5-1 등)


# ── Provenance + Verdict ─────────────────────────────────────────────────


class ProvenanceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_ids: list[int] = Field(default_factory=list)
    """본 카드의 phrasing/룰을 변경한 운영자 의견 #ID 목록 (CEO R1)."""

    last_fixed_at: Optional[datetime] = None


# ── Debug — operator role 응답에서 omit ──────────────────────────────────


class DebugBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str  # 'cpsat' | 'greedy' | 'greedy_fallback'
    solver_status: str = ""
    solve_time_ms: float = 0.0

    objective_breakdown: dict = Field(default_factory=dict)
    """{constraint_id: penalty_value} — solver_decision 집계."""

    audit_anchors: list[dict] = Field(default_factory=list)
    """natural 줄 옆에 mono anchor 표시 — '#5-1 sq_equipment_assignment pass'."""

    schedule_task_row: dict = Field(default_factory=dict)
    """ScheduleTask snapshot — 시작·종료 시각 source-of-truth (S2)."""

    solver_decision_rows: list[dict] = Field(default_factory=list)
    """per-constraint trace + details_json raw (S3)."""

    constraint_config_version: str = ""


# ── 최상위 DecisionCard ──────────────────────────────────────────────────


class DecisionCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Header
    batch_id: int
    task_id: Optional[int] = None
    run_label: str
    process_key: Literal["sheath", "stranding", "insulation", "outsource", "default"]
    process_label: str  # '저압시스 (A120)' / '연선' 등
    sub_chip: Optional[str] = None  # 'A120 묶음 (작은 설비)' 등
    customer_name: str = ""
    customer_priority: int = 99
    due_date: Optional[datetime] = None

    # 배치 위치 box
    placement_text: str  # '시스 3호기에 04-29(수) 14:00 — 22:30 배치'
    placement_calc: dict = Field(default_factory=dict)
    """⓵ 시작·종료 계산 근거 popover 데이터."""

    # 헤더 verdict 1줄 (CEO R2)
    verdict_summary: str = ""

    # ❶~❻ 섹션
    why: list[DecisionLine] = Field(default_factory=list)
    impact: ImpactBlock = Field(default_factory=ImpactBlock)
    handoff: Optional[HandoffBlock] = None
    wip_match: Optional[WipMatchBlock] = None
    outsource_handoff: Optional[OutsourceHandoffBlock] = None
    equipment_day: list[GanttRow] = Field(default_factory=list)
    equipment_day_sort_label: str = ""
    bundle_compare: list[BundleAlternative] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)

    # 시나리오별 펼침 default
    section_default_expanded: dict[str, bool] = Field(default_factory=dict)

    # Provenance (CEO R1)
    provenance: ProvenanceInfo = Field(default_factory=ProvenanceInfo)

    # Engine source — 'llm' | 'rule-based' (build_card 는 항상 'rule-based')
    source: Literal["llm", "rule-based"] = "rule-based"

    # Debug — operator role 응답에서 None 으로 omit (백엔드 1차 게이트)
    debug: Optional[DebugBlock] = None
