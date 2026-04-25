# Architecture Target Review — Eng Manager Sign-off

**Reviewer**: Senior Staff EM (review-only, no code edits)
**Subject**: `docs/architecture-target.md` (448 lines, 2026-04-26)
**Driver**: `docs/next-session-prompt-v2.md` (7-phase round)
**Date**: 2026-04-26

---

## 1. Verdict

**APPROVE WITH CHANGES** — the structure is sound and the leaf-first sequencing is defensible, but five concrete items in §3 must be patched into the plan **before** Phase 1 step 1 ships. Without the changes, Phase 1 step 4 will require a temporary shell that the plan does not currently authorize, and Phase 2 risks a silent LLM regression that the gate cannot catch.

---

## 2. Dimension Scores

| #   | Dimension                          | Score | What pushes it to 10                                                                                                                                                                        |
| --- | ---------------------------------- | ----: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Architecture correctness           | **8** | Move `resolve_spec_setup_min` / `resolve_color_change_min` (pure rules) from `_shared/constraint_params.py` to `domain/constraint_rules.py`; keep only DB load in `_shared`.                |
| 2   | Migration safety / sequencing      | **6** | Add an explicit "intermediate shell" rule for step 4 (cp_sat solver moves) and rewrite step 6 to be **two atomic commits** (route-flip, then test-flip + shell delete).                     |
| 3   | Deferred-5 absorption              | **9** | Promote item #5 (lex wiring) acceptance to "fixture 12 + 13 deterministic + parity gate exempt via EXPECTED_DRIFT" — currently §3 lists fixtures without naming the EXPECTED_DRIFT row.     |
| 4   | Edge cases / failure modes         | **6** | Add (a) SchedulerState-per-retry assertion in `auto_schedule.py` body, (b) a parity smoke-import test that runs after every step.                                                           |
| 5   | Test coverage / verification gates | **7** | Add a per-step `python -c "import app.main"` + cycle detector (`pydeps` or in-tree) to §6 — the 27/27 gate is response-level and will _not_ catch a moved module that imports its old name. |
| 6   | Risk register completeness         | **6** | Missing: (a) routes ORM Top 5 distributed across phases → rework, (b) `lex_min_time` currently has 0 callers (audit C confirmed), so wiring in Phase 3 is a _new feature_, not a "wire".    |
| 7   | Effort estimate realism            | **5** | Phase 1 step 6 alone is 35 routes + 21 tests + shell delete + cycle break — that is 3-4h on its own. Round total of ~13.5h is optimistic by ~30-50%.                                        |
| 8   | Simplicity discipline              | **9** | Plan honors the no-Protocol/no-registry rule; SchedulerState is a frozen-by-default dataclass (§4 Phase 4), which the user memory explicitly allows.                                        |

Mean: 7.0 — execution-ready _after_ changes below.

---

## 3. Top 5 Must-Fix-Before-Phase-1

### M1. Step 4 needs an explicit "temporary shell" clause for top-level solver imports

**Where**: `architecture-target.md` §4 Phase 1 step 4 (lines 257-266) and §7 row 5.
**Why it matters**: I verified `cp_sat_optimizer.py:311` — the `try_preempt_for_urgent` import is **top-level** (`# noqa: E402`), not lazy. When step 4 moves `solver/preemption.py` → `application/scheduling/cp_sat/preemption.py`, the _same atomic commit_ must rewrite this import. The plan implies that's fine, but `cp_sat_optimizer.py` itself is being moved to `orchestrator.py` in the same step (§2 line 88, §4 step 4 last bullet), which means **two files are renaming each other simultaneously**. If step 4 fails verification mid-commit, you have no working baseline.
**Concrete fix**: Split step 4 into 4a (move `solver/*` to `application/scheduling/cp_sat/` with `cp_sat_optimizer.py` keeping its old path and updating only its top-level imports to the new solver paths) and 4b (move `cp_sat_optimizer.py` itself to `orchestrator.py`, _no_ body changes). Each gets its own parity-quick gate. This costs +1 commit, saves a debugging spiral if 17 imports plus a rename collide.

### M2. Step 6 must be two atomic commits, not one

