# NAME — the flockwork naming decision record (INT-013)

**Decision of record:** the product name is **flockwork**.

Tagline: **"Flockwork — the swarm that runs like clockwork."**

Recorded in `ops/INTENTS.md`, INT-013 row (append "17:5x"; queue stamps
bracket the naming session to 2026-10-01, with WQ-018 — this rebrand
pass — enqueued 16:15 and WQ-017's test kit landed 15:5x the same day).
The research repo keeps its descriptive title
("swarm-coordination-experiments"); the paper title is unchanged.

## Why flockwork

The owner's words, verbatim:

> i like flockwork because it makes me think of clockwork. accepted. go.

The association describes the machine honestly: a **flock** (the worker
swarm) that runs like **clockwork** — exactly-once claims, bounded
retries, honest verdicts, every guarantee reduced to a git property you
can verify with `ls-remote` and `cat-file`.

## Availability receipts

At the naming session (INT-013 row):

| Name space | Result |
|---|---|
| PyPI | free |
| GitHub | free |
| Typo neighborhood: lockwork, flockware, gitflak, requorum | all empty |

Re-checked at this rebrand pass (2026-10-01, WQ-018): PyPI `flockwork`
still free (pypi.org/pypi/flockwork/json → 404).

## Candidates killed on collisions (naming session)

murmuration · gitswarm (Perforce) · stash (git subcommand + Atlassian)
· flock (flock.com SaaS) · baton · cachet (CachetHQ) · provenance ·
ephemere · aegisgit · refswarm (rf-swarm confusion pair — owner kill)

## Scope rule — what renames and what NEVER does

| Leads with flockwork (product prose) | NEVER renames (wire compatibility) |
|---|---|
| `README-IMPROVED.md`, `QUICKSTART.md`, user-facing wording | the `refs/swarm/*` protocol namespaces — specs/claims/tasks/verdicts/archive are on the wire; renaming breaks every live board and worker |
| | the code identity: CLI stays `python3 l2/inrepo.py …`, env vars stay `SWARM_*`, repo internals keep the working name `swarmo` |
| | the paper; the example-host-a origins; the GitHub repo rename is the owner's one click at release |

Rule of record: **`refs/swarm/*` never renames** — wire compatibility
outranks branding.

## Provenance

- Decision: `ops/INTENTS.md` INT-013 row (naming session, the-queue-driver).
- Owner test kit this rebrand builds on: WQ-017 (QUICKSTART.md,
  `demo/`, README-IMPROVED.md fill), branch `test-kit` @ 8332403.
- Rebrand pass executed: WQ-018 (this commit) — README-IMPROVED.md and
  QUICKSTART.md lead with flockwork + tagline; this record; namespaces,
  paper, and remote untouched.
