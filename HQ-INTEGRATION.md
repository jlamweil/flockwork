# HQ-INTEGRATION.md — wiring the repaired worker leg into the batcher

Written for the morning harvest (example-host-b night 2026-09-15). Design B won the
frozen decision rule (VERDICTS.md §2); its one marginal build — the
opencode-in-docker worker leg — closed tonight at 5/5 cases single-worker
(experiments/c2) and 5/5 under 3-way parallel contention
(experiments/c3). This note is the map from those files into the live
batcher (SURVEY §A) with zero design change.

## In-repo lane (current)

As of the dogfood run (2026-09-18) and waves through 2026-09-21, the live
batcher wires into `l2/inrepo.py` — the in-repo lane that coordinates on
the project origin itself (claim-CAS refs under `swarm/*`), with the
seed/worker/audit/sweep/reconcile/divergence/law-gate CLI. It is now the
proven coordination substrate; the docker recipe below remains the
container-isolated dispatch backend inside that lane.

- One-line worker invocation:

  ```
  SWARM_ORIGIN=<origin-url> python3 l2/inrepo.py worker <label>
  ```

- Dispatch is selectable; coordination is not. `tools/worker_loop.py`
  (the docker leg) stays as the container-isolated dispatch backend;
  coordination itself is done by git ref-CAS on the origin — claim,
  verdict, and archive refs, no flock between hosts.
- Operational health commands, each printing a JSON receipt:
  `audit`, `reconcile`, `sweep`, `divergence`. Run them per wave to
  requeue orphans, close claim/verdict gaps, and confirm the mirror is
  not diverging from the origin.

The sections below are the 2026-09-15 docker recipe's wiring map and
checklist — still the dispatch backend's contract.

## What the batcher keeps (unchanged)

Everything in SURVEY §A.1–A.10: snapshot-wins ledger, claim=send-intent,
flock singleton per child ledger, parking + 1/min slots, error table,
revive. The worker leg adds ONE dispatch backend behind the existing
spawn step; it replaces no coordination code.

## New dispatch backend (tools/worker_loop.py)

- `Claim(ledger)` = the child's existing flock singleton (H-B1: portable
  to example-host-b, 10/10 races).
- `run_case(...)` = claim → docker dispatch → harvest (git show of the
  container's commit) → host pytest → claim+verdict rows with `att-*`.
- Each child keeps its OWN ledger (production rule A.4); c3's three
  workers contended only on the shared queue, never on each other's
  ledgers, and every task ran exactly once (rename-claim).

## Error-table additions (classify → decide, no new machinery)

All rows below have live instances from example-host-b night (VERDICTS.md §7):

| Observed class | Tonight's instance | Decide |
|---|---|---|
| worker killed mid-dispatch | c4: SIGKILL left claim w/o verdict; sweep killed orphan container by name, requeued; heir completed (1 final verdict, 0 leaked containers) | existing revive path (A.10), verified |
| dispatch timeout (wall-cap) | any case > 240s: kill CONTAINER, not just the client (killed clients leak running containers) | REQUEUE, fresh att-* |
| model death: billing OR catalog | E9 billing ("Insufficient balance", 2.0s); c3 probe: ProviderModelNotFoundError, 1.8s, both clean no-credit | REQUEUE + session exit (A.7); pin full provider/model string per session-file |
| EACCES in container | E10-b, E10-d (root-owned mountpoints) | QUARANTINE image/mount recipe — environmental, not task-fatal |
| external_directory auto-reject | E10-c (/secrets probe) | QUARANTINE recipe: never stage auth under a path a model may probe |
| empty /work at start | E10-a (fixtures beside the mount) | RECIPE bug, not runtime: fixtures inside workspace/ BEFORE mount |

## Container recipe checklist (the four E10 fixes, all load-bearing)

1. Fixtures inside `workspace/` before `docker run` (else empty /work).
2. Workspace dir pre-created by uid 1004 on host (docker otherwise
   root-creates it → pytest Errno 13).
3. No `/secrets` or other probed paths; auth ro-mounted at `$HOME/auth.json`,
   staged by `cp` into `~/.local/share/opencode/` (docker root-creates
   intermediate dirs of mount targets — E10-d — so only $HOME itself is
   a safe mountpoint).
4. Timeout kills the named container (`docker kill <name>`), exit 124 →
   error table.

## Cost profile measured tonight (flash tier, this host)

- 10.5–43.0 s per case wall (median ~12 s), opencode cold-DB migration
  adds a few seconds per fresh HOME. Docker leg idle cost ≈ 0. Disk
  guard: no new images/pulls needed — `swarmo-worker:c1` sufficed.

## Review/lineage payoff

Verdict rows now join to content: `(task, attemptId, commit_sha)`,
commit message carries `Attempt: att-*`. Scoped reviews (E8) can cite
the commit sha directly — ranking signal without transcript trees. The
Wilson ladder (E7) consumes the `fixed` field unchanged.
