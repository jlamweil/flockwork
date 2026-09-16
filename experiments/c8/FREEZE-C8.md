# FREEZE-C8 — the standing two-host discriminator, executed (pre-registered 2026-09-16 morning)

**Frozen before any counted run.** The commit containing this file precedes
any c8 run artifact (git-provable, same discipline as FREEZE-C6/C7). This
file decides, in order: what c8 settles, the hypotheses with falsification
conditions, the mechanical decision rule, the protocol spec, and cleanup.
Nothing below is edited after the run; results land in VERDICTS.md §11
against these thresholds.

## 0. Survey finding + pre-freeze mechanics probe (uncounted, disclosed)

The open link is exactly the one VERDICTS §10 / NIGHT-SUMMARY addendum
scoped out: "multi-host coordination … only testable on a real two-host
setup; not exercised here by construction" (FREEZE-C7 §3 recorded it as
"the standing discriminator for the next two-host opportunity"). That
opportunity now exists: example-host-b→example-host-a BatchMode ssh keys verified 2026-09-16
morning; reverse direction (example-host-a→example-host-b, `ssh you@example-host-b`) verified live in
this session with existing keys only. No new keys were created or
authorized.

**Pre-freeze mechanics probe** (uncounted; op-validity and latency
scouting only, no hypothesis flag evaluated; scratch `/tmp/c8-mech` on
both hosts deleted after): bare repo on example-host-a + base push over
receive-pack/ssh OK; push-CAS via `--force-with-lease=<ref>:` (empty
expect = must-not-exist) create OK, CAS-loss rejected rc=1 "stale info";
delete bound to `<ref>:<old-value>` lease OK, replay rejected; c7-shape
`update-ref --stdin` txn over ssh-exec CAS-loss = rc=128 "cannot lock ref";
notes `--ref=verdicts` 6/6 sequential and 6/6 concurrent burst adds OK;
ssh ControlMaster example-host-b→example-host-a: first op ~305 ms, multiplexed ops ~16 ms
(fresh connection ~340 ms); example-host-a-local git op ~2.6 ms; 4-way concurrent
claim race over the real transport: exactly 1 winner, 3 clean losses,
server-side ref = winner's claim commit. Clock offset example-host-b vs example-host-a
measured ~0.3 s. The counted run uses ONE full driver execution; any
post-freeze discarded run is disclosed in results_c8.json and the run
commit; the counted run is the final clean one (c6/c7 discipline).

## 1. What c8 settles (and what it cannot touch)

Design C's claim-CAS coordination is proven single-host only (c2 H-C1,
c7 H1∧H2). The B-vs-C debate's one open link: does C's mechanism survive
REAL two-host conditions — independent kernels, no shared filesystem, ssh
transport, genuine network contention? B's side is structurally excluded,
not merely untested: flock/rename(2) claims bind to one kernel's VFS; on
two hosts B needs a shared-FS (NFS) or a coordinator server — extra
machinery B does not have. That ceiling is B's standing recorded claim
(§10c: "the B-vs-C question now lives only in … the two-host wall"); it is
carried by citation, not re-tested. c8 is C's leg. A stays dead on its
recorded kill reason regardless of outcome.

**Consequence frozen now** (so nothing is dressed up post-hoc): the
frozen round ranking (B winner, C runner-up, DESIGNS §5 criteria) is
production-evidence-based and CANNOT be reordered by this probe. What c8
settles is the design-search question the round record flags open: C
PROVEN cross-host ⇒ C becomes the credible successor for B's known
multi-host ceiling; C killed ⇒ the kill reason is recorded and C's
fallback credibility drops to single-host. Either result settles it.

## 2. Load-bearing hypotheses (frozen)

Topology: example-host-b + example-host-a (2 hosts, independent kernels); bare coordination
repo on example-host-a; 6 workers (3 per host); 9 clean tasks + 1 crash task.

