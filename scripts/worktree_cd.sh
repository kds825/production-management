# Usage: source scripts/worktree_cd.sh   # then: kbi a
#
# Defines a shell function `kbi {main|a|b}` that:
#   1. cd's into the target worktree,
#   2. sources its .env.worktree (exports BACKEND_PORT / FRONTEND_PORT / LLM_PROVIDER),
#   3. prints pwd + the active ports.
#
# This file is intended to be SOURCED (not executed).
# Detect accidental execution and warn.

if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  echo "worktree_cd.sh is meant to be sourced, not executed." >&2
  echo "Try:  source scripts/worktree_cd.sh   # then: kbi a" >&2
  exit 1
fi

kbi() {
  if [ "$#" -lt 1 ]; then
    echo "Usage: kbi {main|a|b}" >&2
    return 2
  fi

  # Anchor on *this file's* location so `kbi` works from any cwd after sourcing.
  # ${BASH_SOURCE[0]} is bash; ${(%):-%x} is zsh. Try both.
  local _src
  if [ -n "${BASH_SOURCE[0]:-}" ]; then
    _src="${BASH_SOURCE[0]}"
  else
    # zsh
    _src="${(%):-%x}"
  fi
  local _main_wt
  _main_wt="$(cd "$(dirname "$_src")/.." && pwd)"
  local _parent
  _parent="$(cd "$_main_wt/.." && pwd)"

  local _target
  case "$1" in
    main) _target="$_main_wt" ;;
    a)    _target="$_parent/KBI_PoC_track_a" ;;
    b)    _target="$_parent/KBI_PoC_track_b" ;;
    *)
      echo "Unknown worktree: $1 (expected main|a|b)" >&2
      return 2
      ;;
  esac

  if [ ! -d "$_target" ]; then
    echo "Worktree not found: $_target" >&2
    echo "Run ./scripts/setup_worktrees.sh from the main worktree first." >&2
    return 1
  fi

  cd "$_target" || return 1

  if [ -f ".env.worktree" ]; then
    # Export every assignment in .env.worktree for the current shell.
    set -a
    # shellcheck disable=SC1091
    . ./.env.worktree
    set +a
  else
    echo "⚠ $_target/.env.worktree missing — BACKEND_PORT/FRONTEND_PORT not exported" >&2
  fi

  echo "→ $(pwd)"
  echo "  BACKEND_PORT=${BACKEND_PORT:-?}  FRONTEND_PORT=${FRONTEND_PORT:-?}  LLM_PROVIDER=${LLM_PROVIDER:-?}"
}
