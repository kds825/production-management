"""Phase 6 Step 3c-1 — explain_batch facts 정합성 (decision_card 와 동일 phrasing).

2nd opinion §3 — explain_batch 의 본문 facts 가 phrasing.py 를 거치므로
build_card.py 와 fact-level 에서 모순되지 않는다.

본 테스트는 phrasing 등록을 보장한 상태에서 explain_batch 가 phrasing
adequacy_line / handoff_line 결과를 본문에 포함하는지 검증.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.decisions.explain_batch import (
    _build_facts_via_phrasing,
    _ensure_phrasing_registered,
    _make_tagline,
)
from app.application.decisions.phrasing import (
    reset_registry,
)
from app.application.decisions.phrasing_providers import SheathPhrasingProvider
from app.config import settings


@pytest.fixture
def _db():
    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    db.begin_nested()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture(autouse=True)
def _reset_phrasing():
    reset_registry()
    yield
    reset_registry()


# ─────────────────────────────────────────────────────────────────────────
# _ensure_phrasing_registered idempotent
# ─────────────────────────────────────────────────────────────────────────


def test_ensure_phrasing_registered_populates_empty_registry():
    from app.application.decisions.phrasing import _REGISTRY

    assert _REGISTRY == {}
    _ensure_phrasing_registered()
    assert "sheath" in _REGISTRY
    assert "stranding" in _REGISTRY
    assert "insulation" in _REGISTRY
    assert "outsource" in _REGISTRY
    assert "default" in _REGISTRY


def test_ensure_phrasing_registered_is_idempotent():
    from app.application.decisions.phrasing import (
        _REGISTRY,
        register_phrasing_provider,
    )

    # 미리 다른 sheath provider 등록 (sentinel)
    sentinel = SheathPhrasingProvider()
    register_phrasing_provider(sentinel)
    _ensure_phrasing_registered()
    # registry 비어있지 않으므로 _ensure 가 추가 등록하지 않음 — sentinel 보존
    assert _REGISTRY["sheath"] is sentinel


# ─────────────────────────────────────────────────────────────────────────
# facts 본문이 phrasing.adequacy_line / handoff_line 결과 포함
# ─────────────────────────────────────────────────────────────────────────


def test_facts_contains_adequacy_line():
    """sheath batch + equipment 에 대해 adequacy_line(5-1) 결과가 facts 에 포함."""
    from types import SimpleNamespace

    batch = SimpleNamespace(
        process_name="저압시스",
        sq_mm2=150,
        product_group="LV-150",
        customer_name="한국전력",
        customer_priority=2,
        sheath_color="흑",
        conductor_material="CU",
        wip_matched_id=None,
        equipment_code="시스-3호기",
        due_date=None,
        total_length_m=3850,
        extra_length_m=0,
        line_speed_mpm=20,
        estimated_duration_min=510,
    )
    equipment = SimpleNamespace(
        equipment_name="시스 3호기",
        equipment_code="시스-3호기",
        process_name="저압시스",
        material_limit="ALL",
        range_min=100,
        range_max=300,
        range_unit="SQ",
        color_group="A120",
    )

    _ensure_phrasing_registered()
    facts = _build_facts_via_phrasing(batch, equipment, [])

    # adequacy_line 5-1 핵심 어구가 facts 에 포함
    assert "100~300SQ" in facts
    assert "150SQ 적합" in facts
    # Korean 조사 헬퍼 검증 — 시스 3호기 → '는'
    assert "시스 3호기는" in facts


def test_facts_contains_tfr_gv_handoff_line():
    """TFR-GV batch — handoff_line 이 prev label 을 '연선 (절연 스킵 · TFR-GV)' 로 치환."""
    from types import SimpleNamespace

    batch = SimpleNamespace(
        process_name="저압시스",
        sq_mm2=50,  # > 25 — 단선 접지선 아님, 일반 TFR-GV
        product_group="TFR-GV",
        customer_name="한국전력",
        customer_priority=2,
        sheath_color="흑",
        conductor_material="CU",
        wip_matched_id=None,
        equipment_code=None,
        due_date=None,
        total_length_m=2800,
        extra_length_m=0,
        line_speed_mpm=20,
        estimated_duration_min=420,
    )

    _ensure_phrasing_registered()
    facts = _build_facts_via_phrasing(batch, None, [])
    assert "연선 (절연 스킵 · TFR-GV)" in facts


def test_facts_contains_bare_ground_handoff_line():
    """단선 접지선 (TFR-GV SQ ≤ 25) — handoff_line 이 '신선 (연선·절연 스킵)' 로 치환."""
    from types import SimpleNamespace

    batch = SimpleNamespace(
        process_name="저압시스",
        sq_mm2=25,
        product_group="TFR-GV",
        customer_name="한국전력",
        customer_priority=2,
        sheath_color="흑",
        conductor_material="CU",
        wip_matched_id=None,
        equipment_code=None,
        due_date=None,
        total_length_m=2000,
        extra_length_m=0,
        line_speed_mpm=20,
        estimated_duration_min=300,
    )

    _ensure_phrasing_registered()
    facts = _build_facts_via_phrasing(batch, None, [])
    assert "신선 (연선·절연 스킵 — 단선 접지선 SQ≤25)" in facts


def test_facts_contains_outsource_handoff_for_sq_le_10():
    """SQ ≤ 10 batch — _resolve_key 가 outsource 반환 → outsource handoff_line."""
    from types import SimpleNamespace

    batch = SimpleNamespace(
        process_name="저압시스",
        sq_mm2=10,
        product_group="LV-10",
        customer_name="한국전력",
        customer_priority=2,
        sheath_color="흑",
        conductor_material="CU",
        wip_matched_id=None,
        equipment_code=None,
        due_date=None,
        total_length_m=1500,
        extra_length_m=0,
        line_speed_mpm=20,
        estimated_duration_min=200,
    )

    _ensure_phrasing_registered()
    facts = _build_facts_via_phrasing(batch, None, [])
    # OutsourcePhrasingProvider.handoff_line 결과
    assert "외주 작업" in facts
    assert "입고" in facts


def test_facts_contains_constraint_names_from_audit():
    """audit_log.constraints_applied 의 'pass' 결과 name 이 facts 에 포함."""
    from types import SimpleNamespace

    batch = SimpleNamespace(
        process_name="저압시스",
        sq_mm2=150,
        product_group="LV-150",
        customer_name="한국전력",
        customer_priority=2,
        sheath_color="흑",
        conductor_material="CU",
        wip_matched_id=None,
        equipment_code=None,
        due_date=None,
        total_length_m=3850,
        extra_length_m=0,
        line_speed_mpm=20,
        estimated_duration_min=510,
    )

    log = SimpleNamespace(
        constraints_applied=[
            {"id": "5-1", "name": "SQ 적합", "result": "pass"},
            {"id": "10-3", "name": "시스재질 적합", "result": "pass"},
            {"id": "rejected", "name": "거부된 룰", "result": "fail"},
        ]
    )

    _ensure_phrasing_registered()
    facts = _build_facts_via_phrasing(batch, None, [log])
    assert "SQ 적합" in facts
    assert "시스재질 적합" in facts
    assert "거부된 룰" not in facts


# ─────────────────────────────────────────────────────────────────────────
# tagline 폴백 — LLM 미설정 환경에서 phrasing.verdict_summary 사용
# ─────────────────────────────────────────────────────────────────────────


def test_make_tagline_falls_back_to_verdict_summary_when_llm_unavailable(monkeypatch):
    """call_llm_sync 가 None 반환 시 phrasing.verdict_summary 사용."""
    from types import SimpleNamespace

    import app.application.decisions.explain_batch as eb

    monkeypatch.setattr(eb, "call_llm_sync", lambda *a, **kw: None)

    batch = SimpleNamespace(
        batch_id=42,
        process_name="저압시스",
        sq_mm2=150,
        product_group="",
        customer_name="한국전력",
        customer_priority=2,
        sheath_color="흑",
        conductor_material="CU",
        wip_matched_id=None,
        equipment_code=None,
        due_date=None,
        total_length_m=3850,
        extra_length_m=0,
        line_speed_mpm=20,
        estimated_duration_min=510,
        product_type=None,
    )

    _ensure_phrasing_registered()
    tagline, source = _make_tagline(batch, None, [], facts="dummy facts")
    assert source == "template"
    # phrasing.verdict_summary 결과 — '✓' / '⚠' / '·' 중 하나로 시작
    assert tagline[0] in ("✓", "⚠", "·")
