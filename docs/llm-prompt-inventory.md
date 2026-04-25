# LLM Prompt Inventory

**Audience:** anyone touching the Decision Card narrator or planning to swap
LLM providers.
**Scope:** every prompt the backend sends to an LLM in the Decision Card
pathway, plus the kiwipiepy guard that decides whether the LLM output
ships to the UI or gets replaced by the deterministic template.

The legacy narrator at `backend/app/services/llm_explainer.py` is **out of
scope** for this inventory — it powers the older `routes/audit.py` / `routes/plan_pipeline.py`
endpoints and stays untouched per Week 4 scope.

---

## 1. `TemplateProvider` — deterministic Korean fallback

- **File:** `backend/app/services/llm_providers/template.py`
- **Class:** `TemplateProvider`
- **Model:** none — pure Python format string.
- **Token budget:** N/A.

**Output template** (`template.py:36`):

```
이 배치는 {names} 균형으로 {assigned_equipment_id}에 배정됐다.
```

…where `names` is `" + ".join(c.korean_name for c in top3)` from the
top-3 contributions sorted by `weight_applied` descending
(`template.py:32-35`). Empty-contributions edge case
(`template.py:27-28`) returns:

```
{batch_id} 배치는 활성 제약 없이 배정됐다.
```

**Why this exists:** parity tests (`backend/tests/conftest.py` sets
`os.environ.setdefault("LLM_PROVIDER", "template")`) and production
fallback when `decision_narrator.explain_with_filter` detects a
hallucination in the live provider's output.

**Driving payload shape:** `Contribution` from
`backend/app/services/llm_providers/__init__.py` — fields
`constraint_id`, `korean_name`, `weight_applied`. The template only reads
`korean_name` (used for the chain) and `weight_applied` (used to sort);
no other fields are touched.

---

## 2. `AnthropicProvider` — claude-haiku-4-5 with prompt caching

- **File:** `backend/app/services/llm_providers/anthropic.py`
- **Class:** `AnthropicProvider`
- **Model id:** `claude-haiku-4-5-20251001` (`anthropic.py:25`).
- **Token budget:** `max_tokens=200` (`anthropic.py:43`). One sentence in
  Korean fits under that easily; the cap exists so a runaway response
  cannot blow the per-render cost ceiling.

**System message** (`anthropic.py:48-51`, sent with
`cache_control={"type": "ephemeral"}` on `anthropic.py:55`):

```
너는 케이블 제조 스케줄링 시스템의 결정 설명자다. 한국어로 1문장 요약하라.
```

`cache_control: ephemeral` exploits Anthropic's 5-minute TTL prompt cache:
identical system prefix across every Decision Card render in a session, so
only the first request pays the cache write — every subsequent render
bills only the tiny user message.

**User message template** (`anthropic.py:60-66`):

```
배치 {batch_id} ({run_label}):
- {korean_name}({constraint_id}): weight {weight_applied}
- … (one line per contribution)
설비: {assigned_equipment_id}
```

Contribution lines come from `payload.contributions` iterated in input
order — the route layer sorts by `constraint_id` before constructing the
payload, so output is deterministic for cache reuse.

**Driving payload shape:** the `Contribution` dataclass (see
`llm_providers/__init__.py`). The user message uses `korean_name`,
`constraint_id`, and `weight_applied`. `bound` and `delta_if_removed`
fields exist for future sensitivity views and are intentionally unused
here.

---

## 3. Hallucination filter — `decision_narrator.explain_with_filter`

- **File:** `backend/app/services/decision_narrator.py`
- **Why it exists:** even with a clamped system prompt, an LLM can
  fabricate Korean nouns ("외주 라인", "주말 근무") that mean nothing in
  the domain. The filter catches them before the UI renders.

### `_BASE_ALLOW` whitelist (`decision_narrator.py:44-54`)

Always-allowed Korean nouns (domain-generic — too broad to push into the
catalog without bloating every fixture):

```
{"배치", "납기", "설비", "시간", "지연", "회피", "배정", "이", "그"}
```

Per-request `korean_name_catalog` is unioned on top — those are the
constraint-name nouns the route layer pulled from `constraint_config`,
so the allowed set tracks the live constraint roster automatically.

### kiwipiepy filter rule (`decision_narrator.py:57-65`, `89-93`)

1. Tokenize the LLM output with the module-level `Kiwi()` singleton.
2. Keep only tokens whose POS tag starts with `NN` (NNG general, NNP
   proper, NNB bound, NNBC counter — every Korean noun class).
3. Compute `hallucinated = nouns - (_BASE_ALLOW ∪ catalog)`.
4. If `hallucinated` is non-empty, **discard the LLM output** and return
   `TemplateProvider().explain(payload)`. Return `(text, True)` so the
   caller can log the fallback (the `True` flag is surfaced by `routes/decisions.py`).

The filter is a one-way gate: a single unknown noun → template fallback.
That deliberate strictness is why production runs almost never expose a
fabricated entity to operators, even on the rare model glitch.
