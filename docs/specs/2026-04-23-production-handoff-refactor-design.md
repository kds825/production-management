# Production Handoff Refactor — Design Spec

**Date**: 2026-04-23 (Rev 1); 2026-04-23 (Rev 2)
**Author**: jaewoo kim (drafted via `/superpowers:brainstorming` with Claude)
**Status**: Rev 2 — integrates findings from 4 parallel plan-review agents (CEO / Eng / Design / DevEx)
**Target completion**: **9 weeks** (~2026-06-25) — Rev 2 adds Week 5 for hardcoded-weight → DB migration
**Pilot go-live target**: KBI operators use scheduler for real daily decisions post-Week 9

---

## 1. Mission

Transition the KBI scheduling PoC to a **defensible, pilot-ready production system** over 9 weeks, without changing solver behavior. Every scheduling decision must be auditable, explainable to the plant manager in Korean, and reproducible from durable artifacts (schema snapshot, constraint config version, trace rows).

This is a **renovation, not a reconstruction**, **plus one targeted augmentation**: the solver currently consumes hardcoded Python weight constants (`_TARDINESS_WEIGHT`, `_CHAIN_WEIGHT=120`, `_IDLE_WEIGHT=1`, etc.) — it does not read `ConstraintConfig.priority` or `impact_level`. Without migrating those constants into the DB (Week 5), the Admin UI priority slider edits _nothing_ and XAI weight breakdowns are theater. Rev 2 adds this migration as in-scope because fake-UI destroys defensibility.

---

## 2. Drivers (why now)

- **Top driver**: Production handoff — KBI pilot go-live ~2026-06-25.
- **Defensibility requirement**: CPA-level audit standard. For every decision, we must answer "why" with durable evidence that traces back to a live, editable policy.
- **Scaling inflection**: God-files (1,900–2,900 lines) make each constraint addition feel like a gamble.
- **Operator trust**: XAI **Decision Card** (inline slide-down; not popover) lets a plant manager understand "can I move this?" in under 5 seconds and "why here?" in under 15. Grounded by trace, not LLM guessing.

---

## 3. Explicit scope decisions

### Codebase reality corrections (from Eng review)

| Earlier claim                                                                  | Verified reality                                                                                                                                                                                                                                 | Impact                                                                                                                                |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| "Dynamic constraint infrastructure is 70% built"                               | **FALSE for the solver path**. `cp_sat_optimizer.py` never reads `ConstraintConfig.priority/impact_level/implementation_type`. Weights are Python constants. Only `params_json` is consumed (via `ConstraintParams` cache).                      | Week 5 added: hardcoded constants → DB migration. **D2** decision.                                                                    |
| `services/solver/` and `services/greedy/` are cleanly separable                | **FALSE**. Two solvers have bi-directional imports: `cp_sat_optimizer.py` top-imports 14 names from `schedule_optimizer.py`; `schedule_optimizer.py` deferred-imports 4 back. A third neutral `services/scheduling_shared/` package is required. | §7 updated                                                                                                                            |
| Tests touch only public API                                                    | **FALSE**. 32+ tests import private `_`-prefixed symbols (`_CHAIN_WEIGHT`, `_find_available_slot`, `_tardiness_boost_retry`, `_purge_run_tasks`, etc.).                                                                                          | §7 updated: re-export shell must cover every grepped private symbol before any file moves; no `rm` of source files during the 9 weeks |
| `change_set` table is UUID-keyed                                               | **FALSE**. Actual table is `schedule_change_sets` (plural) with `change_set_id Column(String, primary_key=True)`.                                                                                                                                | §5 schema updated to `String(36)` FK                                                                                                  |
| `cp_sat_schedule()` is pure enough to parametrize over `solver_input_override` | **FALSE**. 18 `db.add/delete/flush` sites mid-function. Parity harness must wrap each fixture run in `db.begin_nested()` + rollback.                                                                                                             | §6 updated                                                                                                                            |
| Parity hash on `production_batch.id` is stable                                 | **FALSE**. Batch IDs are UUID/counter-generated per run.                                                                                                                                                                                         | §6: hash uses `(run_label, group_key, equipment_code, start_offset_min)`                                                              |
| Docker compose has backend + frontend services                                 | **FALSE**. Compose has only `db` service.                                                                                                                                                                                                        | §9 updated: **native** backend/frontend dev workflow; Docker only for Postgres; CI uses GitHub Actions services                       |
| Project runs Next.js 14                                                        | **FALSE**. Next.js 16 + React 19. `frontend/AGENTS.md` warns explicitly.                                                                                                                                                                         | §9 updated                                                                                                                            |
| DB creds are `kbi_user/kbi_pass/kbi`                                           | **FALSE**. Actual: `kbi / kbi_poc_2026 / kbi_scheduler` (from `README.md:41`)                                                                                                                                                                    | §9 constants block                                                                                                                    |

### What the original "Project RE-BORN" prompt got wrong

(Unchanged from Rev 1; carried forward for audit trail.)

| Prompt claim                                                       | Reality                                                  | Decision                                    |
| ------------------------------------------------------------------ | -------------------------------------------------------- | ------------------------------------------- |
| "Clean architecture layers must be built from scratch"             | Layers already exist                                     | Keep + split god-files within them          |
| "Three-Tier Constraint Architecture (Global / Process / Temporal)" | Actual categories are free-text and don't map to 3 tiers | Keep `category` free-text; UI offers filter |
| "Delete legacy Greedy algorithms"                                  | Not legacy — still called from 4 places                  | Don't delete; split into `services/greedy/` |
| "Sub-Agent A Full Frontend refactor"                               | Unclear scope                                            | Scope to surfaces consuming new endpoints   |

### What Rev 2 added (beyond the prompt and Rev 1)

