# Deletion Log — Production Handoff Refactor

This log records private-symbol/dead-code removals performed during the
production-handoff refactor. An entry only goes here when BOTH of the
following hold:

1. `vulture --min-confidence 80` flags the symbol AND
2. `grep -rE "\bname\b" backend/app backend/tests` finds zero callers
   (excluding the definition itself).

Entries listed for awareness — never deleted — go in the "Retained" section.

## Week 3 — services/batch_grouping split (Task 3A.3)

No confirmed-dead removals.

Vulture / ruff baseline against the pre-split file
(`backend/app/services/batch_grouping.py`, 1971 lines) reported:

- `urgency_priority_threshold` (line 1659, vulture 100%) —
  **Retained**. Public keyword arg of `execute_auto_splits`. No internal
  reference because the urgency criteria currently live inside
  `detect_split_candidates` (`min_priority <= 7`, `days_until <= 7`),
  but the kwarg remains a documented hook for downstream callers /
  future overrides. Removal would be a behavior-visible API break.
- `urgency_days_threshold` (line 1660, vulture 100%) —
  **Retained**. Same rationale as above.
- `orig_len = float(header.total_length_m or 0)` (line 1546, ruff F841) —
  **Retained**. Unused local in `_apply_auto_split`. Pairs with `orig_dur`
  one line above; almost certainly leftover from a prior refactor where
  both were used in the surplus reallocation. Keeping for now because
  removing it without behavioral verification risks masking a future bug
  in the surplus calculation. Pre-existing baseline; the split moved it
  verbatim into `splitting.py`.

The Week 3 split was structural-only — no behavior changes intended, no
deletions performed.
