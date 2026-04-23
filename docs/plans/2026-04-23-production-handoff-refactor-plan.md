# Production Handoff Refactor — Implementation Plan (Rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax.

**Spec reference**: `docs/specs/2026-04-23-production-handoff-refactor-design.md` Rev 2 (commit `4af4b4e`). **Read the spec first** — this plan executes the spec; it does not redefine it.

**Goal**: 9-week renovation of KBI scheduling PoC into pilot-ready production system. Parallel Strangler-Fig with 2 long-lived branches gated by 7 weekly checks. Includes hardcoded→DB weight migration (Week 5), so admin UI actually affects solver output.

**Architecture**: Track A (backend/solver), Track B (frontend/admin/migrations), plus `main` reference worktree. Weekly merges. Parity harness locks solver behavior. `/master/constraints` extended with 베이스라인 tab (not a new route).

---

## Infrastructure constants (use these exact values everywhere)

```bash
# .env.worktree.example — copied to each worktree as .env.worktree (gitignored)
COMPOSE_PROJECT_NAME=kbi_main   # kbi_track_a / kbi_track_b in those worktrees
POSTGRES_HOST_PORT=5432         # 5433 track_a, 5434 track_b
BACKEND_PORT=8000               # 8001 track_a, 8002 track_b
FRONTEND_PORT=3000              # 3001 track_a, 3002 track_b

# Real DB creds (per README.md:41; NEVER use kbi_user/kbi_pass — that is wrong)
DB_USER=kbi
DB_PASSWORD=kbi_poc_2026
DB_NAME=kbi_scheduler

# Native dev workflow — Docker only for Postgres
# Backend: cd backend && source venv/bin/activate && pytest / uvicorn app.main:app
# Frontend: cd frontend && npm run dev
# DB: docker compose up -d db   (service name is "db", NOT "postgres"/"backend")

# Health endpoint (NOT /health)
HEALTH_URL=http://localhost:${BACKEND_PORT}/api/health

# Next.js 16 + React 19 (read frontend/AGENTS.md before FE tasks)
LLM_PROVIDER=template   # parity/CI default; set anthropic for dev
```

---

## Prerequisites

- [ ] Read spec Rev 2 end-to-end.
- [ ] Read `backend/README.md` (native dev) and `frontend/AGENTS.md` (Next 16 warnings).
- [ ] macOS with ≥16GB RAM (2 worktrees × Postgres + dev servers).
- [ ] Python 3.11, Node 20+, Docker Desktop, `gh` CLI.

---

# Week 0 — Pre-flight + DX Scaffolding

All tasks in `KBI_PoC` (main) until 0.7 creates sibling worktrees.

### Task 0.1: `docs/archive/` baseline artifacts

Files: `docs/archive/schema_asis_20260423.sql`, `docs/archive/constraint_config_sample_20260423.json`, `docs/archive/README.md`, `scripts/dump_constraint_config.py`.

- [ ] **Step 1: Schema dump via native psql (correct creds)**

```bash
docker compose up -d db
sleep 3
docker compose exec -T db pg_dump --schema-only --no-owner --no-privileges -U kbi kbi_scheduler > docs/archive/schema_asis_20260423.sql
wc -l docs/archive/schema_asis_20260423.sql   # ≥ 500 lines expected
```

- [ ] **Step 2: Write `scripts/dump_constraint_config.py`** serializing every column of every `ConstraintConfig` row to JSON (see Rev 1 spec for field list — unchanged).

- [ ] **Step 3: Run dump**

```bash
cd backend && source venv/bin/activate
python ../scripts/dump_constraint_config.py > ../docs/archive/constraint_config_sample_20260423.json
cd ..
```

- [ ] **Step 4: Write `docs/archive/README.md`** with break-glass restore procedure using real creds.

- [ ] **Step 5: Commit**

```bash
git add docs/archive/ scripts/dump_constraint_config.py
git commit -m "docs(archive): Phase 0 baseline — schema + constraint_config"
```

---

### Task 0.2: Tag Phase 0 marker in `constraint_config_history`

File: `scripts/tag_phase0_baseline.py` — idempotent; inserts rows with `notes='PHASE0_INITIAL_20260423'`. Week 2 migration retroactively flips `is_baseline=TRUE` on these rows.

- [ ] Run + verify via psql + commit.

---

### Task 0.3: Create `backend/pytest.ini`

```ini
[pytest]
testpaths = tests
markers =
    parity: slow deterministic solver regression (run with pytest -m parity)
```

- [ ] Commit.

---

### Task 0.4: Add `typecheck` / `test` / `test:e2e` scripts to `frontend/package.json`

