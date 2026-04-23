# Production Handoff Refactor — Design Spec

**Date**: 2026-04-23
**Author**: jaewoo kim (작성: Claude via /superpowers:brainstorming)
**Status**: Design approved; awaiting spec review before plan drafting
**Target completion**: 8 weeks (~2026-06-18)
**Pilot go-live target**: KBI operators use scheduler for real daily decisions post-Week 8

---

## 1. Mission

Transition the KBI scheduling PoC to a **defensible, pilot-ready production system** over 8 weeks, without changing solver behavior. Every scheduling decision must be auditable, explainable to the plant manager in Korean, and reproducible from durable artifacts (schema snapshot, constraint config version, trace rows).

This is a **renovation, not a reconstruction**. Clean architecture layers already exist; dynamic constraint infrastructure is ~70% built; 69 backend tests and a snapshot mechanism already exist. The work is to finish, formalize, decompose, and instrument — not to rebuild from scratch.

---

## 2. Drivers (why now)

- **Top driver**: Production handoff — KBI pilot go-live ~2026-06-18.
- **Defensibility requirement**: CPA-level audit standard. For every schedule decision, we must answer "why" with durable evidence.
- **Scaling inflection**: God-files (2,000–2,900 lines) make each constraint addition feel like a gamble. Splitting them is prerequisite to safe post-pilot iteration.
- **Operator trust**: XAI layered surface (LLM narrative grounded in structured trace) lets a plant manager get an answer to "왜 여기?" in one click, with an expandable weights table for the skeptical case.

## 3. Explicit scope decisions

### What the original "Project RE-BORN" prompt got wrong, and what we dropped

| Prompt claim                                                       | Reality                                                                                                                                                                                             | Decision                                                                                                          |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| "Clean architecture layers must be built from scratch"             | `domain/`, `application/`, `infrastructure/`, `presentation/` already exist                                                                                                                         | Keep existing layers; split god-files _within_ them                                                               |
| "Three-Tier Constraint Architecture (Global / Process / Temporal)" | Actual `ConstraintConfig.category` values are free-text ("due_date", "color_group", "calendar", "setup_time") and don't map to 3 tiers                                                              | Keep `category` as free-text; derive taxonomy from data, not impose one. UI offers category filter, not hierarchy |
| "Delete legacy Greedy algorithms"                                  | `schedule_optimizer.py` is NOT legacy — imported by `routes/schedules.py`, `plan_pipeline.py` (stage2), `urgent_scheduler.py`, and even `cp_sat_optimizer.py` itself. Two solvers coexist by design | Don't delete; split into `services/greedy/` with responsibility-based modules                                     |
| "Dynamic Priority Engine as new coordinator module"                | `constraint_config` + `constraint_config_history` already carry priorities                                                                                                                          | No new subsystem — just a clean `constraint_loader` + `trace_writer`                                              |
| "Sub-Agent A Full Frontend refactor"                               | Unclear what specifically breaks on FE                                                                                                                                                              | Scope FE work to surfaces consuming new endpoints (admin UI, XAI popover)                                         |

### What was missing from the prompt and we added

- **Phase 0 pre-flight**: pg_dump schema + ConstraintConfig JSON sample committed before any code change.
- **Formal parity harness** with 10 golden-input fixtures, strict hash equality, intentional-change discipline.
- **Multi-baseline constraint versioning** (promotable during pilot, not immutable).
- **Operator comment field** at manual-override time — distinguishes "what changed" (snapshot_before/after) from "why changed" (semantic).
- **Run-ID correlation logging** across solver → API → UI (Observability).
- **Performance baseline** captured during Week 1 parity freeze for regression detection.
- **LLM provider abstraction** with a `TemplateProvider` fallback so narrator never hard-fails.

### In-scope god-files

