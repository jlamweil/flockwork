# flockwork — the swarm that runs like clockwork

> How an agent uses this project. Verified 2026-10-11 (CLI + opencode integration + MCP + negative control + skill all VERIFIED-LIVE; C10 protocol-injection defect fixed; evidence in `~/1/proof/flockwork/`).

## What it is
A worker swarm whose **entire coordination substrate is git itself** — no server, no database, no queue daemon. A shared origin's refs under `refs/swarm/` carry task briefs, claims, returns, and verdicts; every guarantee (exactly-once work, honest outcomes, bounded retries) reduces to a git property verifiable with `ls-remote` and `cat-file`. This repo doubles as its own substrate (**D-INREPO**): the machinery lives in `l2/inrepo.py`, and workers coordinate through refs on the project's own origin.

- **CLI:** `flockwork` (Python, entry `l2.inrepo:main`, v0.3.0, installed editable/pip + uv).
- **opencode plugin:** `opencode-plugin/` (the `flockwork-opencode` npm package) — injects the flockwork protocol into the opencode system prompt and drives the claim/return loop.
- **MCP server:** `l2/mcp_server.py`. **Gates:** `l2/gates.py`.

## Zero-context orientation
- `docs/00-ORIENTATION.md` — the project in 15 sentences, how pieces relate, which doc to open for which job, what a verdict means, full glossary.
- `QUICKSTART.md` — run it yourself in five minutes (scratch board, **zero model spend**).

## Tiers (the tightened PoW bar)
- **T1 integrity:** editable install + `import l2.inrepo`; `flockwork --help`.
- **T2 offline:** the in-repo test suite (gates + harness + inrepo).
- **T3 live round-trip:** the CLI drives a real scratch board's refs (claim → return → verdict) via `ls-remote`/`cat-file`.
- **T4 end-to-end:** a full multi-task loop green (verified: 8-task loop), plus the opencode plugin live-driving it.

## The opencode plugin (this box)
Deployed globally so any opencode session can use flockwork:
- `~/.config/opencode/lib/flockwork.ts` — **a file copy** of `opencode-plugin/lib/flockwork.ts` (a **symlink breaks** opencode's plugin loader — keep it a real file).
- `~/.config/opencode/plugins/flockwork.ts` — the loaded plugin (idempotency guard `globalThis.__flockworkRegistered`).

**C10 (fixed 2026-10-11):** the protocol is injected by **mutating** `output.system` (`.push(proto)`), **not** re-assigning the reference — opencode reflects mutation of the same array, and re-assignment silently dropped the protocol from the live prompt (live-confirmed). See commit `11b5fad` + `flockwork.c10.test.ts` (asserts mutation on the original array ref; a re-assign regression now fails).

## Repo layout
- `l2/inrepo.py` — the substrate + CLI entry. `l2/gates.py`, `l2/harness.py`, `l2/mcp_server.py`, `l2/metrics.py`, `l2/refschema.py`.
- `opencode-plugin/` — the opencode integration (lib + tests).
- `demo/`, `docs/`, `CHANGELOG.md` — demo, docs, history.

## State / blockers
- **No current blocker for use.** CLI + plugin + MCP + skill are live-verified on this box.
- **Note:** standalone package *publish* to PyPI/npmjs was previously 403 (token scope). flockwork is consumed everywhere via the opencode plugin (no publish needed).
