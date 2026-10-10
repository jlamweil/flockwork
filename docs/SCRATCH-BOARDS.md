# SCRATCH-BOARDS — the authoring law (HEAD must resolve before workers arrive)

You are building a **board** — a bare origin that workers (human-operated
seats or dispatched models) will clone to do tasks. This page is the
authoring law. It exists because two real boards (WQ-052's gate corpus,
WQ-056's recovery board) were built with the broken construction below
and every clone they served checked out NOTHING — the tasks still got
done, but through an unplanned channel the briefs never promised
(evidence at the bottom). The law is three lines; the smoke test is the
gate that makes the third one non-optional.

## The law

1. **Create the origin so its HEAD points at the branch you will
   actually push.** Either at creation
   (`git init --bare --initial-branch=main board.git`, git >= 2.28) or
   immediately after a plain init
   (`git --git-dir=board.git symbolic-ref HEAD refs/heads/main`).
   A plain `git init --bare` leaves HEAD at `refs/heads/master`
   (git's compiled-in default); if you then push only `main`, HEAD
   stays **unborn** forever — that is the trap.
2. **Push the seed content to that same branch**
   (`git -C seed push /abs/path/board.git main`). Use absolute paths in
   scripts: `git -C seed push board.git main` resolves `board.git`
   *inside `seed/`* and fails with a misleading
   "does not appear to be a git repository".
3. **Smoke-test the board BEFORE launching any worker**: a throwaway
   clone must produce a worktree containing the files your briefs
   claim are "already checked out". Exit code is the gate — no worker
   starts while it is red.

## The smoke test (copy-paste; proven 2026-10-02, WQ-062)

```bash
smoke() { # $1 = origin path, $2 = a file the briefs assume present; rc 0 = board lawful
  local d rc=0
  d=$(mktemp -d) || return 2
  git clone -q "$1" "$d/board" 2>/dev/null || rc=1
  [ -e "$d/board/$2" ] || rc=1
  rm -rf "$d"
  [ $rc -eq 0 ] && echo "SMOKE PASS ($2 in worktree of $1)" \
               || echo "SMOKE FAIL ($2 ABSENT — unborn-HEAD trap, see docs/SCRATCH-BOARDS.md)"
  return $rc
}
smoke /abs/path/board.git docs/T1-SOURCE.md   # non-zero rc = fix the board first
```

A one-liner form, same predicate:

```bash
d=$(mktemp -d); git clone -q "$BOARD" "$d/b" 2>/dev/null; test -e "$d/b/$EXPECTED" \
  && echo SMOKE-PASS || { echo SMOKE-FAIL; false; }; rm -rf "$d"
```

(`test -e` is the real detector: cloning an unborn-HEAD board still
exits 0 with only a one-line warning, so the clone's rc proves nothing.
Run `smoke` against ONE file per task family your board serves.)

## What the trap looks like live

Same seed content, both boards built 2026-10-02 under `/tmp/wq062-demo/`
(transcript in the WQ-062 receipt):

| | trap board (`init --bare` + push main) | lawful board (+ `symbolic-ref HEAD`) |
|---|---|---|
| origin HEAD symref | `refs/heads/master` (unborn — only `main` exists) | `refs/heads/main` |
| `git clone` result | rc **0** + `warning: remote HEAD refers to nonexistent ref, unable to checkout` | rc 0, no warning |
| clone's HEAD | unresolvable (`unknown revision`) | `main` |
| files in worktree | **0** | all of them |
| smoke test | `SMOKE FAIL`, exit 1 | `SMOKE PASS`, exit 0 |

## Constructions, ranked

| construction | HEAD after | board |
|---|---|---|
| `init --bare` + push `main` only | `refs/heads/master`, unborn | **BROKEN — the trap** |
| `init --bare --initial-branch=main` + push `main` | resolves at `main` | OK |
| `init --bare` + `symbolic-ref HEAD refs/heads/main` + push `main` | resolves at `main` | OK |
| clone an existing checkout, push from it (WQ-035 pilot3, WQ-032 origin2 style) | inherited, resolves | OK |
| `init --bare` + push the default branch name instead | resolves at `master` | OK, but off the fleet's `main` convention — don't |

## Why the law is docs, not lane code

The board is the operator's artifact; the lane (`l2/inrepo.py`) cannot
see the difference between a lawful origin and an unborn-HEAD one —
clone rc is 0 either way, and the CAS/claim/verdict machinery works on
refs regardless of what any worktree contains. So the fix is the law +
smoke test at authoring time, and a worker-side tell (next section).
No lane change is intended by this page.

## Worker-side tell (if you ARE the dispatched model)

You are cloned into a board and the worktree is empty while your brief
says files are "already checked out": `git rev-parse --abbrev-ref HEAD`
answers `HEAD` (unborn). **Stop and surface it** — do not improvise
plumbing reads (`git show origin/main:path`) to fake a checkout. The
2026-10-02 boards show work continues that way and can even land
correct-looking output, but the brief's premise is false and a
self-contained `verify:` oracle can pass source-blind. Report the board,
let the operator re-law it.

## Evidence (first-hand, 2026-10-02)

- WQ-056 receipt (driver checkout, `runs/2026-10-02/workqueue/WQ-056/`):
  instrument finding F1 — both WQ-052 and WQ-056 boards had unborn HEAD;
  every worker/reviewer clone checked out nothing; the dispatch models
  read task content via git plumbing (proof: the `localhost:8080` fact
  appears in both worker docs but existed only in `docs/T1-SOURCE.md`,
  absent from every worktree).
- WQ-052 receipt addendum: the 24 reviewer clones were also
  checkout-less; the 12/12 unanimous defect detection is itself the
  proof the models found the content another way.
- WQ-062 (this law): trap re-verified on the kept boards
  (`/tmp/wq056-recovery/origin.git` HEAD = `refs/heads/master`, orphan
  tree `/tmp/inrepo-T1-cyrh7b0r` = 0 tracked files) and re-demonstrated
  end-to-end on fresh origins (table above).