```json
"scripts": {
  "dev": "next dev",
  "build": "next build",
  "start": "next start",
  "lint": "next lint",
  "typecheck": "tsc --noEmit",
  "test": "vitest run",
  "test:watch": "vitest",
  "test:e2e": "playwright test"
}
```

- [ ] Verify `npm run typecheck` runs; commit.

---

### Task 0.5: Write `Makefile` with bootstrap / doctor / verify / parity targets

Full Makefile skeleton per spec §9. Key targets:

- `bootstrap` — idempotent: venv, pip install, `.env.worktree` from example, `docker compose up -d db`, `alembic upgrade head`, verify `/api/health`
- `doctor` — 10-second preflight (port / alembic / pytest / vitest / branch-matches-worktree)
- `verify` — runs lint + typecheck + test + parity
- `parity`, `parity-quick`, `parity-fixture FIXTURE=NN`
- `test`, `test-backend`, `test-frontend`, `seed`, `reset-db`, `lint`, `typecheck`

- [ ] Write, test `make bootstrap && make doctor`, commit.

---

### Task 0.6: `.env.worktree.example` + gitignore + `docker-compose.yml` parameterization

- [ ] Write `.env.worktree.example` with comments per-worktree values.
- [ ] Add `.env.worktree` to `.gitignore`.
- [ ] Update `docker-compose.yml`: `ports: ["${POSTGRES_HOST_PORT:-5432}:5432"]`. Keep existing `db` service name, env vars, pgdata volume.
- [ ] Commit.

---

### Task 0.7: Two sibling worktrees

Files: `scripts/setup_worktrees.sh`, `scripts/worktree_status.sh`, `scripts/worktree_cd.sh`, `scripts/run_id_grep.sh`.

- [ ] **setup_worktrees.sh**: creates `KBI_PoC_track_a` (branch `refactoring/track-a-solver`) and `KBI_PoC_track_b` (branch `refactoring/track-b-admin`); seeds per-worktree `.env.worktree` with correct ports.

- [ ] **worktree_status.sh**: per spec §9; shows branch, last commit, dirty count for all 3 worktrees.

- [ ] **worktree_cd.sh**: shell function `kbi <main|a|b>` that `cd`s and sources `.env.worktree`.

- [ ] **run_id_grep.sh <run_id>**: greps log file (tbd path) + `solver_run` + `solver_decision` + `schedule_change_sets` for the given UUID.

- [ ] Run setup; bootstrap each worktree:

```bash
./scripts/setup_worktrees.sh
for wt in . ../KBI_PoC_track_a ../KBI_PoC_track_b; do
  (cd "$wt" && make bootstrap && make doctor)
done
```

- [ ] Commit.

---

### Task 0.8: Scaffold `.github/workflows/ci.yml`

Backend job uses `services.postgres` (NOT docker-compose-in-CI) with real creds:

```yaml
services:
  postgres:
    image: postgres:15
    env:
      POSTGRES_USER: kbi
      POSTGRES_PASSWORD: kbi_poc_2026
      POSTGRES_DB: kbi_scheduler
    ports: ["5432:5432"]
    options: >-
      --health-cmd pg_isready --health-interval 10s
      --health-timeout 5s --health-retries 5
```

Backend steps: setup-python 3.11 → pip install → alembic upgrade → `pytest -q --ignore=tests/test_parity_harness.py` (parity runs in separate workflow per Task 1.9). Env: `DATABASE_URL=postgresql://kbi:kbi_poc_2026@localhost:5432/kbi_scheduler`, `LLM_PROVIDER=template`.

Frontend steps: setup-node 20 → `npm ci` → `npm run lint && npm run typecheck && npm run test`.

- [ ] Commit; push a throwaway PR to verify both jobs go green.

---

### Task 0.9: `docs/troubleshooting.md` skeleton

Sections keyed by error text/symptom: **port conflict** (show `lsof -i :5432`), **parity red** (point at `make parity-fixture FIXTURE=NN`), **alembic downgrade failed** (point at archive SQL), **docker compose service not found** (reminder: only `db`; backend/frontend native), **worktree contamination** (uncommitted work in wrong worktree), **LLM rate-limited** (fall back to `LLM_PROVIDER=template`).

- [ ] Commit.

---

### Task 0.10: Grep-list every private symbol imported by tests

File: `docs/private-symbol-inventory.md`.

```bash
grep -rEhn 'from app\.services\.(cp_sat_optimizer|schedule_optimizer|batch_grouping) import.*_[a-zA-Z]' backend/tests/ \
  | sed -E 's/.*import //; s/ *#.*//' | tr ',' '\n' | sed -E 's/^ +//; s/ +$//' \
  | sort -u > docs/private-symbol-inventory.md
```

