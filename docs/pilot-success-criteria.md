# Pilot Success Criteria — KBI Production Scheduler

**Status:** `[BEST-GUESS]` — awaiting KBI stakeholder confirmation (Week 2)
**Owner:** jaewoo kim (Samil PwC AI team)
**Last updated:** 2026-04-23 (Task 1.8, Week 1 of Production Handoff Refactor)
**Target pilot go-live:** 2026-06-25 (Week 9 merge)

---

## 1. Purpose & status

This document defines what "the KBI pilot is a success" means in measurable
terms, so that at pilot end (Week 10+) there is a single artifact to evaluate
against — not a negotiation about expectations after the fact.

It is consumed by three audiences:

1. **KBI scheduler & plant manager** — knows what "working" looks like.
2. **Samil PwC engagement team** — knows what to demo, what to instrument.
3. **Internal engineering (this refactor)** — knows what gates Week 9 merge
   and what the CI parity / perf gates have to protect.

**Current status:** best-guess written in Week 1 per spec §6 Q6 and plan
decision D8-B. Every numeric target is labeled:

- `[BEST-GUESS]` — a reasonable starting number the engagement team chose;
  expect it to move after Week 2 stakeholder validation.
- `[TO CONFIRM WITH KBI]` — we literally do not know the answer; must be
  filled in at the Week 2 meeting.
- `[FROM SPEC]` — already fixed by spec/plan; not negotiable in the
  stakeholder meeting (e.g. parity 11/11).

Week 2 stakeholder call revises and re-commits this doc. See §7.

---

## 2. Scope of the pilot

| Dimension             | Value                                                                                                                                               | Label                   |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| Primary user          | KBI factory scheduler, 1 named user                                                                                                                 | `[BEST-GUESS]`          |
| UI language           | Korean only (no English fallback strings)                                                                                                           | `[FROM SPEC]`           |
| Work performed        | Reschedule production in response to daily/weekly order changes; operator-override + 1-click undo; read Decision Card rationale for each suggestion | `[FROM SPEC]`           |
| System being replaced | Current scheduling practice at KBI (spreadsheet / legacy tool — unknown)                                                                            | `[TO CONFIRM WITH KBI]` |
| Pilot duration        | 2–4 weeks of real daily use before broader rollout decision                                                                                         | `[BEST-GUESS]`          |
| Pilot window start    | On or around 2026-06-25 (Week 9 merge → production cutover)                                                                                         | `[FROM SPEC]`           |
| Plant scope           | Cosmolink factory only (single tenant)                                                                                                              | `[FROM SPEC]`           |

### Non-goals (explicit)

These are NOT what the pilot is judged on. If a stakeholder asks for them
mid-pilot, the answer is "post-pilot":

- Multi-tenancy (KBI-only).
- RBAC / authentication / multi-user concurrency.
- Mobile / tablet UI (desktop browser only).
- Auto-decisioning — operator remains in the loop for every schedule change.
- English UI.
- Automatic constraint learning (D7-C defers to post-pilot).

---

## 3. Quantitative acceptance criteria

Organized by theme. Each row: metric / target / measurement method / label.

### 3a. Correctness / parity

| Metric                                             | Target                                            | Measurement                            | Label                    |
| -------------------------------------------------- | ------------------------------------------------- | -------------------------------------- | ------------------------ |
| Parity harness fixtures green at Week 9 merge      | 11 / 11 (same hashes as Week 1 freeze)            | `make parity`                          | `[FROM SPEC]` (Task 1.5) |
| CI parity workflow green on `main` / `refactoring` | All of Weeks 2–9 (no red week)                    | GitHub Actions history                 | `[FROM SPEC]`            |
| `alembic upgrade head` from empty DB               | Succeeds cleanly                                  | Migration CI gate                      | `[FROM SPEC]` (spec §9)  |
| `alembic downgrade` reversibility                  | No data loss; deliberately no-op when destructive | Migration reversibility gate (spec §9) | `[FROM SPEC]`            |

### 3b. Performance

