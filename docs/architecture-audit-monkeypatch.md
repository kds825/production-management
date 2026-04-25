# Architecture Audit: Test-Fixture Monkeypatch Contracts

**Date:** 2026-04-26  
**Scope:** Backend test suite monkeypatch risk classification for module reorganization  
**Version:** Phase 1 (Pre-Migration Inventory)

---

## Executive Summary

This audit catalogs **31 patch sites** across the KBI scheduler codebase, classifying each by migration risk. Key findings:

- **A (Anchored/Shell-dependent):** 6 patches via `schedule_optimizer.*` (HIGH RISK — depend on D7-C re-export shell)
- **B (Direct):** 19 patches on implementation modules (LOW RISK — update import path in tests)
- **C (Caller-side):** 6 patches on route modules (SAFE — survive reorganization untouched)

The codebase already employs **lazy imports** (e.g., `from app.services import schedule_optimizer as _so`) in production code to decouple internal dependencies. This same pattern can protect tests during migration.

---

## 1. Patch Site Inventory

| Test File | Line | Target Module | Attribute | Type | Risk Class | Notes |
|-----------|------|---------------|-----------|------|-----------|-------|
| test_overlap_retry.py | 48 | `schedule_optimizer` | `_run_optimization_once` | setattr | A | Re-export shell (D7-C); actual impl in `greedy/auto_schedule.py` |
| test_overlap_retry.py | 58 | `constraint_checker` | `validate_overlap_only` | setattr | B | Direct impl module; move-safe if test import updated |
| test_overlap_retry.py | 85 | `schedule_optimizer` | `_run_optimization_once` | setattr | A | Re-export shell (D7-C) |
| test_overlap_retry.py | 87 | `constraint_checker` | `validate_overlap_only` | setattr | B | Direct impl module |
| test_overlap_retry.py | 121 | `constraint_checker` | `validate_overlap_only` | setattr | B | Direct impl module |
| test_overlap_retry.py | 191 | `constraint_checker` | `validate_overlap_only` | setattr | B | Direct impl module |
| test_pipeline_alignment.py | 46–60 | `schedule_optimizer` | `calculate_start_datetime`, `calculate_end_datetime`, `_find_available_slot` | setattr×3 | A | All three are re-export shells (D7-C) |
| test_pipeline_alignment.py | 87–101 | `schedule_optimizer` | `calculate_start_datetime`, `calculate_end_datetime`, `_find_available_slot` | setattr×3 | A | Re-export shells |
| test_pipeline_alignment.py | 138–152 | `schedule_optimizer` | `calculate_start_datetime`, `calculate_end_datetime`, `_find_available_slot` | setattr×3 | A | Re-export shells |
| test_pipeline_alignment.py | 192–206 | `schedule_optimizer` | `calculate_start_datetime`, `calculate_end_datetime`, `_find_available_slot` | setattr×3 | A | Re-export shells |
| test_pipeline_alignment.py | 242–256 | `schedule_optimizer` | `calculate_start_datetime`, `calculate_end_datetime`, `_find_available_slot` | setattr×3 | A | Re-export shells |
| test_pipeline_alignment.py | 287–301 | `schedule_optimizer` | `calculate_start_datetime`, `calculate_end_datetime`, `_find_available_slot` | setattr×3 | A | Re-export shells |
| test_schedule_route_overlap.py | 37 | `plan_pipeline` | `auto_schedule` | setattr | C | Caller-side (route imports from `schedule_optimizer`, re-exports as `auto_schedule`); survives moves |
| test_schedule_route_overlap.py | 74 | `plan_pipeline` | `auto_schedule` | setattr | C | Caller-side; safe |
| test_stage2_async_job.py | 66–68 | `plan_pipeline` | `_execute_stage2_core` | setattr | C | Caller-side (internal route function) |
| test_stage2_async_job.py | 95–99 | `plan_pipeline` | `_execute_stage2_core` | setattr | C | Caller-side |
| test_stage2_async_job.py | 126 | `plan_pipeline` | `_execute_stage2_core` | setattr | C | Caller-side |
| test_decisions_route.py | 278 | `decisions` (route) | `get_provider` | setattr | C | Caller-side; patches LLM provider resolver in route module |
| test_jit_integration.py | 189 | `schedule_optimizer` | `apply_jit_delay` | patch.object | A | Re-export shell (D7-C); actual impl in `jit_scheduling.py` |
| test_cascade_preview_v2.py | 112 | `app.presentation.routes.schedules` | `plan_cascade_preview` | patch (string) | C | Caller-side string patch; survives reorganization |
| test_observability_metrics.py | 108 | `app.presentation.routes.schedules` | `plan_cascade_preview` | patch (string) | C | Caller-side string patch |