This is the **authoritative list** that Week 2/3 re-export shells must cover. No source file is `rm`'d in the 9 weeks without every symbol in this list remaining importable.

- [ ] Commit.

---

### Task 0.Close: Week 0 merge to main; all 3 worktrees `make doctor` green

---

# Week 1 — Parity Harness + Pilot Success Criteria

### Task 1.1: Extract `build_solver_input` (pre-split scaffold)

Worktree: **track_a**. Creates `services/solver/__init__.py` and `services/solver/input_builder.py`.

- [ ] Read `cp_sat_optimizer.py` top-of-function DB loads; mirror field-by-field (no speculation) in `SolverInput` Pydantic model.
- [ ] `cp_sat_schedule(run_label, db, ...)` — new keyword-only `solver_input_override`/`num_search_workers`/`random_seed`/`run_id_override`. Positional args preserved for `schedule_optimizer.py:284` caller.
- [ ] `make test-backend` green; commit.

---

### Task 1.2: 10 parity seed scripts

Files: `scripts/seed_parity_scenarios/{01_nominal,02_past_due_skew,03_urgent_reschedule,04_wip_match,05_sheath_color_chain,06_stage1_stage2_handoff,07_calendar_edge,08_capacity_overflow,09_single_batch,10_all_vs_none_constraints}.py`.

Each resets `constraint_config`/`sales_order`/`wip_inventory`/`equipment_master`/`operation_calendar` to produce the named scenario. Reuse setup code from existing tests (`test_edd_mixed_pastdue_ontime.py` → fixture 02, etc.) where possible.

- [ ] All 10 seed scripts deterministic (same output every run).
- [ ] Commit.

---

### Task 1.3: Capture 10 parity fixtures

File: `scripts/capture_parity_fixture.py` runs a seed script → calls `build_solver_input(db)` → dumps JSON with blank `expected_*` fields.

- [ ] Loop through all 10 scenarios; 10 `.json` fixtures written to `backend/tests/fixtures/parity/`.
- [ ] Commit fixtures + capture script together.

---

### Task 1.4: Parity test + `--parity-quick` + transaction isolation

File: `backend/tests/test_parity_harness.py`. Existing `conftest.py` already sets `CPSAT_WORKERS=1` (respect it).

- [ ] Add `--parity-quick` CLI option to conftest.
- [ ] Add `parity_db` fixture that wraps each test in `db.begin_nested()` + rollback; asserts `ScheduleTask` row count unchanged post-test.
- [ ] Implement `test_parity` with **stable hash** (per spec §6):

```python
def _stable_hash(result, run_label, horizon_start):
    items = sorted(
        (
            a.get("group_key", a.get("production_batch_id", "")),
            a["equipment_id"],
            int((a["assigned_start"] - horizon_start).total_seconds() // 60),
        )
        for a in result["assignments"]
    )
    payload = json.dumps([run_label, items], sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()
```

- [ ] Auditor's-trail diff message per spec §6.
- [ ] Run (expect failures — hashes blank); commit.

---

### Task 1.5: Freeze hashes + p99 performance baseline (n=20)

File: `scripts/parity_freeze_current_behavior.py` runs each fixture 20× per spec §10b; writes `expected_output_hash`, `expected_solver_status`, `expected_objective_value` into JSON; writes `baseline_performance.json` with p50/p99.

- [ ] Run; `make parity` should go 10/10 green.
- [ ] **Separate commit** with message starting `parity-update: initial freeze` — this is the contract.

---

### Task 1.6: `scripts/check_performance_regression.py`

Compares current parity runtimes vs `baseline_performance.p99`. Block at 2×, warn at 1.5×.

- [ ] Commit.

---

### Task 1.7: CI guard — fixtures only via `parity-update:` commits

File: `.github/workflows/parity-fixture-guard.yml`. Workflow fails any PR where a `backend/tests/fixtures/parity/*.json` change is in a commit whose subject doesn't start with `parity-update:`.

- [ ] Commit; open throwaway PR to verify guard fires correctly.

---

### Task 1.8: `docs/pilot-success-criteria.md` (D8 best-guess)

Quantitative + qualitative criteria per spec §14. **This doc's values are revalidated with KBI in a Week-1 stakeholder call; if the meeting slips to Week 2, we ship best-guess and update.**

- [ ] Commit.

---

### Task 1.9: `.github/workflows/parity.yml`

Runs `make parity` + performance regression check. Triggered on PR. Uses same Postgres service config as ci.yml.

- [ ] Commit. Verify both workflows green on track_a PR.

