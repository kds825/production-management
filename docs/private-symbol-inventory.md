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

## schedule_optimizer (5 symbols)

- `_DEFAULT_WELDING_MIN`
- `_WIP_SKIP_PROCESSES`
- `_find_available_slot`
- `_purge_run_tasks`
- `_tardiness_boost_retry`

## batch_grouping (3 symbols)

- `_SHEATH_COLOR_RANK`
- `_compose_sheath_group_key`
- `_find_speed`

## Also imported by non-test code (7 symbols)

| Module               | Symbol                   | Importer                                           |
| -------------------- | ------------------------ | -------------------------------------------------- |
| `schedule_optimizer` | `_DEFAULT_WELDING_MIN`   | `backend/app/services/solver/input_builder.py`     |
| `schedule_optimizer` | `_WIP_SKIP_PROCESSES`    | `backend/app/services/solver/input_builder.py`     |
| `schedule_optimizer` | `_extract_core_main_sq`  | `backend/app/services/solver/model_builder.py`     |
| `schedule_optimizer` | `_is_core_group`         | `backend/app/services/solver/model_builder.py`     |
| `schedule_optimizer` | `_st_sq`                 | `backend/app/services/solver/model_builder.py`     |
| `batch_grouping`     | `_SHEATH_COLOR_RANK`     | `backend/app/services/sheath_cluster.py`           |
| `batch_grouping`     | `_WIP_COVERED_PROCESSES` | `backend/app/presentation/routes/plan_pipeline.py` |

---

**Totals.** Test-side private imports: **17** across 3 modules. Non-test-side private imports: **7**. Distinct (module, symbol) pairs that must survive Weeks 2/3: **24**.

---

## Week 3 assignment table (Task 3A.0)

Authoritative mapping of each symbol → its **new home** after Week 3 splits. The original dotted paths (`app.services.cp_sat_optimizer.<sym>`, `app.services.schedule_optimizer.<sym>`) remain importable via re-export shells until Week 9 (D7-C invariant).

Spec §7 module structure:

```
domain/constants.py            # data-only constants
services/scheduling_shared/    # neutral package — resolves circular import
├── calendar_ops.py            # datetime ↔ working-min, base-date resolution
├── slot_filters.py            # equipment/slot narrowing helpers
├── group_ops.py               # group classification + multi-equipment scheduling
└── db_ops.py                  # cross-cutting DB helpers (extension; not in spec but required for _delete_task_safely)
services/solver/               # CP-SAT (constraint_loader, model_builder, objective, trace_writer, input_builder)
services/greedy/               # auto_schedule, reschedule_affected, slot_finder
services/batch_grouping/       # 5 modules per spec §7 (Week 3 Task 3A.3)
```

### From `cp_sat_optimizer.py` (top-imported by tests + by greedy)

| Symbol                        | Current line | Destination                                  | Rationale                                           |
| ----------------------------- | -----------: | -------------------------------------------- | --------------------------------------------------- |
| `_WORK_MIN_PER_DAY`           |           74 | `domain/constants.py`                        | data-only constant                                  |
| `_DUE_HARD_WEIGHT`            |          109 | `domain/constants.py`                        | weight baseline (Week 5 → DB)                       |
| `_CHAIN_WEIGHT`               |          120 | `domain/constants.py`                        | weight baseline (Week 5 → DB)                       |
| `_TRANSITION_WEIGHT`          |          200 | `domain/constants.py`                        | weight baseline (Week 5 → DB)                       |
| `_working_minutes_between`    |          407 | `services/scheduling_shared/calendar_ops.py` | calendar arithmetic                                 |
| `_due_work_min`               |          493 | `services/scheduling_shared/calendar_ops.py` | calendar arithmetic                                 |
| `_compute_group_duration_map` |          575 | `services/scheduling_shared/group_ops.py`    | spec §7 (`compute_group_duration`)                  |
| `_is_multi_equip_group`       |          655 | `services/scheduling_shared/group_ops.py`    | group classification                                |
| `_delete_task_safely`         |          713 | `services/scheduling_shared/db_ops.py`       | DB-cleanup helper, called by both solver and greedy |
| `resolve_base_date`           |          932 | `services/scheduling_shared/calendar_ops.py` | spec §7 (`calendar_ops`)                            |
| `_datetime_to_wmin`           |          960 | `services/scheduling_shared/calendar_ops.py` | spec §7 (`calendar_ops`)                            |
| `cp_sat_schedule`             |         1000 | `services/solver/__init__.py` (re-exported)  | already in services/solver via Week 2 work          |

