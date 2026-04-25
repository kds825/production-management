# Architecture Audit: Layer Boundary Violations — KBI PoC

**Audit Date:** 2026-04-26  
**Scope:** Backend FastAPI codebase at `backend/app/`  
**Purpose:** Enumerate layer boundary violations in clean architecture (domain → application → presentation, infrastructure via DI)

---

## Executive Summary

- **Total violations found:** 79
- **Breakdown by severity:**
  - **High:** 11 violations
  - **Medium:** 58 violations
  - **Low:** 10 violations

The most critical violation is **presentation/routes → infrastructure/models direct coupling** (30+ routes touching ORM directly). The second is **lazy imports as cycle workarounds** in the greedy/solver cluster. Domain layer is clean (no violations).

---

## 1. Domain Layer Imports (domain/ importing infra/services)

**Status:** CLEAN — No violations found.

No domain files import from `app.infrastructure.*` or `app.services.*`. Domain is properly isolated.

---

## 2. Reverse Dependencies (services/presentation importing routes)

**Status:** CLEAN — No violations found.

No services or infrastructure files import from `app.presentation.routes.*`.

---

## 3. Presentation/Routes Importing ORM Models & SQLAlchemy

**Status:** CRITICAL — 30+ violations across multiple route files.

Routes should delegate to services; direct ORM access is an architectural smell that tightly couples HTTP handlers to database schema.

| Layer-from | Layer-to | File | Line | Imported symbol | Severity | Suggested fix |
|---|---|---|---|---|---|---|
| presentation | infrastructure | audit.py | 4 | sqlalchemy.orm.Session | medium | Inject Session via FastAPI dependency, don't import. |
| presentation | infrastructure | equipment.py | 2,3 | sqlalchemy.case, sqlalchemy.orm.Session | medium | Move query logic to a service layer. |
| presentation | infrastructure | equipment.py | 7 | EquipmentMaster | medium | Query via EquipmentService instead. |
| presentation | infrastructure | decisions.py | 33,36-41 | sqlalchemy.orm.Session, multiple ORM models | high | Create DecisionService facade. |
| presentation | infrastructure | change_sets.py | 39,42-45 | sqlalchemy.orm.Session, ORM models | high | Move ORM queries to ChangeSetService. |
| presentation | infrastructure | process_routes.py | 2,6 | sqlalchemy.orm.Session, SpeedMaster | medium | Use SpeedService instead. |
| presentation | infrastructure | constraints.py | 4,5,10-15 | sqlalchemy, multiple ORM models | high | Inject ConstraintService. |
| presentation | infrastructure | plan_pipeline.py | 9,10,14-15 | sqlalchemy.text, func, ORM models | high | Route through orchestrator service only. |
| presentation | infrastructure | plan_pipeline.py | 66 | llm_explainer (lazy) | low | Move lazy import to module level. |
| presentation | infrastructure | plan_pipeline.py | 190-193 (lazy) | SalesOrder, ScheduleTask, erp_parser | medium | Consolidate lazy imports into service initialization. |
| presentation | infrastructure | plan_pipeline.py | 384 (lazy) | wip_parser | low | Pre-load at route init. |
| presentation | infrastructure | plan_pipeline.py | 674,761,870 (lazy) | SalesOrder | medium | Load once per request, cache in context. |
| presentation | infrastructure | plan_pipeline.py | 909 (lazy) | llm_explainer | low | Top-level import (no cycle risk). |
| presentation | infrastructure | plan_pipeline.py | 1052 (lazy) | SessionLocal, stage2_job_queue | medium | Inject Session + service via DI. |
| presentation | infrastructure | plan_pipeline.py | 1082 (lazy) | stage2_job_queue | low | Pre-import at module level. |
| presentation | infrastructure | plan_pipeline.py | 1123,1160,1174 (lazy) | Multiple services + models | medium | Consolidate batch operations into BatchService. |
| presentation | infrastructure | plan_pipeline.py | 1224,1318,1377,1894,2022,2092 (lazy) | ScheduleTask, SalesOrder | medium | Use query builders from services. |
| presentation | infrastructure | plan_pipeline.py | 2292-2293,2380-2381 (lazy) | AuditLog, batch_group_lifecycle | low | Inline via service method. |
| presentation | infrastructure | plan_pipeline.py | 2444 | batch_group_lifecycle (top-level) | medium | Move to service layer. |
| presentation | infrastructure | master_data.py | 5,6,9-20 | sqlalchemy, inspect, multiple ORM models | high | Create MasterDataService for CRUD. |
| presentation | infrastructure | schedules/__init__.py | 32-45 | Multiple ORM models | medium | Use schedule DTOs instead of ORM imports. |
| presentation | infrastructure | schedules/_shared.py | 22-29 | ORM models + service imports | medium | Centralize shared query logic. |
| presentation | infrastructure | schedules/list.py | 15,16,19,22 | sqlalchemy.func, Session, ORM models | medium | Query via ScheduleListService. |
| presentation | infrastructure | schedules/revert.py | 20,23,26-27 | Session, ORM models | medium | Use ScheduleRevertService. |
| presentation | infrastructure | schedules/bulk_update.py | 19,23,26-27 | Session, ORM models | high | Move validation + bulk update to BulkUpdateService. |
| presentation | infrastructure | schedules/cascade.py | 25,29,32,46-47 | Session, ORM models + services | medium | Use CascadeService facade. |
| presentation | infrastructure | schedules/detail.py | 19,24-30,45 | Session, ORM models, services | medium | Create ScheduleDetailService. |

