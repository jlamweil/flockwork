# 00-ORIENTATION — flockwork from zero context

You have never seen this repo. Maybe you have never coordinated anything
with git refs. This page is the no-prerequisites orientation: what the
project is, how the pieces relate, which doc to open for which job, and
what every term means. Then run `QUICKSTART.md` — it is hands-on from
line one.

## The project in 15 sentences

1. **flockwork** (the code, CLI, and history carry the working name
   **swarmo** — same product, see `docs/NAME.md`) coordinates a swarm of
   AI coding workers using nothing but git.
2. There is no server, no database, and no queue daemon: all
   coordination state lives as git **refs** under `refs/swarm/*` on one
   shared **origin** (any bare git repository every participant can
   reach).
3. A **ref** is just a named git pointer you can list with
   `git ls-remote` and read with `git cat-file` — the swarm's whole
   vocabulary is five ref families.
4. A **task** starts when an operator **seeds** a brief (text plus a
   `verify:` oracle line) as a root commit at
   `refs/swarm/specs/<task>`.
5. A **worker** that wants the task **claims** it by pushing an empty
   root commit to `refs/swarm/claims/<task>` with
   `git push --force-with-lease=refs/swarm/claims/<task>:` — a
   create-once compare-and-swap (**CAS**), so git itself rejects the
   second claim and exactly-once needs no lock file.
6. The claim winner clones the origin, **dispatches** the brief to a
   coding model (opencode headless by default; demos use a
   deterministic stand-in), and the brief's `verify:` line then judges
   the resulting tree.
7. A pass lands the fix on `main`, pushes a return ref at
   `refs/swarm/tasks/<task>` pointing at it, and closes the attempt
   with a **verdict** root commit at `refs/swarm/verdicts/<task>` whose
   message reads `task / attempt / fixed / host / oc_rc / pytest_rc`
   (`host:` carries the worker name).
8. An honest **merit failure** — dispatched work that fails its oracle —
   touches nothing but the verdict (`fixed: false`); it never lands on
   main. With the optional debug switch `FLOCKWORK_KEEP_TREE=1` the
   failed attempt's tree is also MOVED to `kept-attempts/<task>@<att>`
   (or `FLOCKWORK_KEEP_DIR`) and the verdict carries its `kept:` path —
   a failure you can audit instead of a failure you must imagine.
9. An **environmental death** — dispatch timeout (`oc_rc: 124`) or a
   failed dispatch that left an empty tree — is never recorded as a
   fix: the attempt is archived under
   `refs/swarm/archive/<kind>/<task>@<att>` and exactly one **heir**
   retries before a final honest `fixed: false`.
10. Every attempt is labeled `att-<worker>-<hex>`; the refs alone must
    separate environmental deaths from merit failures without reading
    any worker's stdout.
11. Four **honesty laws** keep the lane truthful: claim-loss honesty
    (a lost race is healthy contention to re-scan, a broken origin is
    reported, bounded by `SWARM_WORKER_MAX_FAILS` so a sick host exits
    instead of spinning); the law-freshness gate (a worker refuses to
    claim when its copy of `l2/inrepo.py` differs from origin main's
    published copy — stale code never writes to the substrate);
    environmental honesty (law 9); and main-push honesty (a fix is a
    fix only if its commit really lands on main).
12. All machinery is one file, `l2/inrepo.py`; its CLI verbs are
    `seed`, `open_tasks`, `worker`, `audit`, `sweep`, `reconcile`,
    `correction-graph` — plus an optional metrics observer
    (`FLOCKWORK_METRICS=1`, `python3 -m l2.metrics`).
13. `python3 l2/inrepo.py audit` is the board's health instrument:
    `h1_pass: true` means every live claim has its matching return and
    verdict; `claim_orphans` reports stray claims whose specs are gone
    (report-only).
14. This repo doubles as its own substrate (**D-INREPO**): the
    machinery coordinates work on this very repository's origin.
15. You can drive the whole loop in five minutes on a **scratch
    origin** (a throwaway bare repo under `/tmp`, zero model spend, the
    real substrate untouched) — that is exactly what `QUICKSTART.md`
    and `demo/owner_test.sh` do; never point `SWARM_ORIGIN` at an
    origin you don't own while experimenting.

## How the pieces relate

| Piece | What it is | Open it when |
|---|---|---|
| `QUICKSTART.md` | the 9-step hands-on scratch demo | you want to *run* it (first stop) |
| `demo/owner_test.sh` | the same loop scripted, idempotent, ends with `PASS:` | you'd rather one-shot than type |
| `demo/demo-spec.json` | the demo task's brief (`hello.txt` + `verify:`) | you want to see what a task looks like |
| `demo/trivial_fixer.sh` | deterministic stand-in for the model dispatch | you wonder what "dispatch" means |
| `demo/duplicate_claim.sh` | deliberate second-claim probe (CAS rejection) | you want to watch exactly-once hold |
| `README-IMPROVED.md` | refs-model diagram + the five verbs + honesty laws | you want the model, tightly |
| `l2/inrepo.py` | the machinery: every verb, ref write, and gate | you change or audit behavior |
| `tests/` | pytest suite (TDD red→green history in git log) | you touch `l2/` |
| `l2/metrics.py` | optional JSONL observer + summary judge | you instrument pilots |
| `DESIGN-NEXT.md` | design record and what comes next | you want the roadmap |
| `DESIGNS.md` / `VERDICTS.md` / `LOOP-*.md` / `experiments/` | frozen hypotheses, verdict history, nightly records | you want archaeology |
| `docs/NAME.md` | flockwork-vs-swarmo naming decision record | the two names confuse you |