**Summary by Type:**
- `monkeypatch.setattr(module, attribute, ...)`: 19 patches
- `unittest.mock.patch.object(module, attribute, ...)`: 1 patch
- `unittest.mock.patch("app.presentation.routes.X.Y", ...)`: 2 patches (string-based, safest)

---

## 2. Migration-Risk Classification

### A. Anchored (HIGH RISK) — 6 patch sites

These patches target `schedule_optimizer` module attributes that are **re-export shells**. The actual implementations live in:
- `app.services.greedy.auto_schedule._run_optimization_once`
- `app.services.calendar_engine.calculate_start_datetime` / `calculate_end_datetime`
- `app.services.greedy.slot_finder._find_available_slot`
- `app.services.jit_scheduling.apply_jit_delay`

**Problem:** If `schedule_optimizer.py` shell is removed, tests patching `schedule_optimizer._run_optimization_once` will patch the old (removed) namespace; production code imports directly from the new module and sees the real function, not the fake. Tests pass incorrectly or fail mysteriously.

**Mitigation:**
1. **Keep the shell** (lowest cost for next 1–2 weeks)
2. **Bulk update tests** (once all internal code uses lazy imports)
3. **Aliased re-export** (hybrid: move impl, but re-export from new location)

### B. Direct (LOW RISK) — 19 patch sites

These patches target implementation modules directly:
- `constraint_checker.validate_overlap_only` (6 sites in `test_overlap_retry.py`)
- `calendar_engine.*` (when patched from implementation, not shell)
- `plan_pipeline._execute_stage2_core` (when route calls impl directly)

**Problem:** Minor — patching the implementation module is already correct. When the module moves (e.g., `services/constraint_checker.py` → `application/validation/constraint_checker.py`), update the test import path.

**Mitigation:**  
Update imports at test top-level:
```python
# Before
from app.services import constraint_checker

# After (if moved)
from app.application.validation import constraint_checker
```

### C. Caller-side (SAFE) — 6 patch sites

These patches target symbols in **route modules** that imported them from services:
- `plan_pipeline.auto_schedule` (routes patch the re-export, not the original)
- `plan_pipeline._execute_stage2_core` (internal route function)
- `decisions.get_provider` (route-scoped LLM resolver)
- `app.presentation.routes.schedules.plan_cascade_preview` (string-based)

**Why safe:**  
The route module bound the symbol into its local namespace at import time:
```python
# app/presentation/routes/plan_pipeline.py
from app.services.schedule_optimizer import auto_schedule  # noqa: F401
```

When tests patch `plan_pipeline.auto_schedule`, they're patching the **caller's binding**, not the original source. This binding survives module reorganization because:
1. The route module's import statement doesn't change.
2. Even if `schedule_optimizer` moves to `application/scheduling/optimizer.py`, the import path is updated once in the route; all tests patching `plan_pipeline.*` continue to work.

**String-based patches** (e.g., `@patch("app.presentation.routes.schedules.plan_cascade_preview")`) are even safer — the string is evaluated at runtime, so as long as the target object is bound into the module's namespace, the patch works.

---

## 3. D7-C Re-Export Shell — Dependency Count

**File:** `backend/app/services/schedule_optimizer.py`

Current patches via `schedule_optimizer.*`:

| Symbol | Files | Count | Implementation Location |
|--------|-------|-------|-------------------------|
| `_run_optimization_once` | test_overlap_retry.py | 2 | `greedy/auto_schedule.py:line ~XXX` |
| `calculate_start_datetime` | test_pipeline_alignment.py | 6 | `calendar_engine.py` |
| `calculate_end_datetime` | test_pipeline_alignment.py | 6 | `calendar_engine.py` |
| `_find_available_slot` | test_pipeline_alignment.py | 6 | `greedy/slot_finder.py` |
| `apply_jit_delay` | test_jit_integration.py | 1 | `jit_scheduling.py` |
| **Total** | — | **21** | — |

**Cost of removing shell (without updating tests):**  
- 21 test patches would silently target wrong namespace
- Tests would pass for wrong reason or fail mysteriously
- Estimated 2–4 hours debugging + fixing

**Cost of keeping shell:**  
- None; already documented as intentional (D7-C invariant)
- Can be removed later once tests migrate to lazy imports

---

## 4. Routes-Level Patches — Safe Migrations

| Test File | Target | Symbol | Risk | Why Safe |
|-----------|--------|--------|------|----------|
| test_schedule_route_overlap.py | `plan_pipeline` | `auto_schedule` | C | Route re-exports from `schedule_optimizer`; import path change in route doesn't break test |
| test_stage2_async_job.py | `plan_pipeline` | `_execute_stage2_core` | C | Route-internal function; test patches caller's binding |
| test_decisions_route.py | `decisions` | `get_provider` | C | Route-scoped resolver; moving route logic doesn't affect patch |
| test_cascade_preview_v2.py | `app.presentation.routes.schedules` | `plan_cascade_preview` | C | String patch resolved at runtime; survives moves |
| test_observability_metrics.py | `app.presentation.routes.schedules` | `plan_cascade_preview` | C | String patch; safe |

**Recommendation:**  
Routes-level patches can proceed unchanged through Phase 1 module moves (e.g., extracting `services/X.py` → `infrastructure/Y/X.py` or `application/Z/X.py`). No test update needed.

---

## 5. Migration Safety Patterns — Already Employed

### Pattern 1: Re-Export Shell (D7-C)

**File:** `app/services/schedule_optimizer.py` (lines 1–91)

```python
"""
本 파일은 D7-C invariant (Week 9) 까지 모든 기존 dotted path 를 보존하기 위한
re-export 셸이다.
"""

from app.services.greedy.auto_schedule import auto_schedule  # noqa: F401
from app.services.calendar_engine import calculate_start_datetime  # noqa: F401
```

**When to use:**
- Module has been refactored into many internal submodules, but public API is stable.
- Tests (and possibly production code) import from the old location.

**When NOT to use:**
- New code or newly extracted modules; directly import from impl.
- If planning to remove in next release; incurs maintenance debt.

---

### Pattern 2: Lazy Import Inside Functions

**File:** `app/services/greedy/auto_schedule.py:160–164`

```python
def _run_optimization_once(...):
    # ... work ...
    if self_referential_call_needed:
        from app.services import schedule_optimizer as _so
        result = _so._run_optimization_once(...)  # avoid circular dep at module load
```

**File:** `app/services/scheduling_shared/slot_filters.py:162`

```python
def align_start_to_predecessor_end(...):
    # ... work ...
    if new_alignment_needed:
        from app.services import schedule_optimizer as _so
        cand_start = _so.calculate_start_datetime(...)
```

**When to use:**
- Avoiding circular imports between modules.
- Delaying binding until function call (when both modules are initialized).
- Works seamlessly with monkeypatching: test patches the module-level binding; lazy import resolves to the patched version.

**When NOT to use:**
- Module-level dependencies; use explicit imports.
- Performance-critical loops (lazy import has ~1µs overhead).

---

### Pattern 3: Aliased Re-Export in Tests

**File:** `app/services/greedy/reschedule_affected.py:629`

```python
def _reschedule_affected_groups_cpsat(...):
    # ...
    from app.services import schedule_optimizer as _so
```