---

### Task 1.Close: Week 1 merge

- [ ] Track A PR → main. Parity freeze lands.
- [ ] `make doctor` all worktrees; pull main.

---

# Week 2 — `services/solver/` + migrations (Track B merges FIRST)

**Week 2 merge order exception** — Track B goes first because Track A Task 2A.3 depends on new tables.

### Task 2B.1 (track_b): Alembic migration

One migration file adding:

- `solver_run` (new table)
- `solver_decision` (new table) — `manual_override_change_set_id` is `sa.String(36)`, FK `schedule_change_sets.change_set_id` (table name **plural**, PK is String)
- `change_set.override_reason TEXT NULL` — _but_ the existing table is `schedule_change_sets`; confirm exact name/columns first with `psql \d+ schedule_change_sets`
- `constraint_config_history`: `is_baseline BOOL DEFAULT false`, `baseline_tag_name VARCHAR(100)`, `baseline_created_by VARCHAR(100)`, `baseline_created_at TIMESTAMPTZ`, `version_id UUID`

Post-upgrade `op.execute(...)` retroactively sets `is_baseline=TRUE` for Phase-0-marker rows.

`downgrade()` drops in reverse order.

- [ ] Write migration; run `./scripts/test_migration_reversibility.sh` (upgrade → downgrade → upgrade round-trip).
- [ ] Commit.

---

### Task 2B.2 (track_b): ORM models for `solver_run` + `solver_decision`

Files: `backend/app/infrastructure/models/solver_run.py`, `solver_decision.py`. Register in `models/__init__.py`.

- [ ] Commit.

---

### Track B merges to main; Track A rebases onto main.

---

### Task 2A.1 (track_a): `constraint_loader.py` + `ConstraintSpec`

Per spec §7.

- [ ] Write `ConstraintSpec` frozen dataclass.
- [ ] Write `load_active_constraints(db, include_disabled=False)`.
- [ ] Write `backend/tests/test_constraint_loader.py` (frozen assert, default filter).
- [ ] Commit.

---

### Task 2A.2 (track_a): `model_builder.py` + `objective.py`

Port model-construction section of `cp_sat_optimizer.py`. Do NOT yet consume `ConstraintSpec.weight` — **Week 5 migrates hardcoded constants**; Week 2 preserves current behavior (constants still in `cp_sat_optimizer` or `domain/constants.py`).

- [ ] Move assignment-var creation, constraint posting, objective composition. Keep a `penalty_vars: dict[constraint_id, IntVar]` + `hard_literals: dict[constraint_id, BoolVar]` for Week-2 trace writer.
- [ ] Parity green after each sub-commit.
- [ ] Commit.

---

### Task 2A.3 (track_a): `trace_writer.py`

File: `backend/app/services/solver/trace_writer.py`. Writes `solver_run` + `solver_decision` rows.

- [ ] `TraceMetadata` dataclass (run_label, timestamps, solver_status, objective_value, constraint_config_version UUID, input_hash, output_hash, solver_params dict).
- [ ] `write_trace(db, meta, penalty_values, hard_literal_values, specs, assignments) -> UUID`.
- [ ] Wire into `cp_sat_schedule` so every solve writes one `solver_run` + N `solver_decision` rows.
- [ ] `make parity` still green; verify new rows: `psql ... -c "SELECT count(*) FROM solver_run;"`.
- [ ] Commit.

---

### Task 2A.4 (track_a): Run-ID LoggerAdapter + FastAPI middleware

Per spec §10a. Files: `backend/app/infrastructure/logging/{run_context,adapters,middleware}.py`; wire in `main.py`.

- [ ] Write `test_run_id_propagation.py`: `X-Run-Id` header present on responses; log records contain `[run_id=...]` prefix during solve.
- [ ] Commit.

---

### Task 2A.5 (track_a): CI boundary test

File: `backend/tests/test_solver_boundary.py`. Grep `services/solver/` for `from app.infrastructure` outside `constraint_loader.py` — must be empty.

- [ ] Commit.

---

### Task 2.Close: Weekly merge — Track A to main. Pull all worktrees.

---

# Week 3 — `services/greedy/` + `services/batch_grouping/` + `scheduling_shared/`

### Task 3A.0 (track_a): Inventory cross-package deps (circular-import resolution)

