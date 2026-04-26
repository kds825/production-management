"""Phase 6 Step 3b — section_builder.py 단위 테스트."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.application.decisions.phrasing import (
    register_phrasing_provider,
    reset_registry,
)
from app.application.decisions.phrasing_providers import SheathPhrasingProvider
from app.application.decisions.section_builder import (
    DecisionCardSectionBuilder,
    DefaultSectionBuilder,
    HandoffBlock,
    InsulationSectionBuilder,
    OutsourceHandoffBlock,
    OutsourceSectionBuilder,
    SheathSectionBuilder,
    StrandingSectionBuilder,
    WipMatchBlock,
    get_section_builder,
    register_section_builder,
    reset_section_builders,
)


@dataclass
class FakeBatch:
    process_name: str = "저압시스"
    sq_mm2: float = 150.0
    product_group: str = ""
    customer_name: str = ""
    core_count: int = 1
    sheath_color: str = "흑"
    conductor_material: str = "CU"


@pytest.fixture(autouse=True)
def _reset():
    reset_registry()
    reset_section_builders()
    register_phrasing_provider(SheathPhrasingProvider())
    yield
    reset_registry()
    reset_section_builders()


# ─────────────────────────────────────────────────────────────────────────
# Protocol / register / get
# ─────────────────────────────────────────────────────────────────────────


def test_protocol_implementations_match_abstract():
    for cls in (
        SheathSectionBuilder,
        StrandingSectionBuilder,
        InsulationSectionBuilder,
        OutsourceSectionBuilder,
        DefaultSectionBuilder,
    ):
        inst = cls()
        assert isinstance(inst, DecisionCardSectionBuilder)


def test_register_get_dispatches_correctly():
    register_section_builder(DefaultSectionBuilder())
    register_section_builder(SheathSectionBuilder())
    assert get_section_builder("sheath").process_key == "sheath"
    assert get_section_builder("unknown").process_key == "default"


def test_no_registry_raises():
    with pytest.raises(RuntimeError, match="No section builder"):
        get_section_builder("anything")


# ─────────────────────────────────────────────────────────────────────────
# Sheath — HandoffBlock + TFR-GV / 단선 접지선 분기
# ─────────────────────────────────────────────────────────────────────────


def test_sheath_handoff_default_predecessor():
    builder = SheathSectionBuilder()
    phrasing = SheathPhrasingProvider()
    block = builder.build_handoff_section(
        batch=FakeBatch(), audit_rows=[], solver_rows=[], phrasing=phrasing
    )
    assert isinstance(block, HandoffBlock)
    assert block.predecessor_label == "전공정: 절연"


def test_sheath_handoff_tfr_gv_label():
    builder = SheathSectionBuilder()
    phrasing = SheathPhrasingProvider()
    block = builder.build_handoff_section(
        batch=FakeBatch(product_group="TFR-GV", sq_mm2=50),
        audit_rows=[],
        solver_rows=[],
        phrasing=phrasing,
    )
    assert block.predecessor_label == "전공정: 연선 (절연 스킵 · TFR-GV)"


def test_sheath_handoff_bare_ground_label():
    builder = SheathSectionBuilder()
    phrasing = SheathPhrasingProvider()
    block = builder.build_handoff_section(
        batch=FakeBatch(product_group="TFR-GV", sq_mm2=25),  # SQ ≤ 25
        audit_rows=[],
        solver_rows=[],
        phrasing=phrasing,
    )
    assert (
        block.predecessor_label == "전공정: 신선 (연선·절연 스킵 — 단선 접지선 SQ≤25)"
    )


def test_sheath_handoff_picks_solver_start_end():
    builder = SheathSectionBuilder()
    phrasing = SheathPhrasingProvider()
    kst = ZoneInfo("Asia/Seoul")
    solver = SimpleNamespace(
        start_datetime=datetime(2026, 4, 30, 14, 0, tzinfo=kst),
        end_datetime=datetime(2026, 4, 30, 22, 30, tzinfo=kst),
    )
    block = builder.build_handoff_section(
        batch=FakeBatch(),
        audit_rows=[],
        solver_rows=[solver],
        phrasing=phrasing,
    )
    assert block.self_start_at == solver.start_datetime
    assert block.self_end_at == solver.end_datetime


# ─────────────────────────────────────────────────────────────────────────
# Stranding / Insulation — WipMatchBlock
# ─────────────────────────────────────────────────────────────────────────


def test_stranding_returns_wip_block():
    builder = StrandingSectionBuilder()
    audit = [
        SimpleNamespace(
            action_type="wip_match",
            data={
                "matched_wip_id": 4421,
                "wip_total_m": 4200,
                "wip_used_m": 3850,
                "remainder_m": 350,
                "loss_pct": 8.3,
                "candidates": [
                    {"wip_id": 4421, "matched": True},
                    {"wip_id": 4519, "matched": False, "reason": "shortage"},
                ],
            },
        )
    ]
    block = builder.build_handoff_section(
        batch=FakeBatch(process_name="연선"),
        audit_rows=audit,
        solver_rows=[],
        phrasing=None,
    )
    assert isinstance(block, WipMatchBlock)
    assert block.matched_wip_id == 4421
    assert block.wip_total_m == 4200
    assert block.remainder_m == 350
    assert block.loss_pct == 8.3
    assert len(block.candidates) == 2


def test_stranding_no_match_returns_empty_block():
    builder = StrandingSectionBuilder()
    block = builder.build_handoff_section(
        batch=FakeBatch(process_name="연선"),
        audit_rows=[],
        solver_rows=[],
        phrasing=None,
    )
    assert isinstance(block, WipMatchBlock)
    assert block.matched_wip_id is None
    assert block.candidates == []


def test_insulation_returns_wip_block():
    builder = InsulationSectionBuilder()
    audit = [
        SimpleNamespace(
            action_type="wip_match",
            data={"matched_wip_id": 5500, "wip_total_m": 1000, "loss_pct": 3.0},
        )
    ]
    block = builder.build_handoff_section(
        batch=FakeBatch(process_name="저압절연"),
        audit_rows=audit,
        solver_rows=[],
        phrasing=None,
    )
    assert isinstance(block, WipMatchBlock)
    assert block.matched_wip_id == 5500
    assert block.loss_pct == 3.0


# ─────────────────────────────────────────────────────────────────────────
# Outsource — OutsourceHandoffBlock
# ─────────────────────────────────────────────────────────────────────────


def test_outsource_returns_handoff_block():
    builder = OutsourceSectionBuilder()
    kst = ZoneInfo("Asia/Seoul")
    audit = [
        SimpleNamespace(
            action_type="outsource_handoff",
            data={
                "vendor_name": "외주 H공장",
                "order_at": datetime(2026, 5, 1, tzinfo=kst),
                "outsource_start_at": datetime(2026, 5, 2, tzinfo=kst),
                "outsource_end_at": datetime(2026, 5, 4, tzinfo=kst),
                "inbound_at": datetime(2026, 5, 5, tzinfo=kst),
                "lead_days": 4,
            },
        )
    ]
    block = builder.build_handoff_section(
        batch=FakeBatch(process_name="저압시스", sq_mm2=10),
        audit_rows=audit,
        solver_rows=[],
        phrasing=None,
    )
    assert isinstance(block, OutsourceHandoffBlock)
    assert block.vendor_name == "외주 H공장"
    assert block.lead_days == 4


# ─────────────────────────────────────────────────────────────────────────
# default_expanded_for — 시나리오별 펼침
# ─────────────────────────────────────────────────────────────────────────


def test_sheath_default_expanded_scenario_C():
    """C 시나리오 (시스 규격교체) — section_2/3 펼침."""
    builder = SheathSectionBuilder()
    expanded = builder.default_expanded_for("C")
    assert expanded["section_2"] is True
    assert expanded["section_3"] is True


def test_sheath_default_expanded_scenario_A_all_collapsed():
    builder = SheathSectionBuilder()
    expanded = builder.default_expanded_for("A")
    assert expanded["section_2"] is False
    assert expanded["section_3"] is False


def test_sheath_default_expanded_scenario_G_handoff():
    """G 시나리오 (TFR-GV 절연 스킵) — section_3 (handoff) 강조."""
    builder = SheathSectionBuilder()
    expanded = builder.default_expanded_for("G")
    assert expanded.get("section_3") is True


def test_outsource_default_expanded_scenario_E():
    builder = OutsourceSectionBuilder()
    expanded = builder.default_expanded_for("E")
    assert expanded.get("section_3") is True
    assert expanded.get("section_5") is True
