"""OpenAI-compatible gateway narrator (PwC GenAI SharedService).

Replaces the previous direct Anthropic SDK provider. The PwC gateway
exposes an OpenAI-compatible ``/v1/chat/completions`` endpoint that
routes to various backend models (Bedrock Claude, Azure GPT, Vertex
Gemini) via a model alias string.

Why ``openai`` SDK: the gateway speaks the OpenAI wire format, so the
``openai`` Python package is the natural client. ``base_url`` is set to
the gateway endpoint and ``api_key`` to the gateway token.
"""

from __future__ import annotations

import os

from app.infrastructure.llm import ExplainPayload

_MODEL = os.environ.get("LLM_MODEL", "bedrock.anthropic.claude-sonnet-4-6")


class AnthropicProvider:
    """Name kept for backward-compat with get_provider('anthropic')."""

    def __init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get(
                "OPENAI_API_BASE",
                "https://genai-sharedservice-americas.pwcinternal.com/v1",
            ),
        )

    def explain(self, payload: ExplainPayload) -> str:
        contrib_lines = "\n".join(
            f"- {c.korean_name}({c.constraint_id}): weight {c.weight_applied}"
            for c in payload.contributions
        )
        resp = self._client.chat.completions.create(
            model=_MODEL,
            max_tokens=200,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "너는 케이블 제조 스케줄링 시스템의 결정 설명자다. "
                        "한국어로 1문장 요약하라."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"배치 {payload.batch_id} ({payload.run_label}):\n"
                        f"{contrib_lines}\n"
                        f"설비: {payload.assigned_equipment_id}"
                    ),
                },
            ],
        )
        return resp.choices[0].message.content or ""