## Which job are you here for?

- **"Just show it working."** → `QUICKSTART.md` (or
  `demo/owner_test.sh --keep` to keep the board for poking).
- **"Understand the refs."** → `README-IMPROVED.md` § The refs model,
  then the glossary below.
- **"What does a verdict mean?"** → `QUICKSTART.md` step 6 and
  "Reading a verdict" below.
- **"Send real models instead of the shim."** → `QUICKSTART.md`
  § Real model dispatch (drop `OPENCODE_BIN`, set `SWARM_MODEL`).
- **"Use my own origin."** → `SWARM_ORIGIN` / `demo/owner_test.sh
  --origin <URL>` — read the DANGER notes first; seeding writes refs
  every watching worker can see.
- **"Is my board healthy?"** → `python3 l2/inrepo.py audit` —
  `h1_pass: true` is green.
- **"Why two names?"** → `docs/NAME.md`.

## Reading a verdict

`git log -1 --format=%B refs/swarm/verdicts/<task>` gives:

```
verdict
task: hello-demo
attempt: att-w1-3fa1b2     <- claim label att-<worker>-<hex>
fixed: true                <- the verify: oracle passed and the fix landed on main
host: w1                   <- field named host, value is the worker name
oc_rc: 0                   <- dispatch exit code; 124 = timeout
pytest_rc: 0               <- oracle exit code
```

Outcomes at a glance:

| you see | meaning |
|---|---|
| `fixed: true`, `oc_rc: 0`, `pytest_rc: 0` | fix landed on main; task done |
| `fixed: false`, `pytest_rc != 0` | honest merit failure — final, main untouched |
| `fixed: false`, `oc_rc: 124` (or failed dispatch + empty tree) | environmental death — archived, one heir retries |
| no verdict, claim still live | attempt in flight, or a dead claim awaiting `reconcile`'s TTL |

## Glossary

**Git plumbing:** *origin* — the shared bare repo everyone pushes to
(`SWARM_ORIGIN` names it). *bare repo* — a checkout-less git store,
what origins are. *ref* — a named pointer to a commit
(`git ls-remote`, `git cat-file`). *root commit* — a commit with no
parent; claim/verdict/spec commits are root commits so their objects
never leak into consumer clones. *`--force-with-lease=<ref>:`* — git's
compare-and-swap: the push only lands if `<ref>` did not exist; this
is the whole exactly-once mechanism. *scratch board* — a throwaway
bare origin under `/tmp` (or `--origin <URL>` for a real one).

**The lane (refs under `refs/swarm/`):** *`specs/<task>`* — the brief,
seeded by the operator. *`claims/<task>`* — the lease; exactly one
push wins. *`tasks/<task>`* — the return; points at the fix, on
main's lineage. *`verdicts/<task>`* — the outcome. *
`archive/<kind>/<task>@<att>`* — dead attempts, preserved forever.
*task name* — one path segment, no `@`/separators (safety-checked).
*brief* — the spec body; its `verify:` line is the oracle.

**Verbs & workers:** *seed* — declare a task. *claim* — lease it via
the CAS. *work* — clone, dispatch, judge, land. *verdict* — close the
attempt. *evict* — archive dead attempts and free the live refs in
one transaction (`sweep`, and `reconcile` — the TTL backstop for stale
claims). *`worker <name>`* — the claim→dispatch→verify loop as one
command; exits honestly distinct for "queue empty", "unreachable
origin", and "law refusal". *`att`* — attempt label
`att-<worker>-<hex>`. *heir* — the one bounded retry an environmental
death gets. *dispatch* — running the brief through
`$OPENCODE_BIN run --pure -m $SWARM_MODEL <brief>` in a scratch work
tree (`SWARM_DISPATCH_TIMEOUT`, default 600 s). *`OPENCODE_BIN`* — the
dispatch binary; default `opencode`, set it to `demo/trivial_fixer.sh`
for the free deterministic shim. *`SWARM_MODEL`* — model id, default
`hpc-glm/zai-org/GLM-5.3-Flash`. *law-freshness gate* — workers
refuse to claim when their `l2/inrepo.py` differs from origin main's
published copy. *`SWARM_WORKER_MAX_FAILS`* — consecutive-failure
budget (default 3) after which a worker gives up instead of spinning.

**Outcomes & health:** *fixed true/false* — the verdict's headline.
*`oc_rc` / `pytest_rc`* — dispatch vs oracle exit codes; their
combination separates environmental deaths from merit failures.
*h1_pass* — audit green: every live claim has return + verdict.
*claim_orphans* — claims whose specs vanished; report-only.
*correction-graph* — reconstructs the multi-model attempt graph from
refs alone. *exactly-once* — one claim wins per task per attempt; it
is the ref CAS, not a lock.

**Names & places:** *flockwork / swarmo* — same product; CLI and
history keep `swarmo` (`docs/NAME.md`). *example-host-a* — the host hosting
the real production origin (default `SWARM_ORIGIN` points there —
override before experimenting). *D-INREPO* — the design decision that
this repo is its own substrate. *WQ-xxx / INT-xxx* — the work-queue
and intent ids stamping each git-log entry; see the driver repo's
`ops/` for the ledger.
