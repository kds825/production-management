"""Hallucination-block test for the Decision Card narrator (Week 4 Task 4B.2).

Why two scenarios:
  1. ``test_hallucination_falls_back_to_template`` — proves the kiwipiepy
     filter trips on a noun ("외계인") not in the catalog and that the
     fallback substitutes the deterministic TemplateProvider output.
  2. ``test_clean_output_passes_through`` — proves the filter does NOT
     false-positive on legitimate Korean text whose nouns are all covered
     by ``_BASE_ALLOW`` ∪ ``korean_name_catalog``.
"""

from datetime import datetime

from app.application.decisions.narrator import explain_with_filter
from app.infrastructure.llm import (
    Contribution,
    ExplainPayload,
)


class _BadProvider:
    """Returns text containing a Korean noun ("외계인") guaranteed to be
    outside any reasonable scheduling-domain catalog."""

    def explain(self, payload: ExplainPayload) -> str:
        return "이 배치는 외계인이 결정했다."


class _GoodProvider:
    """Returns text whose nouns all live in the base/catalog allowed set."""

    def explain(self, payload: ExplainPayload) -> str:
        return "이 배치는 납기 회피로 EX1에 배정됐다."


def _payload() -> ExplainPayload:
    now = datetime.now()
    return ExplainPayload(
        batch_id="B1",
        run_label="r1",
        contributions=[Contribution("c1", "납기", 100.0)],
        binding_hard_constraints=[],
        assigned_equipment_id="EX1",
        assigned_start=now,
        assigned_end=now,
    )


def test_hallucination_falls_back_to_template() -> None:
    text, fallback = explain_with_filter(
        _BadProvider(), _payload(), korean_name_catalog={"납기"}
    )
    assert fallback is True
    # The bad noun must be scrubbed by the template substitution.
    assert "외계인" not in text


def test_clean_output_passes_through() -> None:
    text, fallback = explain_with_filter(
        _GoodProvider(), _payload(), korean_name_catalog={"납기"}
    )
    assert fallback is False
    # The provider's text should pass through verbatim.
    assert text == "이 배치는 납기 회피로 EX1에 배정됐다."
