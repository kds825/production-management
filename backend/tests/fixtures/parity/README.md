# Parity fixtures

Snapshot of `build_solver_input(db, run_label)` output for each of the 10
parity scenarios (11 files total — scenario 10 contributes two variants).
Produced by `backend/scripts/capture_parity_fixture.py` (Task 1.3 of the
Production Handoff Refactor, Rev 3).

## File inventory

| File                            | Variant | Batches | Purpose                                   |
| ------------------------------- | ------- | ------- | ----------------------------------------- |
| `01_nominal.json`               | —       | 3       | Baseline mixed workload (happy path)      |
| `02_past_due_skew.json`         | —       | 4       | Most SOs past due — tardiness cascade     |
| `03_urgent_reschedule.json`     | —       | 1       | Urgent injection — `frozen_group_keys`    |
| `04_wip_match.json`             | —       | 2       | WIP skip (3 seeded, 1 wip-skipped)        |
| `05_sheath_color_chain.json`    | —       | 6       | Sheath color chain constraint             |
| `06_stage1_stage2_handoff.json` | —       | 2       | Stage-2 child-only (parent rows filtered) |
| `07_calendar_edge.json`         | —       | 3       | SO straddles weekend/holiday              |
| `08_capacity_overflow.json`     | —       | 20      | Demand > capacity — INFEASIBLE stress     |
| `09_single_batch.json`          | —       | 1       | Exactly one batch — minimal edge case     |
| `10a_all_constraints.json`      | `all`   | 3       | Scenario 10 — every constraint enabled    |
| `10b_none_constraints.json`     | `none`  | 3       | Scenario 10 — every constraint disabled   |

## Envelope schema

```json
{
  "scenario_id": "01_nominal",
  "run_label": "20260421_parity_01_nominal",
  "variant": null,
  "solver_input": {
    "base_date": "2026-04-21T08:00:00",
    "wip_skipped": 0,
    "welding_min": 30.0,
    "batches": [ { "...": "ProductionBatch columns, batch_id remapped" } ],
    "equipment_list": [ { "...": "EquipmentMaster columns" } ],
    "equipment_by_process": { "<process_name>": [ ... ] },
    "speed_map": [ ["<eq_code>", <cross_section>, { "...": "SpeedMaster row" } ] ],
    "color_setup_map": { "<eq_code>": <float-or-null> },
    "constraint_params": { "<constraint_id>": { "<param_key>": <value> } },
    "sq_to_wire_d": { "<sq_string>": <wire_diameter_float> }
  },
  "expected_output_hash": "",
  "expected_solver_status": "",
  "expected_objective_value": null
}
```

All keys are JSON-serializable primitives. Dates/datetimes render as
ISO-8601 strings, Decimals render as floats. Timestamp columns
(`created_at`, `updated_at`) are stripped — they are non-deterministic
defaults set at INSERT time and have no bearing on parity.

## `expected_*` fields — filled later

The three `expected_*` fields are BLANK (empty string / null) by design:

- `expected_output_hash` — SHA-256 of the canonicalized solver output.
- `expected_solver_status` — `"OPTIMAL"` / `"FEASIBLE"` / `"INFEASIBLE"`
  / `"UNKNOWN"`.
- `expected_objective_value` — numeric objective reached by cp_sat_schedule.

**Task 1.5** will populate these by running `cp_sat_schedule` against each
fixture n=20 times, confirming bit-identical outputs, and freezing the
consensus hash/status/objective. Until then, the parity harness (Task 1.4)
reads the `solver_input` block only and checks that the refactored
`cp_sat_schedule` path produces the same output as the baseline path on
the same input (mutual-consistency mode), not against a frozen hash.

## Determinism contract

Running `python backend/scripts/capture_parity_fixture.py --all` twice
back-to-back MUST produce a `git diff backend/tests/fixtures/parity/*.json`
of zero bytes. This is the canonical smoke test.

Non-determinism sources handled:

1. **Autoincrement PK drift** — `production_batch.batch_id` and
   `speed_master.speed_id` are remapped to 0-indexed ordinals on capture
   (Strategy B). This preserves the
   `_single_{batch_id}` group-key usage in `cp_sat_schedule` (override
   path, `cp_sat_optimizer.py:1219`).
2. **Timestamp defaults** (`created_at`, `updated_at`) are stripped.
3. **dict/list ordering** — all lists pre-sorted; JSON dumped with
   `sort_keys=True`.
4. **Decimal precision** — converted to `float`.

## Regenerating fixtures

```bash
cd backend
# Regenerate all 11:
python scripts/capture_parity_fixture.py --all
# Regenerate one scenario (e.g. scenario 10 — produces both 10a and 10b):
python scripts/capture_parity_fixture.py --scenario 10
```

If `git diff backend/tests/fixtures/parity/*.json` is non-empty after a
plain regeneration, something changed. Investigate before committing:
real seed change vs. non-determinism regression. The capture script also
rolls back and re-deletes its own rows so Supabase is left clean.
