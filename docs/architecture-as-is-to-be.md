# Architecture: As-Is → To-Be

**Audience:** anyone joining the project after the Production Handoff
Refactor (Weeks 0-9). The two diagrams below show how the codebase
looked before the refactor (god-files, circular imports) and how it
looks now (packages, one-way deps, sliced state).

Both diagrams render natively in GitHub markdown.

---

## As-Is (pre-Week-2)

```mermaid
flowchart TB
    subgraph Backend["backend/app"]
        direction TB
        cp["services/cp_sat_optimizer.py<br/>(MILP solver, ~2k LOC)"]
        sched["services/schedule_optimizer.py<br/>(greedy + cascade, ~1.5k LOC)"]
        plan_route["routes/plan_pipeline.py<br/>(Stage 1+2 god-file)"]
        sched_route["routes/schedules.py<br/>(1,702 LOC, all CRUD/cascade/revert)"]
        cp <-->|circular| sched
        plan_route --> cp
        plan_route --> sched
        sched_route --> sched
    end

    subgraph Frontend["frontend/src"]
        direction TB
        page_old["app/scheduler/page.tsx<br/>(god-page, fetch + state + UI)"]
        view_old["components/scheduler/SchedulerView.tsx<br/>(god-view, gantt + sidebar + toolbar)"]
        store_old["store/scheduleStore.ts<br/>(monolithic Zustand)"]
        page_old --> view_old
        view_old --> store_old
    end

    Frontend -->|fetch| Backend
```

Pain points the diagram makes obvious:

- **Bidirectional `cp_sat_optimizer ↔ schedule_optimizer` import** —
  any change to either file risked an import-cycle traceback.
- **Three god-files own everything in their layer.** A single PR for a
  small admin tweak required edits to a 1,702-LOC route, a 2k-LOC
  optimizer, and a sprawling `page.tsx`.
- **No seam for tests.** Mocking the LLM, the calendar, or the WIP
  matcher meant patching deep into the optimizer file.

---

## To-Be (post-Week-9)

```mermaid
flowchart TB
    subgraph Backend["backend/app"]
        direction TB

        subgraph Services["services/"]
            direction TB
            solver["solver/<br/>constraint_loader · input_builder<br/>model_builder · objective<br/>trace_writer"]
            greedy["greedy/<br/>auto_schedule · slot_finder<br/>reschedule_affected"]
            shared["scheduling_shared/<br/>calendar_ops · db_ops<br/>group_ops · slot_filters"]
            grouping["batch_grouping/<br/>grouper · sheath<br/>splitting · helpers"]
            pipeline["pipeline/<br/>stage1 · stage2<br/>orchestrator · run_labeler"]
            llm["llm_providers/<br/>template · anthropic"]
            narrator["decision_narrator.py<br/>(kiwipiepy filter)"]
        end

        subgraph Routes["presentation/routes/"]
            direction TB
            sched_pkg["schedules/<br/>list · detail · bulk_update<br/>cascade · revert"]
            decisions_route["decisions.py"]
            change_sets_route["change_sets.py"]
            plan_route2["plan_pipeline.py"]
            constraints_route["constraints.py"]
        end

        sched_pkg --> greedy
        sched_pkg --> shared
        plan_route2 --> pipeline
        pipeline --> solver
        pipeline --> greedy
        pipeline --> grouping
        solver --> shared
        greedy --> shared
        decisions_route --> narrator
        narrator --> llm
        constraints_route --> solver
    end

    subgraph Frontend["frontend/src/features/scheduler"]
        direction TB
        page_new["../../app/(main)/scheduler/page.tsx<br/>(thin shell)"]
        sections["page-sections/<br/>SchedulerToolbar · SchedulerBanners<br/>BatchInspector · LateTasksPanel<br/>BatchGroupOrderTable · CompareDetailsModal<br/>EditWarningModal"]
        view_new["components/SchedulerView.tsx<br/>+ scheduler-view/ sub-components<br/>(gantt · sidebar · etc.)"]
        store_new["store/scheduleStore.ts<br/>+ slices/batchesSlice.ts<br/>+ missingReasonsStore.ts"]
        hooks["hooks/<br/>(data fetching + selection)"]

        page_new --> sections
        page_new --> view_new
        page_new --> hooks
        view_new --> store_new
        sections --> store_new
        hooks --> store_new
    end

    Frontend -->|fetch| Backend
```

Wins this layout buys:

- **Each package has one responsibility.** `solver/` builds and runs
  the MILP; `greedy/` does heuristic placement; `scheduling_shared/`
  holds the pieces both need (calendar arithmetic, DB ops, slot
  filters). No circular imports.
- **Routes are thin.** `routes/schedules/` is now five small files,
  each owning one HTTP verb-shape. The previous 1,702-LOC `schedules.py`
  is gone.
- **Frontend has a real layered shape.** `page.tsx` is a shell;
  `page-sections/` carries the major UI regions; `components/`
  carries the leaf widgets; `store/slices/` lets each domain own its
  Zustand slice without colliding.
- **The Decision Card pathway is its own seam.** `routes/decisions.py
→ decision_narrator.py → llm_providers/{template,anthropic}.py` is
  testable end-to-end without touching the legacy `llm_explainer.py`.