| Metric                                               | Target                                | Measurement                                      | Label                                                                                          |
| ---------------------------------------------------- | ------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| Solver p99 regression per fixture                    | ≤ 2× Week 1 baseline; 0 fixtures over | `check_performance_regression.py` in CI          | `[FROM SPEC]` (Task 1.6)                                                                       |
| Solver p99 warn band                                 | 1.5× – 2× baseline → warn, not fail   | Same CI script                                   | `[FROM SPEC]` (Task 1.6)                                                                       |
| Single optimization run wall-clock, KBI nominal load | p99 < 60s                             | Production monitoring (`solver_run.duration_ms`) | `[BEST-GUESS]`; `[TO CONFIRM WITH KBI — what IS nominal load size (orders, equipment, days)?]` |
| UI page first paint (main Gantt view)                | < 3s                                  | Playwright timing or stopwatch                   | `[BEST-GUESS]`                                                                                 |
| Decision Card render after selection                 | < 500ms                               | Playwright timing                                | `[BEST-GUESS]`                                                                                 |

### 3c. Adoption / usage

| Metric                                               | Target                                             | Measurement                                                      | Label                                                                    |
| ---------------------------------------------------- | -------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Business days in pilot window with ≥ 1 solver run    | ≥ 15 of ~20 days                                   | `SELECT date_trunc('day', created_at), count(*) FROM solver_run` | `[BEST-GUESS]`                                                           |
| Operator overrides per week                          | Track only — no threshold                          | `schedule_change_sets` row count by week                         | `[BEST-GUESS]`                                                           |
| Weeks with 0 overrides                               | Alert if ≥ 1 full week (scheduler is not engaging) | Same query                                                       | `[BEST-GUESS]`                                                           |
| Escalation rate (scheduler falls back to old system) | ≤ 1 business day / month                           | Manual log kept by KBI scheduler                                 | `[TO CONFIRM WITH KBI — what IS the fallback path? Spreadsheet? Paper?]` |

### 3d. Audit / defensibility

These are hard gates — a CPA-level audit must pass.

| Metric                                                                                                             | Target                | Measurement                                                             | Label                    |
| ------------------------------------------------------------------------------------------------------------------ | --------------------- | ----------------------------------------------------------------------- | ------------------------ |
| Matched run_id triad: every solver run has `solver_run` + `solver_decision` (+ `schedule_change_sets` on override) | 100% (no orphan rows) | run_id correlation query per spec §9                                    | `[FROM SPEC]`            |
| Non-empty `override_reason` on every manual override                                                               | 100%                  | `SELECT count(*) WHERE override_reason IS NULL OR override_reason = ''` | `[FROM SPEC]`            |
| `parity-update:` commits have WHY body (business driver, not "bumped hash")                                        | 100%                  | PR review checklist                                                     | `[FROM SPEC]` (Task 1.5) |
| `constraint_config` version stamp on every solver run                                                              | 100%                  | `solver_run.constraint_config_version NOT NULL`                         | `[FROM SPEC]` (spec §9)  |

---

## 4. Qualitative acceptance criteria

Each must be verifiable by observation — not belief, not "feels right."

- **Explainability passes a CPA-level audit.** An auditor with no factory
  domain knowledge reads Decision Card + trace and can state, in writing,
  WHY a specific batch landed on specific equipment at a specific time —
  within 5 minutes per decision. Method: dry-run with one Samil PwC
  auditor-colleague (not the engagement team) at Week 8.
  `[BEST-GUESS — 5 min is generous; may revise to 3 min after dry-run]`

- **Korean UI complete.** No English leakage in operator-visible strings
  (button labels, tooltips, error messages, Decision Card narrative,
  변경 이력 diff viewer, 제약 on-off 탭). Method: Playwright snapshot of
  every page + native-Korean-speaker review. `[FROM SPEC]`

- **Decision Card never empty.** Every assignment has non-blank weight bar
  chart + LLM one-liner. When LLM is unavailable (rate-limited, network
  down), graceful degradation to template narrator (`LLM_PROVIDER=template`).
  User never sees "error" or blank panel. `[FROM SPEC]` (spec §8d)

- **LLM hallucination rate ≤ 5%.** Kiwipiepy-based Korean morphological
  filter catches ungrounded claims. Method: run LLM narrator over the 11
  parity fixture outputs; count grounded vs ungrounded claims. Target:
  ≥ 95% grounded. `[FROM SPEC]` (spec §8d)

- **Operator can undo any override within 1 click.** Verified by Playwright
  script simulating scheduler workflow: override → observe schedule change
  → click 되돌리기 → schedule back to pre-override state (byte-for-byte).
  `[FROM SPEC]`

