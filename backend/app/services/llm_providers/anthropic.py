"""Anthropic claude-haiku-4-5 narrator with prompt caching.

Why claude-haiku-4-5: short Korean sentence summarisation has very low
quality requirements — Haiku is ~10× cheaper than Sonnet and round-trip
latency stays under the Decision Card render budget.

Why ``cache_control`` on the system prompt: the system text is identical
across every Decision Card render in a session, so paying the one-time
cache write means every subsequent render reads the cached prefix and
the API only bills for the (small) per-batch user message. Critical for
keeping per-render cost bounded as the dashboard scales.

Why lazy ``anthropic`` import inside ``__init__``: keeps the package
import-safe in test environments that may not have the SDK pinned.
``LLM_PROVIDER=template`` (the default in conftest) never triggers this
code path.
"""

from __future__ import annotations

import os

from app.services.llm_providers import ExplainPayload

_MODEL = "claude-haiku-4-5-20251001"


class AnthropicProvider:
    def __init__(self) -> None:
        from anthropic import Anthropic

        # Empty-string fallback: Anthropic SDK raises clearly on a missing
        # key; using an empty string preserves that error rather than
        # masking it with a KeyError on env access.
        self._client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    def explain(self, payload: ExplainPayload) -> str:
        contrib_lines = "\n".join(
            f"- {c.korean_name}({c.constraint_id}): weight {c.weight_applied}"
            for c in payload.contributions
        )
        msg = self._client.messages.create(
            model=_MODEL,
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": (
                        "너는 케이블 제조 스케줄링 시스템의 결정 설명자다. "
                        "한국어로 1문장 요약하라."
                    ),
                    # Why ephemeral: the system prompt is stable per process
                    # so a 5-minute TTL covers a typical dashboard session
                    # without paying for persistent storage.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"배치 {payload.batch_id} ({payload.run_label}):\n"
                        f"{contrib_lines}\n"
                        f"설비: {payload.assigned_equipment_id}"
                    ),
                }
            ],
        )
        return msg.content[0].text
