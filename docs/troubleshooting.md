# Troubleshooting

Skeleton of known Week 0 symptoms hit so far. Append new sections as the pilot uncovers them; keep each section under 25 lines and organize by the literal error text or user-visible symptom so `grep` finds them fast.

## `EADDRINUSE :3000` / `OSError: [Errno 48] Address already in use :8000`

- **Symptom:** `npm run dev` or `uvicorn` refuses to start, complaining the port is taken.
- **Cause:** A previous dev server is still bound, or two worktrees are trying to use the same port.
- **Fix:**
  - `lsof -i :3000` / `lsof -i :8000` to find the PID, then `kill <pid>`.
  - Or edit `.env.worktree` and change `BACKEND_PORT` / `FRONTEND_PORT`.
  - Or switch to a different worktree — `track_a` uses `8001/3001`, `track_b` uses `8002/3002`.

## Parity harness red (fixture hash mismatch)

- **Symptom:** `make parity` fails with a hash mismatch on one or more fixtures.
- **Cause:** A decision output changed. Per D9-A, comparison is strict-hash — a 1-minute drift on any field fails the whole fixture.
- **Fix:**
  - Reproduce the single offender: `make parity-fixture FIXTURE=NN`.
  - Investigate why the hash changed. If the change is unintentional, fix the regression.
  - If the change is intentional and approved, rebaseline and use the `parity-update:` commit prefix per D9-A discipline so reviewers know the baseline moved on purpose.

## `alembic downgrade` failed / corrupted DB

- **Symptom:** Downgrade aborts mid-migration, or the local DB is in an unusable mixed state.
- **Cause:** A migration's `downgrade()` is incomplete, or manual edits drifted the schema.
- **Fix:**
  - Restore from the archived baseline: `docs/archive/schema_asis_20260423.sql` and `docs/archive/constraint_config_sample_20260423.json`.
  - Follow the break-glass steps in `docs/archive/README.md` to re-seed a fresh Supabase project or local Postgres (Path D is Supabase-native).
  - Note: `c3d4e5f6a7b8` now depends on `b9e2f4a6d018` (batch_group fix, Task 0.1b). Confirm `alembic history` reflects that order before retrying.

## `docker compose: service not found`

- **Symptom:** `docker compose up backend` or `... up frontend` errors with "service not found".
- **Cause:** Wrong mental model. Path D runs backend and frontend **native** (`uvicorn` + `next dev`). `docker-compose.yml` only defines a `db` service, and even that is unused in day-to-day dev.
- **Fix:**
  - Start backend: `cd backend && uvicorn app.main:app --reload --port $BACKEND_PORT`.
  - Start frontend: `cd frontend && npm run dev`.
  - DB: point `DATABASE_URL` at Supabase (or local Postgres) per `.env.worktree`. Do not reach for `docker compose up`.

## Worktree contamination (wrong branch / diffs bleeding between tracks)

- **Symptom:** Edits show up in the wrong worktree; `git status` is dirty in a track you weren't working in.
- **Cause:** Shell is still `cd`'d into the previous worktree, or shared symlinks (`backend/.env`, `backend/venv`) mask which tree is active.
- **Fix:**
  - Diagnose: `./scripts/worktree_status.sh` — prints branch and dirty-file count for all 3 worktrees.
  - Switch cleanly: `git stash -u` → `source scripts/worktree_cd.sh; kbi <target>` → `git stash pop`.
  - Truly corrupted tree: `git worktree remove <path>` then re-run `./scripts/setup_worktrees.sh`.
  - Reminder: both tracks share `backend/.env` and `backend/venv` via symlink; isolation is port-level only.

## LLM rate-limited (Anthropic `429` or request timeout)

- **Symptom:** Backend call to the LLM provider returns `429` or hangs past the client timeout.
- **Cause:** Anthropic rate limit hit, or transient upstream slowness.
- **Fix:**
  - Fall back to the deterministic template provider: `export LLM_PROVIDER=template`.
  - The parity harness already forces `LLM_PROVIDER=template` automatically (D9-A) so parity is unaffected.
  - Verify the default: `grep LLM_PROVIDER .env.worktree` — `.env.worktree.example` ships with `template` as the default.

## `pytest --collect-only` → `AttributeError: 'Package' object has no attribute 'obj'`

- **Symptom:** `INTERNALERROR` during collection, traceback ends in `pytest_asyncio/plugin.py:610`.
- **Cause:** `pytest-asyncio < 0.23.8` combined with `pytest >= 8` and the presence of `backend/tests/__init__.py`. Newer pytest removed `Package.obj`.
- **Fix:**
  - Pin `pytest-asyncio==0.23.8` (or later in the 0.23.x line) in `backend/requirements.txt`. Already pinned since commit `55b154a` (Task 0.5b). If the error returns after a version bump, re-pin.

## `alembic upgrade head` fails with `column batch_group does not exist`

- **Symptom:** Upgrade aborts while applying `c3d4e5f6a7b8`, complaining about a missing `batch_group` column.
- **Cause:** `c3d4e5f6a7b8` builds an index on `batch_group`, but no earlier migration created the column.
- **Fix:**
  - Already fixed by `b9e2f4a6d018_add_batch_group_column.py` (commit `6126ee0`, Task 0.1b).
  - If the error reappears, check `alembic history` — `b9e2f4a6d018` must sit between `f2900467a547` and `c3d4e5f6a7b8`. If it's missing or out of order, restore the migration file and re-stamp.
