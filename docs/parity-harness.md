# Parity Harness — Operational Guide

**Audience:** anyone touching solver code, objective weights, batch-grouping
heuristics, or the scheduling pipeline. Every behavior change must be
explained in terms of "this is or is not a parity break."
**Source-of-truth files:**

- Fixtures: `backend/tests/fixtures/parity/01_nominal.json` … `10b_none_constraints.json`.
- Performance baseline: `backend/tests/fixtures/parity/baseline_performance.json`.
- Test entrypoint: `backend/tests/test_parity_harness.py`.
- Freeze script: `backend/scripts/parity_freeze_current_behavior.py`.
- Perf check: `backend/scripts/check_performance_regression.py`.
- CI guard: `.github/workflows/parity-fixture-guard.yml`.

---

## What "parity" means here

**Parity = regression detection by exact-output equality.** For each
fixture, the harness re-runs the full solver with frozen inputs and
hashes the resulting `ScheduleTask` rows. The hash is compared
**bitwise** against the `expected_output_hash` baked into the fixture
JSON. Any deviation — a one-minute start-time drift, a swapped equipment
id, a flipped constraint contribution — fails the fixture.

The point is _not_ to prove the solver is correct. The point is to make
silent behavior change impossible: if your refactor moves a hash, you
either intended it (and ship a `parity-update:` commit, see below) or
you have a regression.

---

## The 10 fixtures

Lives under `backend/tests/fixtures/parity/`. Each fixture has a sibling
seed script under `backend/scripts/seed_parity_scenarios/` that produces
exactly the input snapshot the JSON expects.

| #   | Fixture file                    | Seeded by                       | Tests                              |
| --- | ------------------------------- | ------------------------------- | ---------------------------------- |
| 1   | `01_nominal.json`               | `01_nominal.py`                 | Happy-path baseline.               |
| 2   | `02_past_due_skew.json`         | `02_past_due_skew.py`           | EDD weights with past-due batches. |
| 3   | `03_urgent_reschedule.json`     | `03_urgent_reschedule.py`       | Urgent-flag promotion path.        |
| 4   | `04_wip_match.json`             | `04_wip_match.py`               | SM재공 inventory consumption.      |
| 5   | `05_sheath_color_chain.json`    | `05_sheath_color_chain.py`      | `W-CHAIN` color-adjacency cost.    |
| 6   | `06_stage1_stage2_handoff.json` | `06_stage1_stage2_handoff.py`   | Stage 1→2 handoff continuity.      |
| 7   | `07_calendar_edge.json`         | `07_calendar_edge.py`           | Friday-shortened-shift + holiday.  |
| 8   | `08_capacity_overflow.json`     | `08_capacity_overflow.py`       | Demand > capacity, soft-fallback.  |
| 9   | `09_single_batch.json`          | `09_single_batch.py`            | Smallest non-trivial input.        |
| 10a | `10a_all_constraints.json`      | `10_all_vs_none_constraints.py` | All toggles on.                    |
| 10b | `10b_none_constraints.json`     | `10_all_vs_none_constraints.py` | All toggles off.                   |

(Yes, fixture #10 produces two JSONs from one seed script — that's why
the count is "10 fixtures" but eleven files.)

Re-seeding from scratch:

```bash
cd backend && source venv/bin/activate
python scripts/seed_parity_scenarios/<fixture>.py
```

---

## How to run

```bash
make parity              # all 10, full p99 envelope
make parity-quick        # 3-fixture subset for pre-push gate
make parity-fixture FIXTURE=02   # single fixture by number prefix
```

The targets resolve under the hood to:

```bash
cd backend && source venv/bin/activate \
  && pytest tests/test_parity_harness.py -m parity -v
```

`--parity-quick` (the `parity-quick` target) restricts to a 3-fixture
subset chosen for coverage breadth at minimum runtime. Use it as the
local pre-push gate; let CI run the full set.

---

## Performance baseline

`backend/tests/fixtures/parity/baseline_performance.json` records `n=20`
runs per fixture with `p50_ms` and `p99_ms`, captured against
`baseline_commit` (currently `b18ecba…`, 2026-04-23) with
`CPSAT_WORKERS=1`.

`scripts/check_performance_regression.py` re-runs each fixture under the
same `CPSAT_WORKERS=1` discipline and computes
`ratio = current_p99 / baseline_p99`:

| ratio     | verdict | exit          |
| --------- | ------- | ------------- |
| ≤ 1.5     | OK      | 0             |
| 1.5 – 2.0 | WARN    | 0 (advisory)  |
| > 2.0     | FAIL    | 1 (CI blocks) |

Internal errors (missing baseline, hash drift between determinism runs)
exit 2 or 3. The check is **read-only** on fixtures — it never
re-bakes the baseline. That's the freeze script's job.

---

## Re-freezing fixtures (`parity-update:` discipline)

Re-freeze ONLY when a deliberate behavior change is approved.

```bash
cd backend && source venv/bin/activate
python scripts/parity_freeze_current_behavior.py
git add backend/tests/fixtures/parity/*.json
git commit -m "parity-update: <one-line why the baseline moved>"
```

The `parity-update:` subject prefix is enforced by
`.github/workflows/parity-fixture-guard.yml` — any commit that touches a
file matching `backend/tests/fixtures/parity/*.json` without that prefix
fails the PR check. Reverts of a parity-update must also carry the
prefix; the auto-generated `Revert "parity-update: …"` subject does not
match and is intentionally rejected.

Why the friction: parity hash is ground truth for MILP-vs-heuristic
equivalence. Letting it move under a `feat:`/`fix:` commit would let a
real regression hide inside an unrelated change.

---

## LLM in parity mode

`backend/tests/conftest.py:26` sets:

```python
os.environ.setdefault("LLM_PROVIDER", "template")
```

So every parity run uses `TemplateProvider` (deterministic Korean
sentence formatter). Real Anthropic API calls are NEVER made during a
parity run — that would inject nondeterminism and bill us per pytest
invocation. The `setdefault` is intentional: if a developer wants to
reproduce a parity-mode harness locally with the live provider, they
can `LLM_PROVIDER=anthropic make parity-fixture FIXTURE=01`. CI never
overrides, so the default holds in CI.

If you add a new provider, ensure it is reachable through the same
`LLM_PROVIDER` env switch (see `backend/app/services/llm_providers/__init__.py`
`get_provider`) so this discipline keeps working unchanged.
