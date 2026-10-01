#!/usr/bin/env bash
# swarmo test kit — deliberate duplicate-claim probe.
#
# Shows the create-once CAS from the outside: claim a task that ALREADY
# has a live claim. Git must reject the second ref write (`! [rejected]
# ... stale info`), and the lane classifies that as a lost race — the
# exactly-once guarantee. No mutation can result: a rejected push
# writes nothing.
#
# usage: demo/duplicate_claim.sh [TASK]   (default TASK=hello-demo)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
TASK="${1:-hello-demo}"
ORIGIN="${SWARM_ORIGIN:?export SWARM_ORIGIN=<origin> first (see QUICKSTART.md)}"

echo "== deliberate duplicate claim on task '$TASK' (a live claim already exists) =="

# guard: with NO live claim this probe would not duplicate anything —
# the create-once push would simply CLAIM the task (a real ref write).
git ls-remote "$ORIGIN" "refs/swarm/claims/$TASK" | grep -q . \
  || { echo "no live claim on $TASK — run the workers first (QUICKSTART step 4)"; exit 2; }

# 1. the raw git fact: a second create-once push must be rejected.
ET=$(git hash-object -t tree /dev/null)
DUP=$(git -c user.email=owner@demo -c user.name=owner-demo \
      commit-tree "$ET" -m "claim $TASK att-owner-dup-DELIBERATE")
if git push --force-with-lease="refs/swarm/claims/$TASK:" \
      "$ORIGIN" "$DUP:refs/swarm/claims/$TASK" 2>&1; then
  echo "UNEXPECTED: duplicate claim was ACCEPTED — the CAS did not hold."
  exit 1
fi

# 2. the lane's own classification of the same event.
python3 - "$TASK" <<'PY'
import json, sys
from l2 import inrepo
res = inrepo.claim_detail("owner-dup", sys.argv[1])
print(json.dumps({
    "duplicate_claim_result": {
        "att": res["att"],
        "rc": res["rc"],
        "stderr": res["stderr"].strip()[:300],
    },
    "classified": inrepo.classify_claim_failure(res["rc"], res["stderr"]),
    "meaning": "att=null + race => exactly-once held; healthy contention",
}, indent=1))
PY
