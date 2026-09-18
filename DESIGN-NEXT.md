# DESIGN-NEXT — swarmo continuation (HQ/example-host-c, 2026-09-17)

**ROUND-1 UPDATE (loop, same day): the §2 lane executed and the design
question is SETTLED.** See LOOP-2026-09-17.md for the five verifications
(V1–V5). Headlines: c8 run 5 counted → **c-proven-cross-host** (17/17
flags, H3 0.33×; VERDICTS §11, example-host-b commit 6c83060); coord repo rescued
to example-host-a `~/swarmo-c8-coord.git`; worker leg verified END-TO-END on
example-host-c with two NEW recipe constraints (own-git-root workspace; PWD env
pinned — opencode anchors by $PWD). §2.1 and §2.2 are DONE; §3's branch
question resolved as **D-HYBRID** (B in-host, C cross-host, one att-*
trail — V3-verified joinable). §2.3 resolution: the dead example-host-c lane's
goal (settle c8) was achieved by the loop itself; the stuck `running`
row is left for the fleet's own `--resume` reconciliation (owner's
evening relaunch) — HQ does not mutate live fleet ledgers.

Written after harvesting the complete example-host-b record (experiments c0–c8,
VERDICTS, NIGHT-SUMMARY, DESIGNS) into this repo. Everything below cites
existing artifacts; nothing is re-derived. This file proposes only what
the frozen design-search record has not yet settled.

## 0. Where the truth lives (as of this harvest)

| Artifact | Location | Status |
|---|---|---|
| Full experiment history c0–c8 | this repo (cloned from example-host-b `main@cccf5cb` via `example-host-b-history.bundle`) | complete, git-provable |
| Uncommitted example-host-b round state (results_c8.json, refs_dump_c8.txt, c8_driver 1-line fix) | committed here as the harvest commit; still uncommitted on example-host-b | preserved |
| Working-tree snapshot (1860 files incl. pytest/ruff caches) | `~/swarmo-pre-git/example-host-b-snapshot-2026-09-17/` | superseded by the clone |
| c8 coordination repo | `/tmp/c8/coord.git` on **example-host-a** | **ephemeral — lives in /tmp** |
| Transport | example-host-c → example-host-a (tailscale, `you@`) → example-host-b (`you@`); example-host-b NOT directly reachable from example-host-c | verified 2026-09-17 |
| example-host-c-side batcher lane for example-host-b (`example-host-b-swaro/gen-0740-1`) | `~/the-queue-driver/runs/2026-09-16/example-host-b-swaro/` | batcher dead; 1 task stuck `running` |
| Pre-december survey pack (SURVEY.md, architecture.md) | already in history (example-host-b carries them under `survey/`) | nothing lost |

## 1. The record's settled points (do not relitigate)

- **Worker leg PROVEN**: opencode headless on example-host-b host (c0/c2/c3) and the
  repaired docker recipe (c2 5/5, c3 5/5 under contention, c6 batch 8/8 →
  20/21, LCB90 .8122 ≥ .75 gate). The four E10 container fixes are the
  load-bearing recipe (HQ-INTEGRATION.md).
- **Design shortlist B > C, A dead** (VERDICTS §10, frozen rule):
  B = production batcher (flock/ledger/claim) stays the substrate;
  C = git ref-CAS claim coordination is the proven crash-safe fallback
  (c7 H1∧H2 single-host, 0.6 s, docker-free).
- **Ladder + Wilson gate live** (tools/calibration.py): ranking signal
  exists without transcripts (scoped reviews, E8).
- **c8 (two-host, the last B-vs-C discriminator) ran 4 times, all 4
  discarded on audit plumbing** — each disclosed, thresholds untouched.
  Substantive phases kept passing every time: contention 9/9 wins
  (W = 0.48–0.69 s), cross-host crash-revive flags all true (victim
  killed, sweep requeued exactly `t-crash`, heir completed, exactly one
  final verdict, orphans zero). The final ssh-quoting repair
  (`cccf5cb`, shlex.quote-join into remote shell) is committed but its
  run never happened: run4's own results file ends
  `verdict: DISCARDED-NOT-COUNTED` on an audit rev-list path error
  (`refs/tasks/t:` missing `--` separator) **after** all substantive
  phases had already completed.

## 2. Immediate lane (highest value, lowest risk)

