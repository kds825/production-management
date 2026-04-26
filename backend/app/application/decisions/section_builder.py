"""decision_card ❸ 섹션 빌더 — ABC + 4 concrete (sheath/stranding/insulation/outsource).

Phase 6 (decision_card) Step 3b. ❸ 섹션은 공정에 따라 다른 dataclass 를
반환한다 (HandoffBlock | WipMatchBlock | OutsourceHandoffBlock). 추상화 도입
은 사용자 §5 한정 면죄부에 따른 것.

도메인 분기:
- 시스 (저압/고압/HFCO/TFR-GV/단선접지선) → HandoffBlock
- 연선 / 절연 → WipMatchBlock
- 외주 → OutsourceHandoffBlock
- default → HandoffBlock 빈 인스턴스 (v1 미노출 공정)

build_handoff_section 은 phrasing provider 와 audit/solver row 를 입력으로
받아 dataclass 를 합성한다 — 자연어 합성은 phrasing 에 위임.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Union


# ── ❸ 섹션 dataclass union ───────────────────────────────────────────────


@dataclass
class HandoffBlock:
    """시스 (저압/고압/HFCO/TFR-GV/단선접지선) 카드 ❸ — 전·후 공정 hand-off."""

    predecessor_label: str
    """예: '저압절연' / '연선 (절연 스킵 · TFR-GV)' / '신선 (연선·절연 스킵)'."""

    predecessor_end_at: datetime | None
    """전공정이 끝나는 시각 (KST)."""

    gap_minutes: int
    """전공정 종료 → 본 작업 시작 사이 갭 (검사 + 캘린더 단축 포함)."""

    self_start_at: datetime | None
    self_end_at: datetime | None

    successor_label: str | None = None
    """후공정 라벨. 시스가 마지막 공정이면 None — '출하' 표기는 frontend."""

    successor_first_slot_at: datetime | None = None
    """후공정 (또는 출하) 가능한 가장 빠른 시각."""


@dataclass
class WipMatchBlock:
    """연선/절연 카드 ❸ — 재공 활용."""

    matched_wip_id: int | None
    """매칭된 WIP id. None 이면 매칭 실패 (신규 SM 투입)."""

    wip_total_m: float
    wip_used_m: float
    remainder_m: float
    """잔량 (다음 동일 SQ 작업으로 이월 가능)."""

    loss_pct: float
    """손실률 % (0~100)."""

    candidates: list[dict] = field(default_factory=list)
    """평가 후보 리스트. {wip_id, matched, reason, length_m, ...}.
    decision_card ❻ 탈락 사유 섹션과 공유."""


@dataclass
class OutsourceHandoffBlock:
    """외주 카드 ❸ — 발주 → 외주 작업 → 입고 → 후공정."""

    vendor_name: str
    order_at: datetime | None
    outsource_start_at: datetime | None
    outsource_end_at: datetime | None
    inbound_at: datetime | None
    successor_label: str | None = None
    successor_first_slot_at: datetime | None = None
    lead_days: int = 0


SectionBlock = Union[HandoffBlock, WipMatchBlock, OutsourceHandoffBlock]


# ── ABC ───────────────────────────────────────────────────────────────────


class DecisionCardSectionBuilder(ABC):
    """공정별 ❸ 섹션 빌더. 명시적 register — phrasing 과 같은 lifespan startup."""

    process_key: str

    @abstractmethod
    def build_handoff_section(
        self, *, batch, audit_rows: list, solver_rows: list, phrasing
    ) -> SectionBlock:
        """❸ 섹션 dataclass 합성."""
        ...

    @abstractmethod
    def default_expanded_for(self, scenario_key: str) -> dict[str, bool]:
        """시나리오별 ❷~❻ 펼침/접힘 default (UI review §3.2)."""
        ...


# ── Concrete: Sheath ──────────────────────────────────────────────────────


class SheathSectionBuilder(DecisionCardSectionBuilder):
    process_key = "sheath"

    def build_handoff_section(
        self, *, batch, audit_rows: list, solver_rows: list, phrasing
    ) -> HandoffBlock:
        # TFR-GV / 단선 접지선 분기 — phrasing.handoff_line 이 prev label 결정
        is_tfrgv = "TFR-GV" in (batch.product_group or "").upper()
        is_bare_ground = is_tfrgv and float(batch.sq_mm2 or 0) <= 25
        prev_label = phrasing.handoff_line(
            params={
                "is_tfrgv": is_tfrgv and not is_bare_ground,
                "is_bare_ground": is_bare_ground,
                "predecessor": _pick_predecessor_label(audit_rows),
            }
        )

        return HandoffBlock(
            predecessor_label=prev_label,
            predecessor_end_at=_pick_predecessor_end(audit_rows),
            gap_minutes=int(_pick_predecessor_gap(audit_rows)),
            self_start_at=_pick_self_start(solver_rows),
            self_end_at=_pick_self_end(solver_rows),
            successor_label=None,  # 시스가 보통 마지막 공정 — 출하
            successor_first_slot_at=_pick_successor_first_slot(audit_rows),
        )

    def default_expanded_for(self, scenario_key: str) -> dict[str, bool]:
        return {
            "A": {"section_2": False, "section_3": False, "section_5": False},
            "B": {"section_2": True, "section_3": False, "section_5": False},
            "C": {"section_2": True, "section_3": True, "section_5": False},
            "D": {"section_2": True, "section_5": True},
            "H": {"section_5": True},
            "G": {"section_3": True},  # TFR-GV — handoff 강조
        }.get(scenario_key, {})


# ── Concrete: Stranding ───────────────────────────────────────────────────


class StrandingSectionBuilder(DecisionCardSectionBuilder):
    process_key = "stranding"

    def build_handoff_section(
        self, *, batch, audit_rows: list, solver_rows: list, phrasing
    ) -> WipMatchBlock:
        wip = _pick_wip_match(audit_rows)
        return WipMatchBlock(
            matched_wip_id=wip.get("matched_wip_id"),
            wip_total_m=float(wip.get("wip_total_m") or 0),
            wip_used_m=float(wip.get("wip_used_m") or 0),
            remainder_m=float(wip.get("remainder_m") or 0),
            loss_pct=float(wip.get("loss_pct") or 0),
            candidates=list(wip.get("candidates") or []),
        )

    def default_expanded_for(self, scenario_key: str) -> dict[str, bool]:
        return {
            "F": {"section_3": True},  # 연선 재공 — handoff 강조
        }.get(scenario_key, {})


# ── Concrete: Insulation ──────────────────────────────────────────────────


class InsulationSectionBuilder(DecisionCardSectionBuilder):
    process_key = "insulation"

    def build_handoff_section(
        self, *, batch, audit_rows: list, solver_rows: list, phrasing
    ) -> WipMatchBlock:
        wip = _pick_wip_match(audit_rows)
        return WipMatchBlock(
            matched_wip_id=wip.get("matched_wip_id"),
            wip_total_m=float(wip.get("wip_total_m") or 0),
            wip_used_m=float(wip.get("wip_used_m") or 0),
            remainder_m=float(wip.get("remainder_m") or 0),
            loss_pct=float(wip.get("loss_pct") or 0),
            candidates=list(wip.get("candidates") or []),
        )

    def default_expanded_for(self, scenario_key: str) -> dict[str, bool]:
        return {}


# ── Concrete: Outsource ───────────────────────────────────────────────────


class OutsourceSectionBuilder(DecisionCardSectionBuilder):
    process_key = "outsource"

    def build_handoff_section(
        self, *, batch, audit_rows: list, solver_rows: list, phrasing
    ) -> OutsourceHandoffBlock:
        info = _pick_outsource_info(audit_rows)
        return OutsourceHandoffBlock(
            vendor_name=info.get("vendor_name", "외주 협력사"),
            order_at=info.get("order_at"),
            outsource_start_at=info.get("outsource_start_at"),
            outsource_end_at=info.get("outsource_end_at"),
            inbound_at=info.get("inbound_at"),
            successor_label=info.get("successor_label"),
            successor_first_slot_at=info.get("successor_first_slot_at"),
            lead_days=int(info.get("lead_days") or 0),
        )

    def default_expanded_for(self, scenario_key: str) -> dict[str, bool]:
        return {
            "E": {"section_3": True, "section_5": True},  # 외주 vs 사내 비교
        }.get(scenario_key, {})


# ── Default fallback (v1 미노출 — DecisionCardEmpty 가 본 block 미사용) ──


class DefaultSectionBuilder(DecisionCardSectionBuilder):
    process_key = "default"

    def build_handoff_section(
        self, *, batch, audit_rows: list, solver_rows: list, phrasing
    ) -> HandoffBlock:
        return HandoffBlock(
            predecessor_label="",
            predecessor_end_at=None,
            gap_minutes=0,
            self_start_at=None,
            self_end_at=None,
        )

    def default_expanded_for(self, scenario_key: str) -> dict[str, bool]:
        return {}


# ── Registry (phrasing 과 별도) ───────────────────────────────────────────


_SECTION_BUILDERS: dict[str, DecisionCardSectionBuilder] = {}


def register_section_builder(builder: DecisionCardSectionBuilder) -> None:
    _SECTION_BUILDERS[builder.process_key] = builder


def reset_section_builders() -> None:
    _SECTION_BUILDERS.clear()


def get_section_builder(process_key: str) -> DecisionCardSectionBuilder:
    builder = _SECTION_BUILDERS.get(process_key) or _SECTION_BUILDERS.get("default")
    if builder is None:
        raise RuntimeError(
            "No section builder registered. "
            "FastAPI lifespan startup 에서 register_section_builder 를 호출했는지 확인."
        )
    return builder


# ── audit/solver row 추출 helpers ─────────────────────────────────────────
# build_card.py 가 채워주는 audit_rows / solver_rows 의 구조를 추상화. 본
# Step 3b 단위 테스트는 dict / SimpleNamespace 로 mock 가능.


def _pick_predecessor_label(audit_rows: list) -> str:
    for r in audit_rows:
        if getattr(r, "action_type", None) == "predecessor_end":
            return getattr(r, "label", "절연")
    return "절연"


def _pick_predecessor_end(audit_rows: list) -> datetime | None:
    for r in audit_rows:
        if getattr(r, "action_type", None) == "predecessor_end":
            return getattr(r, "end_at", None)
    return None


def _pick_predecessor_gap(audit_rows: list) -> int:
    for r in audit_rows:
        if getattr(r, "action_type", None) == "predecessor_gap":
            return int(getattr(r, "minutes", 0) or 0)
    return 0


def _pick_self_start(solver_rows: list) -> datetime | None:
    for r in solver_rows:
        if hasattr(r, "start_datetime"):
            return r.start_datetime
        if hasattr(r, "start_at"):
            return r.start_at
    return None


def _pick_self_end(solver_rows: list) -> datetime | None:
    for r in solver_rows:
        if hasattr(r, "end_datetime"):
            return r.end_datetime
        if hasattr(r, "end_at"):
            return r.end_at
    return None


def _pick_successor_first_slot(audit_rows: list) -> datetime | None:
    for r in audit_rows:
        if getattr(r, "action_type", None) == "successor_first_slot":
            return getattr(r, "at", None)
    return None


def _pick_wip_match(audit_rows: list) -> dict:
    for r in audit_rows:
        if getattr(r, "action_type", None) == "wip_match":
            return getattr(r, "data", {}) or {}
    return {}


def _pick_outsource_info(audit_rows: list) -> dict:
    for r in audit_rows:
        if getattr(r, "action_type", None) == "outsource_handoff":
            return getattr(r, "data", {}) or {}
    return {}