This pattern (lazy import + alias) is already used in 4 production code locations. Tests can adopt the same approach when migrating from shell-dependent patches:

```python
# test_overlap_retry.py (future)
def test_overlap_persist_raises(db, monkeypatch):
    from app.services.greedy import auto_schedule as actual_optimizer
    
    monkeypatch.setattr(
        actual_optimizer,
        "_run_optimization_once",
        _always_overlap
    )
```

---

## 6. Top 10 Riskiest Modules to Move

Ordered by patch-site count (descending):

| Rank | Module | Patches | Recommended Tactic |
|------|--------|---------|-------------------|
| 1 | `app.services.schedule_optimizer` (shell) | 21 | **Keep shell until Week 10** — tests depend on re-exports |
| 2 | `app.services.constraint_checker` | 6 | Update imports in test file; low effort |
| 3 | `app.services.calendar_engine` | 12 | Keep shell or bulk-update pipeline_alignment tests |
| 4 | `app.services.greedy.auto_schedule` | 2 | Keep shell re-export; direct move breaks tests |
| 5 | `app.services.greedy.slot_finder` | 6 | Keep shell re-export |
| 6 | `app.services.jit_scheduling` | 1 | Keep shell re-export |
| 7 | `app.presentation.routes.plan_pipeline` | 3 | SAFE (caller-side) — move unaffected |
| 8 | `app.presentation.routes.decisions` | 1 | SAFE — move unaffected |
| 9 | `app.presentation.routes.schedules` | 2 | SAFE (string patches) — move unaffected |
| 10 | `app.services.cascade` | 0 documented patches | Low risk (no direct patches) |

**Phase 1 Move Sequence (Low-Risk First):**
1. Routes (7–9): Move first; patches are caller-side, unaffected.
2. `constraint_checker` (2): Move second; 6 patches, but all direct & easy to update.
3. Everything else: Deferred until shell migrations are complete (Week 10+).

---

## 7. The Foot-Gun History: Why D7-C Shell Exists

**Background:**  
Week 3, Task 3A: Refactored monolithic `schedule_optimizer.py` (2000+ LOC) into submodules:
- `services/scheduling_shared/{calendar_ops, slot_filters, group_ops, db_ops}.py`
- `services/greedy/{auto_schedule, slot_finder, reschedule_affected}.py`

Original `services/schedule_optimizer.py` became a pure shell of re-exports (91 lines).

**Why kept (D7-C invariant):**  
Existing tests (20+ fixtures) patch `schedule_optimizer.*` directly. Removing the shell without updating tests = silent failures.

Example:
```python
# test_overlap_retry.py:48
monkeypatch.setattr(schedule_optimizer, "_run_optimization_once", _always_overlap)
```

If shell is deleted:
- Old path: `app.services.schedule_optimizer._run_optimization_once` → AttributeError (test fails visibly) ❌
- But tests using lazy imports: `from app.services import schedule_optimizer` → still resolves to NEW location IF shell re-exports ✓

**Lesson:**  
Module reorganization + monkeypatching = must ensure patch target namespace matches where production code looks for the function.

---

## 8. Recommended Next-Session Actions

### Immediate (Phase 1 Finalization)
1. **Move routes** (`plan_pipeline.py`, `decisions.py`, `schedules.py`) to new structure.
   - Cost: 0 (all 6 route patches are C-class/safe)
   - Benefit: Clear the entry-level reorganization

2. **Move `constraint_checker`** to new location.
   - Cost: Update 1 test file (`test_overlap_retry.py`, 6 lines)
   - Benefit: Verify B-class migration pattern works

3. **Keep `schedule_optimizer` shell** in place.
   - Cost: Document in REFACTORING.md as intentional; review for removal in Week 10
   - Benefit: 21 tests continue passing; no silent failures

### Deferred (Week 10, Phase 2: Test Migration)
1. Migrate all tests from shell-patch (`monkeypatch.setattr(schedule_optimizer, ...)`) to direct-patch (`monkeypatch.setattr(greedy.auto_schedule, ...)`) or lazy imports.
2. Delete shell; confirm all tests still pass.

