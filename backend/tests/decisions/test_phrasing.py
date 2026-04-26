"""Phase 6 Step 3a — phrasing.py + 5 Provider 단위 테스트.

목표:
- 5 Provider × 주요 anchor 매트릭스 ~47 snapshot
- 정규화 헬퍼 (_format_minutes / _format_days / _format_kst / _korean_eunneun)
- fault path: anchor key 누락 / params 키 누락 / process_name 미등록 / 외주 우선

LLM 미사용 — deterministic. Provider register 는 매 테스트 reset 으로 격리.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.application.decisions.phrasing import (
    _format_days,
    _format_kst,
    _format_minutes,
    _has_jongseong,
    _korean_eunneun,
    _korean_iga,
    _resolve_key,
    get_phrasing_provider,
    register_phrasing_provider,
    reset_registry,
)
from app.application.decisions.phrasing_providers import (
    DefaultPhrasingProvider,
    InsulationPhrasingProvider,
    OutsourcePhrasingProvider,
    SheathPhrasingProvider,
    StrandingPhrasingProvider,
)


# ── 공용 fake batch (ProductionBatch attribute mirror) ─────────────────────
@dataclass
class FakeBatch:
    process_name: str = "저압시스"
    sq_mm2: float = 150.0
    product_group: str = ""
    customer_name: str = ""
    core_count: int = 1
    sheath_color: str = "흑"
    conductor_material: str = "CU"
    stranding_type: str = "압축연선"


# ─────────────────────────────────────────────────────────────────────────
# §A. 정규화 헬퍼
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "minutes,expected",
    [
        (0, "0분"),
        (1, "1분"),
        (59, "59분"),
        (60, "1시간"),
        (90, "1시간 30분"),
        (120, "2시간"),
        (510, "8시간 30분"),
        (1440, "24시간"),
    ],
)
def test_format_minutes(minutes, expected):
    assert _format_minutes(minutes) == expected


@pytest.mark.parametrize(
    "days,expected",
    [
        (1.2, "+1.2일"),
        (-0.5, "-0.5일"),
        (0.0, "+0.0일"),
        (10.0, "+10.0일"),
        (-3.14159, "-3.1일"),  # 1자리 강제
    ],
)
def test_format_days(days, expected):
    assert _format_days(days) == expected


def test_format_kst_aware():
    # UTC 04:00 = KST 13:00 (월요일이라 가정 — 2026-04-27 은 월)
    dt = datetime(2026, 4, 27, 4, 0, 0, tzinfo=ZoneInfo("UTC"))
    assert _format_kst(dt) == "04-27(월) 13:00"


def test_format_kst_kst_input_passes_through():
    dt = datetime(2026, 4, 30, 14, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert _format_kst(dt) == "04-30(목) 14:00"


def test_format_kst_naive_rejected():
    dt = datetime(2026, 4, 27, 4, 0, 0)  # naive
    with pytest.raises(ValueError, match="timezone-naive"):
        _format_kst(dt)


def test_format_kst_non_datetime_rejected():
    with pytest.raises(TypeError):
        _format_kst("2026-04-27")


@pytest.mark.parametrize(
    "ch,expected",
    [
        ("시", False),  # 받침 없음
        ("스", False),
        ("는", True),  # 받침 있음 (ㄴ)
        ("호기", False),  # 기 — 모음 끝
        ("공장", True),  # 장 — 받침 ㅇ
        ("0", True),  # 영
        ("8", True),  # 팔
        ("2", False),  # 이
        ("V", False),  # 브이 — 모음 끝
    ],
)
def test_has_jongseong(ch, expected):
    assert _has_jongseong(ch) is expected


@pytest.mark.parametrize(
    "noun,expected",
    [
        ("시스 3호기", "는"),  # 호기 — 모음 끝
        ("외주 H공장", "은"),  # 공장 — 받침 ㅇ
        ("A100", "은"),  # 콘 — 받침 ㄴ
        ("TFR-GV", "는"),  # 브이 — 모음 끝
        ("연선 1호기", "는"),  # 기
        ("시스 2호기", "는"),
    ],
)
def test_korean_eunneun(noun, expected):
    assert _korean_eunneun(noun) == expected


@pytest.mark.parametrize(
    "noun,expected",
    [
        ("시스 3호기", "가"),  # 기 — 모음
        ("외주 H공장", "이"),  # 장 — 받침
    ],
)
def test_korean_iga(noun, expected):
    assert _korean_iga(noun) == expected


# ─────────────────────────────────────────────────────────────────────────
# §B. _resolve_key + Registry
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _registry_reset():
    """매 테스트 격리 — register_phrasing_provider 영향 차단."""
    reset_registry()
    yield
    reset_registry()


def test_resolve_key_sheath_variants():
    for pn in ("저압시스", "고압시스", "HFCO시스"):
        assert _resolve_key(FakeBatch(process_name=pn, sq_mm2=150)) == "sheath"


def test_resolve_key_stranding():
    assert _resolve_key(FakeBatch(process_name="연선", sq_mm2=150)) == "stranding"


def test_resolve_key_insulation_variants():
    assert _resolve_key(FakeBatch(process_name="저압절연", sq_mm2=150)) == "insulation"
    assert _resolve_key(FakeBatch(process_name="고압절연", sq_mm2=150)) == "insulation"


def test_resolve_key_outsource_priority_sq_le_10():
    # SQ ≤ 10 면 process_name 무관 외주
    b = FakeBatch(process_name="저압시스", sq_mm2=10)
    assert _resolve_key(b) == "outsource"


def test_resolve_key_outsource_tfr8_16sq():
    b = FakeBatch(process_name="저압시스", sq_mm2=16, product_group="TFR-8(고온)")
    assert _resolve_key(b) == "outsource"


def test_resolve_key_outsource_imarket_tfrgv():
    b = FakeBatch(
        process_name="저압시스",
        sq_mm2=50,
        product_group="TFR-GV",
        customer_name="아이마켓코리아",
    )
    assert _resolve_key(b) == "outsource"


def test_resolve_key_default_for_unknown():
    # 신선 / T·P / 연합 등 미등록 process_name
    assert _resolve_key(FakeBatch(process_name="신선", sq_mm2=150)) == "default"
    assert _resolve_key(FakeBatch(process_name="연합", sq_mm2=150)) == "default"
    assert _resolve_key(FakeBatch(process_name="", sq_mm2=150)) == "default"


def test_get_phrasing_provider_dispatches_correctly():
    register_phrasing_provider(DefaultPhrasingProvider())
    register_phrasing_provider(SheathPhrasingProvider())
    p = get_phrasing_provider(FakeBatch(process_name="저압시스", sq_mm2=150))
    assert p.process_key == "sheath"
    p = get_phrasing_provider(FakeBatch(process_name="신선", sq_mm2=150))
    assert p.process_key == "default"


def test_get_phrasing_provider_no_registry_raises():
    # registry 비어있으면 RuntimeError
    with pytest.raises(RuntimeError, match="No phrasing provider"):
        get_phrasing_provider(FakeBatch())


def test_get_phrasing_provider_falls_back_to_default():
    register_phrasing_provider(DefaultPhrasingProvider())
    # 시스 Provider 미등록이라도 default 폴백
    p = get_phrasing_provider(FakeBatch(process_name="저압시스", sq_mm2=150))
    assert p.process_key == "default"


# ─────────────────────────────────────────────────────────────────────────
# §C. SheathPhrasingProvider — 7 sub-process × 5 anchor = 35 snapshot
# ─────────────────────────────────────────────────────────────────────────


SHEATH_SUBPROCESSES = [
    # (label, equipment, eq_category, sub_chip, fixture_extras)
    ("A120", "시스 1호기", "흑·청·흑/적", "A120", {}),
    ("A100", "시스 4호기", "갈·회·녹/황·백·적", "A100", {}),
    ("HFCO시스", "HFCO 1호기", "HFCO 전용", "HFCO", {"process_name": "HFCO시스"}),
    ("저압시스 일반", "시스 3호기", "흑·청·흑/적", "A120", {}),
    (
        "고압시스",
        "고압 1호기",
        "단일 633",
        "고압",
        {"process_name": "고압시스", "sq_mm2": 633},
    ),
    ("TFR-GV", "시스 2호기", "흑·청·흑/적", "A120", {"product_group": "TFR-GV"}),
    ("단선 접지선", "신선 1호기", "단선", "단선", {"sq_mm2": 25}),
]

SHEATH_ANCHORS = [
    (
        "5-1",
        {"min": 100, "max": 300, "sq": 150, "equipment": "시스 3호기"},
        "시스 3호기는 100~300SQ 시스 작업이 가능합니다 → 150SQ 적합",
    ),
    (
        "10-3",
        {
            "equipment": "시스 3호기",
            "allowed": "PVC·CV",
            "current": "PVC",
        },
        "시스 3호기는 PVC·CV 시스재질을 다룹니다 → 본 작업 PVC 적합",
    ),
    (
        "3-3",
        {
            "equipment": "시스 3호기",
            "eq_category": "A120",
            "color": "흑",
        },
        "시스 3호기는 A120 묶음 설비입니다 → 흑 통과",
    ),
    (
        "4-2_save",
        {"color": "흑"},
        "직전 묶음이 동일 흑이라 색상교체 시간이 들지 않습니다",
    ),
    (
        "4-2_warn",
        {"prev_color": "백", "minutes": 120},
        "직전 묶음이 백이라 색상교체 시간 120분이 발생합니다",
    ),
]


@pytest.mark.parametrize(
    "label,equipment,eq_category,sub_chip,fx_extras", SHEATH_SUBPROCESSES
)
@pytest.mark.parametrize("anchor,params,expected", SHEATH_ANCHORS)
def test_sheath_adequacy_matrix(
    label, equipment, eq_category, sub_chip, fx_extras, anchor, params, expected
):
    """7 sub-process × 5 anchor = 35 snapshot. 본 테스트는 phrasing.py 가
    sub-process 무관하게 같은 자연어를 출력한다는 invariant 확인 (sub-process
    chip 은 별도 헤더 — 자연어에 등장 X)."""
    p = SheathPhrasingProvider()
    assert p.adequacy_line(anchor=anchor, params=params) == expected


def test_sheath_handoff_tfr_gv_branch():
    p = SheathPhrasingProvider()
    assert (
        p.handoff_line(params={"is_tfrgv": True}) == "전공정: 연선 (절연 스킵 · TFR-GV)"
    )


def test_sheath_handoff_bare_ground_branch():
    p = SheathPhrasingProvider()
    assert (
        p.handoff_line(params={"is_bare_ground": True})
        == "전공정: 신선 (연선·절연 스킵 — 단선 접지선 SQ≤25)"
    )


def test_sheath_handoff_default():
    p = SheathPhrasingProvider()
    assert p.handoff_line(params={"predecessor": "저압절연"}) == "전공정: 저압절연"


def test_sheath_gantt_sort_label_memory_correct():
    """memory feedback_sheath_sort_order 3차 iteration — 납기 1차."""
    p = SheathPhrasingProvider()
    assert (
        p.gantt_sort_label()
        == "① 납기 가까운 순 → ② 전공정 ready 시각 → ③ 색상 인접 순"
    )


# ── Sheath severity_for — plan §B.6 표 검증 ─────────────────────────────


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        # color_change
        ("color_change", 0, "save"),
        ("color_change", 1, "warn"),
        ("color_change", 179, "warn"),
        ("color_change", 180, "fail"),
        ("color_change", 360, "fail"),
        # spec_change (kind fixed danger)
        ("spec_change", 0, "save"),
        ("spec_change", 1, "fail"),
        ("spec_change", 300, "fail"),
        # predecessor_gap
        ("predecessor_gap", 0, "save"),
        ("predecessor_gap", 29, "save"),
        ("predecessor_gap", 30, "warn"),
        ("predecessor_gap", 60, "warn"),
        ("predecessor_gap", 90, "fail"),
        # due_slack_days (큰 값이 좋음)
        ("due_slack_days", 1.5, "save"),
        ("due_slack_days", 1.0, "save"),
        ("due_slack_days", 0.5, "warn"),
        ("due_slack_days", -0.5, "warn"),
        ("due_slack_days", -1.0, "fail"),
        # 정의 안 된 kind
        ("unknown_kind", 99, "ok"),
    ],
)
def test_sheath_severity_for(kind, value, expected):
    p = SheathPhrasingProvider()
    assert p.severity_for(kind=kind, value=value) == expected


# ── Sheath impact_line — severity 계열별 자연어 ────────────────────────


def test_sheath_impact_color_change_save():
    p = SheathPhrasingProvider()
    assert (
        p.impact_line(kind="color_change", params={"value": 0})
        == "🎨 색상교체 0분 (절약)"
    )


def test_sheath_impact_color_change_warn():
    p = SheathPhrasingProvider()
    assert (
        p.impact_line(kind="color_change", params={"value": 120}) == "🎨 색상교체 120분"
    )


def test_sheath_impact_due_slack_save():
    p = SheathPhrasingProvider()
    assert (
        p.impact_line(kind="due_slack_days", params={"value": 1.2})
        == "📅 납기 여유 +1.2일"
    )


# ─────────────────────────────────────────────────────────────────────────
# §D. StrandingPhrasingProvider — 5 anchor snapshot
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "anchor,params,expected",
    [
        (
            "5-1",
            {"equipment": "연선 1호기", "min": 50, "max": 200, "sq": 150},
            "연선 1호기는 50~200SQ 연선 작업이 가능합니다 → 150SQ 적합",
        ),
        (
            "5-2",
            {"equipment": "연선 1호기", "stranding_type": "압축"},
            "연선 1호기는 압축 전용 설비입니다 → 본 작업 적합",
        ),
        (
            "10-1",
            {"equipment": "연선 1호기", "allowed": "CU", "current": "CU"},
            "연선 1호기는 CU 재질을 다룹니다 → CU 적합",
        ),
        (
            "10-4",
            {"equipment": "연선 1호기", "voltage": "600"},
            "연선 1호기는 600V 전압대를 다룹니다 → 본 작업 적합",
        ),
        (
            "2-1_match",
            {"sm_id": 4421, "remainder_pct": 12.3},
            "재공 SM-4421 (12.3% 잔량) 매칭 → 신규 SM 투입 절감",
        ),
    ],
)
def test_stranding_adequacy(anchor, params, expected):
    p = StrandingPhrasingProvider()
    assert p.adequacy_line(anchor=anchor, params=params) == expected


def test_stranding_severity_wip_loss():
    p = StrandingPhrasingProvider()
    assert p.severity_for(kind="wip_loss_pct", value=4.9) == "save"
    assert p.severity_for(kind="wip_loss_pct", value=8.0) == "warn"
    assert p.severity_for(kind="wip_loss_pct", value=15.0) == "fail"


# ─────────────────────────────────────────────────────────────────────────
# §E. InsulationPhrasingProvider — 4 anchor snapshot
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "anchor,params,expected",
    [
        (
            "5-1",
            {"equipment": "절연 1호기", "min": 50, "max": 300, "sq": 150},
            "절연 1호기는 50~300SQ 절연 작업이 가능합니다 → 150SQ 적합",
        ),
        (
            "3-1",
            {
                "equipment": "절연 1호기",
                "color_group": "흑·청",
                "color": "흑",
            },
            "절연 1호기는 흑·청 색상그룹 설비입니다 → 흑 통과",
        ),
        (
            "10-2",
            {
                "equipment": "절연 1호기",
                "allowed_compound": "PVC",
                "current": "PVC",
            },
            "절연 1호기는 PVC 컴파운드 재고 보유 → PVC 적합",
        ),
        (
            "10-5",
            {"line_speed": 50},
            "4심 작업 — SpeedMaster 4C 키 적용 (선속 50 m/min)",
        ),
    ],
)
def test_insulation_adequacy(anchor, params, expected):
    p = InsulationPhrasingProvider()
    assert p.adequacy_line(anchor=anchor, params=params) == expected


# ─────────────────────────────────────────────────────────────────────────
# §F. OutsourcePhrasingProvider — 3 anchor snapshot
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "anchor,params,expected",
    [
        (
            "2-2_sq_le_10",
            {"sq": 6},
            "SQ 6 ≤ 10 → 사내 설비 작업 불가, 외주 자동분류",
        ),
        (
            "2-2_tfr8_16",
            {},
            "TFR-8 16SQ 고온 사양 → 외주 전용 분류",
        ),
        (
            "2-2_imarket_tfrgv",
            {},
            "아이마켓코리아 + TFR-GV → 고객 지정 외주",
        ),
    ],
)
def test_outsource_adequacy(anchor, params, expected):
    p = OutsourcePhrasingProvider()
    assert p.adequacy_line(anchor=anchor, params=params) == expected


def test_outsource_severity_lead_days():
    p = OutsourcePhrasingProvider()
    assert p.severity_for(kind="outsource_lead_days", value=2) == "save"
    assert p.severity_for(kind="outsource_lead_days", value=4) == "warn"
    assert p.severity_for(kind="outsource_lead_days", value=10) == "fail"


# ─────────────────────────────────────────────────────────────────────────
# §G. Fault path — anchor 누락 / params 누락 / unknown impact kind
# ─────────────────────────────────────────────────────────────────────────


def test_sheath_anchor_missing_returns_safe_fallback():
    """anchor key 미등록 → '[결정 근거 누락: ...]' 안전 폴백."""
    p = SheathPhrasingProvider()
    out = p.adequacy_line(anchor="non-existent-anchor", params={})
    assert "[결정 근거 누락" in out
    assert "non-existent-anchor" in out


def test_sheath_params_key_missing_returns_safe_fallback():
    """params 에 필요한 키가 빠지면 안전 폴백."""
    p = SheathPhrasingProvider()
    out = p.adequacy_line(anchor="5-1", params={"min": 100})  # max, sq, equipment 누락
    assert "[결정 근거 누락" in out


def test_sheath_unknown_impact_kind_returns_marker():
    p = SheathPhrasingProvider()
    out = p.impact_line(kind="hypothetical_kind", params={"value": 0})
    assert "unknown impact kind" in out


# ─────────────────────────────────────────────────────────────────────────
# §H. Default / verdict_summary smoke
# ─────────────────────────────────────────────────────────────────────────


def test_default_provider_returns_v1_unsupported_message():
    p = DefaultPhrasingProvider()
    assert "v1 결정 카드에서 다루지 않습니다" in p.adequacy_line(
        anchor="any", params={}
    )


def test_default_provider_severity_always_ok():
    p = DefaultPhrasingProvider()
    assert p.severity_for(kind="anything", value=999) == "ok"


def test_sheath_verdict_summary_safe_signal():
    p = SheathPhrasingProvider()
    audit = SimpleNamespace(color_change_min=0, spec_change_min=0, due_slack_days=1.5)
    out = p.verdict_summary(
        batch=FakeBatch(),
        audit=audit,
        solver=None,
        schedule_task=None,
    )
    assert out.startswith("✓")
    assert "색상교체 0분" in out
    assert "+1.5일" in out


def test_sheath_verdict_summary_warn_signal():
    p = SheathPhrasingProvider()
    audit = SimpleNamespace(color_change_min=0, spec_change_min=300, due_slack_days=0.0)
    out = p.verdict_summary(
        batch=FakeBatch(),
        audit=audit,
        solver=None,
        schedule_task=None,
    )
    assert out.startswith("⚠")
    assert "규격교체 300분" in out


def test_sheath_verdict_summary_with_schedule_task_prefix():
    p = SheathPhrasingProvider()
    audit = SimpleNamespace(color_change_min=0, spec_change_min=0, due_slack_days=1.5)
    schedule_task = SimpleNamespace(
        start_at=datetime(2026, 4, 30, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    )
    out = p.verdict_summary(
        batch=FakeBatch(),
        audit=audit,
        solver=None,
        schedule_task=schedule_task,
    )
    assert "04-30(목) 14:00" in out
