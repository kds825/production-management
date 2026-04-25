# Architecture Audit: `backend/app/services/` Inventory

**Audit Date**: 2026-04-26  
**Scope**: All 85 `.py` files under `backend/app/services/` (including 7 sub-packages)  
**Total LOC**: 17,910  
**Deliverable**: Structured inventory for refactoring into domain / application / infrastructure / shared / orchestrator layers.

---

## Master Inventory Table

| File | LOC | Public Symbols (top-level functions / classes / dataclasses) | Imports — Layers | Callers (grep count) | Proposed Layer | Flags |
|------|-----|------|------|------|------|------|
| `audit_logger.py` | 61 | `log_decision()`, `get_audit_trail()` | infra (Session, AuditLog) | audit.py (2), cp_sat_optimizer.py (2), greedy/auto_schedule.py (2), greedy/optimization_loop.py (1), sm_inventory.py (1), wip_matching.py (1) | **shared** (cross-cutting; used by multiple subsystems) | ✓ Cross-cutting |
| `batch_group_lifecycle.py` | 452 | `BatchGroupStatusError`, `BatchGroupNotFoundError`, `BatchGroupWipMatchedError`, `BatchGroupReasonError`, `TaskPosition`, `RestoreAtPlanResult`, `unassign_batch_group()`, `compute_restore_at_plan()`, `restore_batch_group()` | infra (Session, ORM models), services (cascade.snap) | plan_pipeline.py (2 calls) | **application** (stateful DB orchestration, restore lifecycle) | ✓ Mixed responsibility (domain rules + infra DB ops) |
| `batch_grouping/__init__.py` | 43 | Re-exports: `create_batches`, `deduplicate_group_headers`, helpers, sheath key | services internal | plan_pipeline.py (1) | **re-export-shell** (D7-C invariant: preserve dotted paths until Week 9) | ✓ Re-export |
| `batch_grouping/constants.py` | 53 | Color enum `_A120_COLORS` | stdlib only (re module) | batch_grouping/sheath.py (1), batch_grouping/grouper.py (1) | **domain** (pure constants, no side effects) | ✓ Dead suspect: only 2 callers, internal to sub-package |
| `batch_grouping/grouper.py` | 1091 | `create_batches()`, `deduplicate_group_headers()` | infra (Session, 10 ORM models), services (constraint_params, helpers, sheath key) | pipeline/orchestrator.py (1), plan_pipeline.py (1), tests (3+) | **application** (ERP batch creation orchestration; heavy DB I/O) | ✓ Big offender (1091 LOC, Stage 1 core) |
| `batch_grouping/helpers.py` | 264 | `extract_sq()`, `format_spec_display()`, 6 private helpers | infra (4 ORM models, no Session) | grouper.py (7+), batch_grouping/__init__.py (1 re-export) | **shared** (pure stateless extraction/formatting used across batch_grouping) | |
| `batch_grouping/sheath.py` | 48 | `_compose_sheath_group_key()` | internal (constants) | batch_grouping/__init__.py (1), grouper.py (1), schedule_optimizer.py (1) | **domain** (pure key derivation; no DB) | ✓ Dead suspect: only 3 callers (all internal/re-export) |
| `batch_grouping/splitting.py` | 576 | `detect_split_candidates()`, `_apply_auto_split()`, `execute_auto_splits()` | infra (Session, ORM models), services internal | plan_pipeline.py (1 call in tests) | **application** (post-batch auto-split orchestration) | ✓ Secondary feature; dead suspect on public API (1 test-only caller) |
| `calendar_engine.py` | 426 | `_get_category()`, `_get_day_breaks()`, `get_available_hours()`, `get_working_window()`, `calculate_end_datetime()`, `generate_edu_dates()`, `_prev_day_window_end()`, `_active_date_for_reverse()`, `calculate_start_datetime()`, `reverse_advance()`, `_is_last_two_mondays()` | infra (Session, OperationCalendar ORM) | greedy/auto_schedule.py (2), greedy/optimization_loop.py (1), cp_sat_optimizer.py (2), jit_scheduling.py (1), cascade/service.py (1), cascade/snap.py (0 direct), constraint_checker.py (1), scheduling_shared/calendar_ops.py (1), solver/preemption.py (1), schedule_optimizer.py (re-export) | **infrastructure** (calendar parsing + time calculations; reads DB calendar; re-exported via schedule_optimizer) | |
| `cascade/__init__.py` | 17 | Re-exports: `plan_cascade_preview`, `plan_cascade_preview_on_snap`, `UnresolvedReason`, `PushReason`, `PullReason` | services internal | plan_pipeline.py (1) | **re-export-shell** | ✓ Re-export |
| `cascade/bfs.py` | 60 | `same_equipment_overlapping()`, `same_eq_prev_end()`, `successor_tasks()` | internal (snap.py only) | cascade/service.py (2), cascade/pull.py (1), schedule_validators.py (1) | **application** (graph traversal for cascade planning) | |
| `cascade/pull.py` | 52 | `propose_for_successors()` | internal (snap.py, bfs.py, reasons.py) | cascade/service.py (1) | **application** (pull proposal logic for cascade) | ✓ Dead suspect: single caller |
| `cascade/reasons.py` | 18 | `PushReason`, `PullReason`, `UnresolvedReason` enums | stdlib only | cascade/service.py (1), cascade/__init__.py (1 re-export), cascade/validators.py (1) | **domain** (pure enums; no side effects) | |
| `cascade/service.py` | 336 | `CascadePreviewResult`, `plan_cascade_preview_on_snap()`, `plan_cascade_preview()`, 3 private helpers | infra (Session via duck typing), services internal (snap, bfs, pull, validators) | cascade/__init__.py (1 re-export), plan_pipeline.py (1) | **application** (cascade orchestration + preview generation) | |
| `cascade/snap.py` | 126 | `TaskView`, `SnapTask`, `Snap`, `build_snapshot()` | stdlib only (dataclass) | cascade/service.py (1), cascade/validators.py (1), batch_group_lifecycle.py (1), schedule_validators.py (1), plan_pipeline.py (re-export), bulk_update.py (1) | **domain** (pure dataclass snapshots; no DB access, duck-typed) | |
| `cascade/validators.py` | 74 | `_entry()`, `validate_due_date()`, `validate_horizon()`, `validate_cycles()` | internal (snap.py, reasons.py) | cascade/service.py (1) | **domain** (pure validation rules; no DB) | ✓ Dead suspect: 4 small public functions, all called from 1 location |
| `constraint_checker.py` | 706 | `has_overlap()`, `validate_overlap_only()`, `validate_all()`, 11 private checks (`_check_overlap`, `_check_delivery`, `_check_priority_order`, ..., `_check_raw_material_availability()`) | infra (Session, 5 ORM models, ConstraintConfig), services (constraint_params) | plan_pipeline.py (3 calls: 2 explicit + 1 implicit) | **application** (post-schedule validation; heavy business logic + infra coupling) | ✓ Mixed responsibility (domain rules + infra data queries); Medium offender (706 LOC) |
| `constraint_params.py` | 106 | `ConstraintParams` (frozen dataclass with `.load()` classmethod), `resolve_spec_setup_min()`, `resolve_color_change_min()` | infra (Session, ConstraintConfig ORM) | batch_grouping/grouper.py (1), cp_sat_optimizer.py (1), greedy/auto_schedule.py (1), greedy/loaders/master_data.py (1), greedy/optimization_loop.py (1), solver/constraints/global_/predecessor.py (1), solver/constraints/process/sheath_color_hard.py (1), solver/constraints/process/sheath_color_sequence.py (1), solver/input_builder.py (1) | **shared** (pre-fetched config cache shared across multiple subsystems) | ✓ Mixed responsibility (infra DB load + domain rules for spec/color resolution) |
| `cp_sat_optimizer.py` | 1657 | `_resolve_num_workers()`, `_build_snapshot_weights()`, `_priority_label()`, `_compute_group_duration()`, `cp_sat_schedule()` | domain (constants), infra (Session, 4 ORM models), services (audit_logger, calendar_engine, constraint_params, scheduling_shared, solver package) | pipeline/stage1.py (1), tests (2+) | **application** (Stage 1 solver orchestrator; integrates domain rules + infra + solver) | ✓ Biggest offender (1657 LOC; no sub-package organization) |
| `decision_narrator.py` | 94 | `_korean_nouns()`, `explain_with_filter()` | external (kiwipiepy NLP), services (llm_providers) | routes/decisions.py (1 call) | **infrastructure** (NLP-based explanation layer; new stack for migrate from llm_explainer) | ✓ New module replacing llm_explainer; migration underway |
| `erp_parser.py` | 677 | `parse_erp_file()`, `parse_erp_file_incremental()`, 15 private helpers (openpyxl + xlrd) | infra (Session, SalesOrder/ItemMaster ORM) | pipeline/orchestrator.py (1), plan_pipeline.py (2 calls) | **infrastructure** (ERP file parsing; heavy openpyxl/xlrd logic) | ✓ Medium offender (677 LOC, file parsing) |
| `excel_exporter.py` | 741 | `export_plan()`, 8 private helpers (openpyxl formatting) | infra (Session, ProductionBatch ORM), external (openpyxl) | plan_pipeline.py (1 call) | **infrastructure** (Excel export; heavy openpyxl logic) | ✓ Medium-large offender (741 LOC, spreadsheet formatting) |
| `greedy/__init__.py` | 21 | Re-exports: `_find_available_slot`, `auto_schedule`, `reschedule_affected_groups`, `reschedule`, `_reschedule_affected_groups_cpsat` | services internal | plan_pipeline.py (1) | **re-export-shell** | ✓ Re-export |
| `greedy/auto_schedule.py` | 622 | `_should_apply_jit()`, `_group_earliest_due()`, `_sheath_group_color_rank()`, `_sheath_group_due_week_int()`, `auto_schedule()`, `_tardiness_boost_retry()`, `_purge_run_tasks()` | domain (constants), infra (Session, 4 ORM models, EquipmentMaster), services (audit_logger, calendar_engine, constraint_params, scheduling_shared, greedy/slot_finder, jit_scheduling) | plan_pipeline.py (re-export via schedule_optimizer), pipeline/stage2.py (1), greedy/reschedule_affected.py (1) | **application** (Stage 2 greedy scheduling orchestration; heavy stateful loop) | ✓ Large offender (622 LOC; Stage 2 core) |
| `greedy/loaders/__init__.py` | 11 | None (namespace package) | services internal | (none; internal loaders) | **shared** (loader sub-package) | |
| `greedy/loaders/base_date.py` | 39 | `resolve_base_date()` | stdlib only (datetime, zoneinfo) | greedy/optimization_loop.py (re-export, noqa comment) | **application** (timestamp base calculation for scheduling) | ✓ Dead suspect: re-exported in optimization_loop but may not be directly called |
| `greedy/loaders/master_data.py` | 60 | `MasterData` (dataclass), `load_master_data()` | domain (constants), infra (Session, EquipmentMaster, SpeedMaster ORM), services (constraint_params) | greedy/optimization_loop.py (re-export, noqa comment) | **application** (master data assembly for greedy loop) | ✓ Dead suspect: re-exported in optimization_loop but may not be directly called |
| `greedy/loaders/planned_batches.py` | 48 | `load_planned_batches()` | domain (constants), infra (Session, ProductionBatch ORM) | greedy/optimization_loop.py (re-export, noqa comment) | **application** (batch loader for greedy) | ✓ Dead suspect: re-exported in optimization_loop but may not be directly called |
| `greedy/loaders/wip_filter.py` | 45 | `filter_wip_skippable()` | domain (constants), infra (Session, ProductionBatch, WipInventory ORM) | greedy/optimization_loop.py (re-export, noqa comment) | **application** (WIP filtering for greedy) | ✓ Dead suspect: re-exported in optimization_loop but may not be directly called |
| `greedy/optimization_loop.py` | 964 | `_group_earliest_due()`, `_run_optimization_once()`, `_get_tp_line_speed()`, `_get_sheath_type()` | domain (constants), infra (Session, 4 ORM models), services (audit_logger, calendar_engine, constraint_params, scheduling_shared, greedy/loaders/*, greedy/slot_finder), external (re-exports from loaders) | greedy/auto_schedule.py (1), greedy/reschedule_affected.py (1) | **application** (Stage 2 greedy loop orchestration; core scheduling loop) | ✓ Biggest-in-greedy offender (964 LOC); heavily commented; re-exports loaders for compatibility |
| `greedy/reschedule_affected.py` | 638 | `_reset_non_frozen_for_retry()`, `_reschedule_affected_groups_cpsat()`, `reschedule_affected_groups()`, `reschedule()` | domain (constants), infra (Session, 2 ORM models), services (calendar_engine, greedy/auto_schedule, scheduling_shared) | plan_pipeline.py (via schedule_optimizer re-export), pipeline/stage2.py (1) | **application** (retry/rescheduling orchestration after failed greedy attempts) | |
| `greedy/slot_finder.py` | 58 | `_find_available_slot()` | stdlib only (datetime, timedelta) | cp_sat_optimizer.py (1), greedy/auto_schedule.py (1), greedy/optimization_loop.py (1), schedule_optimizer.py (re-export) | **application** (time slot search; pure algorithm, no DB) | |
| `jit_scheduling.py` | 177 | `apply_jit_delay()`, `_backward_pass()` | infra (Session, ProductionBatch, ScheduleTask ORM), services (calendar_engine) | greedy/auto_schedule.py (1), schedule_optimizer.py (re-export), cp_sat_optimizer.py (0) | **application** (just-in-time delay adjustment) | |
| `llm_explainer.py` | 620 | `explain_decision()` (async), `explain_decision_sync()`, `_detect_rule_based_risks()`, `generate_batch_summary_sync()`, `_build_context()`, `_call_llm()` (async), `_call_openai()` (async), `_call_anthropic()` (async), `_call_llm_sync()`, `_template_explanation()` | infra (Session, 3 ORM models), external (httpx, openai, anthropic SDKs) | routes/audit.py (2 calls), routes/plan_pipeline.py (2 calls) | **infrastructure** (LLM explainer layer; LEGACY, being migrated to decision_narrator + llm_providers) | ✓ Legacy stack (620 LOC); dual-mode (openai/anthropic); being phased out; mixed-responsibility (LLM + rules) |
| `llm_providers/__init__.py` | 100 | `Contribution`, `ConstraintRef`, `ExplainPayload` (dataclasses), `Provider` (Protocol), `get_provider()` | services internal (template, anthropic) | decision_narrator.py (1), llm_providers/anthropic.py (1 import), llm_providers/template.py (1 import) | **infrastructure** (LLM provider abstraction layer; new stack) | |
| `llm_providers/anthropic.py` | 69 | `AnthropicProvider` (class) | external (anthropic SDK), services (llm_providers) | llm_providers/__init__.py (via get_provider dispatch, no direct call) | **infrastructure** (Anthropic provider implementation) | ✓ Dead suspect: no direct callers; only instantiated via registry |
| `llm_providers/template.py` | 36 | `TemplateProvider` (class) | services (llm_providers) | llm_providers/__init__.py (via get_provider dispatch, no direct call) | **infrastructure** (Template/fallback provider) | ✓ Dead suspect: no direct callers; only instantiated via registry |
| `pipeline/__init__.py` | 26 | Re-exports: `new_run_label`, `run_solver_stage`, `run_greedy_stage`, `execute_stage1_ingest`, `execute_stage2` | services internal | plan_pipeline.py (1) | **re-export-shell** | ✓ Re-export |
| `pipeline/orchestrator.py` | 229 | `_purge_run_data()`, `execute_stage1_ingest()`, `execute_stage2()` | infra (Session, func/text from sqlalchemy), services (batch_grouping, erp_parser, run_labeler, stage1, stage2, wip_matching) | plan_pipeline.py (2 calls: execute_stage1_ingest, execute_stage2) | **application** (top-level pipeline orchestration; routes to stage1 + stage2) | |
| `pipeline/run_labeler.py` | 77 | `new_run_label()`, `parse_date_yyyymmdd()`, `parse_base_date_yyyymmdd()` | external (fastapi HTTPException), stdlib | plan_pipeline.py (2 calls: new_run_label directly + re-export), tests | **shared** (run label + date parsing utilities) | |
| `pipeline/stage1.py` | 63 | `run_solver_stage()` | infra (Session), services (cp_sat_optimizer) | pipeline/orchestrator.py (1), plan_pipeline.py (re-export) | **application** (Stage 1 entry point; thin wrapper) | |
| `pipeline/stage2.py` | 48 | `run_greedy_stage()` | infra (Session), services (greedy/auto_schedule, greedy/reschedule_affected, constraint_checker) | pipeline/orchestrator.py (1), plan_pipeline.py (re-export) | **application** (Stage 2 entry point; thin wrapper) | |
| `schedule_optimizer.py` | 90 | Re-exports (25+ symbols): constants, calendar_engine functions, JIT, scheduling_shared functions, greedy functions | services internal (no direct domain/infra import) | plan_pipeline.py (2 calls), routes (1+), solver/input_builder.py (1 import) | **re-export-shell** (D7-C invariant: Week 9 legacy compatibility anchor) | ✓ Re-export shell (90 LOC of pure re-exports with noqa F401); monkeypatch-safe anchor for tests |
| `schedule_validators.py` | 69 | `find_same_eq_overlap()`, `find_due_date_violation()`, `find_predecessor_violation()` | services (cascade/bfs, cascade/snap) | routes (cascade.py, bulk_update.py) = 2 route callers | **application** (validator helpers for schedule cascade) | ✓ Dead suspect: only 2 route-level callers; small public API |
| `scheduling_shared/__init__.py` | 14 | None (namespace package) | services internal | (none) | **shared** (helper sub-package) | |
| `scheduling_shared/calendar_ops.py` | 230 | `_work_days_between()`, `_working_minutes_between()`, `_due_work_min()`, `resolve_base_date()`, `_datetime_to_wmin()` | domain (constants), infra (Session) | constraint_checker.py (1), cp_sat_optimizer.py (1 re-export), schedule_optimizer.py (1 re-export) | **shared** (cross-cutting calendar calculations) | |
| `scheduling_shared/db_ops.py` | 44 | `_delete_task_safely()` | infra (Session, ScheduleTask ORM) | solver/preemption.py (1) | **infrastructure** (DB mutation helper; single caller) | ✓ Dead suspect: only 1 caller |
| `scheduling_shared/group_ops.py` | 592 | `_is_core_group()`, `_st_sq()`, `_is_sheath_group()`, `_extract_core_main_sq()`, `_compute_group_duration_map()`, `_is_multi_equip_group()`, `_get_drum_winding_min()`, `_get_stranding_setup_min()`, `_schedule_multi_equipment()` | domain (constants), infra (Session, 3 ORM models), services (calendar_engine) | cp_sat_optimizer.py (re-export), greedy/auto_schedule.py (re-export), greedy/optimization_loop.py (re-export), schedule_optimizer.py (re-export), solver/constraints/global_/predecessor.py (1 call) | **shared** (large batch of equipment/group analysis helpers; heavily re-exported) | |
| `scheduling_shared/slot_filters.py` | 258 | `_find_eligible_equipment()`, `_narrow_by_stranding()`, `align_start_to_predecessor_end()`, `_filter_by_sheath_routing()` | infra (Session, 2 ORM models), services (calendar_engine) | cp_sat_optimizer.py (re-export), greedy/auto_schedule.py (re-export), schedule_optimizer.py (re-export) | **shared** (equipment/slot filtering logic; heavily re-exported) | |
| `sheath_cluster.py` | 310 | `SheathCluster` (dataclass), `_derive_equipment_category()`, `_due_week_int()`, `_due_week_token()`, `build_sheath_clusters()`, `compute_cluster_meta()`, `cluster_sort_key()`, `count_color_transitions()` | stdlib only (dataclass, datetime, collections) | solver/constraints/process/sheath_color_hard.py (1 call to build_sheath_clusters) | **domain** (pure sheath clustering logic; no DB access despite external caller pattern) | ✓ Dead suspect: public API only called once; most functions appear to be internal helpers |
| `sm_inventory.py` | 173 | `update_wip_actual()`, `create_shortage_batches()`, `get_wip_summary()` | infra (Session, 3 ORM models), services (audit_logger) | routes (sm.py implied) | **application** (WIP / shortage batch management; DB-heavy) | |
| `solver/__init__.py` | 38 | Re-exports: `ConstraintSpec`, `load_active_constraints`, `SolverInput`, `build_solver_input`, `TraceMetadata`, `compute_output_hash`, `compute_input_hash`, `write_trace` | services internal | (re-exports) | **re-export-shell** | ✓ Re-export |
| `solver/constraint_loader.py` | 133 | `ConstraintSpec` (frozen dataclass), `load_active_constraints()`, `_row_to_spec()` | infra (Session, ConstraintConfig ORM) | cp_sat_optimizer.py (1) | **infrastructure** (constraint config loader; DB read) | |
| `solver/constraints/__init__.py` | 19 | None (namespace package) | (none) | (none) | **shared** (constraint sub-package) | |
| `solver/constraints/color/__init__.py` | 7 | None (namespace package) | (none) | (none) | **shared** | |
| `solver/constraints/fault/__init__.py` | 6 | None (namespace package) | (none) | (none) | **shared** | |
| `solver/constraints/global_/__init__.py` | 7 | None (namespace package) | (none) | (none) | **shared** | |
| `solver/constraints/global_/decision_vars.py` | 126 | `DecisionVars` (dataclass), `add_decision_vars()` | external (ortools), (none services/domain) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint; solver-specific) | |
| `solver/constraints/global_/frozen_pins.py` | 80 | `apply_frozen_pins()` | external (ortools) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint) | |
| `solver/constraints/global_/idle_terms.py` | 47 | `collect_idle_terms()` | domain (constants), external (ortools) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint) | |
| `solver/constraints/global_/no_overlap.py` | 84 | `add_equipment_no_overlap()` | external (ortools) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint) | |
| `solver/constraints/global_/predecessor.py` | 108 | `compute_proc_groups_by_sq()`, `add_predecessor_precedence()`, `add_core_st_precedence()` | domain (constants), external (ortools), services (scheduling_shared/group_ops) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint; uses domain rules) | |
| `solver/constraints/global_/slack_terms.py` | 44 | `collect_slack_terms()` | external (ortools) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint) | |
| `solver/constraints/global_/warm_start.py` | 78 | `apply_warm_start_hints()` | external (ortools) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint) | |
| `solver/constraints/inventory/__init__.py` | 6 | None (namespace package) | (none) | (none) | **shared** | |
| `solver/constraints/process/__init__.py` | 10 | None (namespace package) | (none) | (none) | **shared** | |
| `solver/constraints/process/edd_pair.py` | 70 | `collect_edd_pair_terms()` | external (ortools) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint) | |
| `solver/constraints/process/sheath_color_hard.py` | 66 | `add_sheath_color_hard_chain()` | external (ortools), services (constraint_params, sheath_cluster) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint; uses domain sheath logic) | |
| `solver/constraints/process/sheath_color_sequence.py` | 89 | `add_sheath_color_sequence_and_tiebreak()` | external (ortools), services (constraint_params) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint) | |
| `solver/constraints/process/transition.py` | 64 | `collect_transition_terms()` | external (ortools) | solver/model_builder.py (re-export, noqa) | **infrastructure** (OR-Tools constraint) | |
| `solver/constraints/product/__init__.py` | 11 | None (namespace package) | (none) | (none) | **shared** | |
| `solver/decision_aggregator.py` | 116 | `_safe_value()`, `_aggregate_intvar_penalties()`, `build_decision_inputs()` | external (ortools, TYPE_CHECKING) | (no external callers found; may be internal to solver flow) | **infrastructure** (OR-Tools solution extraction) | ✓ Dead suspect: no visible callers outside solver package |
| `solver/input_builder.py` | 258 | `SolverInput` (Pydantic BaseModel), `build_solver_input()` | domain (constants), infra (Session, 4 ORM models), external (pydantic), services (constraint_params, schedule_optimizer) | cp_sat_optimizer.py (1) | **application** (solver input assembly; orchestrates data gathering) | |
| `solver/lex_min_time.py` | 218 | `LexResult` (dataclass), `solve_lex_min_time()`, `_infer_horizon()` | external (ortools) | cp_sat_optimizer.py (0 visible callers; code appears to be present but not called) | **infrastructure** (OR-Tools lexicographic optimization) | ✓ Dead suspect: defined but no visible callers in grep; may be unreachable code |
| `solver/model_builder.py` | 439 | `ModelWeights` (dataclass), `BuiltModel` (dataclass), `build_model()` | external (ortools), services (constraint_params, solver/constraints/* via re-exports) | cp_sat_optimizer.py (1), objective.py (1) | **infrastructure** (solver model construction; wraps all constraint modules) | |
| `solver/objective.py` | 110 | `compose_objective()` | external (ortools), services (solver/model_builder) | cp_sat_optimizer.py (1) | **infrastructure** (solver objective function) | |
| `solver/preemption.py` | 257 | `_drums_completable()`, `try_preempt_for_urgent()` | infra (Session, 2 ORM models), services (calendar_engine, scheduling_shared/db_ops) | cp_sat_optimizer.py (0 visible callers) | **infrastructure** (solver preemption logic for urgent jobs) | ✓ Dead suspect: no visible callers; may be unreachable feature |
| `solver/snapshot.py` | 232 | `SnapshotWeights` (dataclass), `_resolve_output_dir()`, `write_snapshot()` | stdlib + json/os | cp_sat_optimizer.py (1 to SnapshotWeights, 1 to write_snapshot) | **infrastructure** (solver snapshot I/O) | |
| `solver/trace_writer.py` | 256 | `TraceMetadata` (dataclass), `compute_output_hash()`, `compute_input_hash()`, `_applied_heuristic()`, `write_trace()` | infra (Session, 2 ORM models), stdlib (hashlib, json), services (solver/constraint_loader) | solver/__init__.py (re-export) | **infrastructure** (solver trace logging/hashing) | |
| `stage2_job_queue.py` | 146 | `Stage2JobRequest` (dataclass), `submit_job()`, `get_job()`, `_reset_for_tests()` | infra (Session), stdlib (logging, threading, uuid), external (none) | plan_pipeline.py (2 calls) | **application** (async job queue for Stage 2) | |
| `tardiness_metrics.py` | 190 | `_priority_label()`, `count_tardiness()` | stdlib only (datetime) | routes (metrics.py implied) | **domain** (tardiness calculation; no DB in function but counted on routes) | |
| `wip_lifecycle_listener.py` | 105 | `_derive_process_stage()`, `_auto_create_expected_wip()`, `register_wip_listener()` | infra (SQLAlchemy event hooks, 2 ORM models) | database.py (1 call to register_wip_listener on init) | **infrastructure** (SQLAlchemy event listener for WIP auto-creation) | |
| `wip_matching.py` | 381 | `match_wip()`, `_find_best_combo()`, `_product_group_matches()`, `_extract_sq()` | infra (Session, 3 ORM models), services (audit_logger) | pipeline/orchestrator.py (1), plan_pipeline.py (1) | **application** (WIP matching orchestration; heavy business logic) | |
| `wip_parser.py` | 515 | `_compute_canonical_hash()`, `parse_wip_excel()`, `parse_wip_file()`, 9 private helpers | infra (Session, 2 ORM models), external (xlrd, openpyxl), stdlib (hashlib, json) | plan_pipeline.py (1 call), tests (3+) | **infrastructure** (WIP file parsing; heavy openpyxl/xlrd logic) | |
| `wip_promotion.py` | 46 | `_promote_expected_to_estimated()` | infra (Session, WipInventory ORM) | plan_pipeline.py (1 call) | **infrastructure** (WIP status promotion logic) | ✓ Dead suspect: tiny module (46 LOC), single caller |
| `wip_template.py` | 139 | `generate_wip_template()` | external (openpyxl), stdlib | plan_pipeline.py (1 call) | **infrastructure** (WIP template generation; openpyxl) | |

**Total Files Audited**: 85 (including 14 namespace packages with minimal/no code)

---

## Section 1: Top 5 LOC Offenders

### 1. `cp_sat_optimizer.py` — 1657 LOC  
**Breakdown** (approximate):
- Core function `cp_sat_schedule()`: ~400 LOC
  - Task parsing + ORM object iteration (100 LOC)
  - Decision tree for solver invocation (150 LOC)
  - Error handling + retry logic (150 LOC)
- `_compute_group_duration()`: ~100 LOC (duration calculation)
- Snapshot weights setup + objective composition: ~200 LOC
- Imports + helper setup: ~60 LOC

**Issue**: Monolithic entry point for entire CP-SAT solver pipeline. Should split into (a) input assembly, (b) solver execution, (c) post-processing.

### 2. `greedy/optimization_loop.py` — 964 LOC  
**Breakdown**:
- `_run_optimization_once()`: ~500 LOC (core greedy loop)
  - Slot finding + assignment (200 LOC)
  - Conflict detection + retry (150 LOC)
  - Logging + introspection (150 LOC)
- Loader re-exports (marked with noqa): ~50 LOC
- `_get_sheath_type()`, `_get_tp_line_speed()`: ~50 LOC
- Infrastructure + comments: ~100 LOC

**Issue**: Core greedy scheduling loop is deeply nested. Reexports loaders (base_date, master_data, planned_batches, wip_filter) suggesting tight coupling.

### 3. `greedy/auto_schedule.py` — 622 LOC  
**Breakdown**:
- `auto_schedule()`: ~350 LOC (entry point + main loop)
  - Loader setup (50 LOC)
  - Group sorting + prioritization (100 LOC)
  - Retry loop + tardiness boost (150 LOC)
- `_tardiness_boost_retry()`: ~150 LOC (fallback retry)
- `_purge_run_tasks()`: ~50 LOC
- Helpers + setup: ~70 LOC

**Issue**: Orchestrates loaders + optimization_loop; tier-2 entry point for Stage 2.

### 4. `solver/model_builder.py` — 439 LOC  
**Breakdown**:
- `build_model()`: ~300 LOC
  - Constraint application loop (200 LOC with re-imported constraint modules)
  - Objective composition (50 LOC)
  - Setup + metadata (50 LOC)
- `BuiltModel` dataclass: ~30 LOC
- `ModelWeights` dataclass + setup: ~50 LOC

**Issue**: Wraps all constraint modules via re-imports (noqa F401). Acts as constraint registry without explicit dispatch; tightly coupled to constraint file structure.

### 5. `constraint_checker.py` — 706 LOC  
**Breakdown**:
- `validate_all()`: ~50 LOC (dispatcher)
- 18 constraint check functions (`_check_overlap`, `_check_delivery`, ..., `_check_raw_material_availability`):
  - Each 25–50 LOC
  - Heavy ORM querying + business logic interleaved
- Type definitions + setup: ~100 LOC

**Issue**: Monolithic validation suite; each check couples domain rules + infra (DB queries, ORM filtering).

---

## Section 2: Re-export Shells

### Canonical D7-C Anchor (Week 9 Compatibility)

| File | Symbols | Count | Reason |
|------|---------|-------|--------|
| `schedule_optimizer.py` | 25 symbols (domain constants + calendar_engine + JIT + scheduling_shared + greedy) | 90 LOC | **Legacy namespace preservation**. Refactored code moved to greedy/, scheduling_shared/, calendar_engine but re-exported here to maintain existing monkeypatches in tests (test_jit_integration, test_tp_routing). All imports marked `# noqa: F401`. Docstring explicitly states "D7-C invariant (Week 9)." |
| `batch_grouping/__init__.py` | 5 symbols (create_batches, deduplicate_group_headers, helpers, sheath key) | 43 LOC | Exposes sub-package (grouper, helpers, sheath, splitting, constants). |
| `cascade/__init__.py` | 5 symbols (cascade orchestration + reason enums) | 17 LOC | Exposes cascade sub-package. |
| `greedy/__init__.py` | 5 symbols (auto_schedule, reschedule_affected_groups, reschedule, slot_finder) | 21 LOC | Exposes greedy sub-package. |
| `pipeline/__init__.py` | 5 symbols (stage1, stage2, orchestrator, run_labeler) | 26 LOC | Exposes pipeline sub-package. |
| `solver/__init__.py` | 8 symbols (constraint_loader, input_builder, trace_writer) | 38 LOC | Exposes solver sub-package. |

**Total re-export LOC**: 235 (out of 17,910 total = 1.3%)

---

## Section 3: Mixed-Responsibility Files

### Critical Candidates for Layer Separation

| File | LOC | Problem | Split Recommendation |
|------|-----|---------|----------------------|
| `llm_explainer.py` | 620 | **Dual responsibility**: (a) infra DB ops (4 ORM model reads), (b) LLM provider integration (httpx + openai/anthropic SDKs), (c) business logic (rule-based risk detection, context building). Mixes legacy openai + anthropic clients. No abstraction layer. | **Migrate to infrastructure/** `llm_providers/` → extract explain logic to domain layer; keep only HTTP I/O in infra. **Status**: New `decision_narrator.py` + `llm_providers/` package already started migration. |
| `constraint_checker.py` | 706 | **18 validation functions** interleave domain rules (`_check_overlap`, `_check_priority_order`) with infra queries (ORM filtering on ProductionBatch, ScheduleTask, EquipmentMaster). Each function is a mix of SQL + Python logic. No separation of concerns. | **Split into** (a) domain layer: pure validation rule predicates (no DB), (b) application layer: orchestration + queries, (c) test: unit test predicates separately from infra. |
| `constraint_params.py` | 106 | **Mixed**: infra load (DB query for ConstraintConfig) + domain rules (`resolve_spec_setup_min`, `resolve_color_change_min` business logic). The parameter resolution is domain knowledge. | **Move** `resolve_spec_setup_min / resolve_color_change_min` to domain layer as pure functions; keep cache load in infra. ConstraintParams dataclass can stay in either layer depending on use. |
| `batch_group_lifecycle.py` | 452 | **Stateful DB orchestration**: unassign, restore, compute restore position mix cascade logic + infra (Session, ORM models, cascade.snap). `compute_restore_at_plan` is domain logic; `restore_batch_group` is application. | **Split into** (a) domain: `RestoreAtPlanResult` + position calc, (b) application: DB mutations + exceptions. |
| `cp_sat_optimizer.py` | 1657 | **Monolithic orchestrator**: domain rules (from app.domain.constants), infra DB ops (4 ORM reads), solver logic (re-exports 20+ constraint modules), calendar + audit logging. Mixes concerns across all layers. | **Refactor to thin application layer** that delegates to (a) input builder, (b) model builder, (c) solver executor (already in solver/ package but not cleanly separated). |
| `sheath_cluster.py` | 310 | **Paradox**: pure domain (dataclass, no DB access) yet used by infra (solver/constraints/process/sheath_color_hard.py calls `build_sheath_clusters()`). Clustering is domain concern but lives in services/. | **Move to domain/** (pure dataclasses + logic); solver/ constraints import from there. |

---

## Section 4: Single-Call-Site Dead Suspects

### Public symbols imported by ≤1 external caller (excluding tests & re-exports)

| File | Symbol(s) | External Callers | Verdict | Risk |
|------|-----------|------------------|--------|------|
| `cascade/pull.py` | `propose_for_successors()` | 1 (cascade/service.py) | **Dead suspect**: only called from one location; no external users. | Low (internal utility). |
| `cascade/validators.py` | `validate_due_date()`, `validate_horizon()`, `validate_cycles()` | 1 (cascade/service.py) | **Dead suspect**: 4 functions, all called from cascade orchestration only. | Low (internal cascade utilities). |
| `greedy/loaders/base_date.py` | `resolve_base_date()` | Re-exported in optimization_loop.py (noqa) but no visible direct callers in grep. | **Dead suspect**: marked as re-export for compatibility but may not be called at all. | Medium (check if really used). |
| `greedy/loaders/master_data.py` | `load_master_data()` | Re-exported in optimization_loop.py (noqa) but no visible direct callers. | **Dead suspect**: re-export convention but no evidence of use. | Medium (verify usage in optimization_loop). |
| `greedy/loaders/planned_batches.py` | `load_planned_batches()` | Re-exported in optimization_loop.py (noqa) but no visible direct callers. | **Dead suspect**: re-export convention but unused. | Medium (same). |
| `greedy/loaders/wip_filter.py` | `filter_wip_skippable()` | Re-exported in optimization_loop.py (noqa) but no visible direct callers. | **Dead suspect**: same pattern. | Medium (same). |
| `batch_grouping/constants.py` | `_A120_COLORS` | 2 (batch_grouping/sheath.py, batch_grouping/grouper.py) — both internal to sub-package. | **Dead suspect**: private constant, only internal users. | Low (internal only). |
| `batch_grouping/sheath.py` | `_compose_sheath_group_key()` | 2 internal (grouper.py) + 1 re-export (schedule_optimizer). | **Dead suspect**: 3 callers but 2 are internal sub-package refs; re-export suggests legacy anchor. | Low (internal + re-export). |
| `scheduling_shared/db_ops.py` | `_delete_task_safely()` | 1 (solver/preemption.py) | **Dead suspect**: single caller, no tests visible. | Medium (verify if preemption is enabled). |
| `batch_grouping/splitting.py` | `execute_auto_splits()` | 1 (plan_pipeline.py, tests only, no production callers in grep). | **Dead suspect**: public API but only test-called. | Medium (feature may be disabled). |
| `wip_promotion.py` | `_promote_expected_to_estimated()` | 1 (plan_pipeline.py) | **Dead suspect**: single caller, 46-line module. | Low (single purpose). |
| `solver/decision_aggregator.py` | `build_decision_inputs()` | 0 (no visible external callers; may be internal to solver flow). | **Dead suspect**: public function but no grep hits outside solver/. | High (unreachable code?). |
| `solver/lex_min_time.py` | `solve_lex_min_time()` | 0 (no visible callers; function appears in code but not invoked). | **Dead suspect**: defined but likely unreachable. | High (likely dead code). |
| `solver/preemption.py` | `try_preempt_for_urgent()` | 0 (no visible callers; may be feature flag gated). | **Dead suspect**: substantial module (257 LOC) with no callers. | High (feature disabled?). |
| `llm_providers/anthropic.py` | `AnthropicProvider` | 0 direct; instantiated via `get_provider()` registry dispatch (1 call from decision_narrator). | **Dead suspect**: no direct imports; hidden caller via dispatch. | Low (registry pattern). |
| `llm_providers/template.py` | `TemplateProvider` | 0 direct; instantiated via registry (may not be called). | **Dead suspect**: fallback provider; unclear if registered. | Medium (verify registry). |
| `schedule_validators.py` | `find_same_eq_overlap()`, `find_due_date_violation()`, `find_predecessor_violation()` | 2 (routes/cascade.py, routes/bulk_update.py) | **Dead suspect**: 3 functions, only route-layer callers. | Low (route validation utilities). |

**Total "Dead" Suspects by Risk**:
- **High Risk** (0 callers): lex_min_time.py, preemption.py, decision_aggregator.py (3 modules, 611 LOC total)
- **Medium Risk** (re-export orphans or feature-gated): greedy/loaders/* (4 modules), db_ops.py, splitting.py, wip_promotion.py (7 modules, 251 LOC)
- **Low Risk** (internal utilities or registry-gated): others

---

## Section 5: Cross-Cutting Candidates

### Modules used from many layers / subsystems

| File | Used By | Call Sites | Type | Recommendation |
|------|---------|------------|------|-----------------|
| `audit_logger.py` | cp_sat_optimizer, greedy/auto_schedule, greedy/optimization_loop, sm_inventory, wip_matching, audit.py (routes) | 6+ across infra + application | **Logging/audit infrastructure** | Keep in **shared** layer; consider formalizing as audit middleware. |
| `constraint_params.py` | batch_grouping/grouper, cp_sat_optimizer, greedy/auto_schedule, greedy/loaders/master_data, greedy/optimization_loop, solver/constraints/process/*, solver/input_builder | 9+ across solver + greedy + batch_grouping | **Config cache** | Keep in **shared**; formalizes as reusable "params snapshot" pattern across modules. |
| `calendar_engine.py` | greedy/auto_schedule, greedy/optimization_loop, cp_sat_optimizer, jit_scheduling, cascade/service, constraint_checker, scheduling_shared/calendar_ops, solver/preemption, plan_pipeline (routes), schedule_optimizer (re-export) | 9+ across greedy, solver, cascade, constraint, scheduling | **Calendar calculations** | Keep in **infrastructure** (reads DB calendar); re-exported via schedule_optimizer for compatibility. Heavy cross-subsystem use. |
| `scheduling_shared/group_ops.py` | cp_sat_optimizer, greedy/auto_schedule, greedy/optimization_loop, solver/constraints/global_/predecessor, schedule_optimizer (re-export) | 5+ | **Group/equipment analysis** | Keep in **shared**; heavily re-exported for backward compatibility. Large utility module (592 LOC). |
| `scheduling_shared/slot_filters.py` | cp_sat_optimizer, greedy/auto_schedule, schedule_optimizer (re-export) | 3+ | **Slot filtering** | Keep in **shared**; re-exported via schedule_optimizer. |

---

## Proposed Layer Mapping Summary

| Layer | Count | Files | Estimated LOC |
|-------|-------|-------|---|
| **Domain** (pure logic, no DB/I/O) | 7 | cascade/snap, cascade/reasons, cascade/validators, batch_grouping/constants, batch_grouping/sheath (move), tardiness_metrics, sheath_cluster (move) | ~600 |
| **Application** (orchestration, state mgmt, use cases) | 18 | cp_sat_optimizer, greedy/auto_schedule, greedy/optimization_loop, greedy/reschedule_affected, constraint_checker, batch_grouping/grouper, batch_group_lifecycle, wip_matching, cascade/service, batch_grouping/splitting, pipeline/orchestrator, stage1, stage2, cascade/bfs, cascade/pull, jit_scheduling, sm_inventory, stage2_job_queue | ~8200 |
| **Infrastructure** (DB, file I/O, external APIs) | 20 | calendar_engine, erp_parser, excel_exporter, wip_parser, wip_template, wip_lifecycle_listener, constraint_checker (part), wip_promotion, llm_explainer, decision_narrator, llm_providers/*, solver/constraints/* (15 modules), solver/model_builder, solver/objective, solver/snapshot, solver/trace_writer, solver/input_builder, solver/lex_min_time, solver/preemption, solver/decision_aggregator, solver/constraint_loader | ~4500 |
| **Shared** (cross-cutting, helpers) | 11 | audit_logger, constraint_params, scheduling_shared/calendar_ops, scheduling_shared/group_ops, scheduling_shared/slot_filters, scheduling_shared/db_ops, pipeline/run_labeler, greedy/loaders/*, sheath_cluster (until move), schedule_validators | ~1800 |
| **Re-export Shells** (D7-C compatibility anchors) | 6 | schedule_optimizer, batch_grouping/__init__, cascade/__init__, greedy/__init__, pipeline/__init__, solver/__init__ | ~235 |
| **Namespace packages** (empty or minimal) | 14 | __init__.py files, constraints/*, loaders/__init__, etc. | ~100 |
| **(Ambiguous / needs review)** | 9 | constraint_params (infra load + domain rules), batch_group_lifecycle (domain + infra), llm_explainer (legacy, being replaced), solver/ constraint modules (domain rules + OR-Tools wrapper) | ~2500 |

---

## Key Findings & Action Items

1. **Three Towers of Monolithic Code** (>600 LOC each):
   - `cp_sat_optimizer.py` (1657): Stage 1 entry point; split input assembly → model builder → executor.
   - `greedy/optimization_loop.py` (964): Stage 2 core loop; extract slot-finding + conflict logic as separate sub-modules.
   - `constraint_checker.py` (706): 18 validation functions; separate domain predicates from infra queries.

2. **Orphaned/Hidden Callers** (611 LOC):
   - `solver/lex_min_time.py` (218): No visible callers; verify if live or dead code.
   - `solver/preemption.py` (257): No callers; likely feature-gated or disabled.
   - `solver/decision_aggregator.py` (116): No external callers found; may be internal to solver flow only.

3. **Re-export Orphans** (251 LOC):
   - `greedy/loaders/*`: marked re-export in optimization_loop.py but actual use unclear; verify against execution trace.
   - `batch_grouping/splitting.py`: public API but only test-called; feature may be disabled.

4. **Migration in Progress**:
   - **LLM Stack**: `llm_explainer.py` (620 LOC, legacy) being phased to `decision_narrator.py` (94) + `llm_providers/` (205). Old code still active; both stacks coexist.

5. **Mixed-Responsibility Candidates for Refactoring**:
   - `constraint_params.py`: split infra load (→ infrastructure) from domain rules (→ domain).
   - `llm_explainer.py`: move logic to domain/application; keep HTTP client in infra.
   - `constraint_checker.py`: factor out domain predicates; orchestrate queries in application layer.

6. **Re-export Anchors (Stable, do not remove)**:
   - `schedule_optimizer.py` (90 LOC): **D7-C Week 9 invariant**. Preserves monkeypatch compatibility for tests. All imports marked `# noqa: F401` with D7-C comments.

---

## Appendix: File Classification Legend

- **Domain**: Pure dataclasses, enums, validation rules; no DB access, no I/O, no external deps (except stdlib).
- **Application**: Orchestration, use-case implementations, state management; may use DB but through clean interfaces.
- **Infrastructure**: DB models, file I/O (openpyxl, xlrd), external APIs (LLM, OR-Tools), file parsing.
- **Shared**: Cross-cutting helpers (logging, caching, time calcs) used by multiple layers.
- **Re-export-shell**: Namespace re-export for backward compatibility; no implementation.
- **Ambiguous**: Requires human review; mixed responsibility or unclear role.
- **Duplicate-suspect / Dead-suspect**: Flagged for further investigation; may indicate duplicate logic, unreachable code, or disabled features.

