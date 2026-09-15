# DESIGNS.md — swarmo best-design search (example-host-b night, 2026-09-15)

**Status: FROZEN before verification.** Hypotheses, measurements, thresholds and
the decision rule below were committed before any discriminating check was run.
Evidence available at freeze time is cited, never re-derived. Later sections
(experiments/, VERDICTS.md) may only report results against this file.

## 0. Host state at freeze (recorded, per df-first rule)

- `df -h /`: **77% used, 103G free** (survey §D said 88% / 54G at staging —
  improved; still treat disk as scarce: no image pulls, no big installs).
- Host tooling: docker present with **2 local images only** (`python:3.12-slim`
  127MB, `swarmo-worker:c1` 255MB) — zero-pull budget; `opencode` CLI at
  `~/.opencode/bin/opencode`; git 2.43; python 3.10; `flock(1)` present;
  `otelcol` **absent**; `opentelemetry` python pkg **absent**; pip has network.
- Running user is uid 1004 (`you`), in the `docker` group.

## 1. Evidence base (fleet = evidence; cite or refute, never ignore)

Production tonight (SURVEY.md §A–B, live on HQ):
- E1 Crash-safe ledger: JSONL append + atomic snapshot, snapshot-wins recovery.
- E2 Claim = send-intent: `attemptId att-*` distinguishes retry-of-task from
  duplicate-task → idempotent requeue/harvest accounting.
- E3 Singleton via `flock(LOCK_EX|LOCK_NB)` on ledger.lock; loser gets
  LockedError (proven on HQ; portability to this host = H-B1, untested here).
- E4 Parking + 1/min staggered slots → **zero registry bursts, zero 429s over
  4 nights** — pacing is load-bearing regardless of transport.