- [ ] Grep `cp_sat_optimizer.py:44` ± 20 lines — collect every symbol top-imported from `schedule_optimizer`. (Rev 2 found 14.)
- [ ] Grep `schedule_optimizer.py` for `from app.services.cp_sat_optimizer import` (deferred local imports). (Rev 2 found 4.)
- [ ] Write assignment table in `docs/private-symbol-inventory.md` — each symbol goes to `domain/constants.py`, `services/scheduling_shared/calendar_ops.py`, `services/scheduling_shared/slot_filters.py`, `services/scheduling_shared/group_ops.py`, `services/solver/*`, or `services/greedy/*`.
- [ ] Commit the assignment table.

---

### Task 3A.1 (track_a): `services/scheduling_shared/` + move shared symbols

One sub-commit per destination module (calendar_ops, slot_filters, group_ops). After each: `make parity-quick`.

- [ ] Commit each sub-move.

---

### Task 3A.2 (track_a): `services/greedy/` package

Re-export shell at `services/schedule_optimizer.py` preserves EVERY private symbol from `docs/private-symbol-inventory.md` (auto-verified via a test that imports them all).

- [ ] Move `auto_schedule`, `reschedule_affected_groups`, slot helpers.
- [ ] Update `cp_sat_optimizer.py:44` imports to new locations.
- [ ] Parity green; all 69+ tests pass.
- [ ] Do **NOT** `rm` `schedule_optimizer.py`. Shell stays through Week 9.
- [ ] Commit.

---

### Task 3A.3 (track_a): `services/batch_grouping/` package (D4 moved from Week 6)

5 modules per spec §7.

- [ ] Read `batch_grouping.py` end-to-end; identify responsibility clusters.
- [ ] `vulture` + `ruff --select F401,F841` baseline report; save to `/tmp/vulture_baseline.txt`.
- [ ] Move cluster-by-cluster. After each: parity green + commit.
- [ ] Confirmed-dead functions → `docs/deletion-log.md` with commit sha + reason.
- [ ] Keep re-export shell at `services/batch_grouping.py` through Week 9.

---

### Task 3B.1 (track_b): Extend `/master/constraints` with `베이스라인` tab (read-only)

**D1-A: extend the existing page; do NOT create a new `/admin/constraints` route.**

- [ ] Read `frontend/src/app/(main)/master/constraints/page.tsx` to understand existing tab infrastructure (파라미터 / on-off / 변경 이력).
- [ ] Read `frontend/src/app/(main)/master/constraints/components/HistoryTab.tsx` as the reference pattern for Tailwind + `var(--color-*)` tokens. Match its style exactly.
- [ ] Add 4th tab `베이스라인`. Empty placeholder list for now (populated in Task 3B.2 once backend diff endpoint lands).
- [ ] Run `verify-pwc-design` skill; zero violations.
- [ ] Commit.

---

### Task 3B.2 (track_b): Version-diff backend + frontend

Backend: `GET /api/constraints/versions/{a}/diff/{b}` in `presentation/routes/constraints.py`. Returns `[{constraint_id, field, value_a, value_b}]`.

Frontend: `VersionDiff` component consumed in 베이스라인 tab.

- [ ] Write `backend/tests/test_constraints_diff.py`.
- [ ] Commit.

---

### Task 3.Close: Weekly merge.

---

# Week 4 — `services/pipeline/` + Decision Card + LLM narrator

### Task 4A.1 (track_a): `services/pipeline/` from `plan_pipeline.py`

4 modules per spec §7: `orchestrator`, `stage1`, `stage2`, `run_labeler`. Route `plan_pipeline.py` becomes thin (request → orchestrator → response).

- [ ] Move by responsibility; parity green after each.
- [ ] Commit.

---

### Task 4B.1 (track_b): `GET /api/decisions/{batch_id}/latest`

File: `backend/app/presentation/routes/decisions.py`. Joins `solver_decision` + `solver_run` + `schedule_change_sets` (when `is_manually_adjusted`). Returns JSON per spec §8c/§8f.

- [ ] Write `backend/tests/test_decisions_route.py`.
- [ ] Commit.

---

### Task 4B.2 (track_b): LLM narrator (Anthropic + Template) + kiwipiepy filter

Files: `backend/app/services/llm_providers/__init__.py` (protocol), `anthropic.py`, `template.py`. Drop OpenAI per D7 scope reduction.

- [ ] `pip install kiwipiepy` (add to `backend/requirements.txt`).
- [ ] `services/llm_explainer.py` — post-response filter:

```python
from kiwipiepy import Kiwi
_kiwi = Kiwi()
ALLOW = {"배치", "납기", "설비", "시간", "지연", "회피", "배정", "이", "그"}

def _korean_nouns(text: str) -> set[str]:
    return {t.form for t in _kiwi.tokenize(text) if t.tag.startswith("NN")}

def explain(provider, payload) -> tuple[str, bool]:
    raw = provider.explain(payload)
    catalog = {c.korean_name for c in payload.contributions}
    if _korean_nouns(raw) - (ALLOW | catalog):
        return TemplateProvider().explain(payload), True
    return raw, False
```

