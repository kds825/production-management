#!/usr/bin/env bash
# One-shot health status for main + track_a + track_b worktrees.
# Tolerates missing worktrees (e.g. track_a not yet set up).

set -euo pipefail

MAIN_WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT_DIR="$(cd "$MAIN_WT/.." && pwd)"

printf "%-8s  %-42s  %-32s  %-10s  %s\n" "LABEL" "PATH" "BRANCH" "DIRTY" "LAST COMMIT"
printf "%-8s  %-42s  %-32s  %-10s  %s\n" "--------" "------------------------------------------" "--------------------------------" "----------" "-----------"

report() {
  local label="$1"
  local path="$2"

  if [[ ! -d "$path/.git" && ! -f "$path/.git" ]]; then
    # Not a git worktree (either missing, or just a plain dir).
    printf "%-8s  %-42s  %-32s  %-10s  %s\n" \
      "$label" "$path" "-" "-" "(worktree not set up)"
    return
  fi

  local branch last dirty
  branch="$(git -C "$path" branch --show-current 2>/dev/null || echo "-")"
  last="$(git -C "$path" log -1 --pretty=format:'%h %s' 2>/dev/null || echo "-")"
  dirty="$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

  printf "%-8s  %-42s  %-32s  %-10s  %s\n" \
    "$label" "$path" "$branch" "$dirty" "$last"
}

report "main"     "$MAIN_WT"
report "track_a"  "$PARENT_DIR/KBI_PoC_track_a"
report "track_b"  "$PARENT_DIR/KBI_PoC_track_b"
