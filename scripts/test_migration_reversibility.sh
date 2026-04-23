#!/usr/bin/env bash
# Alembic migration reversibility test (Rev 3 §9 gate).
#
# Tests upgrade → downgrade → upgrade round-trip against a throwaway local
# Postgres. NEVER touches Supabase (the downgrade path is unsafe for any
# environment with real data — Path D discipline).
#
# Assumes a local Postgres is reachable at POSTGRES_HOST:POSTGRES_PORT with
# superuser-ish role POSTGRES_USER (password POSTGRES_PASSWORD). Sensible
# defaults match docker-compose / local-dev conventions; override via env.
#
# Usage:
#   ./scripts/test_migration_reversibility.sh            # round-trip to head
#   ./scripts/test_migration_reversibility.sh base       # all the way down
#
# Exit codes:
#   0  All three rounds succeeded and final alembic current == head.
#   1  Local Postgres unreachable — skip gracefully (test is best-effort).
#   2  Migration failed somewhere in the round-trip (see logs).

set -euo pipefail

# Belt-and-suspenders Path D guard. The script builds its own local
# DATABASE_URL on line 71 and exports it before any alembic call, so
# structurally it cannot touch Supabase. This extra check makes the
# boundary textually explicit and catches POSTGRES_HOST=*.supabase.co
# which would slip past the structural defense.
if [[ "${POSTGRES_HOST:-}" == *"supabase.co"* ]] \
   || [[ "${DATABASE_URL:-}" == *"supabase.co"* ]]; then
    echo "ERROR: Refusing to run — POSTGRES_HOST or DATABASE_URL targets Supabase." >&2
    echo "       Path D contract: downgrade never runs against Supabase." >&2
    exit 2
fi

readonly POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
readonly POSTGRES_PORT="${POSTGRES_PORT:-5432}"
readonly POSTGRES_USER="${POSTGRES_USER:-postgres}"
readonly POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
readonly TEST_DB="${TEST_DB:-kbi_reversibility_test}"
readonly DOWN_TARGET="${1:-base}"   # default: roll all the way back

# backend/ is the alembic project root; compute path relative to this script.
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly BACKEND_DIR="$(cd "${SCRIPT_DIR}/../backend" && pwd)"

log() { printf '\033[1;34m[reversibility]\033[0m %s\n' "$*" >&2; }
err() { printf '\033[1;31m[reversibility]\033[0m %s\n' "$*" >&2; }

# ---- preflight: Postgres reachable? ----------------------------------------
if ! PGPASSWORD="${POSTGRES_PASSWORD}" psql \
        -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" \
        -d postgres -c 'SELECT 1' >/dev/null 2>&1; then
    err "Local Postgres not reachable at ${POSTGRES_HOST}:${POSTGRES_PORT}"
    err "Skipping reversibility test (Path D: Supabase is never downgraded)."
    err "Start a local Postgres (docker-compose up postgres) to run this."
    exit 1
fi

log "Local Postgres reachable — running round-trip against db '${TEST_DB}'."

# ---- cleanup hook -----------------------------------------------------------
cleanup() {
    log "Dropping throwaway database '${TEST_DB}'..."
    PGPASSWORD="${POSTGRES_PASSWORD}" psql \
        -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" \
        -d postgres -c "DROP DATABASE IF EXISTS ${TEST_DB};" \
        >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ---- fresh DB ---------------------------------------------------------------
PGPASSWORD="${POSTGRES_PASSWORD}" psql \
    -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" \
    -d postgres -c "DROP DATABASE IF EXISTS ${TEST_DB};" >/dev/null
PGPASSWORD="${POSTGRES_PASSWORD}" psql \
    -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" \
    -d postgres -c "CREATE DATABASE ${TEST_DB};" >/dev/null
log "Fresh database created."

# Build a throwaway DATABASE_URL and point alembic at it by overriding the
# env var the project already reads in backend/app/config.py.
TEST_URL="postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${TEST_DB}"
export DATABASE_URL="${TEST_URL}"

run_alembic() {
    # shellcheck disable=SC2068
    ( cd "${BACKEND_DIR}" && alembic $@ )
}

# ---- round 1: upgrade head --------------------------------------------------
log "Round 1: alembic upgrade head"
if ! run_alembic upgrade head; then
    err "Round 1 (upgrade head) failed."
    exit 2
fi

CURRENT=$(run_alembic current 2>/dev/null | awk '/\(head\)/ {print $1}' | tail -n1)
log "After round 1, alembic current = ${CURRENT}"

# ---- round 2: downgrade -----------------------------------------------------
log "Round 2: alembic downgrade ${DOWN_TARGET}"
if ! run_alembic downgrade "${DOWN_TARGET}"; then
    err "Round 2 (downgrade ${DOWN_TARGET}) failed."
    exit 2
fi

# ---- round 3: upgrade head again -------------------------------------------
log "Round 3: alembic upgrade head (round-trip)"
if ! run_alembic upgrade head; then
    err "Round 3 (upgrade head) failed."
    exit 2
fi

CURRENT=$(run_alembic current 2>/dev/null | awk '/\(head\)/ {print $1}' | tail -n1)
if [[ -z "${CURRENT}" ]]; then
    err "Round-trip finished but alembic current did not report a head."
    exit 2
fi

log "All three rounds succeeded. Final head = ${CURRENT}"
exit 0