- [ ] Ensure `backend/tests/conftest.py` forces `os.environ["LLM_PROVIDER"] = "template"` at import time → parity never hits real LLM.
- [ ] Write `test_llm_narrator_hallucination_block.py` — bad provider returns text with non-catalog noun; filter must fall back to template.
- [ ] Commit.

---

### Task 4B.3 (track_b): Decision Card component (D6-B replaces popover)

**Spec §8c locked.** Inline slide-down card beneath clicked row. Three zones with Tailwind + `var(--color-*)` tokens only. Yellow variant for `is_manually_adjusted=true`.

- [ ] Read `frontend/src/shared/ui/Toast.tsx` (existing component with `var(--color-*)` tokens) as pattern reference.
- [ ] Write `frontend/src/features/scheduler/components/DecisionCard.tsx`. Three zones per spec §8c. Handles all missing states per spec §8c (no-trace 404, template-fallback badge, generating timeout, empty contributions, phantom batch, network slow).
- [ ] Manual-override yellow-card variant renders override reason + original solver decision (from `change_set.snapshot_before`) behind disclosure.
- [ ] Integrate into `GanttTaskBlock.tsx`: clicking a batch toggles card beneath the row instead of opening popover. Surrounding rows push down with CSS grid or flex animation.
- [ ] Run `verify-pwc-design`; zero violations.
- [ ] Write `frontend/src/features/scheduler/__tests__/DecisionCard.test.tsx` — vitest covers all missing states.
- [ ] Commit.

---

### Task 4B.4 (track_b): Extend existing `Toast.tsx` to capture `X-Run-Id`

**Do NOT create `ErrorToast.tsx`** — the review flagged this as creating redundant infrastructure.

- [ ] Modify `frontend/src/lib/api/client.ts`: `apiFetch` wrapper captures `resp.headers.get("X-Run-Id")` and attaches to thrown `ApiError`.
- [ ] Extend existing `Toast` component signature to accept optional `meta: { runId?: string }`; renders copy-to-clipboard button when present. Copy shows: "오류 코드 abc12345 [복사] — 담당자에게 전달".
- [ ] Commit.

---

### Task 4.Close: Weekly merge.

---

# Week 5 — Hardcoded weight constants → DB migration (D2-B)

This week makes the Admin UI priority slider actually affect the solver. Without it, Admin UI is theater.

### Task 5A.1 (track_a): Hardcoded weight inventory

- [ ] Grep `cp_sat_optimizer.py` for `_*_WEIGHT`, `_*_K`, `_*_BASE`:

```bash
grep -En '^_[A-Z_]+\s*=\s*[0-9]' backend/app/services/cp_sat_optimizer.py
```

- [ ] Write `docs/hardcoded-weights.md`: every constant → proposed `constraint_id` → current value → default `priority` (0-100 normalized).
- [ ] Commit.

---

### Task 5A.2 (track_a): Seed new `constraint_config` rows

File: `scripts/seed_weight_constraints.py`. Idempotent (skip if row exists). Each new row has `implementation_type='solver_term'` and `params_json={"weight": current_value}`.

- [ ] Run locally; verify via psql.
- [ ] Add to `seed_db.py` so new environments get same defaults.
- [ ] Commit.

---

### Task 5A.3 (track_a): Refactor `objective.attach()` to read `ConstraintSpec.weight`

Replace each hardcoded constant with `spec_by_id["<id>"].priority`. **One constant at a time**; parity-quick after each.

- [ ] `_TARDINESS_WEIGHT` → `specs["c_tardiness"].priority`; parity-quick green; commit.
- [ ] `_CHAIN_WEIGHT` → `specs["c_chain"].priority`; parity-quick green; commit.
- [ ] Continue for each constant inventoried in 5A.1.

If a migration flips parity: **fix the seed value** (adjust `priority` to match original constant exactly). Do NOT update the fixture hash.

---

### Task 5A.4 (track_a): Full parity + test admin UI ↔ solver link

- [ ] `make parity` green.
- [ ] Manual test: edit a priority in Admin UI → run solver → verify `solver_run.objective_value` changed in the expected direction.
- [ ] Commit.

---

### Task 5B.1 (track_b): Promote-baseline + reset-to-any-baseline UI

Backend endpoints per spec §8f:

- `POST /api/constraints/promote-baseline` (new version_id, tag, created_by)
- `POST /api/constraints/reset-to-baseline` (version_id)
- `GET /api/constraints/baselines`

