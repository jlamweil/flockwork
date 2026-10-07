# docs/ — index

Living documentation (kept current with the code) vs dated decision
records (kept as written).

## Living docs

- `00-ORIENTATION.md` — the zero-context entry point: the project in
  15 sentences, pieces table, which-doc-for-which-job, verdict
  anatomy, glossary. Start here if new.
- `REFS-MODEL.md` — the tight model doc: refs diagram, the five verbs
  (seed/claim/work/verdict/evict), the four honesty laws.
- `NAME.md` — the flockwork naming decision record (why the two names;
  `refs/swarm/*` never renames).
- `GATES.md` — the verdict-count gate (n-of-m reviewer verdicts ->
  integration flip); design of record, now landed in `l2/gates.py`
  and exposed as `python3 l2/inrepo.py review|gate`.
- `SCRATCH-BOARDS.md` — the board-authoring law (origin HEAD must
  resolve before workers clone) + the smoke test that makes it
  non-optional.

## Where the rest lives

- Root `README.md` — the public entry point (what/why/status/
  quickstart/layout/license).
- `QUICKSTART.md` (root) — the hands-on 9-step scratch demo.
- `DESIGN-NEXT.md` (root) — design record and what comes next.
- Dated records at the root — `DESIGNS.md` (frozen hypotheses),
  `VERDICTS.md` (audit pair), `LOOP-*.md` (nightly loop records),
  `AUTOWORK-*.md`, `NIGHT-SUMMARY.md`, `SURVEY-FOSS-2026-09-18.md`,
  `HQ-INTEGRATION.md`, `DOCS-NODE.md` — decision records, unedited.
- `experiments/c0`–`c9`, `loop/`, `survey/` — the measurement record.
- `ops/*` references in these docs point at a separate private driver
  repo's ledger, not at this tree.
