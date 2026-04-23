#!/usr/bin/env bash
# Trace a run_id (UUID or prefix) across on-disk logs and the shared Supabase DB.
#
# Searches:
#   1. backend/logs/  (if present)
#   2. .tmp/          (if present)
#   3. Supabase tables: solver_run, solver_decision, schedule_change_sets
#
# Graceful when solver_run / solver_decision tables don't exist yet
# (they arrive in Week 1). schedule_change_sets already exists; its PK
# `change_set_id` is a String (NOT uuid), so no uuid casting is needed.

set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "Usage: $0 <run_id>" >&2
  exit 2
fi

RUN_ID="$1"
MAIN_WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "━━━ run_id_grep: ${RUN_ID} ━━━"

# --- 1 & 2: on-disk greps ------------------------------------------------
for dir in "$MAIN_WT/backend/logs" "$MAIN_WT/.tmp"; do
  if [[ -d "$dir" ]]; then
    echo
    echo "# grep in ${dir}"
    # -I skips binaries; -n gives line numbers; -r recurses. Don't fail the
    # whole script when grep finds nothing (exit 1).
    if grep -RIn --color=never "$RUN_ID" "$dir" 2>/dev/null; then
      :
    else
      echo "  (no matches in ${dir})"
    fi
  else
    echo
    echo "# ${dir} does not exist — skipping"
  fi
done

# --- 3: Supabase queries --------------------------------------------------
echo
echo "# Supabase query (DATABASE_URL from backend/.env)"

if [[ ! -f "$MAIN_WT/backend/.env" ]]; then
  echo "  ✗ backend/.env missing — cannot query DB."
  exit 0
fi

PY="$MAIN_WT/backend/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "  ⚠ backend/venv not set up — run 'make bootstrap' first. Skipping DB query."
  exit 0
fi

RUN_ID="$RUN_ID" MAIN_WT="$MAIN_WT" "$PY" - <<'PY'
import os
import re
import sys

run_id = os.environ["RUN_ID"]
main_wt = os.environ["MAIN_WT"]

# Parse DATABASE_URL out of backend/.env without importing the app.
db_url = None
env_path = os.path.join(main_wt, "backend", ".env")
with open(env_path, "r", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

if not db_url:
    print("  ✗ DATABASE_URL not found in backend/.env")
    sys.exit(0)

try:
    from sqlalchemy import create_engine, text
except Exception as e:
    print(f"  ✗ sqlalchemy import failed: {e}")
    sys.exit(0)

# Normalize to a psycopg2-compatible URL just in case the file uses postgres://
db_url = re.sub(r"^postgres://", "postgresql://", db_url)

try:
    engine = create_engine(db_url, pool_pre_ping=True)
except Exception as e:
    print(f"  ✗ engine create failed: {e}")
    sys.exit(0)

pattern = f"%{run_id}%"

def query(label, sql, params):
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
    except Exception as e:
        msg = str(e).splitlines()[0]
        # Postgres 'relation does not exist' → table missing (pre-Week-1 is fine).
        if "does not exist" in msg or "UndefinedTable" in msg:
            print(f"  # {label} table not yet created — skipping (Week 1 Task)")
        else:
            print(f"  ✗ {label} query failed: {msg}")
        return
    if not rows:
        print(f"  # {label}: no matches")
        return
    print(f"  # {label}: {len(rows)} row(s)")
    for r in rows[:20]:
        print(f"    {tuple(r)}")
    if len(rows) > 20:
        print(f"    … {len(rows) - 20} more")

# solver_run: expected columns include id (uuid-ish). Use text cast for safety.
query(
    "solver_run",
    "SELECT * FROM solver_run WHERE id::text = :rid OR id::text LIKE :pat LIMIT 50",
    {"rid": run_id, "pat": pattern},
)

# solver_decision: expected to reference solver_run via run_id column.
query(
    "solver_decision",
    "SELECT * FROM solver_decision WHERE run_id::text = :rid OR run_id::text LIKE :pat LIMIT 50",
    {"rid": run_id, "pat": pattern},
)

# schedule_change_sets: change_set_id is String PK (not UUID) — no cast.
query(
    "schedule_change_sets",
    "SELECT change_set_id, created_at FROM schedule_change_sets "
    "WHERE change_set_id = :rid OR change_set_id LIKE :pat LIMIT 50",
    {"rid": run_id, "pat": pattern},
)
PY
