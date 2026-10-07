# flockwork — the swarm that runs like clockwork

**flockwork** is a worker-orchestration tool for swarms of AI coding
workers whose **entire coordination substrate is git itself**: no
server, no database, no queue daemon. A shared origin's refs under
`refs/swarm/` carry task briefs, claims, returns, and verdicts, and
every guarantee (exactly-once work, honest outcomes, bounded retries)
reduces to a git property you can verify with `ls-remote` and
`cat-file`.

> **New here — swarms, git refs, or both?** `docs/00-ORIENTATION.md` is
> the zero-context orientation: the project in 15 sentences, how the
> pieces relate, which doc to open for which job, what a verdict means,
> and a full glossary.

> **Naming:** the product name is flockwork; the code, CLI, and repo
> history keep the working name `swarmo` (same product). Decision
> record: `docs/NAME.md`. The `refs/swarm/*` wire namespaces are
> frozen and never rename.

## Why it exists

Cross-host coordination of coding agents has no FOSS equivalent that
does work-claiming on a shared, audit-provable substrate — the field
supervises *sessions* on one machine, not *work* across many hosts
(`SURVEY-FOSS-2026-09-18.md`). flockwork's bet: if the substrate is
just git refs, then exactly-once claiming is a ref CAS, an audit trail
is `git log`, and a dead attempt is a ref you can archive without losing
evidence. The repo is both the code and the measurement record:
frozen hypotheses (`DESIGNS.md`), verdicts against them
(`VERDICTS.md`), and the probes that settled the design
(`experiments/c0`–`c9`).

## What the refs mean (one diagram)

```
  origin  (any git remote — a bare repo)
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

The tight model — five verbs (`seed`/`claim`/`work`/`verdict`/`evict`)
and the four honesty laws that keep the lane truthful — is
`docs/REFS-MODEL.md`.

## Run it yourself (verified)

The scripted end-to-end demo is deterministic and free (zero model
spend; it uses a local scratch origin, never your real board):

```bash
demo/owner_test.sh    # idempotent; ends with a PASS line
```

It seeds a task, races two workers for it, and shows exactly one
claim win, a `fixed: true` verdict, a green audit, and a deliberately
rejected duplicate claim. Verified against this tree: `PASS`, rc 0.

Hands-on, nine copy-pasteable steps: `QUICKSTART.md`.
To use real models, drop the `OPENCODE_BIN=demo/trivial_fixer.sh`
shim and set `SWARM_MODEL` (see QUICKSTART § Real model dispatch).
**Never point `SWARM_ORIGIN` at an origin you don't own while
experimenting** — seeding writes refs every watching worker can see.

The single CLI is `python3 l2/inrepo.py`; verbs: `seed`, `open_tasks`,
`worker`, `audit`, `review`, `gate`, `sweep`, `reconcile`, `relabel`,
`divergence`, `correction-graph`. An optional observer
(`FLOCKWORK_METRICS=1`, summary via `python3 -m l2.metrics`) records
lane events as JSONL without touching the wire. Tests:
`python3 -m pytest -q`.

## Honest status and tradeoffs

- **Proven:** the lane end-to-end on a scratch origin (demo above,
  `tests/` — TDD red→green history in the git log), exactly-once
  claims under real contention (`experiments/c3`, `demo/owner_test.sh`),
  cross-host runs on the author's own hosts (records: `LOOP-*.md`,
  `experiments/c8`–`c9`).
- **Experimental / pilot-stage:** the verdict-count gate (`review`/
  `gate`, `docs/GATES.md`, piloted and landing through the lane
  itself), the metrics observer, `FLOCKWORK_KEEP_TREE`, and the
  multi-model correction-graph protocol (`loop/CORRECTION-GRAPH-SPEC-*`).
- **Known issue:** `tools/test_correction_graph_spec.py` has two
  pre-existing failures (a grammar test whose att label contains a
  `-` in the role, which `mm_model_of`'s documented grammar rejects) —
  the correction-graph spec itself is pre-registered, NOT executed
  (launch is owner-gated). Everything else passes: 231 passed +
  8 xfailed (xfail = deliberately-buggy c1 fixtures, pinned by design).
- **Tradeoff — substrate is one origin:** every guarantee rides on
  git's ref CAS; a broken or unreachable origin is reported loudly
  (fail-loud laws, waves in the git log) but there is no redundancy
  story for the origin itself.
- **Tradeoff — the two names:** `swarmo` persists in code, CLI output,
  and history; expect it there (docs/NAME.md is the record).
- **Provenance note:** references to `ops/` (e.g. `ops/INTENTS.md`)
  point at the ledger of a separate private driver repo, not at this
  tree. Dated files (`LOOP-*`, `AUTOWORK-*`, `DESIGNS.md`,
  `VERDICTS.md`, `NIGHT-SUMMARY.md`, `HQ-INTEGRATION.md`,
  `DOCS-NODE.md`) are decision records, kept as written.

## Project layout

```
l2/               the machinery (inrepo.py = the lane; gates.py,
                  metrics.py, refschema.py, backend.py, rehearse.py)
tests/            pytest suite for the landed lane laws
tools/            worker_loop.py (container-isolated dispatch leg,
                  kept for provenance) + per-wave probe tests
demo/             the free deterministic end-to-end kit (owner_test.sh,
                  trivial_fixer.sh, duplicate_claim.sh, demo-spec.json)
docs/             living documentation (see docs/README.md for the index)
experiments/c0–c9 frozen probe freezes + results (the measurement record)
loop/             experiment result JSONs + frozen protocols
survey/           FOSS landscape survey
QUICKSTART.md     9-step hands-on scratch demo
DESIGN-NEXT.md    design record and what comes next
```

## License

License: **to be decided.** No LICENSE file ships with this tree yet;
the owner has the call.
