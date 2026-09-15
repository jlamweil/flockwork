# NIGHT-SUMMARY — example-host-b night 2026-09-15 (for the morning harvest)

**TL;DR: The frozen design search completed. Design B (ledger-claim
minimal) wins; its one missing piece — the docker worker leg — was
repaired, closed at 12/13 fixes, proven under 3-way parallelism and
mid-dispatch crash, and verified by the production ladder at rung
VERIFIED. A pre-registered fresh-task batch (c6, 01:15) then pushed the
worker lane to 20/21, LCB90 .8122 → rung PROVEN (agree gate .75 met
with independent first-execution evidence). Design C survived all
probes (documented fallback). Design A eliminated on its own
pre-registered burst test. Everything is in this repo; `git log` is the
audit trail.**

## What was frozen vs what was run

- `DESIGNS.md` (committed before any discriminating check): 3 rival
  designs, 11 load-bearing hypotheses, 6 probes, mechanical decision
  rule.
- `VERDICTS.md`: all 6 probes executed against those thresholds; rule
  applied mechanically. B survives (B1✓B2✓B3✓), C survives
  (C1✓C2✓C3✓C4✓), A eliminated (A3✗: stock http.server p95 = 1056 ms
  under a 60-proc burst — a one-line listen-backlog repair, measured,
  but the frozen verdict stands).

## The winner's missing leg, closed tonight

- `tools/worker_loop.py` — claim(flock) → docker opencode → harvest
  (git commit with `Attempt:` trailer) → host pytest → ledger rows.
  5/5 seeded bugs fixed (10.5–43 s each), all host-verified.
- c3: 3 parallel workers, rename-claim queue — exactly-once, 5/5 fixed.
- c4: worker SIGKILLed mid-dispatch — sweep killed the orphan container
  by name, requeued, heir completed; exactly one final verdict, zero
  leaked containers.
- c5: tonight's 15 verdicts through the production Wilson gate →
  worker lane **VERIFIED (12/13 attempts, LCB90 .718)**; model-death lanes held
  at measured. Verdicts also replayed as OTLP/JSON lineage spans
  (30/30 valid, parent-child intact). Note: the n counts leg attempts
  (each an independent execution), not distinct tasks — fizzbuzz
  appears 3× (one environmental death, two fixes, including the c4
  retry path).
- c6 (01:15, next session): **8 fresh tasks (never run earlier that
  night), protocol frozen before the run** (`experiments/c6/FREEZE.md`)
  → 8/8 fixed in 172.7 s → worker lane **20/21, LCB90 .8122 → PROVEN**
  (agree gate .75 met). Spans backfilled (16/16). One disclosed agent
  fault: a mis-started pre-freeze partial run was killed and discarded
  wholesale, unexamined; disclosure lives in the frozen FREEZE.md.

## Mechanical knowledge worth its weight (E10, expanded)

The c1 container leg failed for 4 mechanical reasons, all fixed and
checklisted in `HQ-INTEGRATION.md`: fixtures beside the mount (empty
/work); docker root-creates the workspace mountpoint; /secrets path
trips opencode's external_directory auto-reject; docker root-creates
intermediate dirs of a file mount target (E10-d, found live tonight).
Plus: killing the docker client leaks the container — kill by name.

## Suggested next actions (owner decides)

1. Morning: harvest this repo to HQ; wire `tools/worker_loop.py` per
   `HQ-INTEGRATION.md` (error-table rows all have live instances now).
2. Let the worker lane accumulate nights toward the agree gate (.75) —
   same-night reruns would inflate n without independent evidence.
3. Design C stays the documented fallback; its probe scripts
   (`experiments/c2/git_probe.py`) are the starting point if file+flock
   coordination ever hits a multi-host wall.

## Artifact map

| Path | What |
|------|------|
| `DESIGNS.md` / `VERDICTS.md` | frozen hypotheses + verdicts (audit pair) |
| `experiments/c2/` | probe scripts + results JSONs + container stderr evidence |
| `experiments/c3..c5/` | parallel, crash/revive, calibration + spans |
| `tools/worker_loop.py` | the reusable worker leg (Design B) |
| `tools/calibration.py` | Wilson ladder/RAEE toolkit (unchanged, consumed) |
| `HQ-INTEGRATION.md` | wiring map for the live batcher |

## Second-session audit (22:30, same night)

A fresh session re-verified the record without re-opening the frozen
search — nothing above was changed:

- All result JSONs re-read against the VERDICTS scoreboard: flock 10/10,
  git-CAS 1-accept/31-reject ×10, HTTP p95 1055.7ms FAIL + posthoc 177.6ms,
  OTel 2-span parent-child, worker att-9f3b2b15 (12.1s/416B/exit 0),
  c3 exactly-once 5/5, c4 crash/revive flags, c5 15 verdicts + 30/30 spans —
  all match.
- LCB arithmetic re-derived: 12/13 at z=1.645 → 0.7177 exactly (the gate's
  LCB90 convention), matching tools/calibration.py.
- `pytest tools/test_calibration.py tools/test_worker_loop.py`: 24/24 pass.
- Constraints: no git remote (pushes impossible); all commits inside
  ~/swarmo; working tree clean; nested fixture repos correctly gitignored;
  disk 79% / 96G free at audit (never filled); freeze commit (19:27)
  precedes probe commit (21:21) in history, so freeze-before-verify is
  git-provable.

## Addendum — next night 2026-09-16 (~01:50): round settled

- Worker lane stands at **PROVEN** (20/21, LCB90 .8122 — c6 batch
  committed as 913d87a). No reruns: same-night reruns add no independent
  evidence.
- Design-search round settled (VERDICTS §10, frozen in
  `experiments/c7/FREEZE-C7.md` before the run): shortlist B > C, A dead
  on its recorded kill reason (H-A3). The B-vs-C head-to-head's open
  link — C's crash/attempt semantics in refs — is now **closed**: the
  cheapest separating check (docker-free, model-free, 0.6 s) replayed
  c4's crash/revive on a git-native substrate and reproduced it exactly
  (H1 ∧ H2 PASS). C is a credible crash-safe fallback, not just a
  probe-survivor; B remains the winner.
- Operational takeaway for harvest: unchanged — wire `tools/worker_loop.py`
  per `HQ-INTEGRATION.md`. The remaining real B-vs-C discriminator is
  multi-host coordination (file+flock needs a shared kernel; ref-CAS
  works over git transport) — only testable on a real two-host setup;
  not exercised here by construction.
