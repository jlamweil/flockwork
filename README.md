# flockwork — the swarm that runs like clockwork

[![PyPI version](https://img.shields.io/pypi/v/flockwork.svg)](https://pypi.org/project/flockwork/)
[![Python versions](https://img.shields.io/pypi/pyversions/flockwork.svg)](https://pypi.org/project/flockwork/)

**flockwork** is a worker swarm whose **entire coordination substrate is
git itself**: no server, no database, no queue daemon — a shared origin's
refs under `refs/swarm/` carry task briefs, claims, returns, and
verdicts, and every guarantee (exactly-once work, honest outcomes,
bounded retries) reduces to a git property you can verify with
`ls-remote` and `cat-file`. This repo doubles as its own substrate
(**D-INREPO**): the machinery lives in `l2/inrepo.py`, and workers
coordinate through refs on the project's own origin.

> **New to swarms, git refs, or both?** `docs/00-ORIENTATION.md` is the
> zero-context orientation: the project in 15 sentences, how the pieces
> relate, which doc to open for which job, what a verdict means, and a
> full glossary.

Run it yourself in five minutes: `QUICKSTART.md` (scratch board, zero
model spend).

## Install

```bash
pip install flockwork            # from PyPI (Python >=3.10, zero runtime deps)
```

The installed `flockwork` console script is the same CLI as
`python3 l2/inrepo.py`; see `docs/00-ORIENTATION.md` for how to point it
at a board with `SWARM_ORIGIN`. Requires Python `>=3.10` and pulls **no
third-party runtime dependencies** (all of `l2/` is stdlib-only), so it
installs cleanly into any environment.

## The refs model (one diagram)

```
  origin  (any git remote — a bare repo; e.g. a local bare repo under /tmp)
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
 3. **work** — the claim winner clones the origin, dispatches the brief to
    the model through the dispatch seam (`SWARM_DISPATCH`, default
    `opencode`; `freebuff` is the proven second harness), and the `verify:`
    line (or host pytest) judges the tree. A pass lands the fix on main and
    points the return ref at it; a fail touches nothing but the verdict. The
    dispatched model + harness are stamped on the attempt so attribution is
    harness-aware, and a `dispatch_started` checkpoint is pushed at dispatch
    start so a crash mid-run is legible from the refs.
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
delete. Four honesty laws keep the lane truthful. **Claim-loss
honesty:** a failed claim is classified — a lost race (`! [rejected]
(stale info)`) is healthy contention to re-scan, a structurally broken
origin is reported — and bounded by `SWARM_WORKER_MAX_FAILS` so a sick
host exits instead of spinning. **Law-freshness gate:** each worker
refuses to claim when its running `l2/inrepo.py` blob differs from
origin main's published copy, so stale code never writes to the
substrate. **Environmental honesty:** a dispatch death (timeout, or
failure with an empty tree) is never recorded as a fix — the attempt is
archived and requeued to one heir, then closed with a final honest
`fixed:false`. **Main-push honesty:** a fix is a fix only if its commit
actually lands on main, else the task requeues under the same bounded
machinery — and a change that fails its oracle never lands at all: a
merit failure publishes only its final honest verdict, never to main.

**No-default-origin law:** there is **no** default `SWARM_ORIGIN`. Every
origin resolution is explicit-arg > `SWARM_ORIGIN` at call time > the
import-time `ORIGIN` > a typed `OriginUnsetError` naming the fix. A
write or read leg with no origin anywhere refuses *before* any git/ssh
contact, so a forgotten config can never push into a production board.

`python3 l2/inrepo.py audit` is the board's health instrument
(`h1_pass`: every live claim has a matching return + verdict);
`reconcile` is the TTL backstop for stale claims; `correction-graph`
reconstructs the multi-model attempt graph from refs alone;
`attribution` is the per-task attempt table (model, kind, harness, verdict,
plus `in_progress`/`interrupted` for live and crashed claims); and
`durations` reports the per-model `oc_elapsed` distribution read from the
verdict refs, flagging a near-bound model that risks a timeout.

## Observability (opt-in, zero wire change)

- `FLOCKWORK_METRICS=1` turns on a JSONL observer over the lane's own
  results; `python3 -m l2.metrics [files]` summarizes counts,
  reject-rate, and claim→verdict latency. `--dora` adds the DORA five
  over the same stream. The `dispatch_started` checkpoint + `harness` ride
  the stream too.
- `FLOCKWORK_KEEP_TREE=1` preserves a merit-failed attempt's tree so a
  failure is auditable.
- `FLOCKWORK_DEBUG=1` on `attribution` dumps the raw claim/verdict bodies +
  the claim's lease age (board-scoped, read-only).

## Harness auto-use (opencode / codex / claude)

The substrate is driven either by a headless `worker` (the `QUICKSTART` path) or
by a human-in-the-loop **coding harness**. The harness layer turns an interactive
session into a transparent, honest swarm run: the **opencode plugin**
(`.opencode/`, npm `opencode-flockwork`) activates on a local `.flockwork/origin`
marker and exposes the `fw_*` tools, and the **MCP server** (`l2/mcp_server.py`)
exposes the same tools to any MCP client (codex, claude). Every attempt stamps
its harness + model, so a verdict replays from the refs alone. `demo/wire_board.sh`
wires a project in one step. Full doc: `docs/HARNESS.md`.

## Layout

```
l2/inrepo.py       the lane law: seed/claim/work/verdict/evict + review/gate + audit
l2/metrics.py      the opt-in JSONL observer + DORA judge
l2/gates.py        the verdict-count gate (n-of-m reviewer agreement)
l2/refschema.py    minimal required-field schemas; loud typed refusals
l2/harness.py      the harness policy: marker/init, protocol, backstop, session map + ledgers
l2/mcp_server.py   the harness-agnostic MCP server (the fw_* tools over stdio JSON-RPC)
.opencode/         the opencode plugin (single-export) + lib + the bundled skill
opencode-plugin/   the npm package (opencode-flockwork)
tests/             the suite (hermetic: scratch boards, no network, no default origin)
demo/              owner_test.sh (headless E2E), external_work_e2e.sh (interactive/E2E),
                   wire_board.sh (wire a project), duplicate_claim.sh (CAS probe)
docs/              00-ORIENTATION (start here), REFS-MODEL, GATES, SCRATCH-BOARDS,
                   HARNESS (drive it from your coding agent)
```

## License

Apache-2.0. See `LICENSE`.