| ID | Claim | Falsified if | Measurement | Threshold |
|----|-------|--------------|-------------|-----------|
| **H1** | Exactly-once cross-host: ref-CAS claims over ssh transport give every clean task exactly 1 accepted claim generation + exactly 1 final verdict, with the full {task → attempts → verdicts} map reconstructible from refs + trailers + notes alone | (a) any double-accept: a clean task with ≥2 claim generations (an `@att` preservation ref in the clean phase, or return-commit `Attempt:` ≠ live claim ref's att); (b) any double-verdict: ≥2 verdict notes for any task; (c) reconstruction mismatch vs driver-recorded ground truth; (d) sum of worker-reported wins ≠ 9 or not 1:1 with server refs | contention phase: 6 workers (3 example-host-b + 3 example-host-a) free-for-all over t1..t9, claims = push-CAS, returns = push-CAS create-once, verdicts = notes; end-of-run audit from example-host-b via ssh-exec git reads ONLY | ALL of: 9 claims / 9 returns / 9 verdicts; zero `@att` refs; return-att == claim-att per task; wins-sum == 9; reconstruction exact |
| **H2** | Crash-revive reproduces ACROSS hosts with c4/c7's exact flag set: example-host-b victim SIGKILLed mid-dispatch; sweep runs ON example-host-a; orphan on example-host-b is found and killed FROM example-host-a (reconcile-by-probe over ssh); heir completes ON example-host-a | (a) sweep cannot find/kill the cross-host orphan; (b) requeue leaves a state a second sweep cannot resolve (non-idempotent); (c) dead attempt lost from refs; (d) heir blocked or ≥2 final verdicts; (e) reconstruction mismatch | crash phase on t-crash: victim (example-host-b) claim → tagged dispatch child → SIGKILL; snapshot read cross-host; sweep ×2 on example-host-a (orphan kill via `ssh you@example-host-b` pgrep/kill); heir (example-host-a) fresh CAS claim → return → note | ALL flags true: victim_claim_no_verdict, sweep_ran_on_example-host-a, sweep_requeued == [t-crash.<victim-att>], orphan_killed_from_example-host-a, sweep_idempotent_second_run, heir_fresh_claim, heir_fixed, exactly_one_final_verdict (all 10 tasks), dead_attempt_preserved, attempts_countable_from_repo, orphans_left == 0 on BOTH hosts |
| **H3** | Per-claim transport overhead vs c7's 0.6 s single-host baseline is **measured and reported** (<10x or ≥10x) — a measurement, not a gate: C can win correctness and still lose on operational cost; both outcomes recorded | none — H3 cannot kill (it is a cost annotation) | primary: ratio = (contention-phase wall / 9 task-generations) ÷ (c7 wall 0.6 s / 4 generations = 0.15 s/gen); secondary: in-run per-op latency medians, example-host-b-lane (over ssh) vs example-host-a-lane (local) | label `<10x` iff ratio < 10.0; the label and the raw numbers are recorded either way |

## 3. Decision rule (mechanical, no post-hoc edits)

- **H1 PASS ∧ H2 PASS** → outcome `c-proven-cross-host`: C's claim-CAS
  coordination is proven on a real two-host setup — exactly-once under
  cross-host contention and crash-revive across kernels. **C becomes the
  credible successor for B's known multi-host ceiling**; the B-vs-C open
  link is closed. B remains the round winner (frozen criteria, §1).
- **H1 FAIL** → outcome `c-killed-cross-host`: kill reason = which
  falsifier (a)–(d) fired, recorded verbatim in VERDICTS §11; C's
  fallback designation downgrades to single-host-contention only. B's win
  is reinforced.
- **H2-only FAIL** → outcome `crash-revive-dented-cross-host`:
  exactly-once stands; the crash flag(s) that fired are recorded with a
  repair proposal; fallback designation notes the dent.
- Either way: H3's numbers land in §11 as recorded; A stays dead.

## 4. Protocol spec (frozen; mechanics per §0 probe)

- Scratch everything lives in `/tmp/c8/` on both hosts (deleted at
  sitting end — zero leaked processes/repos, both hosts verified).
  Coordination repo: `example-host-a:/tmp/c8/coord.git` (bare; seeded with one
  base commit pushed from example-host-b over receive-pack).
- Worker script `c8_worker.py` (committed here; scp'd to example-host-a by the
  driver) — modes: `worker` (contention loop), `victim`, `heir`, `sweep`.
- **Claim** = worker mints a claim commit (subject `claim att-<id>`;
  att id = `att-<host><n>-<hex>`) in its scratch repo, then
  `git push --force-with-lease=refs/claims/<task>: <sha>:refs/claims/<task>`
  — empty expect ⇒ must-not-exist ⇒ server-side CAS-create. rc 0 = won;
  rc ≠ 0 = lost (losses leave no trace — E2: attempt = won claim).
  att readback = claim-commit subject (server-side `log -1 --format=%s`).
- **Scan** = one `git ls-remote <url> 'refs/claims/*' 'refs/tasks/*'`;
  free task = no claim ref ∧ no return ref. In-flight (claim, no return)
  tasks are skipped — workers never sweep; revive is driver-driven (c7).
- **Return** = plumbing-built commit on `main`'s tree + `fix-<task>.txt`,
  `Attempt:` trailer, pushed
  `--force-with-lease=refs/tasks/<task>: <sha>:refs/tasks/<task>`
  (create-once CAS; a rejected return is reported, never retried into a
  double-return). **Verdict** = `ssh example-host-a git -C coord.git notes
  --ref=verdicts add -F - <return-sha>`, message `task:/attempt:/fixed:`
  (c7 note shape), bounded retry 3× (insurance; probe showed 0 conflicts).
- **Transport**: example-host-b workers use `ssh://example-host-a/tmp/c8/coord.git` with
  ControlMaster (ControlPath under /tmp/c8, Persist 600); example-host-a workers
  use the local path (same ops, zero transport). The asymmetry is frozen
  and reported (H3 reports both lanes separately).
- **Victim** (example-host-b): claims t-crash, spawns tagged dispatch stand-in child
  (cmdline embeds `c8-t-crash-<att>`, own session — the docker-free
  container stand-in), holds; driver SIGKILLs victim after tag +
  claim-ref visibility, BEFORE any return (c7 flow, cross-host reads for
  the snapshot).
- **Sweep** (runs ON example-host-a, driver-invoked over ssh; c7's crash-safe
  order): for each live claim whose task has no final verdict note:
  (1) reconcile-by-probe — find the tagged orphan ON example-host-b via
  `ssh you@example-host-b pgrep -f <tag>`, SIGKILL it, verify gone (THE cross-host
  leg); (2) preserve the dead attempt at `refs/claims/<task>@<att>`
  (local `update-ref`, create-if-absent — idempotent); (3) CAS-delete the
  live claim (`update-ref --stdin` txn `delete <ref> <old>`, local —
  c7-proven shape). Run twice; second run must requeue nothing.
- **Heir** (example-host-a): fresh CAS-create claim on the deleted ref, return
  commit + verdict note (c7 flow, opposite host from victim).
- **Audit** (driver, example-host-b; git reads ONLY, all via ssh-exec): H2's
  reconstruction from `for-each-ref refs/claims` + `refs/tasks` logs
  (`Attempt:` trailers) + `notes --ref=verdicts`, diffed against
  driver-recorded ground truth. Same for H1's clean-phase checks.
- Guards (c7 carryover): no-B-machinery (source contains no
  `import fcntl` — self-match-evading needle — and no JSONL ledger is
  written); disk checked pre-run on both hosts; orphan scans on both
  hosts post-run must be empty; global deadline 180 s (exceeded ⇒ run
  killed, disclosed as discarded, protocol flags never evaluated).
- Watched, not gates: per-worker CAS-loss counts (contention pressure);
  notes retry counts; ssh ControlMaster setup cost.

## 5. Why this is the cheapest separating check

It IS the standing discriminator (FREEZE-C7 §3 row 2): the one check with
separating power that the single-kernel host could not execute. Nothing
cheaper separates now: single-host re-runs have no power left (c7 closed
crash semantics single-host; contention + lineage are exact on both
designs); simulated "hosts" (containers on one kernel) were explicitly
ruled out in the c7 freeze — flock would hold across them, killing the
separating power. The two-host opportunity arrived; this freeze executes
the pre-recorded check against it, extended with the crash-revive
cross-host replay (the flag set c4/c7 proved) and the H3 cost measurement.

## 6. Pre-registered prediction (informational, written pre-run)

H1 ∧ H2 PASS: git's CAS is server-side atomic over any transport (the
4-way probe race already showed 1 winner over the real wire); the
protocol is c7's proven shape with transport swapped. Residual risk is
plumbing, not semantics: ssh multiplex contention, notes lock, pgrep
across ssh, scan/claim race windows. H3: with ControlMaster (~16 ms/op
amortized, ~5-7 coordination ops/task) the contention phase lands ~1-4x
the 0.15 s/gen single-host baseline — label predicted `<10x`, but the
whole point of H3 is that this is measured, not assumed.

## 7. Cleanup + rails (hard requirements)

- No fleet interference: only `/tmp/c8*` scratch on either host; no keys
  created or modified; no services started; 6 lightweight workers,
  model-free, docker-free (c7-style stand-ins).
- Sitting end: zero `c8` processes and zero `/tmp/c8*` paths on example-host-b AND
  example-host-a (verified by scans in results_c8.json `cleanup` block, run AFTER
  evidence is committed-safe in this repo).
- Commits as you go; working tree clean at end.
