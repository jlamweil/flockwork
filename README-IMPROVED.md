# flockwork — the swarm that runs like clockwork

**flockwork** is a worker swarm whose **entire coordination substrate
is git itself**: no server, no database, no queue daemon — a shared
origin's
refs under `refs/swarm/` carry task briefs, claims, returns, and
verdicts, and every guarantee (exactly-once work, honest outcomes,
bounded retries) reduces to a git property you can verify with
`ls-remote` and `cat-file`. This repo doubles as its own substrate
(D-INREPO, LOOP-2026-09-18.md): the machinery lives in `l2/inrepo.py`,
and workers coordinate through refs on the project's own origin. (The
code, CLI, and repo history carry the working name `swarmo` — same
product. Naming decision record: `docs/NAME.md`.)

Run it yourself in five minutes: `QUICKSTART.md` (scratch board, zero
model spend). Design record and what comes next: `DESIGN-NEXT.md`.
Frozen hypotheses: `DESIGNS.md`; verdict history: `VERDICTS.md`;
nightly loop records: `LOOP-*.md`; experiment freezes: `experiments/`.

## The refs model (one diagram)

```
  origin  (any git remote — a bare repo; e.g. example-host-a swarmo-origin.git)
  ├── refs/heads/main                      the product: landed fixes
  └── refs/swarm/*                         the coordination lane
      ├── specs/<task>      the brief      operator seeds it (root commit,
      │                                    body carries the task + `verify:` line)
      ├── claims/<task>     the lease      create-once CAS push — exactly one
      │                                    worker wins; duplicates are rejected
      ├── tasks/<task>      the return     points at the fix, on main's lineage
      ├── verdicts/<task>   the outcome    root commit: task/attempt/fixed/host/rc
      └── archive/<kind>/<task>@<att>      dead attempts, preserved (never deleted)
```

Claim and verdict commits are ROOT commits, so their objects never
leak into consumer clones or fetches; the whole lane is evictable with
a one-transaction namespace delete.

## The five verbs

1. **seed** — the operator declares a task: `python3 l2/inrepo.py seed
   spec.json` pushes `refs/swarm/specs/<task>` carrying the brief; a
   `verify:` line in the brief is the task's oracle.
2. **claim** — a worker leases a task: a create-once CAS push
   (`push --force-with-lease=<ref>:`) to `refs/swarm/claims/<task>`.
   Git itself rejects the second claim — exactly-once is not a lock
   file, it is the ref database.
3. **work** — the claim winner clones the origin, dispatches the brief
   to the model (opencode headless, or the freebuff driver), and the
   `verify:` line (or host pytest) judges the tree. A pass lands the
   fix on main and points the return ref at it; a fail touches
   nothing but the verdict.
4. **verdict** — the attempt closes as a root commit at
   `refs/swarm/verdicts/<task>`: `fixed: true|false`, host, dispatch
   and oracle exit codes. The refs alone must separate environmental
   deaths from honest merit failures.
5. **evict** — `sweep`/`reconcile` archive dead attempts to
   `refs/swarm/archive/<kind>/<task>@<att>` and free the live refs in
   one atomic transaction, so the task re-enters the queue; eviction
   is bounded (one heir per death, then a final honest `fixed:false`).

## The honesty laws (what keeps the lane truthful)

Claim and verdict commits are ROOT commits so their objects never leak
into consumer fetches, and eviction is a one-transaction namespace
delete. Four honesty laws keep the lane truthful. Claim-loss honesty:
a failed claim is classified — a lost race (`! [rejected] (stale
info)`) is healthy contention to re-scan, a structurally broken origin
is reported — and bounded by `SWARM_WORKER_MAX_FAILS` so a sick host
exits instead of spinning. The law-freshness gate: each worker refuses
to claim when its running `l2/inrepo.py` blob differs from origin
main's published copy, so stale code never writes to the substrate.
Environmental honesty: a dispatch death (timeout, or failure with an
empty tree) is never recorded as a fix — the attempt is archived and
requeued to one heir, then closed with a final honest `fixed:false`;
and main-push honesty: a fix is a fix only if its commit actually
lands on main, else the task requeues under the same bounded
machinery — and a change that fails its oracle never lands at all: a
merit failure publishes only its final honest verdict, never to main.
Scratch hygiene removes every `/tmp/inrepo-*` work tree on every
attempt path, and the `audit` command publishes `claim_orphans` —
report-only claim refs whose specs are gone.

`python3 l2/inrepo.py audit` is the board's health instrument
(`h1_pass`: every live claim has a matching return + verdict);
`reconcile` is the TTL backstop for stale claims; `correction-graph`
reconstructs the multi-model attempt graph from refs alone.
