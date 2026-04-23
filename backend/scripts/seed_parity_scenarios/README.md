# Parity seed scripts

Task 1.2 of the Production Handoff Refactor (Rev 3). These are the
deterministic DB-seed scripts that underpin the Week 1 parity harness.

## Purpose

Each file in this directory is a standalone Python module that, when its
`seed(db)` function is invoked on a session bound to a freshly-truncated
(or SAVEPOINT-wrapped) DB, produces a specific, well-defined scenario
exercising a particular branch of the scheduler:

| Script                          | Scenario                                                |
| ------------------------------- | ------------------------------------------------------- |
| `01_nominal.py`                 | Baseline mixed workload (nothing special)               |
| `02_past_due_skew.py`           | Most SOs past due — tardiness cascade                   |
| `03_urgent_reschedule.py`       | Urgent order injected late — `frozen_group_keys` path   |
| `04_wip_match.py`               | WIP inventory absorbs part of demand — WIP-skip branch  |
| `05_sheath_color_chain.py`      | Heavy sheath color clusters — chain constraint          |
| `06_stage1_stage2_handoff.py`   | Stage1 plan frozen, Stage2 builds on it                 |
| `07_calendar_edge.py`           | SO straddles weekend/holiday — calendar engine          |
| `08_capacity_overflow.py`       | Demand > capacity — INFEASIBLE escalation               |
| `09_single_batch.py`            | Exactly 1 `production_batch` row — minimal edge case    |
| `10_all_vs_none_constraints.py` | All constraint_config on vs. all off (two sub-fixtures) |

## How the parity harness uses them

```text
seed_NN.seed(db)          # insert deterministic rows
      ↓
build_solver_input(db, run_label)
      ↓
dump fields → backend/tests/fixtures/parity/NN.json  (Task 1.3)
      ↓
pytest comparing cp_sat_schedule(..., solver_input_override=<loaded>)
against expected hash                                     (Task 1.4)
```

The seed scripts themselves **never call** `build_solver_input` or any
solver code. They only write ORM rows.

## Running one manually

```bash
cd backend
python scripts/seed_parity_scenarios/01_nominal.py
```

This executes the `__main__` block which creates a real `SessionLocal()`
and calls `seed(db)`. **THE SCRIPT MUTATES THE DATABASE** — it wipes the
tables it touches (see the module docstring of each file for the list).
Safe for local dev DBs; do not run against production.

When the harness invokes `seed(db)` from a pytest fixture, the caller
has already wrapped the session in `db.begin_nested()` (SAVEPOINT), so
the `db.commit()` inside `seed(db)` is a SAVEPOINT release — no real
commit occurs and the outer fixture rollback cleans up.

## Determinism contract

Every seed script MUST:

1. Use only fixed `datetime(...)` / `date(...)` literals (no `.now()`).
2. Never call `uuid.uuid4()` or any unseeded `random.*`.
3. Reset every table it writes via `db.query(Model).delete()` at the top
   of `seed()` so re-running produces identical final state.
4. Use scenario-specific `run_label` with a `YYYYMMDD_parity_NN_<slug>`
   prefix so `build_solver_input` parses a fixed `base_date`.

Running the same seed script twice in a row against the same reset
starting state must produce byte-identical rows (verified by comparing
primary-key-ordered dumps).