- **Breaking-glass recovery rehearsed.** Week 8 dry-run: destroy a staging
  Supabase project, restore from schema archive + `constraint_config`
  sample, verify solver runs green on a parity fixture. Target wall-clock:
  < 1 hour from "project is gone" to "first green parity run."
  `[BEST-GUESS — may be longer on first rehearsal]`

- **Decision Card narrative is truthful.** For each of the 11 parity
  fixtures, an engagement team member verifies the LLM one-liner actually
  describes the top weight bars (not a plausible-sounding fabrication).
  Target: 11/11 fixtures pass spot-check. `[FROM SPEC]`

---

## 5. Out-of-scope (explicit non-criteria)

So stakeholders don't push these mid-pilot and create scope creep:

- Automatic constraint learning / "LLM proposes new constraint weights"
  (D7-C: post-pilot).
- Multi-user / RBAC / authentication.
- Mobile or tablet UI.
- Multi-factory support (KBI-only for now).
- English UI (Korean first; English is post-pilot).
- Real-time collaborative editing of the schedule.
- Automated regrading of historical schedules against new constraint
  versions.

---

## 6. Failure criteria (auto-rollback triggers)

Conditions under which the pilot is paused and rolled back to the prior
system. Any one of these trips a rollback conversation (not necessarily an
immediate rollback — but mandatory RCA within 24h).

| Trigger                                                | Threshold                                                                 | Detection                                                         | Action                                                                                |
| ------------------------------------------------------ | ------------------------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Parity regression                                      | Any fail in `check_performance_regression.py` with default `--fail-ratio` | CI on every deploy                                                | Freeze deploy; RCA; hotfix or rollback                                                |
| Solver INFEASIBLE rate                                 | > 10% of runs over 3 consecutive business days                            | `SELECT count(*) FROM solver_run WHERE status='INFEASIBLE'` daily | Admin surface alert; likely constraint misconfig — revert `constraint_config` version |
| `schedule_change_sets` write failure                   | > 2 failed overrides per day for 2 consecutive days                       | Error log / Supabase metrics                                      | DB-level investigation; pause override feature                                        |
| Scheduler reports "unable to use"                      | 2+ consecutive business days                                              | KBI scheduler direct report                                       | Rollback to prior system; full RCA                                                    |
| Decision Card empty / LLM hard-down > 4 business hours | Template fallback also failing                                            | UI monitoring                                                     | Investigate; no rollback unless parity also breaks                                    |
| Audit triad incomplete                                 | Any orphan `solver_run` without matched `solver_decision`                 | Nightly correlation query                                         | Investigate — defensibility gate broken                                               |

Thresholds are `[BEST-GUESS]` except parity (from spec).

---

## 7. Success criteria review cadence

| When                    | What                                                                                                                   | Output                                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Week 2                  | KBI stakeholder call validates/revises these numbers (scope, load assumptions, fallback path, duration)                | New commit to this doc: replaces `[BEST-GUESS]` / `[TO CONFIRM WITH KBI]` markers with confirmed values |
| Week 6                  | Mid-pilot review at dry-run checkpoint — spot-check quant metrics against Week 1 targets; adjust if systematically off | Commit if criteria revised                                                                              |
| Week 8                  | Breaking-glass rehearsal; CPA-audit dry-run; Korean-UI native-speaker review                                           | Commit rehearsal results as §4 evidence                                                                 |
| End of pilot (Week 10+) | Final evaluation against this doc                                                                                      | Pass/fail report; go/no-go for broader rollout                                                          |

---

## 8. Change log

| Date       | Change                                                                                                                                        | Author              |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| 2026-04-23 | Initial best-guess criteria (Task 1.8, Week 1); all numeric targets labeled `[BEST-GUESS]` / `[TO CONFIRM WITH KBI]` / `[FROM SPEC]` per D8-B | Claude / jaewoo kim |

---

**Cross-references:**

- Spec: `docs/specs/2026-04-23-production-handoff-refactor-design.md`
  (§6 Q6 best-guess rationale; §8d LLM narrator + kiwipiepy filter;
  §9 observability + run_id triad + migration reversibility; §14
  Deliverables — this doc listed).
- Plan: `docs/plans/2026-04-23-production-handoff-refactor-plan.md`
  (Task 1.5 parity harness; Task 1.6 perf regression gate; Task 1.8
  this document; Task 5.\* pilot cutover & rollback).