| File                                               | Lines | Priority | Target split                                             |
| -------------------------------------------------- | ----- | -------- | -------------------------------------------------------- |
| `services/cp_sat_optimizer.py`                     | 2,654 | P0       | `services/solver/` (5 modules)                           |
| `app/(main)/scheduler/page.tsx`                    | 2,719 | P0       | hooks + sub-components                                   |
| `features/scheduler/components/GanttTaskBlock.tsx` | 1,056 | P0       | extract XAI popover component                            |
| `services/schedule_optimizer.py`                   | 2,870 | P1       | `services/greedy/` (3 modules)                           |
| `presentation/routes/plan_pipeline.py`             | 2,660 | P1       | `services/pipeline/` (4 modules)                         |
| `presentation/routes/schedules.py`                 | 1,702 | P1       | `routes/schedules/` sub-package                          |
| `services/batch_grouping.py`                       | 1,971 | P1       | `services/batch_grouping/` (5 modules) + dead-code purge |
| `features/scheduler/components/SchedulerView.tsx`  | 1,468 | P1       | diff-overlay + gantt-grid + task-row components          |
| `features/scheduler/store/scheduleStore.ts`        | 1,291 | P1       | slice-per-concern (orders, batches, diff, filters)       |

### Explicitly deferred to post-pilot backlog (P2)

- `app/(main)/plan-register/page.tsx` (1,910) — stable, rare edits
- `features/scheduling-review/components/ProductionBatchTable.tsx` (1,008)
- `app/(main)/scheduling-review/page.tsx` (1,013)
- Backup/DR procedure, on-call playbook, secrets rotation (Vault), PII anonymization, Grafana dashboards, RBAC

---

## 4. Approach — Parallel Strangler-Fig (2 months, 2 tracks)

```
Week 0  Phase 0 pre-flight (schema dump, baseline tag)
Week 1  Parity harness freeze (both tracks meet here)
Weeks 2–7  Two long-lived branches run in parallel:
           - Track A: solver / backend split, trace writer
           - Track B: admin UI, XAI popover, LLM binding, migrations
         Weekly merge to main, gated by 6 non-negotiable checks
Week 8  KBI dry-run + buffer + handoff packet
```

Rationale: parallel tracks prevent UX-work stalling while solver refactor churns. Strangler-fig (new modules live next to old; old deleted when parity confirms) prevents big-bang risk. Parity gate enables any-week rollback.

---

## 5. Design Section 1 — Trace Schema (XAI source-of-truth)

### Two new tables

**`solver_run`** — one row per `cp_sat_schedule()` invocation.

| Column                      | Type                                  | Purpose                                           |
| --------------------------- | ------------------------------------- | ------------------------------------------------- |
| `run_id`                    | UUID PK                               | also the correlation id in logs                   |
| `run_label`                 | str                                   | joins to existing `production_batch.run_label`    |
| `started_at`, `finished_at` | ts                                    | pilot latency observability                       |
| `solver_status`             | str                                   | OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN         |
| `objective_value`           | bigint                                |                                                   |
| `constraint_config_version` | UUID FK → `constraint_config_history` | the audit-critical field: which config was active |
| `input_hash`                | str (sha256)                          | parity harness compares                           |
| `output_hash`               | str (sha256)                          | parity harness compares                           |
| `solver_params_json`        | JSONB                                 | CP-SAT flags (max_time, workers, seeds)           |

**`solver_decision`** — one row per scheduled batch per run.

| Column                           | Type                           | Purpose                                                                  |
| -------------------------------- | ------------------------------ | ------------------------------------------------------------------------ |
| `decision_id`                    | UUID PK                        |                                                                          |
| `run_id`                         | FK → solver_run                |                                                                          |
| `production_batch_id`            | FK → production_batch          |                                                                          |
| `assigned_equipment_id`          | FK → equipment_master          |                                                                          |
| `assigned_start`, `assigned_end` | ts                             |                                                                          |
| `contributions_json`             | JSONB                          | trace body: `[{constraint_id, weight_applied, bound, delta_if_removed}]` |
| `binding_hard_constraints_json`  | JSONB                          | hard constraints that forced this placement                              |
| `alternative_slots_json`         | JSONB, nullable                | **on-demand only** — populated when operator clicks "왜 여기가 아닌가?"  |
| `is_manually_adjusted`           | bool, default false            | true when operator edits via UI                                          |
| `manual_override_change_set_id`  | UUID FK → change_set, nullable | links to existing audit row (no duplicate snapshot data)                 |
| `llm_summary_text`               | text, nullable                 | cached narrator output                                                   |

### Contract with LLM narrator

The narrator's **only** input is `contributions_json`. It cannot reference a constraint not in the list. Post-response filter rejects any Korean noun outside the catalog + allow-list, falling back to template.

### What this schema does NOT do (YAGNI)

