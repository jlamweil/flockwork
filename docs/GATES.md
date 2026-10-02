# GATES — the verdict-count gate (INT-081 bridge, INT-085(b) wave A)

Design, now LANDED: `l2/gates.py` implements this document (its own
suite, tests/test_gates.py, @ bbc8a3e), and since WQ-032 the law's CLI
is the single surface for it — `python3 l2/inrepo.py review|gate ...`
delegates to the module; audit shows `integrated: true` + the flip sha.
This section's verb sketch below is the design of record for HOW it
reuses `commit_tree`, `push_sha_ref`, and the create-once CAS shape
`claim_detail` already uses. When a task's fix accumulates **n-of-m
independent agreeing verdicts**, an integration ref flips — the
flock-recon bridge, on our own refs substrate.

All refs live under `refs/swarm/`, written create-once via
`git push --force-with-lease=<ref>:` (empty lease base = create-once;
the race loser is rejected). No wire namespace renames (docs/NAME.md:
`refs/swarm/*` never renames).

## 1. Reviewer-verdict ref shape

```
refs/swarm/verdicts/<task>@<reviewer>
```

`<reviewer>` is a refname-safe id (no `@`, no `/`); the m independent
verdicts — one per reviewer — live here, beside the leaf
`refs/swarm/verdicts/<task>`. Why `@`, not `/`:

- `refs/swarm/verdicts/<task>` is a live leaf ref (the final verdict);
  `refs/swarm/verdicts/<task>/<reviewer>` is a D/F conflict — the
  refstore cannot hold a file `…/<task>` and a dir `…/<task>/` at once —
  so the subpath form is illegal. The `@` keeps the reviewer verdict a
  single leaf `…/<task>@<reviewer>` beside the untouched `…/<task>`.
- It is the house per-instance suffix (the `@`-law): `heirs_count`
  already reads `refs/swarm/archive/claims/<task>@<att>`; a reviewer is
  the verdict analogue of an attempt, so `@<reviewer>` is the same pattern.
- Lane reads filter `@`-free refs, so a reviewer verdict is never the
  task's final verdict; the gate alone reads the `@` set
  (`refs/swarm/verdicts/<task>@*`, exactly as `heirs_count`).

## 2. Verdict body schema

Root-commit body, `key: value` lines (mirrors `verdict_body`). Four
required fields, each machine-checkable. The schema IS the instruction —
a ref whose body fails any check is not a verdict. Line 1 is the type
marker `review-verdict`; the gate parses only these four, never extra
lines.

```
review-verdict
task: INT-081
reviewer: rev-b
outcome: agree
evidence: refs/swarm/tasks/INT-081
```

| field | check |
|---|---|
| `task:` | equals the ref's `<task>` component |
| `reviewer:` | equals the ref's `<reviewer>` component |
| `outcome:` | exactly `agree` or `veto` (lowercase) |
| `evidence:` | a git ref the reviewer examined — for a fix review, the return ref `refs/swarm/tasks/<task>` |

Since WQ-054 (INT-081 TAKE 1) these checks are DEFINED in
`l2/refschema.py` — the schema's definition of record, which also
carries the spec-side schema (what + `verify:` done-bar, refused at
claim time). `l2/gates.py` delegates to it and keeps the inline copy
above as its staged-alone fallback; a refused verdict names its
missing field in the gate event (`invalid_reasons`) and audit() counts
refused board refs.

## 3. The count rule

`m` = expected independent reviewers; `n` = agreement threshold. Both
are declared per task in the spec brief (`refs/swarm/specs/<task>`) as
machine-readable lines (`n: 3`, `m: 3`) — the gate reads them from the
substrate, not prose.

- An **agree** is a reviewer verdict with `outcome: agree`.
- The gate counts agrees across the m reviewers by reading
  `refs/swarm/verdicts/<task>@*`.
- Independence is structural: **one verdict per reviewer, create-once
  CAS.** Each reviewer pushes its own `…/<task>@<reviewer>` with an
  empty lease base, so a reviewer records exactly one verdict; a second
  push is rejected. No counter or ballot table — the number of agreeing
  votes IS the number of live `@` refs parsing as `agree`.
