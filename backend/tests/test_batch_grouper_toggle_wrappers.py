"""Track A — batch_grouper 의 toggle wrapper helper 단위 테스트.

2-2 외주 / 2-4 61연선 / 5-5 TFR-GV 의 wrapper 가 ConstraintParams.is_rule_enabled
게이트를 정확히 통과·차단하는지 검증. 룰 본문 (sq<=10, sq>=300 CU 등) 의 정확성은
기존 batch_grouper 통합 테스트 + parity harness 가 담당.
"""

from __future__ import annotations

from app.application._shared.constraint_params import ConstraintParams
from app.application.ingest.batch_grouper import (
    _is_61strand_rule,
    _is_outsource_rule_with_toggle,
    _should_skip_stranding,
)


def _params(enabled: dict[str, bool]) -> ConstraintParams:
    """toggle 게이트만 검증하는 단위 테스트용 — by_id 는 비움."""
    return ConstraintParams(by_id={}, enabled_by_id=enabled)


# ─── 2-2 외주 ────────────────────────────────────────────────────────────


def test_outsource_toggle_off_returns_false_even_when_rule_matches():
    """is_enabled=False → 룰 본문 매치 (sq=5) 여도 False."""
    p = _params({"2-2": False})
    assert (
        _is_outsource_rule_with_toggle(
            sq=5, product_group="일반전선", customer_name="일반시판", params=p
        )
        is False
    )


def test_outsource_toggle_on_runs_rule_body():
    """is_enabled=True → 룰 본문 그대로 (sq<=10 → True / sq>10 → False)."""
    p = _params({"2-2": True})
    assert (
        _is_outsource_rule_with_toggle(
            sq=5, product_group="일반", customer_name="일반시판", params=p
        )
        is True
    )
    assert (
        _is_outsource_rule_with_toggle(
            sq=100, product_group="일반", customer_name="일반시판", params=p
        )
        is False
    )


def test_outsource_missing_row_defaults_to_enabled():
    """legacy DB (row 없음) → True 폴백 → 룰 본문 적용 → 기존 동작 유지."""
    p = _params({})  # 2-2 row 자체가 없음
    assert (
        _is_outsource_rule_with_toggle(
            sq=5, product_group="일반", customer_name="일반시판", params=p
        )
        is True
    )


# ─── 5-5 TFR-GV ──────────────────────────────────────────────────────────


def test_tfr_gv_toggle_off_returns_false_even_for_qualifying_input():
    p = _params({"5-5": False})
    assert _should_skip_stranding(product_group="TFR-GV(7C)", sq=16, params=p) is False


def test_tfr_gv_toggle_on_runs_rule_body():
    p = _params({"5-5": True})
    # TFR-GV + sq<=25 → True (skip stranding)
    assert _should_skip_stranding(product_group="TFR-GV(7C)", sq=16, params=p) is True
    # TFR-GV but sq>25 → False
    assert _should_skip_stranding(product_group="TFR-GV(7C)", sq=70, params=p) is False
    # non-TFR-GV → False
    assert _should_skip_stranding(product_group="일반전선", sq=16, params=p) is False
    # None 안전성
    assert _should_skip_stranding(product_group=None, sq=16, params=p) is False


def test_tfr_gv_missing_row_defaults_to_enabled():
    p = _params({})
    assert _should_skip_stranding(product_group="TFR-GV(7C)", sq=16, params=p) is True


# ─── 2-4 61연선 ──────────────────────────────────────────────────────────


def test_61strand_toggle_off_returns_false_even_for_qualifying_input():
    p = _params({"2-4": False})
    assert _is_61strand_rule(sq=400, conductor_material="CU", params=p) is False


def test_61strand_toggle_on_runs_rule_body():
    p = _params({"2-4": True})
    # sq>=300 CU → True
    assert _is_61strand_rule(sq=400, conductor_material="CU", params=p) is True
    assert _is_61strand_rule(sq=300, conductor_material="CU", params=p) is True
    # sq<300 → False
    assert _is_61strand_rule(sq=100, conductor_material="CU", params=p) is False
    # AL → False
    assert _is_61strand_rule(sq=400, conductor_material="AL", params=p) is False
    # None 재질 안전성
    assert _is_61strand_rule(sq=400, conductor_material=None, params=p) is False


def test_61strand_missing_row_defaults_to_enabled():
    p = _params({})
    assert _is_61strand_rule(sq=400, conductor_material="CU", params=p) is True