- **Hardcoded weight → DB migration** (Week 5) — makes the `priority`/`impact_level` slider actually affect the solver.
- **`services/scheduling_shared/`** package — resolves circular import between solver and greedy.
- **Extend `/master/constraints`** with a new `베이스라인` tab (D1); no new `/admin` route.
- **Decision Card** inline slide-down replaces floating popover (D6).
- **Non-blocking sticky toast** for operator comment (D6); replaces modal that blocks the rush-case flow.
- **2-worktree layout** (Track A + Track B; plus `main` checkout) (D3); 4-worktree aspiration dropped.
- **`make bootstrap` + `make doctor`** preflight — collapses "which worktree / creds / port" cognitive load (DevEx 10-star).
- **Parity fixture seed scripts** — one per fixture; re-freeze 3 months later produces same hashes.
- **`kiwipiepy`** Korean morphological analyzer for LLM hallucination post-filter (naive regex was brittle).
- **LLM OFF in parity mode** (`LLM_PROVIDER=template`) — prevents cost blowup during CI.
- **Pilot success criteria** written Week 1 (best-guess; validated with stakeholder) (D8).

### In-scope god-files

| File                                               | Lines | Priority     | Target split                                             | Week                             |
| -------------------------------------------------- | ----- | ------------ | -------------------------------------------------------- | -------------------------------- |
| `services/cp_sat_optimizer.py`                     | 2,654 | P0           | `services/solver/` (5 modules)                           | 2                                |
| `services/batch_grouping.py`                       | 1,971 | **P0-moved** | `services/batch_grouping/` (5 modules) + dead-code purge | **3** (was Week 6; moved per D4) |
| `features/scheduler/components/GanttTaskBlock.tsx` | 1,056 | P0           | Decision Card integration                                | 4                                |
| `services/schedule_optimizer.py`                   | 2,870 | P1           | `services/greedy/` (3 modules)                           | 3                                |
| `presentation/routes/plan_pipeline.py`             | 2,660 | P1           | `services/pipeline/` (4 modules)                         | 4                                |
| `app/(main)/scheduler/page.tsx`                    | 2,719 | P1           | Hooks + page-sections                                    | 7                                |
| `presentation/routes/schedules.py`                 | 1,702 | P1           | `routes/schedules/` sub-package                          | 7                                |
| `features/scheduler/components/SchedulerView.tsx`  | 1,468 | P1           | Decision-card-aware split                                | 7                                |
| `features/scheduler/store/scheduleStore.ts`        | 1,291 | P1           | Zustand slices                                           | 7                                |

None of the god-file source files are `rm`'d during the 9 weeks. Each becomes a deprecation-warning re-export shell until Week 9 post-pilot, when grep confirms no callers remain.

### Explicitly deferred to post-pilot backlog (P2)

