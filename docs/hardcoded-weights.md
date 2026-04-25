# Hardcoded weight inventory (Week 5 migration)

Each row migrates from a Python constant to a `constraint_config` row whose
`params_json.weight` becomes the live value consumed by `objective.attach()` /
`compose_objective` (via `ConstraintSpec`). The default `priority` (DB column,
0–100) is the operator-facing slider — it is captured here as metadata only;
the parity-preserving solver value is `params_json["weight"]`.

## Constants discovered

```bash
grep -nE '^_[A-Z_]+\s*=\s*[0-9]'  backend/app/services/cp_sat_optimizer.py
grep -nE '^_[A-Z_]+\s*=\s*\{'      backend/app/services/cp_sat_optimizer.py
grep -nE '^_[A-Z_]+'               backend/app/domain/constants.py
```

### From `backend/app/domain/constants.py` (Week 3 baselines)

| Constant               | Current value | Notes                                                                                                                                                                                                            |
| ---------------------- | ------------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_DUE_HARD_WEIGHT`     |        100000 | tardiness_hard=False fallback weight (also the base for `_TARDINESS_WEIGHT`).                                                                                                                                    |
| `_CHAIN_WEIGHT`        |           120 | color-change cost (matches `resolve_color_change_min` default). Currently NOT consumed by `compose_objective` (legacy chain_terms removed); kept as `ModelWeights.CHAIN_WEIGHT` for re-export & potential reuse. |
| `_TRANSITION_WEIGHT`   |           180 | equipment transition setup (per-pair, soft).                                                                                                                                                                     |
| `_WORK_MIN_PER_DAY`    |  840 (=14×60) | not a penalty weight; horizon helper, NOT migrated.                                                                                                                                                              |
| `_DEFAULT_WELDING_MIN` |            30 | not a penalty weight; constraint param fallback, NOT migrated.                                                                                                                                                   |
| `_WIP_SKIP_PROCESSES`  |          dict | not a penalty weight; routing rule, NOT migrated.                                                                                                                                                                |

### From `backend/app/services/cp_sat_optimizer.py`

| Constant                    |                               Current value | Notes                                                                                                                                                     |
| --------------------------- | ------------------------------------------: | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_WORK_MIN_PER_DAY_DEFAULT` |                               1440 (=24×60) | horizon helper, NOT migrated.                                                                                                                             |
| `_MAX_HORIZON_MIN`          |                          90 × 1440 = 129600 | horizon, NOT migrated.                                                                                                                                    |
| `_SOLVER_TIME_LIMIT_SEC`    |                                          30 | runtime config, NOT migrated.                                                                                                                             |
| `_TARDINESS_WEIGHT` (dict)  | `{critical: 1e7, urgent: 1e6, normal: 1e5}` | per-batch tardiness multiplier; values are derived as `_DUE_HARD_WEIGHT × {100,10,1}`. **Each urgency tier becomes its own `c_tardiness_<urgency>` row.** |
| `_IDLE_WEIGHT`              |                                           1 | per-min idle penalty (consumed by `compose_objective` via `weights.IDLE_WEIGHT`).                                                                         |
| `_SLACK_WEIGHT_BASE`        |                                      100000 | on-time slack base; `weight = SLACK_WEIGHT_BASE // slack_min`.                                                                                            |
| `_PAST_SEVERITY_K`          |                                           5 | past-due severity divisor; `1 + past_days/K` multiplier.                                                                                                  |
| `_EDD_PAIR_WEIGHT`          |                                       10000 | EDD pair (normal) violation penalty.                                                                                                                      |
| `_EDD_MIXED_PASTDUE_WEIGHT` |                                  1000000000 | EDD mixed past-due/on-time pair penalty.                                                                                                                  |

## Migration table

Each row below maps a Python constant to a `constraint_config` row that the
solver loads via `load_active_constraints` → `ConstraintSpec.params["weight"]`.