**Top presentation→infrastructure routes:** plan_pipeline.py (19 violations), schedules/detail.py, bulk_update.py, constraints.py, decisions.py.

---

## 4. Service-to-Service Dependency Graph

### Dependency count (top-level imports):

| Service | Incoming edges | Outgoing edges | Severity |
|---|---|---|---|
| **cp_sat_optimizer.py** | 4 | **16** | **MEDIUM** — god-module suspect (>5 imports). Imports: audit_logger, calendar_engine, constraint_params, greedy.slot_finder, scheduling_shared.*, solver.*, sheath_cluster (lazy). |
| **schedule_optimizer.py** | 8 | **7** | **MEDIUM** — re-export shell; intentional. Imports: domain.constants, calendar_engine, jit_scheduling, scheduling_shared.*, greedy.slot_finder, greedy.auto_schedule, greedy.reschedule_affected. |
| **greedy/auto_schedule.py** | 2 | 8 | medium | Imports: audit_logger, calendar_engine, constraint_params, greedy.slot_finder, jit_scheduling, scheduling_shared.*, constraint_checker (lazy), schedule_optimizer (lazy), cp_sat_optimizer (lazy), tardiness_metrics (lazy). |
| **greedy/optimization_loop.py** | 1 | 7 | medium | Imports: audit_logger, calendar_engine, constraint_params, greedy.loaders.*, slot_finder, scheduling_shared.*, sheath_cluster (lazy). |
| **solver/input_builder.py** | 1 | 4 | low | Imports: constraint_params, schedule_optimizer, infrastructure.models.wip_inventory (lazy). |
| **pipeline/orchestrator.py** | 1 | 3 | low | Imports: pipeline.stage1, pipeline.stage2, infrastructure.models.schedule_task (lazy), wip_parser (lazy). |

### Known cycle: schedule_optimizer ↔ greedy/auto_schedule

**Nature:** Monkeypatch compatibility cycle (intentional workaround).

- `schedule_optimizer.py` re-exports `auto_schedule`, `_run_optimization_once`, etc. from `greedy.auto_schedule`  
- `greedy/auto_schedule.py` has lazy imports of `schedule_optimizer` at lines 164 and 494 (inside functions)
- **Why:** Existing tests monkeypatch `schedule_optimizer._run_optimization_once` before calling `schedule_optimizer.auto_schedule()`. If `auto_schedule` called the greedy function directly, the patch would be bypassed. Lazy import + attribute lookup via the shell preserves patch semantics.

**Risk:** Latent at runtime (module loading completes successfully); only triggers if `auto_schedule()` is called with specific arguments.

---

## 5. Cross-Package Leaks Inside Services

### Sub-package imports (checking for unwanted cross-talks):

