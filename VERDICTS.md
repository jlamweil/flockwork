# VERDICTS.md — swarmo best-design search (example-host-b night, 2026-09-15, 21:15)

**Status: results reported against DESIGNS.md, frozen before verification.**
All numbers come from artifacts in `experiments/c2/` committed in this repo.
Nothing below re-derives a frozen threshold; where reality forced a probe
repair, the repair is stated and the frozen verdict is still applied
unchanged. Clock at verdict: 21:15, deadline 02:00.

## 1. Scoreboard

| Probe | Hypothesis | Frozen threshold | Measured | Verdict |
|-------|------------|------------------|----------|---------|
| C-calib | H-B3 gate self-test + cited numbers | self-test passes ∧ E7 numbers stand | 20/20 passed (0.03s); E7 cited per freeze rules | **PASS** |
| C-flock | H-B1 flock exclusive-claim on this host | 10/10 rounds: 1 winner, 31 BlockingIOError, clean release | 10/10 both parts (`results_flock.json`) | **PASS** |
| C-http | H-A3 single-host coordinator burst | 0 non-200 ∧ p95 < 500 ms | 300/300 non-200=0, but p95 = **1056 ms** (p50 8 ms) | **FAIL** |
| C-git | H-C1 ref-CAS exactly-once | 10/10: 1 accept, 31 rejects, final = winner's | 10/10 exact (`results_git.json`) | **PASS** |
| C-git | H-C3 lineage query at 50k commits | grep wall < 5 s | 0.24 s (import of 50k commits: 1.0 s) | **PASS** |
| C-git | H-C4 claims/notes read paths | both < 10 s combined | for-each-ref 10k + 200 notes = 0.087 s | **PASS** |
| C-otel | H-A2 OTel SDK → local collector | ≥1 valid trace_id + child-parent pair | 2 spans, same trace_id, parent-child linked (`results_otel.json`) | **PASS** |
| C-worker | H-A1 worker leg at all | ≥1 case: non-empty diff ∧ host tests pass ∧ exit 0, ≤240s | 5/5 cases fixed; fizzbuzz 12.1s, 416B patch, host 4/4, exit 0 | **PASS** |
| C-worker | H-B2 minimal loop closes, no server | att-* row ∧ exactly-once claim ∧ ≤300s | row att-9f3b2b15; canary held=1/after=0; loop 12.7s | **PASS** |
| C-worker | H-C2 container returns git commit | commit+trailer visible on host ∧ diff matches | 5/5 commits with `Attempt: att-*`; worktree_clean=5/5 | **PASS** |

## 2. Decision rule applied (frozen §5, mechanical)

- **Design A survives iff H-A1 ∧ H-A2 ∧ H-A3** → H-A3 = FAIL → **A does not
  survive.** H-A1 and H-A2 passing are recorded but cannot rescue A under
  the frozen rule.
- **Design B survives iff H-B1 ∧ H-B2 ∧ H-B3** (H-B4 pre-met by E9) →
  all PASS → **B survives.**
- **Design C survives iff H-C1 ∧ H-C2 ∧ H-C3 ∧ H-C4** → all PASS →
  **C survives.**

### Ranking of survivors (frozen criteria)

1. *Fewest unbuilt components to first end-to-end fleet value:*
   B needs nothing beyond tonight's closed worker leg
   (`tools/worker_loop.py`, 5/5 cases); C needs the batcher's
   claim/dispatch/verdict paths re-plumbed onto git refs + notes before
   any production night can use it.
2. *Fraction of causal chain backed by production-night evidence:*
   B's chain is E1–E8 (running production) + E9, with tonight only
   proving portability (flock) and closure (worker leg). C's entire
   chain is tonight-only probes; by its own freeze text, C "has ZERO
   production-night evidence behind it."

**Winner: Design B (ledger-claim minimal). Runner-up: Design C. A is
eliminated by H-A3.** H-A4 cost count (ranks only): 3/5 pillars with zero
production code (P1 server, P2 cyclic orchestration, P3 telemetry); P4/P5
partial (ladder + ledger built; RAEE/PPR + economy not).

## 3. Probe repairs (reality vs frozen sketch — none change a threshold)

1. **H-A3 instrument kept, diagnosis added.** The frozen p95 failure is
   the stock `socketserver` listen backlog (5): 60 simultaneous connects
   force ~1 s SYN retransmits; p50 is 8 ms. Post-hoc labeled repair check
   (`results_http_posthoc-backlog-128.json`): backlog 128 → p95 178 ms,
   0 non-200. A's failure is thus one line wide but the frozen verdict
   stands: the pre-registered coordinator-class server does not absorb
   fleet bursts as-specified.