**Where**: `architecture-target.md` §4 Phase 1 step 6 (lines 277-283).
**Why it matters**: The audit (`architecture-audit-monkeypatch.md` §1) lists 21 patch sites that must move from `schedule_optimizer.*` to new namespaces, AND 17 routes whose imports flip, AND the shell deletion itself, AND the `services/__init__.py` deletion. Bundling these into one commit means the parity gate runs against a state where (a) the shell is gone, (b) tests are flipped, (c) routes are flipped — so any one of those failing produces an unbisectable regression.
**Concrete fix**: Step 6a = route imports flip + shell _kept_ + parity 27/27 gate. Step 6b = tests flip + shell delete + parity gate. The 21-test flip is the highest-risk single op in the round (audit D §6 ranks `schedule_optimizer` shell as risk-1 with 21 patches), and it deserves an isolation boundary.

### M3. SchedulerState mitigation needs a concrete assertion

**Where**: `architecture-target.md` §4 Phase 4 (lines 309-314), §7 row 6 (LLM/DB-stateful drift, _not_ the SchedulerState row).
**Why it matters**: §7 mentions retry contamination only obliquely ("매 retry 마다 새 SchedulerState"). The driver doc §8.3 calls this a **risk** and §11 row 9 reiterates. But "matures retry harness" is not testable. I would not approve Phase 4 with this wording — the user's `feedback_constraint_arch_simplicity` memory says dataclass yes, hidden state no.
**Concrete fix**: Add to §4 Phase 4: "`auto_schedule.py:160-300` retry block constructs `SchedulerState()` **inside** the retry loop body (not before it); add `tests/test_scheduler_state_isolation.py` with one test that monkeypatches `_run_optimization_once` to mutate `state.timeline`, runs 2 retries, asserts retry 2's `state.timeline == {}` at entry."

### M4. lex_min_time wiring is new-feature work, not maintenance

