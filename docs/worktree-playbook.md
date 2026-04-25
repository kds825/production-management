# Worktree Playbook

**Audience:** anyone joining the project after Week 9 who needs to run two
parallel branches (solver vs admin) without merge collisions, or who needs
to debug a "wrong worktree" symptom from the troubleshooting doc.
**Source files:**

- `scripts/setup_worktrees.sh` — bootstrap.
- `scripts/worktree_status.sh` — health summary across all 3 trees.
- `scripts/worktree_cd.sh` — `kbi <name>` shell helper for switching.
- `.env.worktree.example` — port-override template.
- `Makefile` — `bootstrap`, `doctor`, `verify` targets.

---

## Why two worktrees

Track A (solver / backend) and Track B (frontend / admin / migrations)
have almost zero file overlap, so two engineers (or two model sessions)
can ship in parallel **with no merge conflicts** if each works in its
own worktree and only crosses into shared code (`backend/app/services/`,
`backend/app/infrastructure/`) under explicit coordination.

The single-worktree alternative — branch-switch via `git checkout` —
forces a serializing context-switch on every hop and corrupts the dev
server (uvicorn keeps the old code in memory). Worktrees give us
process-level isolation: each tree has its own `node_modules`, its own
`venv`, its own running `uvicorn` on its own port.

---

## Layout

```
~/Desktop/Project/
├── KBI_PoC/              ← main worktree, branch `refactoring`
│   ├── backend/.env             (real file — DATABASE_URL lives here)
│   ├── .env.worktree            (BACKEND_PORT=8000 FRONTEND_PORT=3000)
│   └── ...
├── KBI_PoC_track_a/      ← worktree, branch `refactoring-track-a-solver`
│   ├── backend/.env             (symlink → ../KBI_PoC/backend/.env)
│   └── .env.worktree            (BACKEND_PORT=8001 FRONTEND_PORT=3001)
└── KBI_PoC_track_b/      ← worktree, branch `refactoring-track-b-admin`
    ├── backend/.env             (symlink → ../KBI_PoC/backend/.env)
    └── .env.worktree            (BACKEND_PORT=8002 FRONTEND_PORT=3002)
```

**Path D invariant (`scripts/setup_worktrees.sh:9-12`):** all three
worktrees share **one** Supabase `DATABASE_URL` via the symlinked
`backend/.env`. That means rotating credentials is a one-file change.
The per-worktree `.env.worktree` carries **only port overrides** and the
optional `LLM_PROVIDER` toggle — never DB URLs.

`backend/venv` is also symlinked across worktrees so we don't pay a
3-minute pip install per tree. Isolation between trees is therefore
**port-level only** for running services; the Python interpreter,
installed packages, and DB are shared. This is intentional and called
out as a tradeoff in `docs/troubleshooting.md` ("Worktree contamination").

---

## Setup

```bash
cd ~/Desktop/Project/KBI_PoC
./scripts/setup_worktrees.sh        # idempotent; safe to re-run
```

The script:

1. Verifies `backend/.env` exists with a `DATABASE_URL` (refuses
   otherwise — fail-fast at setup).
2. Writes a port-overridden `.env.worktree` per tree.
3. Creates `refactoring-track-a-solver` and `refactoring-track-b-admin`
   branches off `refactoring` if they don't exist.
4. `git worktree add`s the sibling directories.
5. Symlinks `backend/.env` and `backend/venv` from each worktree to the
   main tree.

---

## Daily workflow per worktree

Each worktree is independent for editing/running, but shares the DB.
The standard loop:

```bash
cd ~/Desktop/Project/KBI_PoC_track_a    # or _track_b, or main
make doctor          # ~10s preflight: env file, alembic head, pytest, branch match
# ...edit, test, commit on this worktree's branch...
make verify          # lint + typecheck + test + parity (full pre-merge gate)
```

`make doctor` is the cheap canary you should run **first** in a fresh
shell. It catches the four most common boot failures:

- Missing `.env.worktree` (suggests `make bootstrap`).
- Missing `DATABASE_URL` in `backend/.env` (hard fail, exit 1).
- Alembic drift between code head and DB current (warn — usually means
  you forgot `alembic upgrade head` after pulling).
- Wrong branch in this worktree (e.g., `track_a` checked out to
  `refactoring` by mistake — common after a careless `git checkout`).

`make verify` runs the full pre-merge gate. Don't skip it before
merging into `refactoring`; CI will run the same thing and you'll get a
faster feedback loop locally.

---

## Cross-worktree status

```bash
./scripts/worktree_status.sh
```

Prints one row per worktree (main, track_a, track_b) with branch, dirty
file count, and last-commit subject. Use it whenever you're not sure
which tree you last touched, or when the troubleshooting "Worktree
contamination" symptom strikes.

`scripts/worktree_cd.sh` defines the `kbi` shell function — `source` it
in your `~/.zshrc` to get `kbi a` (jump to track_a), `kbi b`, `kbi main`
shortcuts that avoid the easy-to-typo absolute paths.

---

## When to use which tree

- **`main` (port 8000/3000):** integration / `refactoring` branch tip.
  Run smoke tests, prep merges, demo to stakeholders.
- **`track_a` (port 8001/3001):** solver / backend service refactors.
  Anything under `backend/app/services/solver/`, `services/greedy/`,
  `services/scheduling_shared/`, `services/pipeline/`.
- **`track_b` (port 8002/3002):** frontend, admin tabs, Alembic
  migrations. Anything under `frontend/`, `backend/app/presentation/`,
  `backend/alembic/versions/`.

Cross-cutting changes (e.g., DB model + service + UI) should pause one
track until the other lands; the worktree layout doesn't protect you
from a logically-coupled diff that spans both trees.
