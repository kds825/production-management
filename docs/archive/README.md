# Archive — Pre-refactor Baseline (2026-04-23)

Snapshots captured at git SHA `c70adaf` (`refactoring` branch, immediately before
Production Handoff Refactor execution begins — see
`docs/specs/2026-04-23-production-handoff-refactor-design.md` Rev 3).

## Source of truth

The project's active database is **Supabase** (project `pgujccdnuidsjxuyqgyi`,
region `ap-northeast-2`, pooler host `aws-1-ap-northeast-2.pooler.supabase.com:5432`,
database `postgres`). The local `docker-compose.yml` `db` service is a PoC
leftover and is NOT used for normal development. Connection string lives in
`backend/.env` (gitignored) as `DATABASE_URL`.

## Contents

- `schema_asis_20260423.sql` — DDL dump via `pg_dump --schema-only` against Supabase (public schema)
- `constraint_config_sample_20260423.json` — 38 rows from `constraint_config`

## Restoration procedure (break-glass)

If a migration corrupts Supabase during the pilot:

1. **Restore schema to a fresh DB** (Supabase project or local Postgres):

   ```bash
   # Against a fresh local Postgres:
   createdb -U postgres kbi_restore
   psql -U postgres -d kbi_restore -f docs/archive/schema_asis_20260423.sql

   # Or against a fresh Supabase project (via psql with DATABASE_URL set):
   psql "$RESTORE_DATABASE_URL" < docs/archive/schema_asis_20260423.sql
   ```

2. **Load baseline `constraint_config` rows**:

   ```bash
   cd backend && source venv/bin/activate
   DATABASE_URL="$RESTORE_DATABASE_URL" \
     python ../scripts/restore_constraint_config.py ../docs/archive/constraint_config_sample_20260423.json
   ```

   (The restore script is not yet written — create it on demand. It should
   `INSERT … ON CONFLICT (constraint_id) DO UPDATE` for every row in the JSON.)

3. **Replay `schedule_change_sets` rows** from the corrupted DB (if recoverable)
   or from application-level audit logs, from the Phase-0 timestamp onward.

4. **Post-incident issue** documenting root cause, timeline, remediation.

## Known constraints on restoration

- **Resolved 2026-04-23 (commit `6126ee0`):** previously, `alembic upgrade head`
  from an empty schema failed because `c3d4e5f6a7b8_add_unassigned_index_and_reason`
  referenced `production_batch.batch_group` but no migration in the chain created
  that column. Task 0.1b of the refactor plan inserted
  `b9e2f4a6d018_add_batch_group_column` between `f2900467a547` and
  `c3d4e5f6a7b8`. Fresh-DB restoration now works end-to-end. The new migration's
  `upgrade()` is idempotent (information_schema guard) and `downgrade()` is a
  deliberate no-op to prevent accidental data loss on Supabase.
- The `docker-compose.yml` `db` service uses `postgres:16-alpine` with creds
  `kbi/kbi_poc_2026/kbi_scheduler`. These are defaults for CI-like throwaway local
  testing only. They do not match Supabase credentials.