- `app/(main)/plan-register/page.tsx` (1,910)
- `features/scheduling-review/components/ProductionBatchTable.tsx` (1,008)
- `app/(main)/scheduling-review/page.tsx` (1,013)
- **LLM-proposes-constraint-changes** (CEO 10-star; D7 stub captures override reasons now, analyzes post-pilot)
- **`alternative_slots` async endpoint** (D7 deferred — defensibility doesn't require counterfactual in-pilot)
- Backend+frontend containerization (native dev works; Dockerize post-pilot when multi-dev needed)
- Backup/DR, on-call playbook, secrets rotation (Vault), PII anonymization, Grafana, RBAC

---

## 4. Approach — Parallel Strangler-Fig (9 weeks, 2 tracks)

```
Week 0  Phase 0 pre-flight (schema dump, baselines tagged, 2 worktrees, bootstrap/doctor, CI scaffold)
Week 1  Parity harness freeze (10 fixtures with reproducible seeds) + pilot-success-criteria.md
Weeks 2–8  Two long-lived branches:
           Track A: solver / backend splits + hardcoded→DB migration + trace writer
           Track B: /master/constraints extension + Decision Card + LLM narrator + migrations
         Weekly merge to main gated by 7 non-negotiable checks
Week 6  KBI dry-run (moved from Week 7 per D5 — 3 weeks buffer for friction fixes)
Week 9  Buffer + handoff packet + v1.0-pilot tag
```

**Rationale**: 9-week cadence with parallel tracks and parity gate preserves safety; D2-D5 resequencing catches input drift (batch_grouping) and operator-UX issues (dry-run) early enough to recover.

---

## 5. Design Section 1 — Trace Schema (XAI source-of-truth)

### Two new tables

**`solver_run`** — one row per `cp_sat_schedule()` invocation. Unchanged from Rev 1 except indexes.

| Column                      | Type                                             | Purpose                                      |
| --------------------------- | ------------------------------------------------ | -------------------------------------------- |
| `run_id`                    | UUID PK                                          | correlation id in logs                       |
| `run_label`                 | str                                              | joins to `production_batch.run_label`        |
| `started_at`, `finished_at` | ts                                               | latency observability                        |
| `solver_status`             | str                                              | OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN    |
| `objective_value`           | bigint                                           |                                              |
| `constraint_config_version` | UUID FK → `constraint_config_history.version_id` | **audit-critical** — which config was active |
| `input_hash`                | str(80)                                          | parity compares                              |
| `output_hash`               | str(80)                                          | parity compares                              |
| `solver_params_json`        | JSONB                                            | CP-SAT flags                                 |

**`solver_decision`** — one row per scheduled batch per run. Rev 2 corrects FK type:

| Column                           | Type                                                                                                                             | Purpose                                                                  |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `decision_id`                    | UUID PK                                                                                                                          |                                                                          |
| `run_id`                         | UUID FK → solver_run, CASCADE                                                                                                    |                                                                          |
| `production_batch_id`            | str(64) FK → production_batch.id                                                                                                 |                                                                          |
| `assigned_equipment_id`          | str(32)                                                                                                                          |                                                                          |
| `assigned_start`, `assigned_end` | ts                                                                                                                               |                                                                          |
| `contributions_json`             | JSONB                                                                                                                            | trace body: `[{constraint_id, weight_applied, bound, delta_if_removed}]` |
| `binding_hard_constraints_json`  | JSONB                                                                                                                            | hard constraints that forced placement                                   |
| `is_manually_adjusted`           | bool, default false                                                                                                              |                                                                          |
| `manual_override_change_set_id`  | **`str(36)`** FK → **`schedule_change_sets.change_set_id`** (Rev 2 fix; was UUID → change_set which would have failed migration) |                                                                          |
| `llm_summary_text`               | text, nullable                                                                                                                   | cached narrator output                                                   |

### LLM narrator grounding (unchanged)

Narrator's only input is `contributions_json`. Post-response filter rejects constraint names not in the catalog. Fallback is `TemplateProvider`.

### What the schema does NOT do (YAGNI — Rev 2 confirms)

- No `alternative_slots_json` column (was in Rev 1; D7-C removed; deferred to post-pilot).
- No 3-tier ontology.
- No pre-computed alternatives.

### Write path

```
cp_sat_schedule() inside services/solver/__init__.py:
  1. specs = constraint_loader.load_active(db)
  2. model, penalty_vars, hard_literals = model_builder.build(inputs, specs)
  3. objective.attach(model, penalty_vars, specs)
  4. status = cp_solver.Solve(model) with num_search_workers=1, seed=42 in parity mode
  5. trace_writer.write(solver, penalty_vars, hard_literals, specs, run_metadata, db)
  6. return {solver_status, objective_value, assignments, run_id}
```

---

## 6. Design Section 2 — Parity Harness

### Golden input set — 10 fixtures with reproducible seed scripts

Location: `backend/tests/fixtures/parity/`. Each fixture has:

- `{name}.json` — captured input + frozen hashes
- `scripts/seed_parity_scenarios/{name}.py` — deterministic DB seed producing this scenario

Without reproducible seeds, a 3-month-later re-freeze produces different batch IDs → fake hash-flip → loss of audit value.

10 scenarios unchanged from Rev 1.

### Hash contract (Rev 2 correction)

```python
output_hash = sha256(sorted(
    (run_label, group_key, equipment_code, start_offset_minutes_from_horizon)
    for d in decisions
))
```

Key fields are the **solver's decision variables**, not post-hoc naming artifacts. `production_batch.id` is UUID/counter-generated and NOT stable across reseeds — so we exclude it.

### Transaction isolation

Each parity fixture runs inside `db.begin_nested()`; post-fixture assertion: `ScheduleTask` row count unchanged from pre-fixture. Prevents the 18-call `db.add/delete/flush` inside `cp_sat_schedule()` from dirtying the DB.

### Strict hash discipline (D9-A)

No semantic-parity tier. Hash flip = separate `parity-update:` prefixed commit with rationale. CI rule: fixture `.json` files may only be modified by commits whose first line starts with `parity-update:`.

### Determinism pins

`num_search_workers=1`, fixed `random_seed=42`. `backend/tests/conftest.py` already sets `CPSAT_WORKERS=1` — Rev 2 respects it rather than re-invents.

### Auditor's Trail diff log

Unchanged from Rev 1. Phase 1 (before Week 2 trace writer): batch-movement + objective delta. Phase 2 (Week 3+): adds constraint-contribution delta.

### LLM off in parity mode

`LLM_PROVIDER=template` forced in parity test env. Prevents 10 fixtures × ~200 batches × real LLM calls = cost blowup + flaky CI.

### Performance baseline — p99 of n≥20 (Rev 2 correction)

Week 1 captures 20 runs per fixture → p99 wall-clock. Rev 1's p95-of-5 had wider confidence interval than the observed value.

CI rule: `current_runtime > 2× p99_baseline` → block (was Rev 1's 3×). Warn at 1.5×.

### Deliverables (Week 1)

1. `backend/tests/fixtures/parity/*.json` — 10 frozen fixtures
2. `scripts/seed_parity_scenarios/*.py` — 10 seed scripts
3. `backend/tests/test_parity_harness.py` with `@pytest.mark.parity`
4. `scripts/parity_freeze_current_behavior.py` — refreeze tool
5. `make parity-quick` (fixtures #01 + #10, ~60s)
6. `make parity-fixture FIXTURE=02` (single-fixture debug)
7. `.github/workflows/parity.yml` (Postgres service; pytest + performance gate)
8. Performance baseline JSON
9. `docs/parity-harness.md`
10. **`docs/pilot-success-criteria.md`** (D8)

---

## 7. Design Section 3 — Constraint-Engine Module Boundaries

### Three cooperating packages (Rev 2 adds `scheduling_shared/`)

```
services/solver/              # CP-SAT
├── __init__.py               # re-exports cp_sat_schedule()
├── constraint_loader.py      # DB → ConstraintSpec (only SQLAlchemy touch)
├── model_builder.py          # (inputs, specs) → (model, penalty_vars, hard_literals)
├── objective.py              # composes penalty terms into minimize()
├── solver_io.py              # input/output normalization + hashing
└── trace_writer.py           # solver_run + solver_decision rows

services/greedy/              # Stage2 + urgent fallback
├── __init__.py               # re-exports auto_schedule, reschedule_affected_groups
├── auto_schedule.py
├── reschedule_affected.py
└── slot_finder.py

services/scheduling_shared/   # NEW (Rev 2) — resolves circular import
├── __init__.py
├── calendar_ops.py           # datetime_to_wmin, resolve_base_date (currently in cp_sat_optimizer)
├── slot_filters.py           # narrow_by_stranding, filter_by_sheath_routing, align_start_to_predecessor_end
└── group_ops.py              # compute_group_duration, schedule_multi_equipment
```

`domain/constants.py` holds data-only constants (`PREDECESSOR_PROCESS`, process order, weight baselines).

**Before any file moves, Task 3A.1 Step 0 produces a grep list of every private symbol imported in tests. All must be covered by the re-export shell.**

### `ConstraintSpec` value object

Unchanged from Rev 1. After Week 5 migration, `weight` becomes causally linked to the objective (currently: admin UI displays it; Week 5 makes it take effect).

### Invariant: solver's SQLAlchemy access is allow-listed

CI test (`backend/tests/test_solver_boundary.py`): grep-style AST
check that only the following modules under `services/solver/`
import `from app.infrastructure`:

- `constraint_loader.py` — reads ConstraintConfig → ConstraintSpec
- `input_builder.py` — reads masterdata → SolverInput
- `trace_writer.py` — writes solver_run + solver_decision

Adding a new boundary-crosser requires updating both the allow-list
in the test and this spec section.

---

## 8. Design Section 4 — UI Surface (Admin + Decision Card + LLM)

### 8a. Constraint Admin — **extend `/master/constraints`** (D1)

Existing `/master/constraints/page.tsx` (280 lines) has 3 tabs: 파라미터 / 제약 on-off / 변경 이력. Rev 2 **adds a 4th tab**: `베이스라인`.

**No new route**; Rev 1's `/admin/constraints` is dropped to prevent divergent pages.

**New tab contents**:

- List of baselines chronologically with tag name, created_by, created_at
- "Promote current as new baseline" button → dialog with (a) diff preview (reuses `VersionDiff`), (b) tag-name field with default `YYYY-MM-DD-HHmm-{initials}`, (c) 승인 근거 free-text, (d) confirm disabled until tag typed
- Reset dropdown: any baseline; confirmation modal shows what will change
- Read-only mode banner when an active `solver_run.finished_at IS NULL` exists (prevents mid-solve edits)
- Soft-lock toast ("지금 다른 사용자가 편집 중입니다") via `updated_at` staleness check

**Korean terminology**: umbrella remains `제약 파라미터` (existing); tabs named `파라미터 / on-off / 베이스라인 / 이력`.

### 8b. Multi-baseline versioning (Rev 3 — reshaped per Task 0.2 discovery)

**Reality check from Task 0.2**: `constraint_config_history` is a narrow change-log, not a wide snapshot. Columns: `history_id` (PK), `constraint_id` (FK), `changed_at`, `changed_by` (free-form string), `old_params_json`, `new_params_json`. There is no `notes`, `priority`, `is_enabled`, etc. on history.

Rev 2 proposed adding `is_baseline`/`baseline_tag_name`/`baseline_created_by`/`baseline_created_at`/`version_id` columns. Rev 3 **drops that migration** in favor of a **`changed_by` marker convention** that works with the existing schema:

**Convention**: a "baseline" = a set of `constraint_config_history` rows (one per constraint) sharing a `changed_by` value of the form `BASELINE_<tag>_<iso_timestamp>`. E.g., `BASELINE_phase0-initial_20260423T101449Z` or `BASELINE_week2-stabilized_20260515T140000Z`.

- **Promote current as new baseline**: `POST /api/constraints/promote-baseline` inserts one history row per constraint with `changed_by=BASELINE_<tag>_<now>` and `new_params_json=current_value`. `old_params_json=previous_baseline_value` or `NULL` if first.
- **List baselines**: `SELECT DISTINCT changed_by FROM constraint_config_history WHERE changed_by LIKE 'BASELINE_%' ORDER BY changed_at DESC`.
- **Reset to a baseline**: load all history rows with that `changed_by`, apply each `new_params_json` back onto `constraint_config.params_json` for the matching `constraint_id`.
- **Diff two baselines**: set-compare the `new_params_json` values per `constraint_id` across two `changed_by` groups.

**Advantages**: no schema migration needed; history table stays normalized; baseline set == just another row in the change-log.

**Week 2 migration (Task 2B.1) scope shrinks**: only adds `solver_run`, `solver_decision`, and `change_set.override_reason` (the baseline-related columns are removed from scope).

**Phase 0 baseline row**: Task 0.2 inserted 38 rows with `changed_by='PHASE0_INITIAL_20260423'` and `changed_at=2026-04-23T10:14:49Z`. For convention consistency, Week 2 renames these via one-off SQL: `UPDATE constraint_config_history SET changed_by='BASELINE_phase0-initial_20260423T101449Z' WHERE changed_by='PHASE0_INITIAL_20260423'`.

### 8c. XAI **Decision Card** — inline slide-down (D6 replaces popover)

When operator clicks a batch, the row highlights and an inline card slides down below it, pushing rows below. Three horizontal zones:

```
┌──────────────────────────────────────────────────────────────┐
│ [Zone 1] 한 줄 요약 + 이동 가능 여부 pill (from hard constraints)
│   "이 배치는 납기 10일 지연 회피를 위해 선행 컬러군 뒤에 배정됨"
│   [이동 가능] / [제약됨: 컬러 인접성] / [불가능: 필수 제약]
├──────────────────────────────────────────────────────────────┤
│ [Zone 2] 가중치 최대 3개 bar-chart mini-viz
│   납기        ████████████  48K
│   컬러        ██             8.5K
│   셋업        ▏               1.2K
│   (펼치기 ▾ 로 전체 가중치 표 확장)
├──────────────────────────────────────────────────────────────┤
│ [Zone 3] 결정 근거 v2026-05-20-14:30 · [버전 비교] · [닫기]
└──────────────────────────────────────────────────────────────┘
```

**Why not floating popover** (Design 10-star):

- Spatial continuity — batch stays in view; operator looks at neighbors while reading
- Plant managers are popover-fatigued (Excel-trained); inline card reads as "more info about this row"
- Bar chart teaches weight intuition; a number-only table never does
- Manual-override renders as **yellow-bordered card** (vs. default blue) in the same slot → operators learn "솔버-파랑" vs "사람-노랑" visual vocabulary
- Multi-select expansion is trivial post-pilot

**Inverted-pyramid fix**: the question operators actually ask when clicking is "can I move this?" → binding hard constraints answer that. Weights are secondary (collapsed).

**Manual-override branch** (from Rev 1; unchanged semantically, now rendered in the yellow card variant):

```
┌─ 사람이 결정한 배치 ──────────────────────────────────────┐
│  수동으로 옮긴 배치라 솔버 분석은 표시하지 않습니다.     │
│  (Rev 2 Korean: natural factory-register)               │
│  조정 사유: 현장 긴급 · "2호기 고장으로 이동"            │
│  조정 시각: 2026-05-20 14:32                             │
│  ─── 원래 솔버의 결정 (참조용) ───                       │
│  eq_02 @ 2026-05-20 08:00 ~ 12:00                        │
│  (이 결정에 대한 분석 보기 ▸)                            │
└──────────────────────────────────────────────────────────┘
```

**Missing states (Design-review-sourced)**:

- Decision row not yet written → "과거 버전에서 생성되어 분석 정보가 없습니다"
- `TemplateProvider` fallback → `템플릿 요약` pill
- LLM generating → 10-second timeout → "요약을 불러올 수 없습니다. 가중치만 표시합니다"
- Empty `contributions_json` → "이 배치는 필수 제약만으로 결정되었습니다 (소프트 가중치 영향 없음)"
- Phantom batch (404) → "배치가 재스케줄되었을 수 있습니다"
- Network slow → 8s timeout → "연결이 느립니다 — 다시 시도"

### 8d. Operator comment — **non-blocking sticky toast** (D6 per Design review)

When operator drag-drops a batch, the move **completes immediately** (no modal blocking rush-case flow). A sticky toast appears bottom-right, persists 60s or until dismissed:

```
┌─ 변경 사유 기록 (선택) ──────────┐
│  [납기 변경] [현장 긴급]         │
│  [설비 고장] [자재 부족]         │  ← 기타 removed (per Design review)
│  ┌─────────────────────────┐     │
│  │ 간단히 (선택)…           │     │
│  └─────────────────────────┘     │
│                      [저장] [X]  │  ← dismiss = skip; no separate button
└──────────────────────────────────┘
```

Unfilled reasons surface as a `사유 미기록 N건` badge on the gantt header → admin batch-review later. Rollback logic: if backend rejects the drag (equipment conflict), the saved reason is invalidated.

### 8e. LLM narrator grounding — 2 providers + Korean morphological filter (Rev 2)

**Providers** (reduced from 3 to 2 — OpenAI dropped per DevEx scope review):

- `AnthropicProvider` (primary)
- `TemplateProvider` (always-available fallback; used in parity mode via `LLM_PROVIDER=template`)
- Future `PwCGatewayProvider` is a drop-in when internal gateway auth is ready; post-pilot.

**Hallucination post-filter** uses `kiwipiepy` Korean morphological analyzer (not naive regex — Rev 2 correction; "납기" vs "납기일" false-positive problem). Extract nouns from LLM output → every noun must be in `constraint_catalog` Korean names + allow-list → else reject, use template.

**Cost control**: one LLM call per `solver_decision` at insert-time; cached in `solver_decision.llm_summary_text`. **Parity runs force `LLM_PROVIDER=template`** — zero LLM cost during CI.

### 8f. API endpoints

| Method                                       | Path               | Status  |
| -------------------------------------------- | ------------------ | ------- |
| GET `/api/constraints`                       | List active        | Exists  |
| PATCH `/api/constraints/{id}`                | Edit one           | **New** |
| POST `/api/constraints/promote-baseline`     | Promote current    | **New** |
| POST `/api/constraints/reset-to-baseline`    | Reset to a version | **New** |
| GET `/api/constraints/baselines`             | List baselines     | **New** |
| GET `/api/constraints/versions/{a}/diff/{b}` | Version diff       | **New** |
| GET `/api/decisions/{batch_id}/latest`       | Decision Card data | **New** |

`POST /api/decisions/{decision_id}/alternatives` **removed** (D7-C deferred post-pilot).

---

## 9. Design Section 5 — 9-Week Delivery Plan + 2 Worktrees

### Infrastructure constants (Rev 3 grounding — Path D: Supabase-native dev)

| Item                              | Value                                                                                                                                                   |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Active dev DB**                 | **Supabase** (project `pgujccdnuidsjxuyqgyi`, region `ap-northeast-2`); connection via `DATABASE_URL` in gitignored `backend/.env`                      |
| `docker-compose.yml` `db` service | **Unused in normal dev** — PoC leftover. Creds `kbi/kbi_poc_2026/kbi_scheduler` are idle defaults for CI-like throwaway local Postgres if ever needed.  |
| Backend default port              | `8000`                                                                                                                                                  |
| Frontend default port             | `3000`                                                                                                                                                  |
| Health endpoint                   | `/api/health` (not `/health`)                                                                                                                           |
| Next.js version                   | **16.2.1** (per `frontend/AGENTS.md`)                                                                                                                   |
| React version                     | **19**                                                                                                                                                  |
| pytest config                     | `backend/pytest.ini` (created Week 0 — not currently present)                                                                                           |
| `conftest.py`                     | `backend/tests/conftest.py` (exists; sets `CPSAT_WORKERS=1`)                                                                                            |
| Backend/frontend runtime          | **Native**; no Docker required for day-to-day dev                                                                                                       |
| CI                                | GitHub Actions with `services.postgres` (throwaway fresh Postgres per run; needs alembic fix from Task 0.1b)                                            |
| Alembic bootstrap status          | ⚠ Broken on empty DB until Task 0.1b — `c3d4e5f6a7b8_add_unassigned_index_and_reason` indexes `production_batch.batch_group` which no migration creates |

### 9-week schedule (Rev 2)

| Week  | Track A (backend/solver)                                                                                                                                                                                                                                                                     | Track B (frontend/admin/migrations)                                                                                                                                                                                                                                                                                     | Close gate                                   |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **0** | Phase 0: `pg_dump` (native `psql -U kbi -d kbi_scheduler`); constraint_config JSON; tag "Phase 0 initial" in history; create `backend/pytest.ini`; add `typecheck`/`test` npm scripts; write `make bootstrap` + `make doctor` + `make verify`; scaffold `.github/workflows/`; worktree setup | —                                                                                                                                                                                                                                                                                                                       | `make doctor` green in all worktrees         |
| **1** | Parity harness: 10 fixtures with seed scripts, freeze, p99 performance baseline (n=20), `make parity-quick`, CI parity workflow; grep-list private symbols in tests                                                                                                                          | Write `docs/pilot-success-criteria.md` (best-guess D8); write `docs/troubleshooting.md` skeleton                                                                                                                                                                                                                        | Parity green; success criteria committed     |
| **2** | `services/solver/` split (P0): `input_builder`, `constraint_loader`, `model_builder`, `objective`, `solver_io`. Transaction-isolation wrapper for parity. Run-ID LoggerAdapter + middleware.                                                                                                 | **Track B merges FIRST (Week 2 exception)**: Alembic for `solver_run` + `solver_decision` (str FK → `schedule_change_sets`) + `change_set.override_reason`. **No `constraint_config_history` schema changes** (Rev 3: baseline = `changed_by` marker convention, no new columns needed). Round-trip reversibility test. | Parity green; new tables populating          |
| **3** | `services/greedy/` split (P1) + **`services/batch_grouping/` split (P0 moved from Week 6 per D4)** + dead-code purge. `services/scheduling_shared/` created for circular-import resolution.                                                                                                  | Extend `/master/constraints` — add 베이스라인 tab scaffold (read-only list); version-diff component                                                                                                                                                                                                                     | Parity green; no circular imports            |
| **4** | `plan_pipeline.py` split (P1) → `services/pipeline/`                                                                                                                                                                                                                                         | **Decision Card** component (replaces popover design) + LLM narrator binding + kiwipiepy filter + `TemplateProvider` fallback + UI run_id error toast                                                                                                                                                                   | Click-batch → Decision Card with LLM summary |
| **5** | **Hardcoded→DB migration (D2)**: `_TARDINESS_WEIGHT`, `_CHAIN_WEIGHT`, `_IDLE_WEIGHT`, `_SLACK_WEIGHT_BASE`, `_PAST_SEVERITY_K` → `constraint_config.params_json["weight"]`. Model builder consumes from specs. Parity green after each constant migrated.                                   | Promote-baseline dialog + reset-to-any-baseline + non-blocking sticky toast for operator comment + `change_set.override_reason` wiring                                                                                                                                                                                  | Parity green; priority slider affects solver |
| **6** | Integration pass; backend observability polish                                                                                                                                                                                                                                               | **KBI dry-run (moved from Week 7 per D5)** — operators walk through pilot scenarios; friction-log written                                                                                                                                                                                                               | Friction-log documented                      |
| **7** | `schedules.py` route split (P1) → `routes/schedules/` sub-package                                                                                                                                                                                                                            | `scheduler/page.tsx` split (P0) + `SchedulerView.tsx` decision-card-aware split + `scheduleStore.ts` slices                                                                                                                                                                                                             | Parity green                                 |
| **8** | Friction-log P0 fixes (backend side)                                                                                                                                                                                                                                                         | Friction-log P0 fixes (UI side)                                                                                                                                                                                                                                                                                         | Dry-run P0 items closed                      |
| **9** | Final integration + documentation                                                                                                                                                                                                                                                            | Handoff packet; `v1.0-pilot` tag                                                                                                                                                                                                                                                                                        | Release notes committed                      |

### Non-negotiable per-week gates (7 checks)

1. Parity harness green (`pytest -m parity`)
2. Unit tests green (69+)
3. Playwright E2E smoke green
4. Frontend lint + typecheck clean (`npm run lint && npm run typecheck`)
5. Backend ruff + mypy clean
6. `worktree_status.sh` shows zero uncommitted changes
7. **Migration reversibility** — `./scripts/test_migration_reversibility.sh` passes for any migration touching existing tables

### 2-worktree layout (D3)

```
~/Desktop/Project/
├── KBI_PoC/           # main checkout — reference + weekly merge target
├── KBI_PoC_track_a/   # refactoring-track-a-solver
└── KBI_PoC_track_b/   # refactoring-track-b-admin
```

**Port allocation** (Rev 3 — Path D; all worktrees share the Supabase DB):

| Worktree | Backend port | Frontend port |
| -------- | ------------ | ------------- |
| main     | 8000         | 3000          |
| track_a  | 8001         | 3001          |
| track_b  | 8002         | 3002          |

Per-worktree `.env.worktree` sets `BACKEND_PORT` + `FRONTEND_PORT` only. Backend + frontend run **natively**. All worktrees read `DATABASE_URL` from the shared `backend/.env` (which points at Supabase). DB isolation during tests comes from `db.begin_nested()` + rollback, not from separate DB instances.

### `make bootstrap` + `make doctor` (DevEx 10-star — Rev 3 Supabase-native)

`make bootstrap` (idempotent, no Docker):

1. Creates `backend/venv` if missing; installs `backend/requirements.txt`
2. Copies `.env.worktree.example` → `.env.worktree` if missing
3. Verifies `backend/.env` has `DATABASE_URL` set (else prints setup instructions)
4. `cd backend && alembic current` — reports current migration; does NOT run `upgrade head` by default (Supabase is already migrated; running upgrade on a shared DB could affect other worktrees mid-work)
5. Verifies `curl http://localhost:${BACKEND_PORT}/api/health` once backend is started manually

`make doctor` (10-second preflight):

- ✓ `.env.worktree` loaded with correct `BACKEND_PORT` / `FRONTEND_PORT`
- ✓ `backend/.env` has `DATABASE_URL` pointing at a Postgres instance
- ✓ `alembic current` matches code head (warns if out of sync; does not auto-upgrade)
- ✓ pytest and vitest are discoverable
- ✓ current branch matches worktree expectation (track_a → `refactoring-track-a-solver`, etc.)

### Shared-DB discipline (Rev 3)

Because all worktrees share Supabase, migrations are a coordination point:

1. Only Track B creates/applies migrations (per ownership map).
2. Track B's migration lands on Supabase as part of the PR merge (`alembic upgrade head` run once after merge to main).
3. Before running any code or tests, every worktree MUST `git pull` main to resync code with DB state.
4. `make doctor` warns if `alembic current` is behind the code's latest revision (signal to `git pull` + re-read PR notes).

### Weekly merge protocol

1. Friday: from `KBI_PoC` main, `make parity` against latest both branches.
2. **Track B merges to main first** for Week 2 (exception — Track A 2A.3 depends on Track B migrations); subsequent weeks: Track A first.
3. Track B rebases onto updated main, re-runs parity, merges.
4. All worktrees `git pull` updated main; `make doctor` to re-verify.

### Ownership map (conflict prevention)

Same as Rev 1 except:

- `services/scheduling_shared/` — Track A owns
- `frontend/src/app/(main)/master/constraints/*` — Track B owns (existing page extension)
- No separate `frontend/src/app/(main)/admin/*` (D1 rejected)

### Additional DX deliverables (Week 0 + ongoing)

- `Makefile`: `bootstrap`, `doctor`, `verify`, `parity`, `parity-quick`, `parity-fixture FIXTURE=NN`, `test`, `test-backend`, `test-frontend`, `seed`, `reset-db`, `lint`, `typecheck`
- `scripts/worktree_cd.sh` — shell function `kbi <main|a|b>` with env loading
- `scripts/run_id_grep.sh <run_id>` — grep logs + DB for one run
- `scripts/test_migration_reversibility.sh`
- `docs/troubleshooting.md` — keyed by common errors
- `.env.worktree.example` checked in; `.env.worktree` gitignored

---

## 10. Design Section 6 — Observability + Performance Baseline

### 10a. Run-ID correlation

```
backend/app/infrastructure/logging/
├── run_context.py       # contextvars.ContextVar
└── adapters.py          # LoggerAdapter "[run_id=...]" prefix
```

- Set in `cp_sat_schedule()` entry; propagates via contextvars
- FastAPI middleware sets `X-Run-Id` header on every response
- Frontend `apiFetch` captures header; error toast shows run_id with copy button

### 10b. Performance baseline (Rev 2: p99 of n≥20)

Written to `tests/fixtures/parity/baseline_performance.json`:

```json
{
  "frozen_at": "2026-04-30T14:00:00Z",
  "git_sha": "<main HEAD>",
  "hardware": "macOS / Postgres 15 docker",
  "fixtures": {
    "01_nominal": { "p50_sec": 8.2, "p99_sec": 10.4, "samples": 20 }
  }
}
```

CI rule: `> 2× p99` blocks merge; `> 1.5× p99` warns.

---

## 11. Post-pilot backlog (Rev 2 additions)

| Item                                                                  | Rationale                                                                                    |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Backup / DR procedure                                                 | KBI IT owns their Postgres                                                                   |
| On-call / incident playbook                                           | KBI organizational decision                                                                  |
| Secrets rotation (Vault / KMS)                                        | Pilot uses env vars                                                                          |
| PII anonymization                                                     | Legal review; not pilot-blocking                                                             |
| Grafana / Datadog                                                     | Logs sufficient for pilot                                                                    |
| RBAC                                                                  | Single-editor pilot                                                                          |
| P2 god-files (plan-register, scheduling-review, ProductionBatchTable) | Stable; low regression risk                                                                  |
| **LLM-proposes-constraint-changes (CEO 10-star)**                     | Override reasons are captured in pilot; analysis is post-pilot "PwC consulting point" per D7 |
| **`alternative_slots` async endpoint**                                | Pilot doesn't require counterfactual; defensibility is intact without it                     |
| **Backend/frontend containerization**                                 | Native dev works for solo; containerize when multi-dev arrives                               |
| **`PwCGatewayProvider` for LLM**                                      | Gateway auth integration when available                                                      |

---

## 12. Risks & mitigations (Rev 2 updates)

| Risk                                                                         | Likelihood        | Mitigation                                                                                   |
| ---------------------------------------------------------------------------- | ----------------- | -------------------------------------------------------------------------------------------- |
| Parity hash flips for reasons we can't explain                               | Medium            | `num_search_workers=1` + fixed seed + stable hash keys (not `production_batch.id`)           |
| LLM provider outage                                                          | Medium            | `TemplateProvider` always-available fallback                                                 |
| Dead code in `batch_grouping.py` called via reflection                       | Low               | `vulture` + `ruff`; all deletions separate commits; revert trivial                           |
| Track A / B merge conflicts                                                  | Low               | Ownership map; `constraint_config.py` model owned by Track B, consumed via `ConstraintSpec`  |
| KBI dry-run (Week 6) reveals UX blocker                                      | Medium-High       | 3-week buffer (Weeks 7-9); P1 items slip to post-pilot if needed                             |
| Runtime regression                                                           | Medium            | p99 baseline + CI gate                                                                       |
| `schedule_optimizer.py` greedy path edge cases                               | Medium            | Fixtures #03 (urgent), #06 (stage2 handoff)                                                  |
| Migration corrupts existing tables                                           | Low-Medium        | Round-trip test gate; Phase 0 break-glass restore                                            |
| Manual-override UI shows invalid weights                                     | Low               | §8c explicit yellow-card branch                                                              |
| **`services/solver/` ↔ `services/greedy/` circular import** (Rev 2)          | Medium            | `services/scheduling_shared/` neutral package; Week 3 task inventory before any move         |
| **Hardcoded-weight → DB migration breaks solver** (Rev 2)                    | Medium            | Week 5 migrates one constant at a time; parity green after each; bail on any flip            |
| **Test files import private symbols not covered by re-export shell** (Rev 2) | High if unchecked | Week 3 Task 0: grep-list **every** private symbol in tests; shell covers all before any move |
| **FK type mismatch `UUID` → `VARCHAR`** (Rev 2 caught pre-implementation)    | Resolved          | §5 uses `str(36)` FK; correct `schedule_change_sets` table name                              |
| **4-worktree memory pressure** (Rev 2 mitigated)                             | Resolved          | D3: reduced to 2 worktrees + main                                                            |

---

## 13. Open questions for implementation phase

1. ~~CI existence — plan adds Actions workflows in Week 0.~~ **Resolved**: no existing CI; Week 0 scaffolds.
2. ~~`mypy` / `ruff` configuration.~~ **Resolved**: Week 0 adds baseline config.
3. Does `constraint_checker.py` (706 lines) build constraints, or only validate? Confirm during Week 2.
4. `batch_grouping.py` exact split boundaries — confirm during Week 3 after end-to-end read.
5. Pilot deployment target: where does KBI's production run? (Affects `baseline_performance.json.hardware` field.)
6. **Can the stakeholder meeting confirming `docs/pilot-success-criteria.md` (D8) happen in Week 1?** If not, Week 2 writes a best-guess placeholder and iterates.

---

## 14. Deliverables (end of Week 9)

1. `docs/archive/schema_asis_20260423.sql` — pre-refactor DDL
2. `docs/archive/constraint_config_sample_20260423.json`
3. `docs/pilot-success-criteria.md` (Week 1 best-guess + Week-2 stakeholder confirmation)
4. `docs/architecture-as-is-to-be.md`
5. `docs/api-spec.md` (regenerated OpenAPI)
6. `docs/operator-runbook.md`
7. `docs/constraint-catalog.md`
8. `docs/llm-prompt-inventory.md`
9. `docs/parity-harness.md`
10. `docs/deletion-log.md`
11. `docs/worktree-playbook.md`
12. `docs/troubleshooting.md` (Rev 2 addition)
13. **`docs/decision-card-rationale.md`** — why inline, not popover (for successor engineers; Rev 2)
14. `v1.0-pilot` git tag
15. Pilot-ready native run: `make bootstrap && make doctor && cd backend && uvicorn app.main:app` + `cd frontend && npm run dev`

---

## 15. Explicit non-goals

- Rewriting the CP-SAT algorithm or greedy algorithm. Both freeze under parity.
- Adding new constraint types during the 9 weeks.
- FE redesign beyond Decision Card + /master/constraints extension.
- Multi-tenancy, auth, RBAC.
- **LLM-proposes-constraint-changes** (D7-C defers).
- **Containerizing backend+frontend** (native dev + containerized CI for now).
- **Semantic-parity tier** (D9-A: strict hash only).

---

## 16. Change log

| Date       | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Author              |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| 2026-04-23 | Initial design through brainstorming session (6 design sections, 3 revisions)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | jaewoo kim / Claude |
| 2026-04-23 | Rev 1: manual-override UI branch (§8c), migration reversibility gate (§9), 2 risks (§12)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | jaewoo kim / Claude |
| 2026-04-23 | **Rev 2**: Integrated 4-agent plan review. 9 judgment calls decided (D1-D9). Major changes: extend `/master/constraints` (D1-A), add Week 5 hardcoded→DB migration (D2-B), reduce to 2 worktrees (D3-B), batch_grouping→Week 3 (D4-B), dry-run→Week 6 (D5-B), Decision Card replaces popover (D6-B), LLM-proposes deferred (D7-C), best-guess success criteria (D8-B), strict hash only (D9-A). 15 must-fix corrections: docker services, DB creds, `/api/health`, Next.js 16, `schedule_change_sets` FK type, 32+ private-symbol re-exports, circular-import via `scheduling_shared/`, stable parity hash keys, transaction isolation, positional args, Postgres port param, `pytest.ini` + npm scripts, real CSS tokens (not placeholders). | jaewoo kim / Claude |
| 2026-04-23 | **Rev 3 (Path D — Supabase-native)**: Rev 2 had assumed `docker-compose` `db` service was the active dev DB. Task 0.1 execution revealed it's a PoC leftover; the real DB is **Supabase** (`DATABASE_URL` in `backend/.env`). §9 rewritten: `make bootstrap`/`make doctor` drop Docker dependency; worktree table drops Postgres port column; shared-DB discipline section added. Task 0.1b inserted to fix pre-existing alembic `batch_group` missing-migration bug (blocks fresh-DB bootstrap + CI). Archive README corrected to reflect Supabase as source of truth.                                                                                                                                                                       | jaewoo kim / Claude |
| 2026-04-23 | **Rev 3 continued (§8b reshape)**: Task 0.2 execution revealed `constraint_config_history` is a narrow change-log (`old_params_json` / `new_params_json` / `changed_by`), NOT a wide snapshot. Rev 2's proposed schema additions (`is_baseline`, `baseline_tag_name`, etc.) are dropped. §8b rewritten: baseline = `changed_by=BASELINE_<tag>_<ts>` marker convention. Week 2 Task 2B.1 migration scope shrinks to just `solver_run` + `solver_decision` + `change_set.override_reason` (no history table changes). Phase 0 marker renamed to `BASELINE_phase0-initial_20260423T101449Z` via one-off SQL in Week 2.                                                                                                                           | jaewoo kim / Claude |