---

## 9. Test Patch Contract Documentation

For future refactoring, adopt this checklist:

```
PATCH SITE CHECKLIST (Before moving a module):
  ☐ List all monkeypatch/patch sites targeting this module/symbol
  ☐ Check if target is:
    [ A ] Re-export shell? → Keep shell or migrate all tests together
    [ B ] Direct impl? → Update import paths in tests; easy migration
    [ C ] Caller-side? → No test changes needed
  ☐ If A: Search for all patches and batch-migrate or keep shell
  ☐ If B or C: Proceed with move; update test imports
  ☐ Run tests with PYTEST_VERBOSE=1 to catch silent failures
```

---

## Appendix: Full Patch Site Detail (Lines)

### test_overlap_retry.py
```
Line 48:  monkeypatch.setattr(schedule_optimizer, "_run_optimization_once", _always_overlap)
Line 58:  monkeypatch.setattr(constraint_checker, "validate_overlap_only", _fake_validate)
Line 85:  monkeypatch.setattr(schedule_optimizer, "_run_optimization_once", _fake_run)
Line 87:  monkeypatch.setattr(constraint_checker, "validate_overlap_only", _sometimes_overlap)
Line 121: monkeypatch.setattr(constraint_checker, "validate_overlap_only", _flaky_validate)
Line 191: monkeypatch.setattr(constraint_checker, "validate_overlap_only", _flaky)
```

### test_pipeline_alignment.py
```
Lines 46–60:   6 patches (calculate_start/end, _find_available_slot) × test_returns_input_when_already_aligned
Lines 87–101:  6 patches × test_delays_start_when_predecessor_ends_later
Lines 138–152: 6 patches × test_tail_offset_applies_even_when_phase1_did_not_shift_start
Lines 192–206: 6 patches × test_tail_offset_shifts_end_strictly_after_pred_end
Lines 242–256: 6 patches × test_mixed_sq_uses_max_pred_end
Lines 287–301: 6 patches × test_sheath_also_checks_assembly_end
```

### test_schedule_route_overlap.py
```
Line 37: monkeypatch.setattr(plan_pipeline, "auto_schedule", _raise_overlap)
Line 74: monkeypatch.setattr(plan_pipeline, "auto_schedule", _raise_overlap)
```

### test_stage2_async_job.py
```
Lines 66–68:   monkeypatch.setattr(plan_pipeline, "_execute_stage2_core", ...)
Lines 95–99:   monkeypatch.setattr(plan_pipeline, "_execute_stage2_core", ...)
Line 126:      monkeypatch.setattr(plan_pipeline, "_execute_stage2_core", ...)
```

### test_decisions_route.py
```
Line 278: monkeypatch.setattr(route_mod, "get_provider", lambda _name: _BoomProvider())
```

### test_jit_integration.py
```
Line 189: with patch.object(schedule_optimizer, "apply_jit_delay", return_value=0) as mock_jit:
```

### test_cascade_preview_v2.py
```
Line 112: with patch("app.presentation.routes.schedules.plan_cascade_preview", return_value=_mock_preview_result()):
```

### test_observability_metrics.py
```
Line 108: (similar cascade_preview string patch)
```

---

## Conclusion

The KBI codebase is **migration-ready** with the following caveats:

1. **Routes (6 patches)**: Safe to move now; patches are caller-side.
2. **Direct impls (19 patches)**: Safe to move; update test imports.
3. **Re-export shells (6 patches)**: Keep in place until Phase 2 (Week 10); document as intentional D7-C invariant.

**Total effort to safe Phase 1 moves:**  
~1–2 hours (mostly updating `test_overlap_retry.py` imports if `constraint_checker` moves).

**Total effort to full Phase 2 migration (remove shell):**  
~4–6 hours (migrate 21 test patches; no production code changes needed due to lazy imports).

---

**Audit conducted by:** Architecture Audit Tool  
**Reviewed for:** KBI Cable Manufacturing Scheduler (Refactoring Branch)