### From `schedule_optimizer.py` (top-imported by `cp_sat_optimizer:46` and `solver/*`)

| Symbol                           | Current line | Destination                                  | Rationale                                      |
| -------------------------------- | -----------: | -------------------------------------------- | ---------------------------------------------- |
| `_WIP_SKIP_PROCESSES`            |           53 | `domain/constants.py`                        | static lookup table                            |
| `_DEFAULT_WELDING_MIN`           |           70 | `domain/constants.py`                        | data-only constant                             |
| `PREDECESSOR_PROCESS`            |           89 | `domain/constants.py`                        | spec §7 (PREDECESSOR_PROCESS explicitly named) |
| `_is_core_group`                 |           99 | `services/scheduling_shared/group_ops.py`    | group classification                           |
| `_st_sq`                         |          104 | `services/scheduling_shared/group_ops.py`    | sheath-twist SQ helper used by group ops       |
| `_is_sheath_group`               |          118 | `services/scheduling_shared/group_ops.py`    | group classification                           |
| `_extract_core_main_sq`          |          162 | `services/scheduling_shared/group_ops.py`    | group SQ extraction                            |
| `_tardiness_boost_retry`         |          467 | `services/greedy/auto_schedule.py`           | greedy retry policy                            |
| `_purge_run_tasks`               |          580 | `services/greedy/auto_schedule.py`           | greedy state-reset helper                      |
| `_schedule_multi_equipment`      |         1553 | `services/scheduling_shared/group_ops.py`    | spec §7 (`schedule_multi_equipment`)           |
| `_find_eligible_equipment`       |         1902 | `services/scheduling_shared/slot_filters.py` | equipment filtering                            |
| `_narrow_by_stranding`           |         1955 | `services/scheduling_shared/slot_filters.py` | spec §7 (`narrow_by_stranding`)                |
| `align_start_to_predecessor_end` |         2008 | `services/scheduling_shared/slot_filters.py` | spec §7 (`align_start_to_predecessor_end`)     |
| `_find_available_slot`           |         2090 | `services/greedy/slot_finder.py`             | spec §7 (greedy package, `slot_finder`)        |
| `_get_drum_winding_min`          |         2134 | `services/scheduling_shared/group_ops.py`    | duration-component helper                      |
| `_get_stranding_setup_min`       |         2162 | `services/scheduling_shared/group_ops.py`    | duration-component helper                      |
| `_filter_by_sheath_routing`      |         2234 | `services/scheduling_shared/slot_filters.py` | spec §7 (`filter_by_sheath_routing`)           |

### From `batch_grouping.py` (Week 3 Task 3A.3 split target)

| Symbol                      | Current line | Destination                             | Rationale                    |
| --------------------------- | -----------: | --------------------------------------- | ---------------------------- |
| `_WIP_COVERED_PROCESSES`    |           40 | `services/batch_grouping/wip_filter.py` | WIP-coverage lookup          |
| `_SHEATH_COLOR_RANK`        |           58 | `services/batch_grouping/sheath.py`     | sheath color rank table      |
| `_compose_sheath_group_key` |           79 | `services/batch_grouping/sheath.py`     | sheath group key composition |
| `_find_speed`               |   (see file) | `services/batch_grouping/speed.py`      | speed-master lookup          |

Final 3A.3 sub-module split is finalized at task time after reading `batch_grouping.py` end-to-end (per spec known-compromise on file-by-file move lists).

### Cross-package import resolution (circular-import elimination)

Currently:

- `cp_sat_optimizer.py:46` top-imports 15 symbols from `schedule_optimizer` → blocks moving either file independently.
- `schedule_optimizer.py` deferred-imports 4 symbols from `cp_sat_optimizer` (`_datetime_to_wmin` ×3 sites, `resolve_base_date`, `cp_sat_schedule`, `_delete_task_safely`).

After Week 3 moves:

- All 15 cp_sat→schedule top-imports resolve via `domain/constants.py` (5) and `services/scheduling_shared/{calendar_ops, slot_filters, group_ops, db_ops}` (10). Top-level `from app.services.schedule_optimizer import (...)` block at `cp_sat_optimizer.py:46` is fully eliminated.
- All 4 schedule→cp_sat deferred imports resolve via `services/scheduling_shared/{calendar_ops, db_ops}` and `services/solver/__init__.py`. Inline `from app.services.cp_sat_optimizer import ...` calls disappear.
- Re-export shells at `app/services/cp_sat_optimizer.py` and `app/services/schedule_optimizer.py` preserve every symbol on this inventory until Week 9 (D7-C).
