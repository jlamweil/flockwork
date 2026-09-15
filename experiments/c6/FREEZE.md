# FREEZE.md — c6 fresh-task batch (pre-registered 2026-09-16 ~01:10)

**Frozen before any run.** The commit containing this file precedes any
c6 ledger row (git-provable, same discipline as DESIGNS.md → VERDICTS.md).

**Aborted pre-freeze start, disclosed:** the harness operator (session
agent) mis-started `fresh_batch.py` moments before this commit (a
compound shell command accidentally included the run). That aborted
attempt was killed, ALL its artifacts deleted (ledgers, workspaces,
queue), and none of its rows exist or count. The models never saw any
of these 8 tasks before the freeze commit other than through that
aborted start (partial dispatches of 6 tasks, results unseen and
destroyed); the honest posture is disclosure + wholesale discard, and
the frozen decision rule below applies to the clean re-run only.

## Why this batch is admissible ladder evidence (and reruns are not)

The night goal (VERDICTS §8): the worker lane's rung is decided by
Wilson LCB90 at the agree gate (.75). NIGHT-SUMMARY's caution — "same-night
reruns would inflate n without independent evidence" — rules out re-running
the c2 five. It does NOT rule out new first-execution attempts: what makes
an evidence unit independent is that the execution is the task's first, not
the clock date. c6 therefore uses EIGHT NEW tasks, never dispatched before
tonight, in the same micro-difficulty class (one seeded defect, one-line-class
fix, pytest-decided) so the lane's task mix stays comparable.

## Frozen inputs

- Tasks (c6_driver.FRESH_BUGS): palindrome, clamp, average, revwords,
  vowels, lastindex, evens, lookup. Bug sources and tests are in the
  frozen commit; fixtures assert baseline failure before dispatch.
- Machinery: c3's rename(2)-claim queue, 3 workers, per-child ledgers,
  tools/worker_loop.py unchanged. Lane/model unchanged:
  `google/gemini-3.5-flash-lite` on `swarmo-worker:c1`.
- Each task's intended one-line fix was host-verified BEFORE the freeze
  (bug fails pytest, fix passes) so no task can hang the 240s budget
  through impossibility.

## Frozen decision rule (mechanical, no post-hoc edits)

1. Every ledger attempt counts — success or failure.
2. Environmental death := verdict with `commit_sha=None ∧ patch_bytes=0`
   (timeout-124, model/provider death, EACCES at work start). Such a task
   is requeued ONCE (c4 heir semantics); a merit failure (patch produced,
   host tests fail) is FINAL. This caps any task at 2 attempts and both
   count.
3. After the batch: lane record = all tonight verdicts for the lane
   (c2 + c3 + c4 + billing probes + c6) through tools/calibration.py
   `gate_rung` UNCHANGED (LCB90, z=1.6449, accept .70 / agree .75,
   MIN_N=5). Whatever rung results is recorded, promoted or demoted —
   two merit failures would legitimately demote the lane to `measured`.

## Pre-registered predictions (Informational, written pre-run)

Derived from tools/calibration.py itself (not hand arithmetic — an
earlier draft of this table had two wrong values, caught and fixed
BEFORE the freeze commit):

- 8/8 fixed → 20/21 → LCB90 = .8122 → **proven** (night goal met).
- 7/8 → 19/21 → .7492 → verified (misses agree gate).
- 6/8 → 18/21 → .6913 → measured (demoted below accept; recorded as-is).

Continuation policy (pre-registered, not improvised): if the rung after
c6 is below proven AND the clock is before 01:45, further NEW-task
batches may run under this same protocol (new tasks each time, every
attempt counts, merit failures final, recompute after each batch).
Stopping while rung < proven happens only at 01:45 hard stop.

- Regression risks watched (not gates): exactly-once claims per queue
  generation; every claim joins a verdict; zero leaked containers.
