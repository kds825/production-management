"""Deterministic template narrator — the test default and production fallback.

Why a hand-written template (no LLM):
  - Tests must never hit a real Anthropic endpoint (cost + nondeterminism).
    ``conftest.py`` forces ``LLM_PROVIDER=template`` so this is what runs.
  - Production also uses this when ``decision_narrator.explain_with_filter``
    detects hallucinated nouns from the live provider (see kiwipiepy filter)
    — a guaranteed-safe fallback prevents UI corruption.

Output shape is intentionally one short Korean sentence: the Decision Card
slot is ~80 characters wide and longer summaries get truncated downstream.
Top-3 contributions by weight balance signal vs noise.
"""

from __future__ import annotations

from app.services.llm_providers import ExplainPayload


class TemplateProvider:
    """Deterministic Korean narrator. Top-3 contributions by weight."""

    def explain(self, payload: ExplainPayload) -> str:
        # Edge case: no constraints contributed (e.g. trivially unconstrained
        # batch). Returning a clear "활성 제약 없이" sentence beats showing an
        # empty " + " chain that would look like a UI bug.
        if not payload.contributions:
            return f"{payload.batch_id} 배치는 활성 제약 없이 배정됐다."
        # Sort descending by weight and take top 3 — stable since Python's
        # sort is stable, so ties keep input order (which is constraint_id
        # order from the route layer).
        top = sorted(
            payload.contributions, key=lambda c: c.weight_applied, reverse=True
        )[:3]
        names = " + ".join(c.korean_name for c in top)
        return f"이 배치는 {names} 균형으로 {payload.assigned_equipment_id}에 배정됐다."
