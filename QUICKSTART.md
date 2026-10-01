# QUICKSTART — run a swarm coordination race yourself, on a scratch board

swarmo coordinates workers entirely through git refs (`refs/swarm/*`)
on a shared origin. This kit gives you a **scratch origin** — a local
bare repo under `/tmp` — so you can drive the whole loop yourself
without touching the real example-host-a substrate. Work from the repo root.

One command first — the scripted version of everything below
(idempotent, safe to rerun, ends with a PASS line):

```bash
demo/owner_test.sh                 # add --keep to keep the board for step-through
```

Stepping through it by hand instead — every line is copy-pasteable:

```bash
# 1. a fresh scratch origin: local bare repo, main = your current HEAD
rm -rf /tmp/swarmo-owner-demo && mkdir -p /tmp/swarmo-owner-demo
git init -q --bare /tmp/swarmo-owner-demo/origin.git
git -C /tmp/swarmo-owner-demo/origin.git symbolic-ref HEAD refs/heads/main
git push -q /tmp/swarmo-owner-demo/origin.git HEAD:refs/heads/main
export SWARM_ORIGIN=/tmp/swarmo-owner-demo/origin.git

# 2. seed the demo task (declares refs/swarm/specs/hello-demo)
python3 l2/inrepo.py seed demo/demo-spec.json

# 3. the queue as workers see it
python3 l2/inrepo.py open_tasks

# 4. launch two workers (two terminals; or run the first with ` &`)
OPENCODE_BIN=$PWD/demo/trivial_fixer.sh python3 l2/inrepo.py worker w1
OPENCODE_BIN=$PWD/demo/trivial_fixer.sh python3 l2/inrepo.py worker w2

# 5. watch the refs appear on the board
git ls-remote "$SWARM_ORIGIN" 'refs/swarm/*'

# 6. read the verdict (scratch board is local, so plain git works)
git -C /tmp/swarmo-owner-demo/origin.git log -1 --format=%B refs/swarm/verdicts/hello-demo

# 7. audit the board — h1_pass true means every claim returned with a verdict
python3 l2/inrepo.py audit hello-demo

# 8. prove exactly-once: this duplicate claim is REJECTED by the ref CAS
demo/duplicate_claim.sh hello-demo

# 9. tear the board down
rm -rf /tmp/swarmo-owner-demo
```

What you just saw: two workers raced for one task; exactly one claim
won (create-once CAS — the loser re-scanned, found the queue empty,
and exited honestly with `worker_done, completed 0`); the winner
dispatched the brief, passed the task's `verify:` oracle, landed the
fix on main, and the board recorded a `fixed: true` verdict.

**Real model dispatch:** drop `OPENCODE_BIN=demo/trivial_fixer.sh` —
workers then send the brief to opencode headless (`SWARM_MODEL`
selects the model, default `hpc-glm/zai-org/GLM-5.3-Flash`). The
`demo/trivial_fixer.sh` shim exists only so the demo is deterministic
and free.

**A real origin instead of the scratch board:** `export SWARM_ORIGIN`
at any origin git can reach (e.g. an ssh bare on another host) and
repeat steps 2–8. The scripted version accepts
`demo/owner_test.sh --origin <URL>` for that — it will seed
`hello-demo` on whatever you name, so name carefully. Never point
`SWARM_ORIGIN` at an origin you don't own while experimenting.

Deeper design: `DESIGN-NEXT.md`. What each ref means: `README-IMPROVED.md`.
