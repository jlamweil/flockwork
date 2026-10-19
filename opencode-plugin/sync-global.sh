#!/usr/bin/env bash
# Keep the *deployed global* opencode plugin in sync with the repo source.
#
# The opencode plugin is loaded from two global paths (see AGENTS.md "the opencode
# plugin (this box)"):
#   ~/.config/opencode/lib/flockwork.ts      <- file COPY of opencode-plugin/lib/flockwork.ts
#   ~/.config/opencode/plugins/flockwork.ts  <- thin loader (idempotency guard)
#
# A symlink breaks opencode's plugin loader, so the lib must be a REAL file. This
# script copies the repo lib to the global path as a real file, or (--check) just
# verifies it is present, a real file, and byte-identical to the repo source.
#
# Usage:
#   opencode-plugin/sync-global.sh            # sync (copy) into place
#   opencode-plugin/sync-global.sh --check    # 0 = in sync, 1 = drifted/absent
set -euo pipefail

REPO_LIB="$(cd "$(dirname "$0")" && pwd)/lib/flockwork.ts"
GLOBAL_LIB="${FLOCKWORK_GLOBAL_LIB:-$HOME/.config/opencode/lib/flockwork.ts}"

if [ ! -f "$REPO_LIB" ]; then
  echo "missing repo source: $REPO_LIB" >&2
  exit 1
fi

check() {
  if [ ! -f "$GLOBAL_LIB" ]; then
    echo "GLOBAL PLUGIN ABSENT: $GLOBAL_LIB" >&2
    return 1
  fi
  if [ -L "$GLOBAL_LIB" ]; then
    echo "GLOBAL PLUGIN IS A SYMLINK (breaks loader): $GLOBAL_LIB" >&2
    return 1
  fi
  if ! cmp -s "$REPO_LIB" "$GLOBAL_LIB"; then
    echo "GLOBAL PLUGIN DRIFTED from repo source: $GLOBAL_LIB" >&2
    diff -u "$GLOBAL_LIB" "$REPO_LIB" >&2 || true
    return 1
  fi
  echo "global plugin in sync: $GLOBAL_LIB"
  return 0
}

if [ "${1:-}" = "--check" ]; then
  check
  exit $?
fi

mkdir -p "$(dirname "$GLOBAL_LIB")"
cp "$REPO_LIB" "$GLOBAL_LIB"
chmod 644 "$GLOBAL_LIB"
GLOBAL_LIB="$GLOBAL_LIB" check
