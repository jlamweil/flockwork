# NIGHT-SUMMARY — example-host-b night 2026-09-15 (for the morning harvest)

**TL;DR: The frozen design search completed. Design B (ledger-claim
minimal) wins; its one missing piece — the docker worker leg — was
repaired, closed at 12/13 fixes, proven under 3-way parallelism and
mid-dispatch crash, and verified by the production ladder at rung
VERIFIED. Design C survived all probes (documented fallback). Design A
eliminated on its own pre-registered burst test. Everything is in this
repo; `git log` is the audit trail.**

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
  worker lane **VERIFIED (12/13, LCB90 .718)**; model-death lanes held
  at measured. Verdicts also replayed as OTLP/JSON lineage spans
  (30/30 valid, parent-child intact).

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
