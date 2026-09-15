# Survey pack for the swarmo design search (example-host-b, 2026-09-15)

Question: what should swarmo reuse vs invent? Below is what already
runs in production TONIGHT on this fleet — treat each as a falsifiable
prior, not a decision.

## A. Claim/ledger swarm core (batcher/, live on HQ)

1. Crash-safe ledger: append JSONL (fsync) + atomic snapshot
   (tmp+fsync+rename); recovery loads SNAPSHOT, JSONL is audit only.
   Snapshot-wins prevents split-brain duplicate sends after child death.
2. Claim = send-intent: queued→running mints fresh `attemptId att-*`
   + attempts+=1. Distinguishes retry-of-task from duplicate-task;
   required for idempotent requeue/harvest accounting.
3. Singleton: flock(LOCK_EX|LOCK_NB) on ledger.lock; second instance
   gets LockedError, never silent sharing.
4. Spawn: one child per session file, each with own ledger/SSH/slot;
   fail-fast loader + folder/slot conflict audit BEFORE any spawn.
5. Parking: children sleep till scheduleTime (no idle SSH held);
   staggered 1/min slots → zero registry bursts, zero 429s over 4 nights.
6. Cap: flock live-slot pool shared per-host; park-first-then-acquire.
7. Errors: pure classify→decide table (REQUEUE / REAP_AND_RETRY /
   RESUME_ONCE / QUARANTINE / ABORT); quota-exhaustion = requeue+exit,
   never in-child sleep.
8. Liveness: stuck_after (transcript-growth resets — progress beats
   stuck) + error_budget + max_turn wall-cap.
9. Abandon/harvest: silence_seconds heartbeat → SILENT-STALE; reconcile
   by resume+read probe (never blind re-send: resume sweeps inputs).
10. Revive: supervisor loop (10 min): pidfile-dead + queued/running
    work pending → relaunch --resume. Stops at window edge.

## B. Review ladder + calibration (tools/verdict_scoring.py, live)

LADDER: refuted < conjectured < heuristic < measured < verified < proven.
Promotion gate: Wilson LCB90 >= 0.70 accept / 0.75 agree, MIN_N = 5.
Live tonight: 5 PROVEN methods (10/10→21/23 counts); MIN_N demonstrably
held a 4/4 lane unproven. Reviews are SCOPED (evidence pointers only,
no transcripts — transcripts measured p50 1.09x author cost).
Maps to spec pillars 4–5: the correction graph + RAEE/PPR are the
unbuilt half; the ladder + Wilson gate + scoped review are built.

## C. Worker harness note (spec says "SWE-agent or OpenCode in docker")

Open question, example-host-b-verifiable tonight: does opencode run headless in
an isolated container on example-host-b, and can it execute-then-return a patch
diff? That single experiment falsifies-or-grounds the whole worker leg.

## D. Hard constraints on example-host-b

Disk 88% (54G free at staging): check `df -h` before ANY pull/image
build; record, never fill. No pushes anywhere (owner acts). No tree
commits outside ~/swarmo (fresh dir — `git init` it, commit freely
inside it). Morning harvest pulls ~/swarmo back to HQ.