- The gate **fires** iff `count(agree) == n` **and** `count(veto) == 0`
  (see §5); until then it does nothing.

## 4. The flip

The integration marker is written with the same create-once CAS shape as
`claim_detail`:

```
sha  = commit_tree(empty_tree(), "-m", <integrated-body>)
push --force-with-lease=refs/swarm/integrated/<task>:  <sha>:refs/swarm/integrated/<task>
```

- **Racing flips: exactly one wins.** The empty lease base (create-once)
  rejects a concurrent second push (`stale info` / `reference already
  exists`) — the ref database, not a lock file, decides.
- **Re-run after a flip: honest idempotent no-op.** If
  `refs/swarm/integrated/<task>` already exists the create-once push
  fails; the gate reads that as "already integrated", returns success,
  and writes nothing — never an error, never a second write.

## 5. Veto rule (design decision — pinned)

**A veto is a hard stop.** A single `outcome: veto` among the m
expected reviewers blocks the flip regardless of the agree count.

- To the count: a veto is not an agree, and — unlike an abstention — it
  actively fails the §3 fire condition, because `count(veto) == 0` is a
  conjunct, not a by-product of the arithmetic. With m=3, n=2 and one
  veto, the other two can still agree (`count(agree)==2`) yet the gate
  stays closed.
- To the flip: no `refs/swarm/integrated/<task>` is created while any
  veto is live. Verdicts are create-once, so a veto is FINAL for that
  reviewer — the only way past is a new reviewer set (a new integration
  round), never an override.

Chosen over "veto = one fewer agreement": the point of independent
verdicts is that any one independent objection halts integration; the
safe side is the conservative one.

## 6. Worked example — 3-of-3 ending in a flip

Task `INT-081`; the spec declares `n: 3`, `m: 3`, reviewers
`rev-a rev-b rev-c`. The attempt already landed:
`refs/swarm/tasks/INT-081` (return) and `refs/swarm/verdicts/INT-081`
(final verdict, `fixed: true`) exist.

| step | ref written | body (`outcome`) | gate state |
|---|---|---|---|
| 1 | `refs/swarm/verdicts/INT-081@rev-a` | `agree` | agree=1 < 3 |
| 2 | `refs/swarm/verdicts/INT-081@rev-b` | `agree` | agree=2 < 3 |
| 3 | `refs/swarm/verdicts/INT-081@rev-c` | `agree` | agree=3, veto=0 → FIRE |

Full body of step 2's verdict (`refs/swarm/verdicts/INT-081@rev-b`):

```
review-verdict
task: INT-081
reviewer: rev-b
outcome: agree
evidence: refs/swarm/tasks/INT-081
```

Step 3 fires the gate; it writes the flip with this body:

```
integrated
task: INT-081
n: 3
m: 3
agreed: rev-a rev-b rev-c
evidence: refs/swarm/tasks/INT-081
```

…pushed create-once to `refs/swarm/integrated/INT-081`. Re-running the
gate finds the ref live → idempotent no-op.

## 7. Falsifiable properties (pin each in the suite)

1. A reviewer's second create-once push to `refs/swarm/verdicts/<task>@<reviewer>` is rejected (rc≠0); no reviewer ever yields two verdicts.
2. The gate fires iff `count(agree)==n` and `count(veto)==0`; with `count(agree)==n-1` it writes nothing.
3. One live `veto` blocks the flip even when `count(agree)==n`.
4. Two concurrent flip pushes to `refs/swarm/integrated/<task>` yield exactly one ref, one loser rejected.
5. Re-running the gate after a successful flip returns success with the ref unchanged (no error, no second write).
6. `refs/swarm/verdicts/<task>@<reviewer>` coexists with the leaf `refs/swarm/verdicts/<task>` (both resolvable; no D/F conflict).
7. A verdict whose `reviewer:` field mismatches the ref's `<reviewer>` is not counted as a valid vote.
8. A verdict whose `outcome:` is neither `agree` nor `veto` is not counted.
9. The gate never fires when its `evidence:` ref is absent or malformed.
10. A reviewer verdict under the illegal `refs/swarm/verdicts/<task>/<reviewer>` path is never produced or counted; the `@` shape is the only legal per-reviewer ref.