Frontend in 베이스라인 tab:

- Two-step promote dialog: diff preview (reuse `VersionDiff`) → tag form with default `YYYY-MM-DD-HHmm-{initials}` + 승인 근거. Confirm disabled until tag typed.
- Reset dropdown lists all baselines chronologically. Confirmation modal shows diff before applying.
- Read-only banner when `solver_run.finished_at IS NULL` exists.
- Soft-lock toast on stale `updated_at` (concurrent edit).

- [ ] Write backend; write frontend. `verify-pwc-design` zero violations.
- [ ] Commit.

---

### Task 5B.2 (track_b): Non-blocking sticky toast for operator comment (D6 per Design review)

Spec §8d. Drag-drop persists immediately; toast appears bottom-right 60s.

- [ ] Chips: `납기 변경` / `현장 긴급` / `설비 고장` / `자재 부족`. **No `기타`.**
- [ ] Dismiss = skip (no separate button).
- [ ] Badge `사유 미기록 N건` on gantt header for admin batch-review.
- [ ] Rollback: if backend rejects drag, toast shows "이동 실패 — 사유가 저장되지 않았습니다."
- [ ] Backend: `PATCH /api/change-sets/{id}/reason` → writes `schedule_change_sets.override_reason`; if the change affects existing `solver_decision` rows, flips `is_manually_adjusted=TRUE` + sets `manual_override_change_set_id`.
- [ ] Commit.

---

### Task 5.Close: Weekly merge.

---

# Week 6 — KBI dry-run (D5 moved from Week 7)

### Task 6.1: Dry-run playbook

File: `docs/kbi-dry-run-playbook.md`. 8 scenarios:

1. Upload a month's ERP orders
2. Review plan-register page
3. Run solver
4. Click a batch → Decision Card opens; verify LLM summary + bar chart + 이동 가능 pill
5. Edit a constraint in `/master/constraints` 파라미터 tab → re-run solver → confirm objective changed
6. Reset that constraint to baseline → re-run → confirm reverted
7. Manually drag a batch → reason toast appears; pick chip + save
8. Click the moved batch → yellow-card Decision Card; verify override reason shows, original solver decision accessible
9. Promote current as new baseline `YYYY-MM-DD-HHmm-Jaewoo-튜닝1`

- [ ] Commit.

---

### Task 6.2: Execute dry-run with KBI stakeholder observing

File: `docs/dry-run-friction-log.md`. For each friction encountered:

| Scenario | Observed | Severity (P0/P1/post-pilot) | Repro steps | Suspected file |

- [ ] After dry-run: triage each item; P0 go to Week 8 tasks.
- [ ] Commit friction-log.

---

### Task 6.Close: Weekly merge — friction-log is the delivered artifact.

---

# Week 7 — FE god-files + `schedules.py` route

### Task 7B.1 (track_b): Split `scheduler/page.tsx` (2,719 lines)

Break into thin page + `hooks/useSchedulerData.ts`, `hooks/useSchedulerKeyboard.ts`, `hooks/useBatchCompareMode.ts`, sub-components in `page-sections/*`. Preserve Decision Card integration from Week 4.

- [ ] Commit per sub-extract; parity green.

---

### Task 7B.2 (track_b): Split `SchedulerView.tsx` (1,468 lines) decision-card-aware

Target: `features/scheduler/components/scheduler-view/{index, DiffOverlay, GanttGrid, TaskRow, Toolbar}.tsx`. `TaskRow` emits the slot where `DecisionCard` slides in.

- [ ] `verify-pwc-design` zero violations.
- [ ] Commit.

---

### Task 7B.3 (track_b): Split `scheduleStore.ts` (1,291 lines) into Zustand slices

`features/scheduler/store/slices/{orders,batches,diff,filters}Slice.ts`; `index.ts` composes.

- [ ] Playwright E2E still green.
- [ ] Commit.

---

### Task 7A.1 (track_a): Split `routes/schedules.py` (1,702 lines)

`routes/schedules/{list,detail,bulk_update,cascade,revert}.py` per spec §9.

- [ ] Existing tests green.
- [ ] Commit.

---

### Task 7.Close: Weekly merge.

---

# Week 8 — Friction-log P0 fixes

For each P0 item in `docs/dry-run-friction-log.md`:

- [ ] Tag with owning track (A or B).
- [ ] Write fix; `make verify` green.
- [ ] Commit with message `fix(dry-run): <item>`.

P1 items parked in `docs/post-pilot-backlog.md`.

### Task 8.Close: All P0 items closed; weekly merge.

---

# Week 9 — Handoff packet + release