1. **Run c8 one more time with the committed repair.** The recorded
   failure is one line of audit code (`rev-list refs/tasks/t` needs
   `--`); the fix is already sitting uncommitted-adjacent on example-host-b and
   mirrored here. Suggested change: complete the `--`-separator fix in
   `c8_driver.py`'s audit path, commit it as a disclosed probe repair
   (no threshold touched), then run. Expected outcome per the evidence:
   H1/H2 PASS cross-host, H3 measured (<10× vs 0.6 s — actual observed
   W ≈ 0.48–0.69 s suggests it may even be ~1×). Write VERDICTS §11:
   either **C proven cross-host** (C becomes the credible successor for
   multi-host; B keeps single-host production) or **C killed with
   reason** (multi-host is B's documented ceiling). Either way the
   design-search question is settled — that was the morning goal.
2. **Move the coordination repo out of /tmp** (example-host-a reboot risk):
   `git clone --bare /tmp/c8/coord.git ~/swarmo-c8-coord.git` on example-host-a,
   or re-seed from this repo's history if it vanished. Record the path
   in the results file.
3. **Decide the stuck example-host-c lane** (`example-host-b-swaro/gen-0740-1`, task
   `t-2df4c6a29ea5` running): either revive it (`--resume`, baseline
   flags from runs/2026-09-17/TODO.md) if the morning goal is still
   wanted, or reconcile it as dead with a ledger note. Do not leave a
   `running` claim that no supervisor owns — that is exactly the
   SILENT-STALE shape the batcher's sweep exists for.
4. **example-host-b tree hygiene**: example-host-b `~/swarmo` still has the uncommitted
   results + driver fix. Since this repo now holds them, the example-host-b-side
   cleanup is: verify hashes match the harvest, commit or reset there
   per the owner's preference (no pushes from HQ; owner acts on example-host-b).

## 3. After c8: the two honest branches

**Branch C-wins (C proven cross-host).** The multi-host ceiling that
kept B on top disappears. The design consequence is *not* "replace the
batcher tomorrow" — it is: C becomes the coordination substrate for any
deployment that spans hosts, B remains the single-host production path,
and the HQ-INTEGRATION wire-up (tools/worker_loop.py behind the spawn
step) proceeds unchanged because it is coordination-agnostic.

**Branch B-wins (C killed).** Multi-host is recorded as B's ceiling.
The batcher stays the one substrate everywhere; the swarm stays
per-host. Same integration work, smaller ambition, cheaper system.

**Both branches converge on the same next build item** — wire the
proven worker leg into the live batcher (HQ-INTEGRATION.md map):
claim → docker dispatch → harvest (git show) → host pytest →
ledger rows with `att-*`. That is the only marginal build Design B
ever required, and it is already proven at the component level.

## 4. Deliberately deferred (per DESIGNS §2 / VERDICTS — with the trigger)

| Pillar (D0 spec) | Status | Trigger to build |
|---|---|---|
| HTTP coordinator, X-Session registry, GET /start | deferred | a second *account* or an external donor joins; until then files+flock+git beat a server on every measured axis |
| OTel/OpenInference → Langfuse | deferred (OTLP backfill exists, c6 16/16) | ladder verdicts need cross-session trace joins they cannot express as ledger rows |
| Correction graph + RAEE/PPR leaderboard | deferred (Wilson ladder live) | ≥2 models compete on the same task stream and scoped reviews can no longer separate them (MIN_N evidence exhausted) |
| Overlay staging + credit economy | deferred | first non-owner contributor appears; before that, ledger rows ARE the accounting |

## 5. Reuse-vs-invent map (evidence-graded)

- **Reuse as-is**: batcher core E1–E6 (production, 4 nights zero-429),
  calibration ladder E7, scoped review E8, worker recipe (HQ-INTEGRATION
  checklist), git-CAS sweep/heir machinery (c7/c8 scripts) for any
  multi-host need.
- **Repair, don't redesign**: c8 audit path (`--` separator class of
  ssh-argv-into-shell bugs — the pattern killed runs 3/4; audit all
  remote git invocations for it).
- **Invent only on trigger**: §4 table. Nothing in the D0 five-pillar
  spec has an untriggered component worth building first.

## 6. Constraints carried forward

- df-first on every host before anything that allocates (example-host-b 78–99%
  historical range; example-host-c 95% — tightest right now).
- Freeze-before-run, disclose-discard, thresholds-untouched: the
  discipline is the project's core asset; every rule in VERDICTS
  exists because a run earned it.
- example-host-b is reached only via example-host-a; user is `you` there (not `jlam`);
  BatchMode only; no orphaned processes/repos at sitting end
  (c7/c8 no-orphans flag, cross-host).
- No pushes to any remote; this repo's remote is the local bundle.
  Owner acts on the fleet; HQ harvests.
