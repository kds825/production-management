# Private-symbol inventory (Weeks 2/3 re-export surface)

Authoritative list of every `_private` symbol currently imported from `app.services.cp_sat_optimizer`, `app.services.schedule_optimizer`, and `app.services.batch_grouping` — the three modules scheduled for extraction in Weeks 2-3 of the Production Handoff Refactor.

**D7-C invariant.** No source file on this list may be `rm`'d until every symbol below remains importable from its original dotted path, either directly or via a re-export shell at that path. Violating this invariant silently breaks the test suite mid-refactor.

**Generator.** `scripts/gen_private_symbol_inventory.py` (AST-based; handles multi-line imports the grep one-liner misses). For aliased re-exports (`from X import _foo as _bar`) the source-side name `_foo` is recorded.

**Regenerate.** `python3 scripts/gen_private_symbol_inventory.py > docs/private-symbol-inventory.md` — run before Week 2 kickoff and again after Weeks 2/3 to confirm no new private-import surface emerged. Output is deterministic; re-running on an unchanged tree yields no diff.

## cp_sat_optimizer (9 symbols)

- `_CHAIN_WEIGHT`
- `_DUE_HARD_WEIGHT`
- `_TRANSITION_WEIGHT`
- `_WORK_MIN_PER_DAY`
- `_compute_group_duration_map`
- `_datetime_to_wmin`
- `_due_work_min`
- `_is_multi_equip_group`
- `_working_minutes_between`

## schedule_optimizer (3 symbols)

- `_find_available_slot`
- `_purge_run_tasks`
- `_tardiness_boost_retry`

## batch_grouping (3 symbols)

- `_SHEATH_COLOR_RANK`
- `_compose_sheath_group_key`
- `_find_speed`

## Also imported by non-test code (2 symbols)

| Module | Symbol | Importer |
| --- | --- | --- |
| `batch_grouping` | `_SHEATH_COLOR_RANK` | `backend/app/services/sheath_cluster.py` |
| `batch_grouping` | `_WIP_COVERED_PROCESSES` | `backend/app/presentation/routes/plan_pipeline.py` |

---

**Totals.** Test-side private imports: **15** across 3 modules. Non-test-side private imports: **2**. Distinct (module, symbol) pairs that must survive Weeks 2/3: **17**.