| Constant                        | Current value | constraint_id          | Default priority (0–100) | Notes                                                                                                                                                                |
| ------------------------------- | ------------: | ---------------------- | -----------------------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_DUE_HARD_WEIGHT`              |        100000 | `c_due_hard`           |                       50 | tardiness_hard=False fallback weight. Also serves as base for `c_tardiness_normal`.                                                                                  |
| `_CHAIN_WEIGHT`                 |           120 | `c_chain`              |                       50 | color-change cost; passed through `ModelWeights.CHAIN_WEIGHT` (currently no consumer in `compose_objective`, but parity preserved by carrying the value end-to-end). |
| `_TRANSITION_WEIGHT`            |           180 | `c_transition`         |                       50 | equipment transition setup (Round 2 HIGH #6).                                                                                                                        |
| `_IDLE_WEIGHT`                  |             1 | `c_idle`               |                       50 | per-min idle penalty.                                                                                                                                                |
| `_SLACK_WEIGHT_BASE`            |        100000 | `c_slack_base`         |                       50 | on-time slack base; `_w = max(1, base // slack_min)`.                                                                                                                |
| `_PAST_SEVERITY_K`              |             5 | `c_past_severity_k`    |                       50 | past-due severity divisor; not strictly a "weight" but a tunable scalar.                                                                                             |
| `_EDD_PAIR_WEIGHT`              |         10000 | `c_edd_pair`           |                       50 | EDD pair (normal).                                                                                                                                                   |
| `_EDD_MIXED_PASTDUE_WEIGHT`     |    1000000000 | `c_edd_mixed_pastdue`  |                       50 | EDD mixed past-due/on-time pair.                                                                                                                                     |
| `_TARDINESS_WEIGHT["critical"]` |      10000000 | `c_tardiness_critical` |                       50 | per-batch tardiness, critical urgency.                                                                                                                               |
| `_TARDINESS_WEIGHT["urgent"]`   |       1000000 | `c_tardiness_urgent`   |                       50 | per-batch tardiness, urgent urgency.                                                                                                                                 |
| `_TARDINESS_WEIGHT["normal"]`   |        100000 | `c_tardiness_normal`   |                       50 | per-batch tardiness, normal urgency.                                                                                                                                 |

## Priority normalisation choice

`compose_objective` (Week 2 implementation) reads `weights.IDLE_WEIGHT`,
`weights.EDD_PAIR_WEIGHT`, etc. directly from the `ModelWeights` bundle —
there is no existing `spec.weight × priority/50` arithmetic to follow. For
Week 5 we adopt **Option (b)**: the migration delivers DB-driven raw weights
read via `ConstraintSpec.params["weight"]`. The `priority` column remains the
operator-facing UX field (Admin UI slider) but is **not** consumed by
`compose_objective` in this Week 5 increment.

This is recorded as a **known compromise**: the `priority` slider becomes a
durable knob whose value is persisted, audited, and visible to the LLM
narrator (Week 4) and trace_writer, but it does not yet move the objective
function. A follow-up (Week 5+ extension) can add `weight × (priority/50)`
scaling once the operator-side semantics are validated with the pilot user.

The default `priority=50` value chosen for every seeded row is the column
default already in the schema — keeping it neutral preserves the current
distribution-of-influence shape across all weights.

## Migration plan

- **5A.2** — seed `constraint_config` rows above with raw weights matching the
  Python constants exactly. Re-run safe; existing rows skipped.
- **5A.3a..k** — replace each constant in `cp_sat_optimizer.py`'s
  `ModelWeights(...)` construction with a lookup against `specs_by_id[<id>]
.params["weight"]`. One sub-commit per constant; parity-quick green after
  each. Dict constants (`_TARDINESS_WEIGHT`) expand into a constructed dict
  from the three tardiness rows.
- **5A.4** — full parity (10/10) + programmatic admin-UI ↔ solver verification
  (priority change → re-solve → objective_value moves IFF the priority is
  causally wired; otherwise document the UX-only compromise).

## Status

- [x] Task 5A.1 — inventory complete (this file).
- [ ] Task 5A.2 — seed script + `seed_db.py` integration.
- [ ] Task 5A.3 — per-constant migration (a..k).
- [ ] Task 5A.4 — full parity + admin UI verification.
