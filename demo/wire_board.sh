#!/usr/bin/env bash
# flockwork — wire a project as a swarm-usable board.
#
# Composes the two documented steps (docs/SCRATCH-BOARDS.md + `flockwork
# harness init`) into one safe helper:
#   1. create the board origin (bare, HEAD -> main) — the authoring law
#   2. mirror the project's current HEAD onto board main (the product baseline)
#   3. smoke-test the board (a fresh clone must contain the project's files)
#   4. stamp the local `.flockwork/origin` marker (the plugin's sole activation
#      source) and keep it gitignored
#
# Non-destructive to the project: it only creates a new board beside it and a
# local, gitignored marker. It NEVER touches the project's commits or working
# tree (adding a `.gitignore` line, uncommitted, is the only exception).
#
#   demo/wire_board.sh <project-dir> [board-origin-path] [--force]
#
#   <project-dir>          an existing git repository to make swarm-usable
#   [board-origin-path]    where to create the board (default: <project>-origin.git
#                          beside the project). Refused if it already hosts
#                          live refs/swarm/* unless --force.
#   --force                re-mirror main / re-point the marker over a live board
set -euo pipefail

usage() { echo "usage: demo/wire_board.sh <project-dir> [board-origin-path] [--force]" >&2; exit 64; }
[ $# -ge 1 ] || usage
PROJ="$(cd "$1" && pwd)"; shift
BOARD="${1:-}"; [ $# -ge 1 ] && shift || true
if [ -z "$BOARD" ]; then BOARD="${PROJ}-origin.git"; fi
BOARD="$(cd "$(dirname "$BOARD")" && pwd)/$(basename "$BOARD")"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

fail() { echo "FAIL: $*" >&2; exit 1; }

# 0. guards ---------------------------------------------------------------
git -C "$PROJ" rev-parse --git-dir >/dev/null 2>&1 || fail "$PROJ is not a git repo"
if [ -e "$BOARD" ] && git --git-dir="$BOARD" for-each-ref 'refs/swarm/*' 2>/dev/null | grep -q .; then
  [ "$FORCE" = 1 ] || fail "$BOARD already hosts live refs/swarm/* — refusing (pass --force to re-mirror main)"
fi

# 1. board origin (bare, HEAD -> main): the SCRATCH-BOARDS authoring law ----
#    A plain `init --bare` leaves HEAD at an unborn master; the symref below
#    is what makes a worker clone actually check out the tree.
if [ ! -e "$BOARD" ]; then
  git init -q --bare "$BOARD"
fi
git -C "$BOARD" symbolic-ref HEAD refs/heads/main

# 2. mirror the project's current HEAD onto board main (the product baseline)
git -C "$PROJ" push -q --force "$BOARD" HEAD:refs/heads/main

# 3. smoke test: a clone of the board must contain one of the project's files
#    Capture the full filtered list first (no early-closing `head` in the pipe)
#    so a large repo never SIGPIPEs its producer into `set -e`.
candidates="$(git -C "$PROJ" ls-files 2>/dev/null | grep -vxF -e '.flockwork' -e '.flockwork/origin' || true)"
[ -n "$candidates" ] || candidates="$(git -C "$PROJ" ls-files 2>/dev/null || true)"
expected="$(printf '%s\n' "$candidates" | sed -n '1p')"
[ -n "$expected" ] || fail "project has no tracked files to mirror"
d="$(mktemp -d)"; git clone -q "$BOARD" "$d/board" 2>/dev/null
if [ -e "$d/board/$expected" ]; then echo "SMOKE PASS: a fresh clone of $BOARD contains '$expected'"; else
  rm -rf "$d"; fail "board main is missing '$expected' (unborn-HEAD trap — see docs/SCRATCH-BOARDS.md)"
fi
rm -rf "$d"

# 4. the local marker (the plugin's sole activation source) ---------------
if [ -f "$PROJ/.flockwork/origin" ] && [ "$(cat "$PROJ/.flockwork/origin")" != "$BOARD" ]; then
  [ "$FORCE" = 1 ] || fail "$PROJ/.flockwork/origin already points elsewhere ($(cat "$PROJ/.flockwork/origin")) — refusing to re-point (pass --force)"
fi
mkdir -p "$PROJ/.flockwork"
printf '%s\n' "$BOARD" > "$PROJ/.flockwork/origin"
# keep the marker local + gitignored (the documented behavior)
if [ -f "$PROJ/.gitignore" ]; then
  grep -Fxq '.flockwork' "$PROJ/.gitignore" || printf '.flockwork\n' >> "$PROJ/.gitignore"
else
  printf '.flockwork\n' > "$PROJ/.gitignore"
fi

echo "WIRE OK: $PROJ -> $BOARD"
echo "  marker: $PROJ/.flockwork/origin  (= $BOARD)"
echo "  next:   open $PROJ in opencode (the plugin auto-activates on the marker), or"
echo "          drive it via the flockwork CLI / the MCP server. Seed a task, then"
echo "          run the fw_* protocol. 'flockwork debug <task>' reconstructs the run."
