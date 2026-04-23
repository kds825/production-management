# Archive — Pre-refactor Baseline (2026-04-23)

Snapshots captured at git SHA `63c8c61` (tip of `refactoring` branch before Production
Handoff Refactor begins execution — see `docs/specs/2026-04-23-production-handoff-refactor-design.md` Rev 2).

## Contents

- `schema_asis_20260423.sql` — full DDL dump via `pg_dump --schema-only`
- `constraint_config_sample_20260423.json` — all rows of `constraint_config` at baseline

## Restoration procedure (break-glass)

Run these steps if a migration corrupts production during the pilot:

1. Restore the schema to a fresh DB:

   ```
   createdb -U kbi kbi_new
   psql -U kbi -d kbi_new -f docs/archive/schema_asis_20260423.sql
   ```

   (Or via docker: `docker compose exec -T db psql -U kbi -d kbi_new < docs/archive/schema_asis_20260423.sql`)

2. Load baseline constraint rows:

   ```
   cd backend && source venv/bin/activate
   python ../scripts/restore_constraint_config.py docs/archive/constraint_config_sample_20260423.json
   ```

   (Write this restore script if not already present — it should INSERT rows matching the JSON.)

3. Replay `schedule_change_sets` rows from main DB since the Phase 0 timestamp.

4. Open a post-incident issue documenting what went wrong.

## Connection constants

- User: `kbi`
- Password: `kbi_poc_2026`
- Database: `kbi_scheduler`
- Service name in docker-compose: `db` (not `postgres`)