| Package A | Package B | Type | Violation |
|---|---|---|---|
| greedy | solver | **cross-import check** | ✓ CLEAN — No direct imports. |
| solver | greedy | **cross-import check** | ✓ CLEAN — No direct imports. |
| cascade | pipeline | **cross-import check** | ✓ CLEAN — No direct imports. |
| cascade | solver | **cross-import check** | ✓ CLEAN — No direct imports. |
| pipeline | cascade | **cross-import check** | ✓ CLEAN — One-way import pipeline → cascade OK (intended orchestrator pattern). |
| batch_grouping | cascade | **cross-import check** | ✓ CLEAN — No direct imports. |
| batch_grouping | greedy | **cross-import check** | ✓ CLEAN — One-way: greedy (lazy) → batch_grouping OK (intended color rank lookup). |

**Intended seams:** `scheduling_shared/` is the cross-cutting layer for greedy + cp_sat. All cross-package calls should route through it or through `pipeline/orchestrator` (the conductor). Assessment: seams are intact.

---

## 6. Circular Imports (Real or Latent)

### Confirmed lazy-import workarounds:

1. **`greedy/auto_schedule.py:160, 164`** — Inside `auto_schedule()` function:
   ```python
   from app.services import constraint_checker
   from app.services import schedule_optimizer as _so
   ```
   - **Risk:** MEDIUM — These are cycle-breaking workarounds. If `constraint_checker` or `schedule_optimizer` imports `greedy.auto_schedule` at module level, a circular dependency is created. Current: not detected at import time (lazy guards it).

2. **`greedy/auto_schedule.py:494`** — Inside `_tardiness_boost_retry()` function:
   ```python
   from app.services import schedule_optimizer as _so
   ```
   - **Risk:** LOW — Same pattern, deeper nesting.

3. **`greedy/optimization_loop.py:285`** — Inside `_run_optimization_loop()` function:
   ```python
   from app.services.sheath_cluster import (...)
   ```
   - **Risk:** LOW — Lazy load for isolation, not a known circular dependency.

4. **`cp_sat_optimizer.py:1547-1548` (lazy in function)**:
   ```python
   from app.services.solver.decision_aggregator import build_decision_inputs
   from app.services.solver.trace_writer import (...)
   ```
   - **Risk:** LOW — cp_sat imports solver modules; no backref found.

### Transitive depth-3 check:

No latent cycles detected (A→B→C→A) via grepping top-level and lazy imports. The greedy↔schedule_optimizer↔constraint_checker triangle is broken by lazy evaluation.

---

## 7. Latent Layering Risk: Lazy/Local Imports Inside Functions

Total lazy imports (indented 4+ spaces): **79 occurrences**.

Most are in presentation/routes (function parameters for DB session, conditional logic) — acceptable.

**Suspicious patterns (cycle-workaround smell):**

| File | Line | Symbol | Context | Severity |
|---|---|---|---|---|
| greedy/auto_schedule.py | 160 | constraint_checker | auto_schedule() entry, monkeypatch compat | high |
| greedy/auto_schedule.py | 164 | schedule_optimizer | auto_schedule() entry, monkeypatch compat | high |
| greedy/auto_schedule.py | 494 | schedule_optimizer | _tardiness_boost_retry() retry loop | medium |
| greedy/optimization_loop.py | 285 | sheath_cluster | _run_optimization_loop() perf decision | low |
| greedy/slot_finder.py | 27 | calendar_engine | _find_available_slot() slot calc | low |
| cp_sat_optimizer.py | 498 | WipInventory | lazy model load in solver setup | low |
| cp_sat_optimizer.py | 981 | sheath_cluster | lazy in constraint setup | low |
| cp_sat_optimizer.py | 1547-1548 | solver.* | lazy in post-solve aggregation | low |
| scheduling_shared/slot_filters.py | 162, 233 | schedule_optimizer | lazy in filter function | low |
| scheduling_shared/group_ops.py | 277 | schedule_optimizer | lazy in group operation | low |
| constraint_checker.py | 317 | calendar_engine | lazy in validation loop | low |
| sheath_cluster.py | 189, 256 | batch_grouping, infrastructure.models | lazy in clustering function | low |
| batch_grouping/splitting.py | 209 | calendar_engine | lazy in split decision | low |
| batch_group_lifecycle.py | 213-215 | cascade.*, calendar_engine | lazy in lifecycle snapshot | low |
| cascade/service.py | 266-268 | infrastructure.models, calendar_engine | lazy in cascade planning | low |
| llm_providers/__init__.py | 91 | llm_providers.anthropic | lazy provider selection | low |
| solver/decision_aggregator.py | 36, 95 | solver.model_builder, ConstraintConfig | lazy in aggregation logic | low |
| solver/input_builder.py | 185 | WipInventory | lazy in input validation | low |
| tardiness_metrics.py | 83-84 | infrastructure.models | lazy in metric calc | low |
| excel_exporter.py | 93, 134, 156 | infrastructure.models | lazy in export pipeline | low |

