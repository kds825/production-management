"""LLM provider abstraction for the Decision Card narrator (Week 4 Task 4B.2).

Why this package exists separately from the legacy ``app.services.llm_explainer``:
  - Legacy ``llm_explainer.py`` stays untouched — it is wired into
    ``routes/audit.py`` and ``routes/plan_pipeline.py`` and changes there carry
    behavioural risk for callers we are not refactoring this week.
  - The new Decision Card pathway needs a clean Provider seam so that tests
    (and parity runs) can swap in a deterministic ``TemplateProvider`` while
    production keeps the option to call Anthropic. Keeping the seam in a
    fresh package avoids polluting the legacy module's namespace.

Design choices:
  - ``Provider`` is a ``typing.Protocol`` instead of an ABC so the test fakes
    in ``tests/test_llm_narrator_hallucination_block.py`` do not need to
    inherit anything — duck typing is enough.
  - ``ExplainPayload`` / ``Contribution`` / ``ConstraintRef`` are frozen-ish
    dataclasses (we do not freeze because dataclass mutation is not a hazard
    for this narrow callsite) so the route layer can construct them with
    keyword args without coupling to provider internals.
  - ``get_provider`` reads the *name string* (typically from
    ``os.environ["LLM_PROVIDER"]`` at the route layer) and returns a fresh
    instance. Defaulting to ``TemplateProvider`` keeps tests deterministic
    even if env is missing — fail-safe over fail-loud here, because a
    missing env var should never break a Decision Card render.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class Contribution:
    """One constraint's weighted contribution to the solver objective.

    ``bound`` and ``delta_if_removed`` are reserved for the Week 5 admin
    dashboard sensitivity view and are optional in the Week 4 happy path.
    """

    constraint_id: str
    korean_name: str
    weight_applied: float
    bound: float | None = None
    delta_if_removed: float | None = None


@dataclass
class ConstraintRef:
    """Lightweight reference to a constraint that bound this decision."""

    constraint_id: str
    korean_name: str


@dataclass
class ExplainPayload:
    """Per-decision context handed to a provider.

    Why a single payload object: keeps the ``Provider.explain`` signature
    stable when we add fields (e.g. tardiness deltas) — providers can
    grow attribute reads without touching every call site.
    """

    batch_id: str
    run_label: str
    contributions: list[Contribution]
    binding_hard_constraints: list[ConstraintRef]
    assigned_equipment_id: str
    assigned_start: datetime
    assigned_end: datetime


class Provider(Protocol):
    """Narrator interface — one method, returns a Korean sentence."""

    def explain(self, payload: ExplainPayload) -> str:  # pragma: no cover - protocol
        ...


def get_provider(name: str) -> Provider:
    """Resolve a provider by name — falls back to ``TemplateProvider``.

    Why a string switch and not a registry: only two providers exist in
    the PoC scope; a registry would be overkill. The Anthropic import is
    lazy so that test environments without ``anthropic`` installed still
    boot the app.
    """
    if name in ("anthropic", "openai"):
        from app.infrastructure.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    return TemplateProvider()


# Eager import — TemplateProvider has no heavy deps and is the test default,
# so paying the import cost up front avoids a per-call import inside
# ``get_provider`` on the hot path.
from app.infrastructure.llm.template_provider import TemplateProvider  # noqa: E402,F401
