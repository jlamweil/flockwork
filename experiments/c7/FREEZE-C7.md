# FREEZE-C7 — design-search round settlement (pre-registered 2026-09-16 ~01:40)

**Frozen before any run.** The commit containing this file precedes any
c7 artifact (git-provable, same discipline as DESIGNS.md → VERDICTS.md
and c6's FREEZE.md). This file decides, in order: the survey finding,
the ranked shortlist, the head-to-head, the cheapest separating check,
and the mechanical decision rule. Nothing below is edited after the run;
results land in VERDICTS.md against these thresholds.

## 0. Survey finding (which clause of the night goal applies)

The night goal's fallback clause ("if the search's verdicts are not yet
on disk … execute the survey's own cheapest pending discriminating check")
does **not** trigger: at survey time (01:30–01:40) the design search's
verdicts ARE on disk and complete — VERDICTS.md §§1–9 (all 10 frozen
probe rows adjudicated; A eliminated, B winner, C runner-up; B's leg
closed through lane PROVEN at 20/21, LCB90 .8122). No newer search
process or output exists anywhere on disk (checked: no live swarmo
processes, nothing under ~ outside this repo modified tonight).
**Primary path applies:** adjudicate → preregister head-to-head →
execute the cheapest separating check.

## 1. Ranked shortlist (adjudication of the on-disk record)

| Rank | Design | Discriminating-check outcome |
|------|--------|------------------------------|
| 1 | **B ledger-claim minimal** | H-B1 ✓ 10/10 flock races; H-B2 ✓ loop closes, exactly-once claim; H-B3 ✓ gate self-test + E7 cited; H-B4 pre-met (E9). Since verdict: c3 exactly-once parallel 5/5; c4 SIGKILL mid-dispatch → sweep → heir, exactly one final verdict; c5 lane VERIFIED (12/13, .7177); c6 fresh-task batch 8/8 → **PROVEN** (20/21, LCB90 .8122 ≥ .75). |
| 2 | **C git-native** | H-C1 ✓ ref-CAS 10/10 exact (1 accept/31 rejects); H-C2 ✓ container returns commit visible on host; H-C3 ✓ 50k-commit lineage grep 0.24 s; H-C4 ✓ 10k refs + 200 notes reads 0.087 s. Runner-up on the frozen ranking criteria (zero production-night evidence; batcher re-plumb required). **One causal link explicitly unproven** (DESIGNS.md §2, "Where fleet evidence cuts"): E2's retry-of-task vs duplicate-task semantics "re-expressed in ref names — possible (refs/claims/<task>@att-N) but unproven." |
| dead | **A D0-as-written** | **Kill reason: H-A3 FAIL** — frozen threshold `0 non-200 ∧ p95 < 500 ms` under a 60-proc burst measured **p95 = 1056 ms** (p50 8 ms; 0 non-200 does not rescue the conjunction). Post-hoc labeled repair check (backlog 128 → p95 178 ms) is recorded but cannot unkill A: the frozen rule is applied unchanged. A enters the record with this kill reason and stays dead; it is never silently deleted. |

Ranking rule applied unchanged from DESIGNS.md §5 (fewest unbuilt
components to first fleet value; fraction of causal chain backed by
production-night evidence). Both are production-evidence criteria no
same-night probe can move, so **this round's check cannot reorder ranks
1–2**; what it settles is the open load-bearing difference between them
(below). This is stated now so the result is not dressed up post-hoc as
a rank change.

## 2. Head-to-head preregistration: B vs C

The only dimension where the two records genuinely differ: **crash and
attempt semantics of the claim layer.** B's side is proven live (c4:
SIGKILL mid-dispatch → claim-without-verdict → sweep reconciles by probe
→ heir completes → exactly one final verdict, dead attempt retained).
C's side is exactly the link DESIGNS.md flags unproven. B's half of this
head-to-head is therefore already banked and is carried by citation to
`experiments/c4/results_c4.json` (same pattern as H-B4's "pre-met by E9");
the round's new work is C's half.

### Load-bearing hypotheses (frozen)

A hypothesis is load-bearing if false ⇒ the design's causal chain breaks
at that link.

| ID | Claim | Falsified if | Measurement | Threshold |
|----|-------|--------------|-------------|-----------|
| **H1** | A git-native claim/attempt/verdict protocol — ref-CAS claims, attempt refs (`refs/claims/<task>@att-*`), return commits with `Attempt:` trailers, `refs/notes/verdicts` — reproduces c4's proven crash outcome using ONLY git primitives (no flock, no JSONL ledger): after a mid-dispatch SIGKILL + sweep + heir, the repo alone shows exactly one final verdict per task with the dead attempt preserved and countable (E2 semantics) | (a) zero or ≥2 final verdict notes for the victim task after heir completion; (b) the heir cannot mint a fresh attempt without lock-file or ledger machinery; (c) the dead attempt is lost — not recoverable from refs/reflog/trailers; (d) the sweep's requeue (create attempt-ref, then CAS-delete the live claim ref) leaves an ambiguous state a fresh sweep cannot resolve | replay c4's flow docker-free on a scratch repo `gnq/`: 1 victim task (worker SIGKILLed after spawn of its tagged dispatch stand-in child, before return commit), 2 clean tasks (one under a 2-proc claim race), sweep, heir; audit flags mirroring c4's (`victim_claim_no_verdict`, `sweep_requeued`, `heir_fixed`, `exactly_one_final_verdict`) plus `attempts_countable_from_repo` and `no_b_machinery` (no `fcntl`, no ledger writes — asserted in code) | ALL flags true ∧ H1's failure conditions (a)–(d) all absent |
| **H2** | Attempt/verdict lineage is queryable from the repo alone: `for-each-ref refs/claims` + `git notes --ref=verdicts list` + `git log --grep 'Attempt:'` reconstruct the full {task → attempts → verdicts} map, including victim-attempt-without-verdict | reconstruction omits any attempt, invents any verdict, or misassigns task/attempt relations | same run: build the map from git reads only; diff against ground truth recorded by the driver | exact match |

### Decision rule (mechanical, no post-hoc edits)

- **H1 PASS ∧ H2 PASS** → outcome `crash-semantics-closed`: C's unproven
  link is closed; C's fallback credibility upgrades from "survived
  single-host probes" to "crash/attempt semantics reproduced". B remains
  winner tonight (frozen criteria, §1); the B-vs-C question moves to the
  remaining real discriminators: batcher re-plumb cost and the two-host
  wall (VERDICTS §4) — which this host cannot exercise (§3).
- **H1 FAIL** → outcome `fallback-dented`: C records its first failure
  class in the record (which of (a)–(d) fired); the fallback designation
  downgrades to "single-host-contention proven only". B's win is
  reinforced. The failure is recorded, never deleted.
- **H2-only FAIL** → record as a lineage-query defect in the proposed
  ref layout (repair stated), H1 verdict unaffected.
- Either way: A stays dead on its recorded kill reason.

### Pre-registered prediction (informational, written pre-run)

H1 and H2 pass: git's CAS is the same primitive H-C1 proved exact 10/10;
reflog preserves every claim-ref transition; notes proved readable at
scale in H-C4. Residual risk is protocol plumbing (create-with-old=0⁴⁰,
CAS-delete syntax, pgrep tagging), not semantics.

## 3. Why this is the cheapest separating check (alternatives recorded)

| Candidate separating check | Verdict |
|---|---|
| **Crash/attempt semantics (chosen, H1/H2)** | B proven (c4) vs C explicitly unproven (DESIGNS §2) — the open difference. Cost: docker-free, model-free, one scratch repo, seconds. Repo precedent for docker-free coordination tests: worker_loop unit tests (9db3aba). |
| Multi-host claims (B's flock/rename needs a shared kernel FS; C's ref-CAS works over git transport) | NOT executable here: one kernel; docker containers share it, so flock would hold across "fake hosts" — no separating power on this box. Recorded as the standing discriminator for the next two-host opportunity. |
| Lineage query speed | No separating power left: B's ledger joins fast (c5/c6: 47 rows, 23 verdicts joined same-night); C's reads fast (H-C3 0.24 s, H-C4 0.087 s). |
| Exactly-once under contention | No separating power left: H-B1 10/10 and H-C1 10/10 both exact. |

## 4. Protocol spec (frozen)

- Scratch repo `experiments/c7/gnq/` (gitignored — regenerable; evidence
  = results JSON + refs/reflog dump, c3/c6 precedent).
- Claim = `git update-ref --stdin` txn `update refs/claims/<task> <new>
  <old>` (c2's proven shape: the CAS lives in update's <oldvalue>; the
  initial create uses old = 0×40). Claim value = blob sha of a one-line
  `att-<role>-<hex>` claim blob (readable via `git cat-file`).
- Dispatch stand-in = a tagged child process (`pgrep -f`-findable:
  cmdline embeds `gnq-<task>-<att>`), standing in for the container so
  the sweep's reconcile-by-probe has an orphan to kill. Work quality is
  a stand-in (return commits are scripted); the probe tests the
  coordination protocol, not the model leg — which is shared by both
  designs and already proven (c2, c6).
- Victim: parent SIGKILLs the victim worker after the stand-in child is
  up, before the return commit → orphaned child + claim-without-verdict.
- Sweep (idempotent, crash-safe order): for each live claim ref whose
  task has no final verdict note: (1) `pgrep -f` the attempt tag → kill
  orphan; (2) create `refs/claims/<task>@<att>` at the dead value
  (attempt preserved — never deleted silently); (3) CAS-delete the live
  claim ref (`delete <ref> <oldvalue>`). If interrupted between (2) and
  (3), re-running resolves: (2) is a no-op when present, (3) retries.
- Heir: fresh CAS create (old = 0×40) → return commit with
  `Attempt: att-heir-*` trailer on `refs/tasks/<task>` → verdict note in
  `refs/notes/verdicts`.
- Watched, not gates (c6 style): exactly-once per claim ref in the 2-proc
  race on the clean task; zero leftover tagged processes after sweep.
- B-machinery guard: the script asserts it never imports fcntl and never
  writes a JSONL ledger; the only state is the git repo.
- Disk guard: df checked pre-run (78% / 99G free); the probe adds < 1 MB.
