"""Decision Card narrator with kiwipiepy hallucination filter.

Why this module is separate from the legacy ``app.services.llm_explainer``:
  - The legacy module is consumed by ``routes/audit.py`` and
    ``routes/plan_pipeline.py`` and we explicitly do not want to perturb
    their behavior in Week 4.
  - The new Decision Card pathway needs a thin function that (a) calls a
    pluggable provider and (b) post-validates the output against a domain
    noun catalog. Bolting that logic onto the legacy module would entangle
    two unrelated callers on the same code path.

Why kiwipiepy for the filter:
  - Korean tokenization is non-trivial (no whitespace word boundaries) so
    a regex-based "is this noun in the catalog" check produces high
    false-positive rates. kiwipiepy exposes Korean POS tags (NN*) so we
    can extract just the nouns and compare to the allowed set.
  - The single ``Kiwi()`` instance is module-level — kiwipiepy loads a
    ~50MB trained model on construction, so reusing one instance keeps
    per-render cost flat.

Why a tiny ``_BASE_ALLOW`` set:
  - Nouns like "배치", "설비", "시간" are domain-generic and appear in
    almost every legitimate summary. Forcing the catalog to repeat them
    would push catalog maintenance into every test fixture. The base set
    is the floor; the route layer adds dynamic constraint-name nouns on
    top via ``korean_name_catalog``.
"""

from __future__ import annotations

from kiwipiepy import Kiwi

from app.services.llm_providers import (
    ExplainPayload,
    Provider,
    TemplateProvider,
)

# Module-level singleton — see "Why kiwipiepy" above.
_kiwi = Kiwi()

# Floor of always-allowed Korean nouns. Keep this list short and generic;
# domain-specific nouns belong in the per-request catalog.
_BASE_ALLOW = {
    "배치",
    "납기",
    "설비",
    "시간",
    "지연",
    "회피",
    "배정",
    "이",
    "그",
}


def _korean_nouns(text: str) -> set[str]:
    """Return the set of Korean noun forms in ``text``.

    Why ``tag.startswith("NN")``: kiwipiepy tags include NNG (general
    noun), NNP (proper noun), NNB (bound noun), NNBC (counter). All four
    are nouns we want to validate. Verbs (VV*) and particles (J*) are
    intentionally skipped — they cannot hallucinate domain entities.
    """
    return {t.form for t in _kiwi.tokenize(text) if t.tag.startswith("NN")}


def explain_with_filter(
    provider: Provider,
    payload: ExplainPayload,
    korean_name_catalog: set[str],
) -> tuple[str, bool]:
    """Run ``provider.explain`` and fall back to template on hallucination.

    Returns
    -------
    (summary_text, was_template_fallback)
        ``was_template_fallback`` is ``True`` when the provider produced
        a Korean noun outside the union of ``_BASE_ALLOW`` and
        ``korean_name_catalog``. The caller should surface this flag so
        the UI can label uncertain summaries (Week 5 polish).

    Why fall back instead of raising:
      A Decision Card render must never 500 just because the LLM said
      something off-topic. The TemplateProvider output is always safe
      and deterministic, so silent fallback preserves UX while still
      letting the caller log the event via the boolean.
    """
    raw = provider.explain(payload)
    allowed = _BASE_ALLOW | korean_name_catalog
    hallucinated = _korean_nouns(raw) - allowed
    if hallucinated:
        return TemplateProvider().explain(payload), True
    return raw, False