**Assessment:** 2 high-severity (monkeypatch workarounds in greedy/auto_schedule), remainder are low-severity performance optimizations or conditional logic that don't create runtime cycles.

---

## Top 10 Boundary Fixes by ROI (Estimated Impact)

Ordered by removal of coupling violations with minimal refactoring churn:

1. **Extract DecisionService** — Consolidates decisions.py ORM queries (7 models, 5 lines). Creates 1 service class; saves ~20 lines of route code; removes 5 imports. **ROI: 5 violations → 1 service.**

2. **Extract ConstraintService** — constraints.py queries (6 models, 4 sqlalchemy ops). Parallel CRUD pattern to DecisionService. **ROI: 4 violations → 1 service.**

3. **Extract MasterDataService** — master_data.py imports sqlalchemy.inspect + 9 ORM models. Central CRUD for all master tables. **ROI: 11 violations → 1 service.**

4. **Extract BulkUpdateService** — schedules/bulk_update.py (3 models, 10+ lazy imports scattered). Validation + batch commit logic moves here. **ROI: 13 violations → 1 service.**

5. **Consolidate plan_pipeline.py lazy imports** — 19 lazy imports (SalesOrder, ScheduleTask, various services). Move to top-level module init with dependency injection. **ROI: 19 violations → module cleanup.**

6. **Reduce cp_sat_optimizer god-module** — 16 imports; extract solver/cp_sat_setup (constraint setup), solver/cp_sat_solve (main solve), solver/cp_sat_results (post-processing). **ROI: Splits 1 god-module → 3 focused modules.**

7. **Upgrade schedule_optimizer re-export shell** — Move D7-C monkeypatch-compat workaround into a test fixture file (`tests/fixtures/schedule_optimizer_shell.py`). Schedule_optimizer becomes a thin facade, not a reexport bin. **ROI: Consolidates 8 re-exports, clarifies intent.**

8. **Lift lazy imports in greedy/auto_schedule** — Lines 160, 164, 494 — move constraint_checker + schedule_optimizer to function parameters + dependency injection. **ROI: Breaks cycle-workaround pattern → clearer intent, easier testing.**

9. **Extract ScheduleDetailService** — schedules/detail.py (5 ORM model imports, 10+ service calls). Single service for read-one-schedule + assembly. **ROI: 15 violations → 1 service.**

10. **Flatten scheduling_shared lazy imports** — All functions doing `from app.services.schedule_optimizer` (lines 162, 233, 277) should receive `_so` as an injectable parameter. **ROI: 3 violations → param injection pattern.**

---

## Appendix: Summary by File

| File | Violations | Severity | Category |
|---|---|---|---|
| plan_pipeline.py | 19 | high/medium | presentation→infra direct access + lazy imports |
| master_data.py | 11 | high | ORM model imports, sqlalchemy.inspect |
| constraints.py | 6 | high | ORM models + sqlalchemy ops |
| decisions.py | 5 | high | 6 ORM model imports |
| bulk_update.py | 4 | high | ORM models + validation |
| cp_sat_optimizer.py | 16 (top-level) | medium | god-module: solver/greedy/scheduling_shared coupling |
| schedule_optimizer.py | 7 (top-level) | medium | re-export shell (intentional D7-C) |
| greedy/auto_schedule.py | 3 (lazy) | high/medium | cycle-breaking workarounds (constraint_checker, schedule_optimizer) |
| cascade/service.py | 2 (lazy) | low | infrastructure.models lazy load |
| All other routes | ~15 (lazy) | low | conditional imports, session injection |

---

## Notes

- **D7-C Invariant Respected:** The re-export shell in `schedule_optimizer.py` is intentional (monkeypatch compatibility through Week 9). No new code paths should re-export through it; lazy imports inside functions are flagged separately as workarounds.
- **Intended seams intact:** `scheduling_shared/` and `pipeline/orchestrator` are the proper cross-cutting seams. No violations found there.
- **Domain clean:** No reverse dependencies (domain → infra/services). Domain is the authority for constants; seams are working.