- No new "priority coordinator" class.
- No pre-computed alternatives on every run (cost: 2× solver time avoided).
- No 3-tier ontology.

### Write path

```
cp_sat_schedule(inputs) inside services/solver/__init__.py:
  1. specs = constraint_loader.load_active(db)
  2. model, penalty_vars = model_builder.build(inputs, specs)
  3. objective.attach(model, penalty_vars, specs)
  4. status = cp_solver.Solve(model)
  5. trace_writer.write(solver, penalty_vars, specs, run_metadata, db)
  6. return {solver_status, objective_value, assignments, run_id}
```

---

## 6. Design Section 2 — Parity Harness

### Golden input set — 10 fixtures at `backend/tests/fixtures/parity/`

| #   | Scenario                                 | Purpose                         |
| --- | ---------------------------------------- | ------------------------------- |
| 01  | Nominal monthly plan                     | base case                       |
| 02  | Past-due skew (mixed past-due + on-time) | EDD / severity regressions      |
| 03  | Urgent reschedule trigger                | `urgent_scheduler` path         |
| 04  | WIP match-and-skip                       | `wip_matching` path             |
| 05  | Sheath color chain                       | adjacency optimization          |
| 06  | Stage1 → Stage2 handoff                  | two-stage pipeline              |
| 07  | Calendar edge (Fri/Mon/holiday)          | `calendar_engine`               |
| 08  | Capacity overflow (FEASIBLE ≠ OPTIMAL)   | non-optimal status still parity |
| 09  | Single-batch degenerate                  | tiny-input bug catcher          |
| 10  | All-constraints-on vs all-off            | "config not read" detector      |

### Contract

- **Primary gate**: `output_hash = sha256(sorted([(batch_id, equipment_id, start_iso, end_iso)]))` — exact equality required.
- **Determinism pins**: `num_search_workers=1`, fixed seed. Parity runs slower than production, accepted trade.
- **No tolerance mode**: if hash flips, the developer updates fixture in a _separate commit_ with rationale. CPA "explain every move" discipline.

### Auditor's Trail diff log

```
❌ Parity Violation in Scenario #5 (Sheath Color Chain)
────────────────────────────────────────────────────────
  fixture          : tests/fixtures/parity/05_sheath_color_chain.json
  expected hash    : sha256:4a2f... (frozen 2026-04-30 on cb3ce08)
  actual hash      : sha256:9b81...
  objective        : expected=142500 actual=141900 Δ=-600
  solver_status    : expected=OPTIMAL actual=OPTIMAL
────────────────────────────────────────────────────────
  Assignment diff (3 batches moved):
    MOVED  B042  eq_05@Mon 08:00 → eq_03@Mon 14:00
    MOVED  B044  eq_03@Mon 14:00 → eq_05@Mon 08:00
    MOVED  B071  eq_05@Mon 15-08:00 → eq_05@Mon 15-16:00
  No batches added or removed.
────────────────────────────────────────────────────────
  Constraint contribution delta (Phase 2; available after Week 2):
    c_edd_weight  expected=48000 actual=47400 Δ=-600 ← likely cause
  Action: If intentional, update expected_hash in SEPARATE commit with rationale.
```

### Performance baseline (captured Week 1 at zero additional cost)

- Parity run records `wall_clock_sec` per fixture × 5 repetitions → p50/p95.
- Written to `tests/fixtures/parity/baseline_performance.json`.
- CI rule: `> 1.5× baseline_p95` → warn; `> 3× baseline_p95` → block.

### Deliverables (Week 1)