2. **H-C1 command syntax.** Frozen sketch said `verify` then `update` in
   one `update-ref --stdin` txn; git rejects two touches of one ref
   ("multiple updates ... not allowed"), so all 32 racers died on format
   until the CAS moved into `update <ref> <new> <old>` (same semantics).
   First real run after fix: 10/10 exact.
3. **E10-d (new, found live during C-worker).** Docker root-creates the
   intermediate dirs of a bind-mount file target: mounting auth at
   `$HOME/.local/share/opencode/auth.json` made `~/.local` root-owned and
   opencode died on `mkdir $HOME/.local/state` (EACCES, 1.2s, exit 1 —
   evidence: `e10d-auth-mount-EACCES.txt`, ledger row att-a64d722c).
   Repair: ro-mount auth at `$HOME/auth.json`, stage with c1's proven
   `cp` into `~/.local/share/opencode/`. Next attempt fixed the case.
   The failed attempt staying in the ledger and the retry succeeding on a
   fresh `att-*` is E2's claim=send-intent semantics observed live.
4. **Harness lessons (recorded so they aren't relearned):** flock is
   bound to the open file description — an unreleased `Claim` object
   excluded the main process from its own lock (canary now releases
   explicitly); `git fast-import` accepts `commit <ref>` only on one
   line; a ledger poll must key on the latest attempt's id, not the
   first completed row.

## 4. What C's survival means (record, not roadmap)

C passing all four probes is a real result: git CAS gave exactly-once
claims under 32-way contention, and 50k-commit lineage + 10k-ref reads
are sub-second — the mechanism B uses for claims has a functionally
proven git-native rival substrate. C stays the documented fallback if B's
file+flock coordination ever hits a wall B cannot repair (e.g., HQ
crash-safety across hosts). Runner-up status is the honest verdict, not
consolation: B wins tonight because its chain is production-backed
end-to-end and its marginal build already closed.

## 5. Next highest-value action (per loop rule)

Implement B's marginal leg for fleet use: the worker loop exists
(`tools/worker_loop.py`); remaining tonight work is a concurrency
demonstration beyond the single canary — N parallel loop instances
contending on a shared task queue, claiming exactly-once and returning
verified patches — then the HQ-side integration note for the morning
harvest.

## 6. c3 result (21:30): the winner's loop under real contention

`experiments/c3/parallel_demo.py`: N=3 worker processes contend over the
5 c2 tasks via rename(2) claims on a shared pending/ queue (atomic;
losers get FileNotFoundError — the folder analogue of H-C1's ref-CAS).
Production topology preserved: per-child ledgers (SURVEY A.4), claims
carry `att-w<i>-*`.

- **exactly_once: true** — 5/5 tasks claimed once (w0×2, w1×1, w2×2).
- **all_fixed: true** — every task returned a verified patch; host
  tests pass on all 5.
- **every_claim_has_verdict: true** — 10/10 ledger rows joined.
- Wall: 155.4 s for the whole swarm run.
- Artifacts: `results_c3.json`, per-worker ledgers `results_c3_ledger.w*.jsonl`.

HQ wiring map: `HQ-INTEGRATION.md` (dispatch backend, error-table
additions, container recipe checklist, cost profile).

## 7. c4 result (21:50) + failure-class evidence: the leg survives crashes

`experiments/c4/crash_revive.py` — victim worker SIGKILLed mid-dispatch
(container running), then a revive sweep + heir worker:

- **victim_claim_no_verdict: true** — the crash left exactly the E2
  shape: claim row without verdict, no false credit.
- **sweep_requeued: [sumto.att-victim-5ce14a]** — the sweep reconciled
  by probe: killed the orphan container BY NAME (names embed
  task+attemptId — this is why), requeued the folder. Zero containers
  leaked (E10 postscript's leak is now handled).
- **heir_fixed: true ∧ exactly_one_final_verdict: true** — fresh
  `att-heir-*` completed the task; final state = one verdict, fixed.

Failure-class probes (`experiments/c3/probe_billing_death.py`,
`results_billing_probe_ledger.jsonl`): model-death through the leg dies
fast (1.8 s), leaves no patch, `fixed=false`, ledger clean. Two
sub-instances recorded: bare `glm-5.3-flash` and provider-prefixed —
both `ProviderModelNotFoundError` in-container (catalog is
host-config-dependent; E9's "Insufficient balance" is the same family).
Error-table consequence: model-death → REQUEUE + session exit; pin the
full `provider/model` string per session-file.

## 8. c5 (21:50): the chain closes on tonight's own data

**Ladder consumes the new verdict stream** (`experiments/c5/calibrate_tonight.py`):
all 15 ledger verdicts written tonight by the new leg, run through the
production gate (Wilson LCB90, accept .70 / agree .75, MIN_N=5):

| Lane | Record | LCB90 | Rung tonight |
|------|--------|-------|--------------|
| google/gemini-3.5-flash-lite (worker leg) | 12/13 | 0.7177 | **verified** |
| glm-5.3-flash (bare id) | 0/1 | 0.0 | measured (n < MIN_N) |
| github-copilot/glm-5.3-flash | 0/1 | 0.0 | measured (n < MIN_N) |

The one failure in 13 is E10-d — an environmental death the retry
semantics recovered from, still honestly counted. (n counts leg
attempts — each an independent execution; fizzbuzz legitimately
appears three times: failed, fixed, re-fixed via the c4-style retry
path.) PROVEN needs the agree gate (≥.75); more nights of data, not
same-night reruns, promote the lane. The two model-death lanes are
held at `measured`: the ladder refuses to promote or refute on n=1 —
exactly the conservatism E7 claimed as load-bearing.

**Lineage backfill** (`experiments/c5/backfill_spans.py`): all 15
verdicts replayed as OTLP/JSON spans to the local collector — 30/30
spans valid, 15 traces, every trace with the worker-dispatch →
host-verify parent-child shape, attemptId/task/fixed/commit_sha as
attributes. Pillar-3 is therefore an optional increment on Design B
(one env-gated exporter), not a rebuild; receiver speaks both protobuf
and JSON (`experiments/c2/otel_receiver.py`).

## 9. c6 (01:15–01:25, next session): fresh-task batch → lane PROVEN

`experiments/c6/` — the night goal (§8: "PROVEN needs the agree gate")
closed the same night with **independent first-execution evidence, not
same-task reruns**. Protocol frozen in `experiments/c6/FREEZE.md`
(commit 6108331) BEFORE any run: 8 NEW seeded tasks (palindrome, clamp,
average, revwords, vowels, lastindex, evens, lookup — never dispatched
earlier tonight, intended fixes host-verified pre-freeze), same
micro-difficulty class as the c2 five; machinery unchanged (rename(2)
claims, 3 workers, `tools/worker_loop.py`, lane/model/image unchanged);
mechanical requeue rule (environmental death = no commit ∧ no patch →
one heir attempt max; merit failure final); ladder recompute through
`tools/calibration.py` with UNCHANGED thresholds.

- **Batch: 8/8 fixed** — 172.7 s wall, zero environmental deaths, zero
  merit failures, zero requeues; exactly-once holds per generation
  (8 claims / 8 verdicts / unique attempt ids); all commits carry
  `Attempt: att-*`, worktrees clean, 0 leaked containers.
- **Ladder: worker lane `google/gemini-3.5-flash-lite` 20/21,
  LCB90 = 0.8122 ≥ .75 → rung PROVEN** (was 12/13 / .7177 verified).
  Matches the pre-registered 8/8 prediction (20/21 → .8122) exactly.
  The two model-death lanes stay measured at n=1, still honestly so.
- **Lineage continuity:** the 8 new verdicts replayed as OTLP/JSON
  spans (`experiments/c6/backfill_spans_c6.py`) — 16/16 spans, 8
  traces, same parent-child shape and attribute layout as c5's 30
  (`spans_c6_backfill.jsonl`).
- **Disclosed process fault:** the freeze was committed after a
  mis-started partial run (agent error); that attempt was killed, all
  its artifacts destroyed unexamined, and the disclosure is written
  into the frozen FREEZE.md itself. The counted run is the clean
  re-run; freeze commit precedes every ledger row in history.

Tests still 24/24 (`pytest tools/test_calibration.py
tools/test_worker_loop.py`).

## 10. Round settlement (01:30–01:50, 2026-09-16): the search adjudicates again

Night goal executed against the on-disk record. **Survey finding:** the
design search's verdicts WERE on disk and complete at survey time
(§§1–9; no newer search process or output anywhere on disk), so the
goal's fallback clause ("execute the survey's own cheapest pending
discriminating check instead") did **not** trigger — the primary path
was used: adjudicate → preregister → execute. Protocol frozen in
`experiments/c7/FREEZE-C7.md` (commit 1b7632e) BEFORE any run.

### (a) Ranked shortlist with each rival's discriminating-check outcome

| Rank | Design | Outcome |
|------|--------|---------|
| 1 | **B ledger-claim minimal** | All gates PASS (H-B1✓B2✓B3✓, B4 pre-met) + hardening chain c3/c4/c5/c6; worker lane PROVEN (20/21, LCB90 .8122). |
| 2 | **C git-native** | All gates PASS (H-C1✓C2✓C3✓C4✓); runner-up on frozen criteria. One link explicitly unproven entering tonight: E2 retry/duplicate semantics in ref names (DESIGNS §2) — resolved below. |
| dead | **A D0-as-written** | **Kill reason: H-A3 FAIL** (p95 1056 ms vs <500 ms frozen, 60-proc burst). Recorded with its kill reason, not deleted; the post-hoc backlog repair (178 ms) does not reopen a frozen verdict. |

### (b) Head-to-head B vs C (preregistered before the run)

Only open dimension between the top two: crash/attempt semantics of the
claim layer — B's side proven (c4, carried by citation), C's side the
unproven link. Frozen hypotheses with falsification conditions
(FREEZE-C7 §2): **H1** — a git-native protocol (ref-CAS claims,
`refs/claims/<task>@att-*` attempt refs, `Attempt:` trailers,
`refs/notes/verdicts`) reproduces c4's crash outcome using ONLY git
primitives: after mid-dispatch SIGKILL + sweep + heir, exactly one final
verdict per task, dead attempt preserved and countable; falsified by
(a) ≠1 final verdict, (b) heir needing lock/ledger machinery, (c) lost
dead attempt, (d) sweep requeue ambiguity. **H2** — the repo alone
reconstructs {task → attempts → verdicts} exactly; falsified by any
omission/invention. Mechanical decision rule frozen: both PASS →
`crash-semantics-closed`; H1 FAIL → C records its first failure class,
fallback downgraded. The check was the **cheapest separating** one:
multi-host claims (the other candidate) is infeasible on this one-kernel
host — flock would hold across fake container-"hosts", so it has no
separating power here and remains the standing discriminator for a
two-host opportunity; speed and contention have no separating power left
(both designs measured/proven fast and exact).

### (c) Executed: H1 ∧ H2 PASS — outcome `crash-semantics-closed`

`experiments/c7/gn_crash_revive.py` replayed c4 docker-free on a scratch
repo (0.6 s wall, no model, no container; B-machinery guard: no flock,
no ledger file):

- **victim_claim_no_verdict: true** — SIGKILL mid-dispatch left the
  c4/E2 shape: live claim ref, no return commit, no verdict note.
- **sweep_requeued: [t-victim.att-victim-4f6332]** — orphan killed by
  tag (names embed task+attempt — same reason c4 killed containers by
  name); dead attempt preserved in `refs/claims/t-victim@att-victim-4f6332`
  (never deleted silently); live claim CAS-deleted; second sweep run
  requeued nothing (idempotent).
- **heir_fixed: true ∧ exactly_one_final_verdict: true** — heir claimed
  fresh via CAS-create on the deleted ref and returned exactly one final
  verdict note; verdicts_per_task = 1/1/1.
- **attempts_countable_from_repo: true (H2 exact)** — for-each-ref +
  notes + trailers alone reconstructed {t-victim: [att-heir, att-victim],
  1 verdict; t-a/t-b: 1, 1}, matching ground truth exactly. Notably the
  repo needs no reflogs: attempt refs + trailers carry the lineage
  (git does not reflog custom refs by default here — the design doesn't
  need them).
- Watched, not gates: 2-proc claim race → exactly 1 accept / 1 clean
  reject; zero orphaned processes after sweep.

**Consequences (by the frozen rule):** C's fallback credibility upgrades
from "survived single-host probes" to "crash/attempt semantics closed";
B remains winner tonight (frozen ranking criteria are production-
evidence-based and no same-night probe can move them — stated in the
freeze, §1, so this is not post-hoc). The B-vs-C question now lives
only in the batcher re-plumb cost and the two-host wall. A stays dead
on its recorded kill reason.

**Probe repairs (reality vs frozen sketch — none change a threshold):**
the `no_b_machinery` guard self-matched its own source twice (literal
"fcntl" in the check line), and the runtime leg `"fcntl" not in
sys.modules` measured the stdlib (subprocess imports fcntl), not the
probe — dropped; the H2 `returns` parse was polluted by `git log`
chunk boundaries — switched to `rev-list`. The counted run is the final
clean re-run; every flag in it is asserted in `results_c7.json`.

### Verification (01:50, same session)

- **Freeze-before-run is git-provable:** freeze commit 1b7632e
  (01:44:15+02:00) precedes EVERY c7 run — the two discarded
  guard-iteration runs and the counted run (victim_killed_at
  01:46:36+02:00) — which precedes the results commit 766ceb8
  (01:47:09). The discarded runs were audit-guard plumbing failures
  only (no protocol flag involved), disclosed in the run commit.
- **Reproducibility:** the check re-run in a throwaway copy of the
  script PASSed 2/2 with fresh attempt ids (att-victim-11d7b2 vs the
  counted run's att-victim-4f6332); committed evidence verified
  byte-identical after (sha256). The repro is a stability check, not
  counted evidence.
- **Claims re-read against artifacts:** §10's flags match
  `results_c7.json` field-for-field (h1_pass, h2_pass,
  exactly_one_final_verdict, dead_attempt_preserved,
  attempts_countable_from_repo, race 1-accept, no orphans,
  no_b_machinery); `refs_dump_c7.txt` shows the preserved
  `@att-victim-4f6332` ref beside the heir's live claim.

## 12. c9 — C on real workloads, cross-host (2026-09-18, HQ/example-host-c)

**Probe:** c9 — git ref-CAS coordinating four REAL model repairs
(opencode edits + host pytest oracle) from example-host-c (GLM-5.3-Flash,
hpc-glm) and example-host-b (gemini-3.5-flash-lite) against one bare coord repo
on example-host-a (`~/swarmo-c9-coord.git`). Frozen preregistration:
`experiments/c9/FREEZE-C9.md`, committed before any run.

| Hypothesis | Frozen threshold | Measured | Verdict |
|------------|------------------|----------|---------|
| H1 exactly-once | one live claim/return/verdict per task, atts chain claim→return→verdict, zero orphans | 7/7 flags true (`results_c9.json`); crash-revive + requeue exercised for real, dead refs preserved at `@`-markers | **PASS** |
| H2 verdict==oracle | every verdict equals independent host pytest on the LIVE return commit | 4/4 match after disclosed repair (below) | **PASS** |
| H3 host split | measured, not gated | example-host-c:1 / example-host-b:3 (single contest fizzbuzz, example-host-c won the claim) | measured |

**Disclosed repair (one, plumbing-only):** the example-host-b v1 worker ran
pytest with cwd=repo root instead of the task dir → file-not-found →
recorded false negatives on three real fixes (common-mode trap also
present in the first audit draft; both caught by ground-truth re-run
before counting). Repair: rejudge via the frozen H2 oracle on the
live return commits, verdict refs swapped in one guarded server
transaction, poisoned verdicts preserved at
`refs/verdicts/<task>@v1-poisoned`. No threshold moved; the v1
verdicts remain readable for audit.

**Verdict: `c-real-workload-cross-host-verified`.** Combined with §11
(synthetic shape) and the loop rounds (V1–V7 in
`LOOP-2026-09-17.md`), D-HYBRID — B in-host, C cross-host, one
`att-*` audit trail — is the best verified design, now including real
workloads.

## 13. Dogfood — the swarm builds swarmo (2026-09-18)

The in-repo lane (§ refs/swarm/*, LOOP round 4) ran its first real
workload on swarmo's own origin (example-host-a, durable path): 3 backlog
tasks (doc + seeded-bug), workers on example-host-c (opencode/hpc-glm) and example-host-b
(opencode/gemini-3.5-flash-lite), freebuff dispatch attempted 6x and
verdict idle-window-gated (SingletonBusy against an active remote
session — honest env rows, all @-archived).

| Task | Winner | Verdict |
|---|---|---|
| T1-readme-quality | example-host-c att-0482af | true |
| T2-flock-bug | example-host-b att-e3283d | true (seeded 1/2 → 2/2 pytest) |
| T3-docstring | example-host-c att-5fe5bb | true |

H1 exactly-once PASS; content verified by hand after the audit
(DOCS-NODE.md real, README-IMPROVED.md lane-accurate, flock bug fixed).
Five live findings encoded: sh(env=), nodes-pull-not-scp, zombie-TUI
gate, remote-session nondisplaceability (freebuff = idle-window
backend), origin-host audit reads. Record: AUTOWORK-2026-09-18.md.

**Verdict: `swarmo-dogfood-verified`.** The system's first customer was
itself, and the receipt is in the repo it delivered.
