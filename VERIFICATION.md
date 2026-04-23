# Week 0 Retrospective Verification — Production Handoff Refactor (Rev 3)

Date: 2026-04-23
Branch: refactoring
HEAD at verification: 3182792
Commits verified: c70adaf..3182792 (14 commits)

## Overall verdict: **PASS-with-caveats**

All 7 Week 0 goals are substantively achieved. Five are clean PASS, two are PASS-with-caveats (track worktrees pinned pre-b20cf81 so their local `Makefile`+`.gitignore`+CI-trigger list are stale; archive README still labels the batch_group migration as an open bug). No FAILs, nothing that blocks Week 1 kickoff. Every goal has concrete evidence in the repo; no claim depends on commit messages alone.

## Headline findings

- **Baseline is real and matches the archive.** Supabase `constraint_config_history` has exactly 38 rows with `changed_by='PHASE0_INITIAL_20260423'`, all at `2026-04-23T10:14:49.889787Z` — identical rowcount to `docs/archive/constraint_config_sample_20260423.json`. Schema dump (1060 lines, 18 `CREATE TABLE`s, `batch_group` + `constraint_config_history` present) is a usable break-glass artifact.
- **Alembic chain is surgically correct.** `f2900467a547 → b9e2f4a6d018 → c3d4e5f6a7b8` confirmed via `alembic history`; `upgrade()` is idempotent (information_schema guard), `downgrade()` is an intentional Supabase-safety no-op with an explanatory docstring. `alembic current` on Supabase = `a7c9e11d4f22` (head). 439 tests collect cleanly; pytest-asyncio pinned at 0.23.8.
- **Worktree tooling works, but the two track worktrees are frozen at 55b154a** (they precede b20cf81's Makefile/plan/spec fix). Result: `make doctor` in `../KBI_PoC_track_{a,b}` emits a false-positive `✗ expected refactoring/track-a-solver` because their local Makefile is stale. The main worktree's `doctor` is clean. Both tracks also show `?? backend/venv` untracked — `.gitignore` matches `backend/venv/` (dir) but not the symlink. Neither blocks Week 1, but a track-side `git merge refactoring` (or even `git pull`) before anyone actually runs tests there is required.
- **CI is Path-D-safe and YAML-valid.** `services.postgres:15` with throwaway creds, `LLM_PROVIDER=template`, zero Supabase references, triggers include `refactoring-track-a-solver` and `refactoring-track-b-admin`. Backend job excludes parity harness (deferred to Task 1.9 per Decision D9-A).
- **Private-symbol inventory is AST-based, deterministic, and matches spec invariant.** Regen produced zero diff; exactly 4 `##` H2 headings (3 source modules + 1 non-test). 15 test-side + 2 non-test-side private symbols captured for the D7-C invariant.
- **One real doc drift:** `docs/archive/README.md` still labels the `alembic upgrade head` `batch_group` failure as an open bug ("will not work until that bug is fixed"). It was fixed in commit 6126ee0. Fix the README opportunistically in Week 1.

---

## Goal 1: Break-glass baseline

**Verdict**: **PASS**

**Evidence**:

- `docs/archive/schema_asis_20260423.sql` — 1060 lines, 18 `CREATE TABLE` statements, pg_dump header preserved (`Dumped from database version 17.6`); includes `production_batch.batch_group character varying(50)` at line 367 and `constraint_config_history` CREATE TABLE at line 110.
- `docs/archive/constraint_config_sample_20260423.json` — JSON with `exported_at: 2026-04-23T09:49:25.071129Z`, `row_count: 38`, `rows: [38 items]` (shape `{exported_at, row_count, rows}` is a superset of a naive array — more useful as an audit artifact).
- Supabase `constraint_config_history` SELECT (read-only, via venv SQLAlchemy engine against pooler host `aws-1-ap-northeast-2.pooler.supabase.com`):
  ```
  COUNT, MIN, MAX: (38, 2026-04-23T10:14:49.889787+00:00, 2026-04-23T10:14:49.889787+00:00)
  distinct markers: ['PHASE0_INITIAL_20260423']
  ```
  38 rows match the JSON archive rowcount. MIN==MAX confirms atomic single-transaction insert (commit b180b97, `scripts/tag_phase0_baseline.py`).
- `scripts/tag_phase0_baseline.py` is idempotent (line 44-50: bails out if the marker already exists); uses the narrow-schema reality (`changed_by` string, `new_params_json` NOT NULL with `{}` coercion on line 65). Docstring aligns with spec §8b.
- Spec §8b (lines 294-311) is fully rewritten to the marker convention, explicitly **drops** Rev 2's `is_baseline`/`baseline_tag_name`/... column additions, and plans a Week 2 one-off SQL rename of `PHASE0_INITIAL_20260423 → BASELINE_phase0-initial_20260423T101449Z`. The 2026-04-23T10:14:49Z timestamp written in spec line 311 matches Supabase reality exactly.

**Gap**: None material. (Minor: the JSON archive has the timestamp `09:49:25Z` while the Supabase PHASE0 insert is `10:14:49Z` — a ~25-minute gap is expected because the pg_dump + JSON export run in Task 0.1 predated the Task 0.2 marker insert; this is correct and intentional.)

---

## Goal 2: Repeatable bootstrap

**Verdict**: **PASS**

**Evidence**:

- `make doctor` in main worktree (transcript in appendix): all checks green, `alembic at head (a7c9e11d4f22)`, `pytest discoverable`, `vitest discoverable`, `branch matches worktree (refactoring)`.
- `Makefile` `bootstrap` target: requires `python3.11`, creates venv, pip-installs `backend/requirements.txt`, copies `.env.worktree.example` → `.env.worktree` if missing, **fail-fasts** if `backend/.env` lacks `DATABASE_URL` (exit 1), runs `alembic current` for visibility. Deliberately does NOT auto-run `upgrade head` on the shared Supabase DB — matches spec §9 line 481 discipline.
- `doctor` target performs the 4 checks required by the phase goal: (a) `.env.worktree` presence, (b) `DATABASE_URL` in `backend/.env`, (c) alembic head == alembic current comparison (warns, doesn't mutate), (d) branch/worktree matching.
- `.env.worktree.example` and live `.env.worktree` are byte-identical per `diff` (template-shipped); both declare `LLM_PROVIDER=template` as default.
- Scripts pass `bash -n` syntax check (run on all 4 scripts: `setup_worktrees.sh`, `run_id_grep.sh`, `worktree_cd.sh`, `worktree_status.sh` → all OK).
- `setup_worktrees.sh` uses `set -euo pipefail` (line 16); is idempotent at every level (branch creation, worktree add, env copy, symlink creation all guarded); uses `ln -sf "$MAIN_ENV" "$link"` for `backend/.env` (line 91, not `cp` — matches spec requirement).

**Gap**: None. The separate "Makefile assumption that `backend/venv` is symlinked from main" is called out in Red Flags (see below) but the current setup is coherent: `setup_worktrees.sh` handles the symlink; `doctor` will still emit `✗ pytest missing` cleanly if the symlink isn't there.

---

## Goal 3: Path-D-safe CI

**Verdict**: **PASS**

**Evidence**:

- `.github/workflows/ci.yml` parses as valid YAML (`python -c "import yaml; yaml.safe_load(...)"` → no exception).
- Line 17-29: `services.postgres:15` with throwaway creds `kbi / kbi_poc_2026 / kbi_scheduler`, 10s healthcheck, 10 retries.
- Line 32: `DATABASE_URL=postgresql://kbi:kbi_poc_2026@localhost:5432/kbi_scheduler` — points at the ephemeral service, never Supabase.
- Line 33: `LLM_PROVIDER: template` — forces deterministic fallback, matches D9-A strict-hash parity discipline.
- Line 7, 10: trigger branches include `[main, refactoring, refactoring-track-a-solver, refactoring-track-b-admin]` (both push and pull_request).
- `grep -n 'SUPABASE\|supabase' .github/workflows/ci.yml` returns no hits — comment-only mention of "Supabase-native for dev" on line 1 is the only instance of the string and it explicitly disclaims CI usage.
- Line 56: backend job runs `pytest -q --ignore=tests/test_parity_harness.py` — parity excluded as required by Task 1.9 deferral.
- Frontend job runs `npm ci` + `lint` + `typecheck` + `test` on Node 20, which matches `frontend/package.json` `vitest` dev-dep (line 34) and the `typecheck: tsc --noEmit` / `test: vitest run` scripts that exist.

**Gap**: None.

---

## Goal 4: Worktree tooling

**Verdict**: **PASS-with-caveats**

**Evidence**:

- `git worktree list` returns exactly 3 entries with branches `refactoring`, `refactoring-track-a-solver`, `refactoring-track-b-admin` (dash-separated, matching the b20cf81 correction to spec+plan).
- `bash scripts/worktree_status.sh` output (appendix) — tabulates all 3 worktrees, their branches, dirty counts, and last-commit SHAs.
- Port isolation works: main `.env.worktree` = `BACKEND_PORT=8000 / FRONTEND_PORT=3000`; track_a = `8001/3001`; track_b = `8002/3002`. No collisions.
- `../KBI_PoC_track_a/backend/.env` and `.../track_b/backend/.env` are symlinks to `/Users/jaewookim/Desktop/Project/KBI_PoC/backend/.env` (verified via `ls -la`). Same for `backend/venv` (shared Python interpreter, saves ~5 min per worktree bootstrap).
- `setup_worktrees.sh` refuses to clobber a real file masquerading as a symlink (lines 86-90 and 113-117) — safe to re-run on a partially-set-up worktree.
- `worktree_cd.sh` handles both bash and zsh (`BASH_SOURCE[0]` vs `(%):-%x` fallback), detects accidental execution, exports `BACKEND_PORT/FRONTEND_PORT/LLM_PROVIDER`.
- Plan Task 0.7 (plan line 339) and spec §9 table (design.md lines 460-461, 490) consistently use `refactoring-track-a-solver` / `refactoring-track-b-admin`.

**Caveats** (not blockers; Week 1 housekeeping):

1. `../KBI_PoC_track_a` and `../KBI_PoC_track_b` are both pinned at commit `55b154a`, which is **before** b20cf81 landed. Their local copies of `Makefile`, plan, spec, CI YAML, inventory doc, and troubleshooting.md are stale. `make doctor` in either track therefore says `✗ expected refactoring/track-a-solver, got refactoring-track-a-solver` — false negative triggered by stale Makefile, not by actual misconfiguration. A `git merge refactoring` (or at least `git pull`) in each track resolves this in one command.
2. Both tracks show `?? backend/venv` in `git status` (see worktree_status output dirty=1 on each). Root cause: `.gitignore` declares `backend/venv/` with a trailing slash (directory match), but `backend/venv` is a symlink; git sees a file-that-isn't-ignored-by-the-dir-pattern. Adding `backend/venv` (no slash) to `.gitignore` would fix it. Cosmetic.
3. The Task 0.7 executor's note about "tracks pinned at 55b154a" — the verification confirms that diagnosis is accurate.

---

## Goal 5: Known-bug fixes

**Verdict**: **PASS**

**Evidence (alembic batch_group)**:

- `backend/alembic/versions/b9e2f4a6d018_add_batch_group_column.py` — revision=`b9e2f4a6d018`, down_revision=`f2900467a547`; `upgrade()` guards with `information_schema.columns` SELECT before `op.add_column` (idempotent on Supabase which already had the column); `downgrade()` is a `pass` with a thorough docstring justifying the Supabase-safety no-op (lines 53-62).
- `backend/alembic/versions/c3d4e5f6a7b8_add_unassigned_index_and_reason.py` — confirmed `down_revision: Union[str, Sequence[str], None] = "b9e2f4a6d018"` (line 14), so the chain is `f2900467a547 → b9e2f4a6d018 → c3d4e5f6a7b8` as required.
- `alembic history` output (appendix) shows the three revisions in the correct order.
- `alembic current` against Supabase returns `a7c9e11d4f22 (head)` → no drift between code head and DB state.

**Evidence (pytest-asyncio)**:

- `backend/requirements.txt:15` pins `pytest-asyncio==0.23.8` exactly.
- `pytest --collect-only` succeeds: `439 tests collected in 0.61s` (appendix). No `AttributeError: 'Package' object has no attribute 'obj'` from the asyncio plugin. Only warning is an unrelated Pydantic 2.x deprecation notice.
- Troubleshooting guide (docs/troubleshooting.md lines 61-65) documents the symptom and references commit `55b154a` as the fix SHA — both `55b154a` and `6126ee0` resolve to real commits via `git log`.

**Gap**: None.

---

## Goal 6: Private-symbol inventory

**Verdict**: **PASS**

**Evidence**:

- `scripts/gen_private_symbol_inventory.py` uses `ast.ImportFrom` (line 84), not regex — correctly handles the multi-line import form the docstring warns about.
- Determinism: `python3 scripts/gen_private_symbol_inventory.py > /tmp/psi_regen.md; diff docs/private-symbol-inventory.md /tmp/psi_regen.md` → **no diff** (zero bytes of output). Re-run produces byte-identical output.
- `grep -E '^## ' docs/private-symbol-inventory.md` returns exactly 4 H2 headings:
  - `cp_sat_optimizer (9 symbols)`
  - `schedule_optimizer (3 symbols)`
  - `batch_grouping (3 symbols)`
  - `Also imported by non-test code (2 symbols)`
    3 source modules + 1 non-test section = matches the documented invariant on line 215 of the generator.
- Totals footer: 15 test-side + 2 non-test-side = 17 distinct (module, symbol) pairs — this is the D7-C re-export surface.
- Source-side naming preserved for aliased imports (line 92: `name = alias.name`) — required to guarantee re-export shells use the right symbol name.
- Exits 2 on `SyntaxError` during collection (fail-fast per user's global conventions; a skipped file would silently let a Week 2 `rm` remove a symbol still in use).

**Gap**: None.

---

## Goal 7: Doc consistency (Path D)

**Verdict**: **PASS-with-caveats**

**Evidence**:

- Spec §9 (Rev 3) rewritten end-to-end: §9.472 declares "All worktrees read `DATABASE_URL` from the shared `backend/.env`"; §9.481 warns against auto-upgrade on a shared DB; §9.492 "Shared-DB discipline (Rev 3)" codifies 4 rules for migration coordination; §16 change log entries at lines 648-650 document Rev 3 and the subsequent §8b reshape in lucid detail.
- Plan Rev 3 Task 0.1b spells out the batch_group fix (plan lines 64-163), Task 0.7 uses dash-separated branch names (line 339), doctor target in plan's Task 0.5 matches the repo Makefile (plan lines 262-268).
- `grep -Rn 'refactoring/track' docs/` returns 1 hit: plan line 339, and that hit is the **explicit warning** "NOT `refactoring/track-*`". No operator would be misled.
- `grep -Rn 'docker compose up.*backend\|docker compose up.*frontend' docs/` returns 1 hit: `docs/troubleshooting.md:34` — that hit is the **anti-pattern symptom string** in the section titled `` `docker compose: service not found` `` which teaches operators NOT to do this. Context on line 35: "Path D runs backend and frontend **native**." Intended pedagogical instance, not an instruction.
- `docs/troubleshooting.md` references correct SHAs: `55b154a` (pytest-asyncio pin, line 65) and `6126ee0` (batch_group migration, line 72). Both resolve.
- `.github/workflows/ci.yml` first line: `# Path D: Supabase-native for dev; CI uses throwaway Postgres service` — correctly distinguishes dev vs CI posture.
- Tests referencing `DATABASE_URL` are `backend/tests/{test_migration_preflight.py, test_jit_integration.py, conftest.py, test_batch_group_lifecycle.py, test_sheath_spec_list.py, api/test_constraints_params.py}` — 6 files. This is integration test scope and acceptable for now; Week 1 risk noted below.

**Caveats**:

1. **Real drift**: `docs/archive/README.md` lines 53-57 still label the alembic `batch_group` failure as an open bug ("restoration from a fully-fresh DB will not work until that bug is fixed"). The bug is closed as of commit `6126ee0`. This is a break-glass guide that an operator reads under stress — it should be updated to say "Fixed 2026-04-23 by migration `b9e2f4a6d018_add_batch_group_column` (commit `6126ee0`)". Not urgent; low-risk content since other docs are correct, but worth a 2-line fix in Week 1.
2. `docker-compose.yml` is still present and the Makefile's `db-fresh` target still uses it (lines with `docker compose up -d db`). Consistent with the spec §9 intent ("`db-fresh` CI-like helper...normal dev does NOT need this", plan line 220); nothing misleading, just noting that Path D did not actually delete `docker-compose.yml`, and that's correct per the Rev 3 decision ("Leave `docker-compose.yml` as-is", plan line 330).

---

## Red flags / follow-ups for Week 1

1. **Track worktrees are stale (55b154a pre-b20cf81)** — Before anyone lands actual Week 1 code in track_a or track_b, run `git -C ../KBI_PoC_track_a merge refactoring` (equivalently for track_b). Otherwise they'll run against a Makefile, plan, spec, troubleshooting doc, and CI config that all predate the b20cf81 branch-naming correction (among other things). Low-impact because both tracks today have **no unique work**; cost of fixing today = 2 commands, cost of fixing after work piles up = conflict-prone rebase.

2. **Pre-existing test brittleness against Supabase** — `pytest --collect-only` succeeds (439 tests) but 6 test files (listed in Goal 7 evidence) directly reference `DATABASE_URL`. In CI the throwaway Postgres will serve them, but locally a solo dev running `make test` or `make verify` executes them against the **shared Supabase instance**. If any mutates schema or leaks rows, all 3 worktrees + any running backend are affected. Add Week 1 Task 1.0: audit these 6 files for `db.begin_nested() + rollback` discipline, and consider a `pytest.ini` `env` override forcing a separate schema (`SET search_path`) for integration runs. Directly connects to the "Shared-DB discipline" rule spec §9.492 says must already be in force.

3. **`docs/archive/README.md` still labels Task 0.1b as an open bug** — The break-glass doc is the one that gets read during incidents; it's misleading today. 5-minute fix: replace the last bullet of "Known constraints on restoration" with "Fixed 2026-04-23 by migration `b9e2f4a6d018_add_batch_group_column` (commit `6126ee0`); restoration from an empty schema now succeeds."

Minor follow-ups (informational, not risks): the `?? backend/venv` untracked-symlink noise in track worktrees (one-line .gitignore fix), the absent `scripts/restore_constraint_config.py` mentioned by archive/README.md (plan says "create on demand", so not a drift — just a note that the break-glass path has one step that requires ad-hoc scripting).

---

## Runtime check transcript (appendix)

### `make doctor` — main worktree

```
═══ KBI_PoC — doctor ═══
  ✓ .env.worktree present
  ✓ DATABASE_URL configured
  ✓ alembic at head (a7c9e11d4f22)
  ✓ pytest discoverable
  ✓ vitest discoverable
  ✓ main worktree (refactoring)
```

### `make doctor` — ../KBI_PoC_track_a

```
═══ KBI_PoC_track_a — doctor ═══
  ✓ .env.worktree present
  ✓ DATABASE_URL configured
  ✓ alembic at head (a7c9e11d4f22)
  ✓ pytest discoverable
  ⚠ vitest missing (run npm install)
  ✗ expected refactoring/track-a-solver, got refactoring-track-a-solver
```

Note: the `✗` line is a false negative caused by the stale Makefile (track_a is pinned at 55b154a, before the Makefile was updated in b20cf81). Branch name itself is correct.

### `make doctor` — ../KBI_PoC_track_b

```
═══ KBI_PoC_track_b — doctor ═══
  ✓ .env.worktree present
  ✓ DATABASE_URL configured
  ✓ alembic at head (a7c9e11d4f22)
  ✓ pytest discoverable
  ⚠ vitest missing (run npm install)
  ✗ expected refactoring/track-b-admin, got refactoring-track-b-admin
```

Same stale-Makefile caveat.

### `pytest --collect-only` (backend)

```
... [tests listed] ...
venv/lib/python3.11/site-packages/pydantic/_internal/_config.py:295: PydanticDeprecatedSince20
    (unrelated Pydantic 2.x deprecation warning, no failure)

========================= 439 tests collected in 0.61s =========================
```

### `alembic history` (backend, first 15 rows)

```
f5d8a1c09e21 -> a7c9e11d4f22 (head), add parent_run_label to production_batch
e4f7a9c21b30 -> f5d8a1c09e21, audit_log.task_id FK 에 ON DELETE CASCADE 추가
da5c8ab3d523 -> e4f7a9c21b30, add kind column to schedule_change_sets
cffe753b1054 -> da5c8ab3d523, add speedmaster updated_at
708591555e9b -> cffe753b1054, wip_upload_log table
dc5d1c469f8e -> 708591555e9b, wip unique index + status rename
d1e2f3a4b5c6 -> dc5d1c469f8e, add constraint_config_history and timestamps
c3d4e5f6a7b8 -> d1e2f3a4b5c6, add schedule_change_sets
b9e2f4a6d018 -> c3d4e5f6a7b8, add unassigned partial index + unassign_reason column
f2900467a547 -> b9e2f4a6d018, add batch_group column to production_batch
b2c3d4e5f6a7 -> f2900467a547, add core column to wip_inventory
a1b2c3d4e5f6 -> b2c3d4e5f6a7, add wip_id to sales_order
90b349ba1c61 -> a1b2c3d4e5f6, add spec_raw to production_batch
d854c4dacf49 -> 90b349ba1c61, add SM inventory lifecycle columns
<base> -> d854c4dacf49, initial schema — 14 tables
```

Chain `f2900467a547 → b9e2f4a6d018 → c3d4e5f6a7b8` confirmed (read from the middle three lines; alembic prints in reverse chronological order).

### `alembic heads` / `alembic current` (backend, against Supabase)

```
a7c9e11d4f22 (head)

INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
a7c9e11d4f22 (head)
```

Code head and DB current match.

### Supabase PHASE0 marker SELECT (read-only)

```
host=aws-1-ap-northeast-2.pooler.supabase.com db=postgres
COUNT, MIN, MAX: (38, 2026-04-23 10:14:49.889787+00:00, 2026-04-23 10:14:49.889787+00:00)
distinct markers: ['PHASE0_INITIAL_20260423']
```

### YAML parse

```
$ python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
YAML OK
```

### `bash -n` on all scripts

```
=== scripts/run_id_grep.sh ===         OK
=== scripts/setup_worktrees.sh ===     OK
=== scripts/worktree_cd.sh ===         OK
=== scripts/worktree_status.sh ===     OK
```

### Private-symbol inventory determinism

```
$ python3 scripts/gen_private_symbol_inventory.py > /tmp/psi_regen.md
$ diff docs/private-symbol-inventory.md /tmp/psi_regen.md
$ (no output — diff clean)
```

### `bash scripts/worktree_status.sh`

```
LABEL     PATH                                        BRANCH                            DIRTY       LAST COMMIT
--------  ------------------------------------------  --------------------------------  ----------  -----------
main      /Users/jaewookim/Desktop/Project/KBI_PoC    refactoring                       0           3182792 docs(refactor): private-symbol inventory for Week 2/3 re-export shells
track_a   /Users/jaewookim/Desktop/Project/KBI_PoC_track_a  refactoring-track-a-solver  1           55b154a fix(test): pin pytest-asyncio to 0.23.8 to resolve Package.obj AttributeError
track_b   /Users/jaewookim/Desktop/Project/KBI_PoC_track_b  refactoring-track-b-admin   1           55b154a fix(test): pin pytest-asyncio to 0.23.8 to resolve Package.obj AttributeError
```

Both tracks pinned at 55b154a confirms the stale-Makefile root cause.

### `git worktree list`

```
/Users/jaewookim/Desktop/Project/KBI_PoC          3182792 [refactoring]
/Users/jaewookim/Desktop/Project/KBI_PoC_track_a  55b154a [refactoring-track-a-solver]
/Users/jaewookim/Desktop/Project/KBI_PoC_track_b  55b154a [refactoring-track-b-admin]
```

Exactly 3 entries, dash-separated branches match spec §9 / plan Task 0.7.

### Symlink verification (track worktrees)

```
../KBI_PoC_track_a/backend/.env  -> /Users/jaewookim/Desktop/Project/KBI_PoC/backend/.env
../KBI_PoC_track_a/backend/venv -> /Users/jaewookim/Desktop/Project/KBI_PoC/backend/venv
../KBI_PoC_track_b/backend/.env  -> /Users/jaewookim/Desktop/Project/KBI_PoC/backend/.env
../KBI_PoC_track_b/backend/venv -> /Users/jaewookim/Desktop/Project/KBI_PoC/backend/venv
```

Both tracks share a single DATABASE_URL (Path D invariant) and a single Python interpreter.

### Port isolation (all 3 `.env.worktree` files)

```
main:    BACKEND_PORT=8000 / FRONTEND_PORT=3000
track_a: BACKEND_PORT=8001 / FRONTEND_PORT=3001
track_b: BACKEND_PORT=8002 / FRONTEND_PORT=3002
```

No collisions.
