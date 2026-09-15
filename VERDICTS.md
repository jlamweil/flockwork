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
