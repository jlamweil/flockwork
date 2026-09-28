# RECEIPT — c1 frozen fixtures xfail-marked (INT-025, 2026-09-28)

Seat: swarmo-2226 (zcode fleet). Classification: **stale-by-design**, not broken-by-refactor.

## What was observed

Root-level `python3 -m pytest -q` showed 8 failures across the 5 c1 case dirs
(sumto 2, initials 2, dates 2, fizzbuzz 1, addtag 1), e.g.
`test_ten assert 45 == 55` (sumto's `range(1, n)` excludes n).

## Why stale-by-design (evidence)

- `batch.sh` `make_fixture` writes these implementations **deliberately buggy** —
  the bug is the experiment: each case is handed to an LLM agent to fix
  ("Fix the bug in … so that `python3 -m pytest` passes").
- `results.jsonl` (C1 survey, 2026-09-15) records `fixed:false` for **all 5
  cases** — the committed fixtures preserve the pre-fix state the ledger
  describes. The failures ARE the recorded outcome.
- Path history: the case files' only touching commit is `43ea615` (design-era
  c2 evidence commit); none of today's INT-013 wave commits touched
  `experiments/c1/` (verified via `git log -- experiments/c1/`).
- The tests import only their local sibling modules — no main-repo (`l2/`,
  `tools/`) code is involved, so no later refactor could have broken them.

## What changed (frozen-experiment convention: never delete, no logic rewrites)

- The 8 failing tests got `@pytest.mark.xfail(strict=True, reason=XF)` with a
  dated reason string; assertion bodies and both directories' implementation
  files are byte-untouched. The 6 tests that pass against the frozen behavior
  were left unmarked (they faithfully record the parts the frozen bugs satisfy).
- This receipt file.

## Falsifier / drift guard

`strict=True`: if a frozen fixture ever starts passing (fixture edited, or a
future regeneration changes behavior), the suite goes RED with XPASS instead of
silently absorbing the drift — forcing a fresh dated receipt. Note
`batch.sh make_fixture` rewrites case dirs wholesale on a rerun, wiping both
markers and this state; a rerun is a NEW experiment by definition and should
file its own receipt.