- E5 Error table classify→decide; quota exhaustion = requeue+exit.
- E6 Revive supervisor (10 min, pidfile-dead + pending → relaunch --resume).
- E7 Ladder `refuted<conjectured<heuristic<measured<verified<proven` +
  Wilson LCB90 gate (accept .70 / agree .75, MIN_N=5) is **built and live**:
  5 PROVEN methods (10/10 → 21/23 counts); MIN_N demonstrably held a 4/4 lane
  unproven (tools/calibration.py; HQ's exact z convention unconfirmed).
- E8 Scoped reviews (evidence pointers, no transcripts; transcripts measured
  p50 1.09x author cost) — ranking signal exists WITHOUT full transcript trees.

Fleet experiments in this repo (run before freeze; inspected, not re-run):
- E9 experiments/c0: `opencode run` **on host** with a google model fixed the
  fizzbuzz order-of-checks bug: git diff 2 lines, host pytest **4/4 pass now**,
  wall 56.8s. A glm-5.3-flash run in the same session died in 2.0s on
  "Insufficient balance" → model availability is a real failure mode.
- E10 experiments/c1 (docker `swarmo-worker:c1`, 5 cases, **0 fixed**): three
  mechanical causes identified from artifacts: (a) batch.sh wrote fixtures to
  `case-<n>/` not `case-<n>/workspace/` → container saw EMPTY /work (glob "0
  matches", pytest "collected 0 items"); (b) `workspace/` was auto-created by
  docker as root:root → uid-1004 container could not write (pytest cache
  Errno 13); (c) a `/secrets` mount triggered opencode's
  `external_directory` permission auto-reject → run aborted ("read failed").
  Container mechanics themselves worked: docker ran, opencode ran, model
  (gemini-3.5-flash-lite) responded and executed pytest/pkgutil.
- E11 SURVEY §C: opencode-headless-in-docker was flagged as THE open
  experiment that "falsifies-or-grounds the whole worker leg" — c1 attempted
  it, but failed for causes (a)–(c), so the leg is NOT yet grounded or refuted.

## 2. Rival designs

### Design A — D0-AS-WRITTEN: Federated HTTP Coordinator (5-pillar spec)

Express/Postgres coordinator (metadata/consensus only), task-brief-as-sole-API
over `GET /start`, X-Session registry, sandboxed client containers posting
content-addressed patches to `POST /returns`; LangGraph cyclic orchestration
with CodeCoR intermediate pruning gates; OTel/OpenInference spans → self-hosted
Langfuse; directed correction graph + RAEE/PPR leaderboard; public reasoning
ledger, overlay staging, chained credit economy.

**Causal argument.** A central metadata server *causes* vendor-neutral joining
(any client that can read Markdown can join) and hard server/client isolation;
universal OTel lineage *causes* every outcome to be attributable to a
(model, prompt, harness) vertex, which *causes* the correction graph and
RAEE/PPR to have data to rank, which *causes* self-improving worker selection;
the credit ledger *causes* sustained donor participation. The bet: pay the
large up-front build to get the full flywheel.

**Where fleet evidence cuts.** E1–E6 re-invent, as files+flock, exactly what
the D0 coordinator's queue/registry does — production got crash-safe dispatch
WITHOUT the server. E4 shows burst-free pacing was hard-won and transport-
independent; D0's HTTP queue inherits that problem, not a solution to it.
E7 shows the spec's ladder (Pillar 4's calibration core) already exists in
production; the spec's genuinely-new parts are OTel (P3), correction graph +
RAEE/PPR (P4's top half), and the economy (P5).

### Design B — LEDGER-CLAIM MINIMAL: "the batcher wins, repair one leg"

Keep everything in production (E1–E8) as the coordination AND lineage AND
ranking substrate. Replace nothing. Add only the missing leg: a repaired
opencode-in-docker worker driven by claim → dispatch → harvest → host-verify →
ledger-row. Ledger attempt records + scoped review pointers ARE the lineage
(pillar-3 function); the Wilson ladder IS the ranking (pillar-4 function);
ledger attempts ARE the usage accounting (pillar-5 function, no credits).
RAEE/PPR, Langfuse, credit economy: explicitly deferred until the ladder's
verdict stream shows a need they would fill.

**Causal argument.** Claim=send-intent *causes* idempotent requeue after any
crash (snapshot-wins, E1/E2); flock singleton *causes* split-brain immunity
(E3); parking+slots *causes* zero-burst dispatch (E4); scoped reviews + Wilson
gate *causes* trustworthy method ranking at bounded review cost (E7/E8).
Therefore coordination, lineage, ranking and accounting functions are already
caused by running code, and the marginal build is only the worker leg — whose
failure modes are fully diagnosed mechanical bugs (E10), not unknowns.

**Where fleet evidence cuts.** E9 proves an on-host fallback worker works
today; E10 proves the container leg fails today — Design B bets that E10's
three causes are sufficient explanations (fixable without design change).

### Design C — GIT-NATIVE SWARM: "the repo is the coordinator, ledger, and lineage"

Genuinely different mechanism: no HTTP server AND no application ledger files.
A bare git repo (already the harvest path, SURVEY §D) is the substrate:
- **Claim** = compare-and-swap on a ref: `git update-ref --stdin`
  `verify refs/claims/<task> <old>` then `update ... <new>` — git's ref
  locking makes stale-value claims fail; exactly-once without any lock file
  protocol of our own.
- **Return** = a commit on `refs/tasks/<task>` by the containerized worker;
  the patch is the commit diff — content-addressed by construction.
- **Lineage** = commit parents + trailers (`X-Session`, `Attempt: att-*`,
  `Corrects: <sha>`); `git log` IS the trace tree (pillar-3 function).
- **Verdicts** = `refs/notes/verdicts` annotations on return commits carrying
  ladder rungs (pillar-4 input); Wilson aggregation reads notes.
- **Overlay/harvest** = the existing pull-to-HQ flow, made the live path.

**Causal argument.** Content-addressing *causes* tamper-evidence and free
idempotence (same patch = same sha); ref-CAS *causes* exactly-once claims with
zero custom concurrency code; offline-first append-only history *causes* HQ
disconnects to be non-fatal (no coordinator to be down); and it builds only on
competence the fleet already exercises (batcher git-inits every workspace).

**Where fleet evidence cuts.** E2's retry/duplicate semantics must be
re-expressed in ref names — possible (refs/claims/<task>@att-N) but unproven;
nothing in production runs git as a coordination substrate, so C has ZERO
production-night evidence behind it — it must win on tonight's probes or lose.

## 3. Load-bearing hypotheses (frozen)

A hypothesis is load-bearing if false ⇒ the design's causal chain breaks at
that link. Each names its falsification condition, measurement, threshold.

### Design A
| ID | Claim | Falsified if | Measurement | Threshold |
|----|-------|--------------|-------------|-----------|
| H-A1 | A sandboxed container can execute a bugfix and return a verified patch (worker leg works at all under isolation) | corrected c1-style run yields empty patch or failing host tests | 1 case (fizzbuzz, proven model class), files present+writable mount+auth staged in HOME, existing image, wall ≤ 240s; harvest `git diff`, run host pytest | ≥1 case: non-empty diff ∧ host tests all pass ∧ container exit 0 |
| H-A2 | OTel SDK spans can reach a local collector on this host (pillar-3 is groundable, not aspirational) | SDK unavailable in venv, or export produces no valid span at receiver | venv + `opentelemetry-sdk` + OTLP/HTTP exporter → minimal localhost receiver; inspect received payloads for trace_id/span_id/parent | ≥1 complete span received with valid trace_id and one child-parent pair |
| H-A3 | A single-host HTTP coordinator absorbs fleet-scale concurrent brief pulls without error (pillar-1 capacity) | non-200s or pathological latency under fleet-scale burst | 60 concurrent processes × 5 GETs of a ~2KB brief from `python3 http.server` on localhost; count statuses, p50/p95 | 0 non-200 ∧ p95 < 500 ms |
| H-A4 | (cost, feeds ranking, not a gate) D0's unbuilt half dominates build cost | — | inventory: pillars with zero production code per SURVEY | recorded as count n/5 unbuilt |

### Design B
| ID | Claim | Falsified if | Measurement | Threshold |
|----|-------|--------------|-------------|-----------|
| H-B1 | flock exclusive-claim semantics hold across processes on THIS host (E3 portable) | a second `LOCK_EX\|LOCK_NB` holder ever succeeds | (a) 2-proc holder/contender probe ×10 rounds; (b) 32-proc race on one lock ×10 rounds | 10/10 rounds: exactly 1 winner, 31 contenders get BlockingIOError, lock released cleanly |
| H-B2 | The minimal loop closes with NO server component on this host: flock-claim → docker worker → harvest → host-verify → ledger row (attemptId, ts, verdict) | any stage requires a long-running server process, or the row is not appended / not idempotent-shaped | same run as H-A1 wrapped in claim+ledger (≈50 lines); inspect ledger row JSON | row appended with `attemptId att-*` ∧ claim was exactly-once ∧ total loop ≤ 300 s |
| H-B3 | Ledger+scoped-review lineage suffices for trustworthy ranking (pillar-3/4 functions met without OTel/RAEE/PPR) | tools/calibration.py gate self-test fails, or live numbers contradict SURVEY | run `pytest tools/test_calibration.py`; cite E7 counts (5 PROVEN, MIN_N binding on 4/4) | self-test passes ∧ cited numbers stand |
| H-B4 | Worker leg has a working fallback without docker (resilience, not gate) | — | E9 by citation (c0: host opencode, 4/4 pass, 56.8s) | already met by E9 |

### Design C
| ID | Claim | Falsified if | Measurement | Threshold |
|----|-------|--------------|-------------|-----------|
| H-C1 | git ref compare-and-swap gives exactly-once claims across concurrent processes | two processes both win a claim, or a loser's write lands | 32 procs race `update-ref --stdin` (verify+update with expected old value) on one ref ×10 trials; count accepts; read ref after | 10/10 trials: exactly 1 accept, 31 rejects, final ref value = winner's |
| H-C2 | A containerized worker can return its result as a git commit visible on host (content-addressed returns) | commit absent on host, or container user cannot write the repo | same run as H-A1 plus in-container `git add -A && git commit` with `Attempt:` trailer; host reads `git log` | 1 commit with trailer visible from host ∧ diff matches harvested patch |
| H-C3 | Lineage queries stay fast at fleet-scale history (`git log` = trace tree) | task-ancestry query is too slow for nightly jobs | synthesize ~50k-commit repo via `git fast-import` (fixed tree, trailer per commit); time `git log --grep` for one task's ancestry | query wall < 5 s |
| H-C4 | Claims-table and verdict read paths stay fast at fleet scale | `for-each-ref` over claims or notes aggregation too slow | create 10k `refs/claims/t-*` (batched update-ref) + 200 `refs/notes/verdicts`; time `for-each-ref` full list and note reads | both < 10 s combined |

## 4. Cheapest discriminating checks (pre-registered)

1. **C-flock** → H-B1. Pure python + `fcntl.flock`, /tmp files. ~seconds.
2. **C-otel** → H-A2. venv (~10–20MB, network available), localhost:4318
   receiver. Fallback if SDK install fails: hand-built OTLP/JSON POST validates
   receiver+wire only; SDK leg recorded UNTESTED. ~2 min.
3. **C-worker** → H-A1 + H-B2 + H-C2 in ONE run (expensive probe, shared):
   repair E10's three bugs — fixtures inside `workspace/` before mount,
   `chmod` workspace writable by uid 1004 (docker root-mountpoint trap), auth
   staged by ro-mount into container HOME (no `/secrets` path to probe).
   Existing `swarmo-worker:c1` image, NO pull. Model: google class (proven in
   E9; glm class died on billing, E9). ≤ 240s timeout.
4. **C-http** → H-A3. localhost http.server, 60×5 burst. ~seconds.
5. **C-git** → H-C1, H-C3, H-C4. update-ref race + fast-import 50k + 10k refs.
   Repo size ~10MB on 103G free — inside budget. ~2 min.
6. **C-calib** → H-B3. `pytest tools/test_calibration.py`. ~seconds.

Disk guard: re-run `df -h` before C-worker and C-git; write only under
`~/swarmo/experiments/`; abort any step that would add > 500MB (none should).

## 5. Decision rule (frozen)

- Design **A survives** iff H-A1 ∧ H-A2 ∧ H-A3 all pass. (H-A4 only ranks.)
- Design **B survives** iff H-B1 ∧ H-B2 ∧ H-B3 all pass. (H-B4 pre-met by E9.)
- Design **C survives** iff H-C1 ∧ H-C2 ∧ H-C3 ∧ H-C4 all pass.
- Rank survivors by: (1) fewest unbuilt components to first end-to-end fleet
  value; (2) fraction of causal chain backed by production-night evidence
  (E1–E9) vs tonight-only probes. Winner = best rank; runner-up recorded.
- If nothing survives: record which load-bearing legs failed and the minimal
  repair each design needs; state which failure class dominates.

Pre-registered expectation (to be honest about priors): B is heavily favored
by E1–E9; A's H-A4 cost count will be high but its probes are cheap and worth
having as grounding; C is the long shot and dies instantly if H-C1 fails.
