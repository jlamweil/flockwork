# FREEZE-C9 — real-workload cross-host coordination (preregistered)

Committed BEFORE any run. Thresholds/hypotheses below are frozen;
deviations are disclosed in results, never silently applied.

## Question (the recorded limit, LOOP round 1)

C's cross-host verdict (VERDICTS §11) rests on the synthetic 9-task
shape (claim/return/verdict as empty commits, no real work). Does git
ref-CAS coordination hold when the claimed work is REAL model work —
opencode edits files, host pytest decides — across two hosts running
TWO DIFFERENT MODELS?

## Hypotheses (frozen)

- **H1 (coordination under real work, exactly-once)**: 4 real tasks
  seeded as broken fixtures in the coord repo (main). Two workers —
  example-host-c (hpc-glm) + example-host-b (gemini-3.5-flash-lite) — claim via push-CAS
  (`--force-with-lease=refs/claims/<task>:` empty-expect), fix the
  task for real, push the fix commit to `refs/tasks/<task>` (same
  create-once CAS), push the verdict commit to
  `refs/verdicts/<task>` (create-once CAS). PASS = every task has
  exactly one claim, one return with `Attempt:` trailer matching its
  claim's att, one verdict with `fixed: true|false` and attempt ==
  claim att. No double-accept, no double-verdict.
- **H2 (merit is honest)**: host pytest on each task's final tree
  decides `fixed`; the verdict message carries pass/fail from the
  tests, not from the model's claim. PASS = verdict field matches
  independent host pytest re-run by the audit phase.
- **H3 (cross-model diversity is real)**: the two hosts' winning
  claims split across BOTH models (not one host winning everything
  by being faster to spawn); measured, not gated — the split ratio is
  recorded. (If one host sweeps 4/4 that is a recorded outcome about
  spawn asymmetry, not a coordination failure.)

## Mechanical rules (frozen)

- A task whose first attempt dies environmentally (no commit produced:
  timeout, provider error) is requeued ONCE via the c8 sweep shape
  (CAS-delete live claim, preserve dead attempt at
  `refs/claims/<task>@<att>`, heir claims fresh). Merit failure
  (patch produced, tests fail) is FINAL — verdict `fixed: false`,
  no retry (the ladder must not reward inflation).
- Fixtures: 4 tasks (fizzbuzz-order, sum-off-by-one, palindrome-edge,
  sort-stability), each a dir with the bug + host-runnable pytest.
  Broken at seed; main carries them; workers push fix commits ON TOP
  of main's task dir (git-native: the coord repo IS the workspace
  history).
- Auth: example-host-b worker uses its existing opencode auth (c6-proven);
  example-host-c worker uses hpc-glm provider (V4/V6-proven). No new auth
  anywhere. Freebucks untouched (neither model is freebuff).
- df-first both hosts before start; zero orphans at end (c7/c8
  no-orphans flag, cross-host); every discard disclosed; thresholds
  and hypotheses untouched after this commit.
- Counted run: exactly one; driver writes results_c9.json + refs
  dump; VERDICTS §12 records the outcome either way.
