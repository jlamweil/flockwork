#!/usr/bin/env bash
# swarmo owner test kit — the whole coordination loop, end to end.
#
# Idempotent: every run builds a FRESH scratch origin (a local bare
# repo under /tmp) and drives it through the full lifecycle —
#   seed -> open_tasks -> two workers race one task -> refs -> verdict
#   -> audit -> deliberate duplicate-claim rejection
# — with a deterministic stand-in for the model dispatch leg
# (demo/trivial_fixer.sh), so a run costs zero model spend and zero
# network. Your real origin is NEVER touched unless you say so.
#
# usage:
#   demo/owner_test.sh              # scratch origin, torn down at the end
#   demo/owner_test.sh --keep       # keep /tmp/swarmo-owner-demo for manual play
#   demo/owner_test.sh --origin URL # DANGER: run against a real origin instead
#
# Requires: git, python3. Run from anywhere (it cds to the repo root).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

SCRATCH_BASE="${TMPDIR:-/tmp}/swarmo-owner-demo"
TARGET_ORIGIN=""
KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep)   KEEP=1; shift ;;
    --origin) TARGET_ORIGIN="${2:?--origin needs a URL}"; shift 2 ;;
    --help|-h) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
  esac
done

step() { printf '\n==== %s ====\n' "$*"; }

if [ -n "$TARGET_ORIGIN" ]; then
  SWARM_ORIGIN="$TARGET_ORIGIN"
  SCRATCH_OWNED=0
  echo "!! REAL ORIGIN MODE: $SWARM_ORIGIN"
  echo "!! this run SEEDS a demo task on it (specs/claims/tasks/verdicts refs)."
  sleep 3
else
  # fresh scratch board every run: the idempotency guarantee
  rm -rf "$SCRATCH_BASE"
  mkdir -p "$SCRATCH_BASE"
  SWARM_ORIGIN="$SCRATCH_BASE/origin.git"
  SCRATCH_OWNED=1
fi
export SWARM_ORIGIN
SHIM="$REPO/demo/trivial_fixer.sh"

step "0. board = $SWARM_ORIGIN"
if [ "$SCRATCH_OWNED" = 1 ]; then
  git init -q --bare "$SWARM_ORIGIN"
  git -C "$SWARM_ORIGIN" symbolic-ref HEAD refs/heads/main
  git push -q "$SWARM_ORIGIN" HEAD:refs/heads/main
  echo "scratch origin created; main = this checkout's HEAD ($(git rev-parse --short HEAD))"
else
  echo "using the origin you named; NOT creating or deleting anything else"
fi

step "1. seed the demo task (l2/inrepo.py seed)"
python3 l2/inrepo.py seed demo/demo-spec.json

step "2. the queue as workers see it (l2/inrepo.py open_tasks)"
python3 l2/inrepo.py open_tasks

step "3. two workers race for the task (deterministic shim dispatch)"
W1LOG="$SCRATCH_BASE/w1.log" W2LOG="$SCRATCH_BASE/w2.log"
if [ "$SCRATCH_OWNED" = 0 ]; then W1LOG="$(mktemp)" W2LOG="$(mktemp)"; fi
( OPENCODE_BIN="$SHIM" python3 l2/inrepo.py worker w1 >"$W1LOG" 2>&1; echo $? >"$W1LOG.rc" ) &
W1=$!
( OPENCODE_BIN="$SHIM" python3 l2/inrepo.py worker w2 >"$W2LOG" 2>&1; echo $? >"$W2LOG.rc" ) &
W2=$!
wait "$W1"; wait "$W2"
echo "--- worker w1 (rc $(cat "$W1LOG.rc")) ---"; cat "$W1LOG"
echo "--- worker w2 (rc $(cat "$W2LOG.rc")) ---"; cat "$W2LOG"
for rc in "$W1LOG.rc" "$W2LOG.rc"; do
  [ "$(cat "$rc")" = "0" ] || { echo "FAIL: a worker exited nonzero ($rc)"; exit 1; }
done

step "4. the refs on the origin (git ls-remote refs/swarm/*)"
git ls-remote "$SWARM_ORIGIN" 'refs/swarm/*' 'refs/heads/main'

step "5. the verdict, read from the origin host"
python3 - <<'PY'
import os
from l2 import inrepo
body = inrepo.origin_body(os.environ["SWARM_ORIGIN"],
                          "refs/swarm/verdicts/hello-demo")
print(body.strip() or "(no verdict ref found)")
PY

step "6. audit the board (l2/inrepo.py audit — h1_pass must be true)"
python3 l2/inrepo.py audit hello-demo

step "7. deliberate duplicate claim — the CAS must REJECT it"
demo/duplicate_claim.sh hello-demo

step "8. teardown"
if [ "$SCRATCH_OWNED" = 1 ] && [ "$KEEP" != 1 ]; then
  rm -rf "$SCRATCH_BASE"
  echo "scratch board removed. rerun anytime; use --keep to keep it."
else
  echo "board kept at: $SWARM_ORIGIN (export SWARM_ORIGIN=$SWARM_ORIGIN to play)"
fi

echo
echo "PASS: seed -> race -> exactly-one-claim -> fix on main -> verdict -> audit green -> duplicate rejected."
