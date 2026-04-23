#!/usr/bin/env bash
# Creates/ensures two sibling worktrees for parallel refactoring:
#   ../KBI_PoC_track_a on refactoring-track-a-solver  (solver extraction)
#   ../KBI_PoC_track_b on refactoring-track-b-admin   (greedy/LLM/UI)
#
# NOTE: dash-separated (not refactoring/track-*) — the existing `refactoring`
# branch blocks namespaced children under Git's refname hierarchy.
#
# Path D (Supabase-native) invariant:
# All 3 worktrees share ONE backend/.env via symlink — one DATABASE_URL to rotate.
# Port isolation here is ONLY for the dev servers (backend uvicorn + frontend vite),
# never for the database.
#
# Idempotent: re-running is safe.

set -euo pipefail

# Resolve main worktree (this script's repo root) absolutely.
MAIN_WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT_DIR="$(cd "$MAIN_WT/.." && pwd)"
MAIN_ENV="$MAIN_WT/backend/.env"

if [[ ! -f "$MAIN_ENV" ]]; then
  echo "✗ $MAIN_ENV missing — create it (with DATABASE_URL) before setting up worktrees." >&2
  exit 1
fi
if [[ ! -f "$MAIN_WT/.env.worktree.example" ]]; then
  echo "✗ $MAIN_WT/.env.worktree.example missing — required template." >&2
  exit 1
fi

# Write main's own .env.worktree if missing (port 8000/3000).
write_env_worktree() {
  local path="$1"
  local backend_port="$2"
  local frontend_port="$3"
  local target="$path/.env.worktree"
  if [[ -f "$target" ]]; then
    echo "  = $target already present — leaving untouched"
    return
  fi
  cp "$MAIN_WT/.env.worktree.example" "$target"
  # Replace the BACKEND_PORT / FRONTEND_PORT defaults from the template.
  # Template has `BACKEND_PORT=8000   # main: 8000, ...` etc.; keep the comment intact.
  sed -i.bak -E "s/^BACKEND_PORT=[0-9]+/BACKEND_PORT=${backend_port}/" "$target"
  sed -i.bak -E "s/^FRONTEND_PORT=[0-9]+/FRONTEND_PORT=${frontend_port}/" "$target"
  rm -f "${target}.bak"
  echo "  + wrote $target (BACKEND_PORT=${backend_port}, FRONTEND_PORT=${frontend_port})"
}

# Seed main's .env.worktree too (8000/3000) so `make doctor` in main is consistent.
write_env_worktree "$MAIN_WT" 8000 3000

ensure_branch() {
  local branch="$1"
  # --list needs to be quoted; handles `/` in name fine.
  if git -C "$MAIN_WT" branch --list "$branch" | grep -q .; then
    echo "  = branch '$branch' already exists"
  else
    git -C "$MAIN_WT" branch "$branch" refactoring
    echo "  + created branch '$branch' from refactoring"
  fi
}

ensure_worktree() {
  local wt_path="$1"
  local branch="$2"
  if git -C "$MAIN_WT" worktree list | awk '{print $1}' | grep -Fxq "$wt_path"; then
    echo "  = worktree $wt_path already registered"
  else
    git -C "$MAIN_WT" worktree add "$wt_path" "$branch"
    echo "  + added worktree $wt_path on $branch"
  fi
}

symlink_backend_env() {
  local wt_path="$1"
  mkdir -p "$wt_path/backend"
  local link="$wt_path/backend/.env"
  # If it's already a correct symlink, leave it.
  if [[ -L "$link" && "$(readlink "$link")" == "$MAIN_ENV" ]]; then
    echo "  = $link already symlinked to $MAIN_ENV"
    return
  fi
  # Refuse to clobber a real (non-symlink) file — that would be data loss.
  if [[ -e "$link" && ! -L "$link" ]]; then
    echo "✗ $link is a regular file, not a symlink. Refusing to overwrite." >&2
    echo "  Inspect/remove it manually, then re-run." >&2
    exit 1
  fi
  ln -sf "$MAIN_ENV" "$link"
  echo "  + symlinked $link -> $MAIN_ENV"
}

# Share backend/venv across worktrees via symlink — saves ~5min of pip install
# per worktree and ensures all 3 worktrees run the same Python interpreter
# against the same resolved requirements. Only links if main's venv exists;
# otherwise we leave it and print a hint so the user can `make bootstrap`
# in main first.
symlink_backend_venv() {
  local wt_path="$1"
  local main_venv="$MAIN_WT/backend/venv"
  local link="$wt_path/backend/venv"

  if [[ ! -d "$main_venv" ]]; then
    echo "  ⚠ $main_venv missing — run 'make bootstrap' in main first, then re-run this script to link venvs"
    return
  fi
  if [[ -L "$link" && "$(readlink "$link")" == "$main_venv" ]]; then
    echo "  = $link already symlinked to $main_venv"
    return
  fi
  if [[ -e "$link" && ! -L "$link" ]]; then
    echo "✗ $link is a real directory, not a symlink. Refusing to overwrite." >&2
    echo "  Remove it manually (it's regenerable) then re-run." >&2
    exit 1
  fi
  ln -sf "$main_venv" "$link"
  echo "  + symlinked $link -> $main_venv"
}

setup_track() {
  local label="$1"    # a | b
  local suffix="$2"   # solver | admin
  local backend_port="$3"
  local frontend_port="$4"

  local branch="refactoring-track-${label}-${suffix}"
  local wt_path="$PARENT_DIR/KBI_PoC_track_${label}"

  echo "━━━ track_${label} (${branch}) ━━━"
  ensure_branch "$branch"
  ensure_worktree "$wt_path" "$branch"
  symlink_backend_env "$wt_path"
  symlink_backend_venv "$wt_path"
  write_env_worktree "$wt_path" "$backend_port" "$frontend_port"
}

echo "━━━ main (refactoring) ━━━"
echo "  main worktree: $MAIN_WT"

setup_track a solver 8001 3001
setup_track b admin  8002 3002

echo
echo "Done. Next:"
echo "  git worktree list"
echo "  ./scripts/worktree_status.sh"
echo "  (cd ../KBI_PoC_track_a && make bootstrap && make doctor)  # optional bootstrap"