### Task 9.1: Generate handoff docs

Per spec §14 items 1-14:

- [ ] 1-2 already exist from Week 0.
- [ ] 3 `docs/pilot-success-criteria.md` revalidated against actual dry-run results.
- [ ] 4 `docs/architecture-as-is-to-be.md` — Excalidraw or mermaid module diagram (before / after).
- [ ] 5 `docs/api-spec.md` — regenerate via `curl localhost:8000/openapi.json`.
- [ ] 6 `docs/operator-runbook.md` with screenshots of Decision Card + Admin UI + reason toast.
- [ ] 7 `docs/constraint-catalog.md` — autogen from `constraint_config` rows: id / name / category / current default / rationale.
- [ ] 8 `docs/llm-prompt-inventory.md` — all templates in `services/llm_providers/`.
- [ ] 9 `docs/parity-harness.md` — operational guide incl. `parity-update:` discipline.
- [ ] 10 `docs/deletion-log.md` — finalized.
- [ ] 11 `docs/worktree-playbook.md` — 2-worktree layout + `make bootstrap/doctor`.
- [ ] 12 `docs/troubleshooting.md` — final pass.
- [ ] 13 `docs/decision-card-rationale.md` — why inline, not popover (design-review reasoning preserved for successors).
- [ ] 14 `v1.0-pilot` tag after merge.

---

### Task 9.2: Final merge + tag

```bash
cd KBI_PoC   # main
git pull
git tag -a v1.0-pilot -m "KBI pilot handoff — Rev 2 spec"
git push origin v1.0-pilot
```

---

### Task 9.3 (optional): Worktree cleanup

Keep track*a/track_b if post-pilot work is imminent; otherwise `git worktree remove ../KBI_PoC_track*{a,b}`.

---

## Self-Review

### Spec coverage

| Spec section                                                                             | Task(s)            |
| ---------------------------------------------------------------------------------------- | ------------------ |
| §3 Scope corrections (docker native, creds, Next 16, `schedule_change_sets`)             | 0.1, 0.6-0.8, 2B.1 |
| §5 Trace schema + correct FK types                                                       | 2B.1, 2B.2, 2A.3   |
| §6 Parity harness (stable hash, seed scripts, tx isolation, p99, `parity-update:` guard) | 1.2-1.7            |
| §7 Module boundaries (solver + greedy + scheduling_shared + batch_grouping)              | 2A.1-3, 3A.0-3     |
| §7 Private-symbol re-export shells                                                       | 0.10, 3A.1-3       |
| §7 CI boundary test                                                                      | 2A.5               |
| §8a Extend `/master/constraints` with 베이스라인 tab                                     | 3B.1-2, 5B.1       |
| §8b Multi-baseline                                                                       | 2B.1, 5B.1         |
| §8c Decision Card (replaces popover) + missing states                                    | 4B.3               |
| §8d Non-blocking toast                                                                   | 5B.2               |
| §8e 2-provider LLM + kiwipiepy + parity off                                              | 4B.2               |
| §9 9-week schedule                                                                       | Weeks 0-9          |
| §9 2-worktree + bootstrap/doctor                                                         | 0.5-0.7            |
| §9 Migration reversibility                                                               | 2B.1               |
| §10a Run-ID correlation                                                                  | 2A.4, 4B.4         |
| §10b p99 performance baseline                                                            | 1.5-1.6            |
| Hardcoded→DB weight migration (D2-B)                                                     | 5A.1-4             |
| Pilot success criteria (D8)                                                              | 1.8                |

### Placeholders

None. Where content depends on reading an existing file (2A.2 model_builder port, 3A.3 batch_grouping split, 5A.1 weight inventory), plan explicitly names the reading step + acceptance criterion (parity green).

### Type consistency

`ConstraintSpec` defined Task 2A.1; consumed in 2A.2 (model_builder), 2A.3 (trace_writer — via `spec_by_id`), 4B.2 (LLM narrator input), 5A.3 (weight read site).

### Known compromises (explicit)

- Task 2A.2 model-builder port has temporary hardcoded-constant usage until Week 5 migration (intentional; parity-preserving two-phase move).
- Exact split boundaries for `batch_grouping.py` (3A.3) and file-by-file move lists are finalized at task-time after reading the file end-to-end.
- Week 2 merge-order exception (Track B first) is explicit.

---

## Execution Handoff

**Plan Rev 2 complete. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks. Best fit for the 80+ tasks across 2 tracks and 10 weeks (incl. Week 0).

**2. Inline Execution** — run in this session using `executing-plans` skill, batch checkpoints. Risk: context pressure builds over 10 weeks.

**Which approach?**