1. 10 fixture JSONs (committed with frozen hashes)
2. `backend/tests/test_parity_harness.py` (pytest `-m parity`)
3. `scripts/parity_freeze_current_behavior.py`
4. `make parity-quick` local target (fixtures #01 + #10, ~60s)
5. CI workflow step: `pytest -m parity` on every PR
6. Baseline performance JSON
7. `docs/parity-harness.md` (how to intentionally update a fixture)

---

## 7. Design Section 3 — Constraint-Engine Module Boundaries

### `services/solver/` package

```
services/solver/
├── __init__.py          # re-exports cp_sat_schedule() — backwards compat
├── constraint_loader.py # DB → list[ConstraintSpec] — only SQLAlchemy touch
├── model_builder.py     # (inputs, specs) → (cp_model, penalty_vars_dict)
├── objective.py         # composes penalty_vars into minimized objective
├── solver_io.py         # input normalization + hashes + result extraction
└── trace_writer.py      # writes solver_run + solver_decision rows
```

### `services/greedy/` package (from `schedule_optimizer.py`)

```
services/greedy/
├── __init__.py          # re-exports auto_schedule, reschedule_affected_groups, PREDECESSOR_PROCESS
├── auto_schedule.py     # main greedy loop (stage2 caller)
├── reschedule_affected.py  # urgent cascade handler
└── slot_finder.py       # _find_available_slot, _find_eligible_equipment, _get_stranding_setup_min
```

Shared helpers → `domain/constants.py`.

### `ConstraintSpec` value object (typed boundary)

```python
@dataclass(frozen=True)
class ConstraintSpec:
    constraint_id: str
    name: str
    category: str                    # free text from DB
    is_enabled: bool
    weight: int                      # ConstraintConfig.priority
    impact_level: Literal["hard", "soft"]
    params: dict
    applicable_processes: list[str]
    implementation_type: Literal["solver_term", "pre_filter", "post_filter"]
```

**`implementation_type` rationale**:

- `pre_filter` — reduces CP-SAT variable count before solve (performance lever)
- `solver_term` — real optimization penalty
- `post_filter` — post-solve business-rule validation

### `services/batch_grouping/` package (from `batch_grouping.py`, 1,971 lines)

```
services/batch_grouping/
├── __init__.py
├── header_grouper.py      # sheath header aggregation
├── sub_batch_splitter.py  # overload splits + shortage batch
├── lifecycle.py           # version copy, parent_run_label, frozen warnings
├── dedup.py               # header / sub-batch post-hoc cleanup
└── sort_policy.py         # cluster sort keys
```

**Dead-code purge during split**: `vulture` + `ruff --select F401,F841` run before each sub-module commit. Flagged items → `docs/deletion-log.md`.

### Invariant: solver never imports SQLAlchemy

CI rule: in `services/solver/`, grep for `from app.infrastructure` returns 0 hits outside `constraint_loader.py`. Boundary enforced as a unit test.

---

## 8. Design Section 4 — Admin UI + XAI Popover + LLM Narrator

### 8a. Constraint Admin UI — `/admin/constraints`

- samildevkit design system (`pwc-design` skill + `verify-pwc-design` to confirm)
- Table + side-drawer edit pattern
- Filters: category multi-select, process multi-select, enabled-only toggle
- Row actions: edit, reset-to-baseline (any listed baseline)
- Footer actions: "Reset all to baseline", "Promote current as new baseline", "View version diff", "Export as JSON"

**Backend validation**:

- `impact_level=hard` constraints cannot be disabled
- `priority` ∈ [0, 100]
- Every save writes a new `constraint_config_history` row

### 8b. Multi-baseline versioning

`constraint_config_history` schema additions (Week 2 migration):

| Column                | Type                |
| --------------------- | ------------------- |
| `is_baseline`         | bool, default false |
| `baseline_tag_name`   | str, nullable       |
| `baseline_created_by` | str, nullable       |
| `baseline_created_at` | ts, nullable        |

"Promote current as new baseline" → snapshots current `constraint_config` rows into history with `is_baseline=true` + operator-supplied tag name. Reset dropdown lists all baselines chronologically.

### 8c. XAI popover on `GanttTaskBlock`

Click a batch → popover:

```
┌─ 왜 이 배치는 여기에 배정되었나? ─────────────────────┐
│  [LLM 요약]                                           │
│  이 배치는 10일 지연 납기를 회피하기 위해,            │
│  선행 컬러군(파랑)과 인접 배정되었습니다.             │
│  ───  상세 가중치 (펼치기 ▾)  ───                    │
│  납기 준수       48,000  [binding]                    │
│  컬러 인접성      8,500                               │
│  셋업 시간        1,200                               │
│  결정 근거 버전: v2026-05-20-14:30                    │
│  [다른 슬롯 검토]  [버전 비교]                         │
└───────────────────────────────────────────────────────┘
```

**If `is_manually_adjusted=true`** — explicit UI branch (the trace is no longer valid for this batch):

```
┌─ 이 배치는 수동으로 조정되었습니다 ──────────────────────┐
│  수동 조정되어 솔버 가중치 분석은 제공되지 않습니다.    │
│                                                         │
│  조정자:    오퍼레이터 @ 2026-05-20 14:32               │
│  조정 사유: 현장 긴급 · "2호기 고장으로 이동"           │
│                                                         │
│  ─── 원래 솔버의 결정 (참조용) ───                      │
│  eq_02 @ 2026-05-20 08:00 ~ 12:00                       │
│  (이 결정에 대한 가중치 분석 보기 ▸)                     │
└─────────────────────────────────────────────────────────┘
```

**Rationale**: Once a human overrides, the `contributions_json` describes why the solver _wanted_ something other than what's now on the gantt. Showing the solver's weights on a manually-moved batch is misleading at best, hallucinatory at worst. The explicit branch tells the truth: "this is a human decision; here's what the solver would have done, if you want to compare." Solver's original weights are **one click away**, not hidden — for audit — but **not the default view**.

Trace data for the manually-moved batch is **preserved unchanged** in `solver_decision` — only the UI rendering differs. Audit reports can still query both views.

### 8d. Operator comment modal (new; at manual-override time)

When operator drag-drops a batch:

```
┌─ 변경 사유 기록 ───────────────────┐
│  Preset chips:                     │
│    [납기 변경] [현장 긴급]          │
│    [설비 고장] [WIP 변동] [기타]   │
│  Free text (선택):  [____________] │
│         [건너뛰기]  [저장]          │
└────────────────────────────────────┘
```

Writes to `change_set.override_reason` (new column).

### 8e. LLM narrator grounding contract

```python
class LLMExplainerInput(BaseModel):
    production_batch_id: str
    assigned_equipment_name: str
    assigned_start: datetime
    contributions: list[ContributionItem]
    binding_hard_constraints: list[str]
    constraint_catalog: dict[str, ConstraintDisplayInfo]  # id → korean_name
```

**Template (locked)**:

> 당신은 공장 스케줄러 결과 설명자입니다. 다음 '기여 목록'에 명시된 제약조건만 언급하세요. 기여 목록에 없는 제약조건은 절대 언급하지 마세요. 한국어로 한 문장, 40자 이내.

**Post-response validation**: extract Korean nouns from output → every noun must be in `constraint_catalog` + generic-word allow-list → else reject, fall back to template.

**Provider abstraction**:

```python
class LLMProvider(Protocol):
    def explain(self, input: LLMExplainerInput) -> str: ...
```

Implementations: `AnthropicProvider`, `OpenAIProvider`, `TemplateProvider` (always-available). Selected via `LLM_PROVIDER` env. Future `PwCGatewayProvider` drops in without caller changes.

**Cost control**: LLM called once per decision at insert-time; result cached in `solver_decision.llm_summary_text`. Re-render only on explicit refresh.

### 8f. New / modified API endpoints

| Method                                           | Path                            | Status  |
| ------------------------------------------------ | ------------------------------- | ------- |
| GET `/api/constraints`                           | List active                     | Exists  |
| PATCH `/api/constraints/{id}`                    | Edit one                        | **New** |
| POST `/api/constraints/reset-to-baseline`        | Reset all to a baseline         | **New** |
| POST `/api/constraints/{id}/reset-to-baseline`   | Reset one                       | **New** |
| POST `/api/constraints/promote-baseline`         | Promote current as new baseline | **New** |
| GET `/api/constraints/versions`                  | List baselines + history        | **New** |
| GET `/api/constraints/versions/{a}/diff/{b}`     | Version diff                    | **New** |
| GET `/api/decisions/{batch_id}/latest`           | Trace for XAI popover           | **New** |
| POST `/api/decisions/{decision_id}/alternatives` | Async side-solve                | **New** |

---

## 9. Design Section 5 — 8-Week Delivery Plan + Worktrees

### 8-week schedule

| Week | Track A (backend/solver)                                                                                                                                                         | Track B (frontend + migrations)                                                                                      | Close gate                                  |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| 0    | Phase 0: `pg_dump --schema-only → docs/archive/schema_asis_20260423.sql`; `ConstraintConfig` rows → JSON; tag `baseline=Phase 0 initial`. `git worktree` setup.                  | —                                                                                                                    | Baseline rows + artifacts committed         |
| 1    | Parity harness: 10 fixtures, freeze script, pytest, CI, `make parity-quick`, performance baseline                                                                                | —                                                                                                                    | Parity green; hashes + perf committed       |
| 2    | `services/solver/` split (P0): constraint_loader, model_builder, objective, solver_io, trace_writer. Run-ID LoggerAdapter + middleware. `cp_sat_optimizer.py` → re-export shell. | Alembic: `solver_run`, `solver_decision`, `change_set.override_reason`, `constraint_config_history` baseline columns | Parity green; new tables populating         |
| 3    | `services/greedy/` split (P1): auto_schedule, reschedule_affected, slot_finder. Shared → `domain/constants.py`                                                                   | Admin UI shell (samildevkit): route, table, side drawer, version-diff component (read-only first)                    | Parity green; admin UI staging read-only    |
| 4    | `plan_pipeline.py` split (P1) → `services/pipeline/` (orchestrator, stage1, stage2, run_labeler)                                                                                 | XAI popover on `GanttTaskBlock` (P0) + LLM narrator binding + `LLMProvider` abstraction + UI run_id error display    | Parity green; click-batch → trace + summary |
| 5    | `schedules.py` route split (P1) → `routes/schedules/` sub-package (list, detail, bulk_update, cascade, revert)                                                                   | Operator comment modal + `change_set.override_reason` wiring + "Promote as new baseline" button + baseline dropdown  | Parity green; override writes reason        |
| 6    | `batch_grouping.py` split (P1) + dead-code purge (`vulture`, `ruff`) → `services/batch_grouping/`                                                                                | `SchedulerView.tsx` split (P1) → diff-overlay, gantt-grid, task-row                                                  | Parity green; deletion-log.md started       |
| 7    | Backend stabilization, docs for Track A modules                                                                                                                                  | `scheduler/page.tsx` split (P0) + `scheduleStore.ts` slices (P1) + KBI dry-run prep                                  | Parity green; KBI dry-run scheduled         |
| 8    | Buffer + fix friction-log items from KBI dry-run                                                                                                                                 | Same; handoff packet finalization                                                                                    | `v1.0-pilot` tag; release notes             |

### Non-negotiable per-week gates

1. Parity harness green
2. 69+ unit tests green
3. Playwright E2E smoke green
4. Frontend lint + typecheck clean
5. Backend ruff + mypy clean (if configured; flag Week 1 if not)
6. `worktree_status.sh` shows zero uncommitted changes across all worktrees at end of week
7. **Migration reversibility**: every Alembic migration touching existing tables must pass `upgrade → downgrade → upgrade` round-trip on a copy of production-shaped data. Automated via `scripts/test_migration_reversibility.sh` (new, Week 2)

### Migration safety protocol (Week 2 — Track B)

`solver_run` and `solver_decision` are **new tables** → additive migrations, low risk.

**Migrations touching existing tables require extra discipline:**

| Migration                                                                                                                                                                                                             | Risk                                | Mitigation                                                  |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------- |
| `ALTER TABLE change_set ADD COLUMN override_reason TEXT NULL`                                                                                                                                                         | Low (additive, nullable)            | Down-migration: `DROP COLUMN`; round-trip test required     |
| `ALTER TABLE constraint_config_history ADD COLUMN is_baseline BOOL DEFAULT FALSE, ADD COLUMN baseline_tag_name VARCHAR(100), ADD COLUMN baseline_created_by VARCHAR(100), ADD COLUMN baseline_created_at TIMESTAMPTZ` | Low (additive, nullable + defaults) | Down-migration: `DROP COLUMN` × 4; round-trip test required |

**Per-migration requirements**:

1. `down_revision` is explicit, tested.
2. `downgrade()` is written, not `pass`.
3. Round-trip script: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` on a dev database loaded with the Phase 0 snapshot.
4. If the migration touches data (not just schema), a data-loss warning is printed on downgrade with explicit confirmation.

**Break-glass recovery**: `docs/archive/schema_asis_20260423.sql` + `constraint_config_sample_20260423.json` from Phase 0 are the **authoritative pre-refactor state**. If a migration corrupts prod during pilot, restoration path is: `pg_restore` schema from archive → reload ConstraintConfig rows from JSON → replay committed change_sets since Phase 0.

### Git worktree 200% plan

**Layout**:

```
~/Desktop/Project/
├── KBI_PoC/               # main — reference only, no commits
├── KBI_PoC_track_a/       # refactoring/track-a-solver
├── KBI_PoC_track_b/       # refactoring/track-b-admin
└── KBI_PoC_parity/        # main — parity-runner; keeps dev free
```

**Setup**:

```bash
cd ~/Desktop/Project/KBI_PoC
git worktree add ../KBI_PoC_track_a -b refactoring/track-a-solver
git worktree add ../KBI_PoC_track_b -b refactoring/track-b-admin
git worktree add ../KBI_PoC_parity main
```

**Docker separation**:
| Worktree | `COMPOSE_PROJECT_NAME` | Backend port | Frontend port |
|---|---|---|---|
| main | `kbi_main` | 8000 | 3000 |
| track_a | `kbi_track_a` | 8001 | 3001 |
| track_b | `kbi_track_b` | 8002 | 3002 |
| parity | `kbi_parity` | 8010 | — |

Each worktree has `.env.worktree` setting the project name + ports; `docker-compose.yml` consumes via env.

**Ownership map (conflict prevention)**:
| Path | Owning worktree |
|---|---|
| `backend/app/services/solver/`, `greedy/`, `pipeline/`, `batch_grouping/` | Track A |
| `backend/app/presentation/routes/schedules*` | Track A |
| `backend/app/presentation/routes/constraints.py`, `decisions.py` | Track B |
| `backend/alembic/versions/*` | Track B (ALL migrations) |
| `backend/app/infrastructure/models/*` | Track B |
| `backend/app/services/llm_explainer.py` | Track B |
| `backend/tests/fixtures/parity/`, `test_parity_harness.py` | Track A |
| `frontend/**` | Track B |
| `docs/**` | feature-shipping worktree |

**The shared interface**: `ConstraintSpec` (in `services/solver/constraint_loader.py`, Track A). Track B writes the SQLAlchemy model; Track A consumes via loader. Enforced by CI rule: Track A `services/solver/` may not import `app.infrastructure` outside `constraint_loader.py`.

**Weekly merge protocol (Fridays)**:

1. `KBI_PoC_parity` runs full parity against each branch independently.
2. If both green: Track A merges to main first (solver is upstream dependency).
3. Track B rebases onto updated main, re-runs parity, merges.
4. All worktrees `git pull` main.

**Daily status**: `scripts/worktree_status.sh` — shows branch, last commit, git status for all worktrees in one command.

---

## 10. Design Section 6 — Observability + Performance Baseline

### 10a. Run-ID correlation

```
backend/app/infrastructure/logging/
├── run_context.py       # contextvars.ContextVar for run_id
└── adapters.py          # LoggerAdapter injecting "[run_id=...]" prefix
```

- `run_id` is the UUID of `solver_run` row (or a request-scoped UUID for non-solver calls).
- Set in `cp_sat_schedule()` entrypoint; propagates via contextvars to all downstream.
- Log format: `[run_id=abc123] [component=solver.model_builder] message`
- FastAPI middleware: every response gets `X-Run-Id` header.
- Frontend: `apiFetch` wrapper captures header; error toast shows `run_id` with copy-to-clipboard button.

### 10b. Performance baseline (Week 1 capture)

Written to `tests/fixtures/parity/baseline_performance.json`:

```json
{
  "frozen_at": "2026-04-30T14:00:00Z",
  "git_sha": "<current main>",
  "hardware": "docker compose / kbi_parity",
  "fixtures": {
    "01_nominal": { "p50_sec": 8.2, "p95_sec": 9.1 }
  }
}
```

Each fixture run 5× for p50/p95. CI rule: `> 1.5× p95` warns in PR; `> 3× p95` blocks merge.

---

## 11. Post-pilot backlog (out-of-scope for these 8 weeks)

| Item                                                                  | Deferred rationale                        |
| --------------------------------------------------------------------- | ----------------------------------------- |
| Backup / DR procedure                                                 | KBI IT owns their Postgres                |
| On-call / incident playbook                                           | KBI organizational decision post-pilot    |
| Secrets rotation (Vault / KMS)                                        | Env vars acceptable for single-user pilot |
| PII / customer-name anonymization                                     | Needs legal review; not pilot-blocking    |
| Metrics dashboard (Grafana / Datadog)                                 | Observability logs sufficient for pilot   |
| RBAC (multi-user admin UI)                                            | Single editor during pilot                |
| P2 god-files (plan-register, scheduling-review, ProductionBatchTable) | Stable; low regression risk               |

---

## 12. Risks & mitigations

| Risk                                                                                | Likelihood  | Mitigation                                                                                                                                                        |
| ----------------------------------------------------------------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Parity hash flips for reasons we can't explain                                      | Medium      | Week 1 `num_search_workers=1` + fixed seed pins determinism; any flip forces separate-commit rationale                                                            |
| LLM provider rate-limit or outage during pilot                                      | Medium      | `TemplateProvider` fallback is always available; popover shows fallback badge                                                                                     |
| Dead code in `batch_grouping.py` turns out to be called via reflection              | Low         | `vulture` + `ruff` flagging; every deletion committed separately; revert trivial                                                                                  |
| Track A / Track B merge conflicts on `constraint_config.py`                         | Low         | Ownership map assigns model to Track B; Track A consumes via typed loader                                                                                         |
| KBI dry-run (Week 7) reveals operator-UX blocker                                    | Medium-High | Week 8 buffer dedicated to friction-log items; P1 items slip to post-pilot if buffer full                                                                         |
| Runtime regression post-refactor                                                    | Medium      | Performance baseline + CI gate catches at PR time                                                                                                                 |
| `schedule_optimizer.py` greedy path has uncovered edge cases                        | Medium      | Parity fixtures #03 (urgent), #06 (stage2 handoff) specifically exercise greedy                                                                                   |
| Alembic migration corrupts `change_set` or `constraint_config_history` during pilot | Low-Medium  | Every migration touching existing tables requires `upgrade→downgrade→upgrade` round-trip gate (Week 2). Phase 0 archive is break-glass recovery source.           |
| Manual-override UI shows invalid weight breakdown, misleading operator              | Low         | Section 8c explicit branch: `is_manually_adjusted=true` hides weights, shows override reason + original solver decision on demand. Trace row preserved for audit. |

---

## 13. Open questions for implementation phase

1. Does the project have an existing CI (GitHub Actions, other)? If not, Week 1 includes CI setup in the harness deliverable.
2. Is `mypy` / `ruff` already configured? If not, Week 1 or Week 2 adds baseline config.
3. Does `constraint_checker.py` (706 lines) do only validation, or also build constraints? If the latter, split boundary adjusts in Week 2.
4. `batch_grouping.py` split boundaries are a best-guess; confirm during Week 6 after reading the file end-to-end.
5. Pilot hardware / production deployment target: where does Docker compose actually run? Determines the "hardware" field in performance baseline and informs Week 8 handoff runbook.

---

## 14. Deliverables (end of Week 8)

1. `docs/archive/schema_asis_20260423.sql` — pre-refactor DDL
2. `docs/archive/constraint_config_sample_20260423.json` — baseline data
3. `docs/architecture-as-is-to-be.md` — module diagram before/after
4. `docs/api-spec.md` — regenerated OpenAPI
5. `docs/operator-runbook.md` — edit constraint, read trace, reset baseline
6. `docs/constraint-catalog.md` — every constraint's id/name/category/default/rationale
7. `docs/llm-prompt-inventory.md` — all templates for audit
8. `docs/parity-harness.md` — fixture inventory + intentional-update procedure
9. `docs/deletion-log.md` — every file deleted with commit hash + reason
10. `docs/worktree-playbook.md` — how the worktree layout works, for successor engineers
11. `v1.0-pilot` git tag on main
12. Pilot-ready image: `docker compose up` produces a working system on any KBI-local environment

---

## 15. Explicit non-goals

- Rewriting the CP-SAT algorithm or greedy algorithm. Both freeze under parity.
- Adding constraint types during the 8 weeks. Only reorganizing what exists.
- FE redesign beyond admin UI + XAI popover + operator modal. Visual language stays.
- Multi-tenancy, auth, RBAC. Single-user pilot.
- Rewriting LLM explainer semantics — only the input contract and provider layer.

---

## 16. Change log

| Date       | Change                                                                                          | Author              |
| ---------- | ----------------------------------------------------------------------------------------------- | ------------------- |
| 2026-04-23 | Initial design approved through brainstorming session (6 design sections, 3 revisions)          | jaewoo kim / Claude |
| 2026-04-23 | Rev 1: added manual-override UI branch (8c), migration reversibility gate (9), 2 new risks (12) | jaewoo kim / Claude |