**Where**: `architecture-target.md` §3 row 5, §4 Phase 3 (lines 295-308).
**Why it matters**: I verified `solver/lex_min_time.py` — confirmed by audit C §5.5 ("Zero importers"). Phase 3 introduces the `min_time_mode: bool` flag _and_ the call site _and_ fixtures 12/13 _and_ INFEASIBLE fallback semantics. The plan §4 Phase 3 shows a 7-line if/else snippet but does not address: (a) what `result` shape `solve_lex_min_time` returns vs `solve_weighted_sum` (the `LexResult` dataclass is different), (b) what happens on INFEASIBLE in lex mode (does it fall through to weighted-sum, or 410?), (c) how Phase 5 records the comparison if both modes ran on different `run_label` (the §9.2 curl issues two POSTs against the same run_label — only legal if Stage2 is idempotent for the same input, which it isn't because of `_purge_run_data`).
**Concrete fix**: Before Phase 3 starts, add a §4.3a sub-step: "Adapter from `LexResult` → existing `BuiltModel.solve_result` shape, INFEASIBLE → fallback to weighted-sum with audit log entry, two separate run_labels for §9.2 comparison." Otherwise Phase 3 will discover at hour 2 that the fixtures need new endpoints.

### M5. ConstraintParams pure-function split is mis-classified

**Where**: `architecture-target.md` §1.3 row "application/_shared/" justification, §4 step 3, §2 line 79.
**Why it matters**: The plan says "DB load + 도메인 rules — 분리 비용 대비 가치 낮아 한 파일 유지." Audit A §3 disagrees and is right: `resolve_spec_setup_min` and `resolve_color_change_min` are pure dispatch over a frozen dataclass — zero DB. Eight callers (audit A row `constraint_params.py`) include `solver/constraints/process/sheath_color_hard.py` which is itself supposed to be in `application/scheduling/cp_sat/constraints/`. So the plan ends up with `cp_sat/constraints/` (use-case layer) calling `\_shared/constraint_params.py:resolve__`(cross-cutting layer) for what is plainly a domain rule. This is the same anti-pattern audit B §3 flags as the route-ORM coupling.
**Concrete fix**: Split into`domain/constraint*rules.py`(the two`resolve*_`functions, pure) +`application/\_shared/constraint_params.py`(the dataclass +`.load()` classmethod). Cost is one extra file and updating 9 import sites — already in scope for step 3, near-zero marginal cost. This raises dimension 1 from 8 to 10.

---

## 4. Top 5 Nice-to-Have

1. **Cycle detector in §6 gate** — Add `python -c "import app.main; from app import services" || exit 1` to the per-step block. Catches the audit B §6 latent cycles without needing a tool.
2. **Phase 4 SchedulerState frozen-by-default** — Use `@dataclass(frozen=True)` for the read-only master-data fields (`equipment_by_process`, `speed_map`, `constraint_params`, `welding_min`); keep timeline/predecessor as `field(default_factory=...)`. Half-frozen split makes contamination _impossible_ for the read-only half.
3. **§5 should explicitly preserve `cascade/__init__.py`** — Audit A confirms it's a 17-LOC re-export shell at `services/cascade/`. After the move to `application/cascade/`, the inits must follow. Plan §2 line 131 says "그대로" but doesn't list the inits in §4 step 5 explicitly.
4. **Phase 2 unit-test coverage on hallucination filter** — §4 Phase 2 says "신규 unit tests" generically. State the contract: filter must reject N≥3 specific hallucinated noun classes for both `explain_batch` and `summarize_run`. Otherwise the LLM_NONDETERMINISTIC parity policy hides regressions.
5. **Effort revision in §10** — User-facing approval table benefits from a corrected total: Phase 1 alone is realistically 5-6h with the M1+M2 split. Communicate this so the user can plan multi-session.

---

## 5. Devil's Advocate

A peer EM would push back: **"You're approving a plan where the very first big-bang step (Phase 1) moves ~30 modules across 6 sub-steps in one session, and the only safety net is a 27-endpoint response diff. The audit found 79 boundary violations and a known monkeypatch cycle (`schedule_optimizer ↔ greedy.auto_schedule`) — both still standing after Phase 1. Why not Phase 1A (boundary fixes), Phase 1B (move), and gate Phase 1B on Phase 1A green?"**

My response: the plan is correct to defer the 19 plan_pipeline ORM violations to post-pilot (§9 row 4) — those would double the round and the pilot deadline is real. But the peer is right that Phase 1 step 6 is the single largest single-session risk in the entire round. M2 above (split step 6) is the minimum acceptable mitigation; if the team wants extra safety, also add a **Phase 1 dry-run** step where step 1-5 land but step 6 is gated behind a separate user approval at end of session. The shell can sit for 24h with no harm — audit D confirms no production callers depend on its _absence_. That single change (one more approval gate, zero rework cost) takes Phase 1 from "high-risk session" to "medium-risk session with rollback point."

---

## 6. Sign-off Conditions

Before Phase 1 step 1 atomic commit:

- [ ] M1 — split step 4 into 4a/4b
- [ ] M2 — split step 6 into 6a/6b
- [ ] M3 — SchedulerState retry assertion + isolation test in §4 Phase 4
- [ ] M4 — lex adapter + fallback + dual-run_label clarification in §4 Phase 3 / §9.2
- [ ] M5 — ConstraintParams pure-rule split into `domain/constraint_rules.py`

After these patches, the plan is execution-ready and I sign off.

**Files referenced**:

- `/Users/jaewookim/Desktop/Project/KBI_PoC/docs/architecture-target.md`
- `/Users/jaewookim/Desktop/Project/KBI_PoC/docs/next-session-prompt-v2.md`
- `/Users/jaewookim/Desktop/Project/KBI_PoC/docs/architecture-audit-services.md`
- `/Users/jaewookim/Desktop/Project/KBI_PoC/docs/architecture-audit-boundaries.md`
- `/Users/jaewookim/Desktop/Project/KBI_PoC/docs/architecture-audit-cleanup.md`
- `/Users/jaewookim/Desktop/Project/KBI_PoC/docs/architecture-audit-monkeypatch.md`
- `/Users/jaewookim/Desktop/Project/KBI_PoC/backend/app/services/cp_sat_optimizer.py` (lines 49, 75-82, 311, 1334, 1547-1553)
- `/Users/jaewookim/Desktop/Project/KBI_PoC/backend/app/services/greedy/auto_schedule.py` (lines 160, 164, 494)
- `/Users/jaewookim/Desktop/Project/KBI_PoC/backend/app/services/sheath_cluster.py` (verified zero infrastructure deps)
- `/Users/jaewookim/Desktop/Project/KBI_PoC/backend/app/services/audit_logger.py` (verified 6 callers across 5 modules — cross-cutting confirmed)
- `/Users/jaewookim/Desktop/Project/KBI_PoC/backend/app/services/solver/lex_min_time.py` (218 LOC, 0 callers — confirmed)
