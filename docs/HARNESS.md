# HARNESS — drive the swarm from your coding agent (opencode / codex / claude)

`QUICKSTART.md` and `docs/REFS-MODEL.md` describe the **substrate**: the git-ref
lane, the five verbs, the honesty laws. This page is the **harness layer** on top
of it — how a habitual *interactive* session in a coding harness (opencode, codex,
claude, any MCP client) becomes a transparent, honest swarm-substrate run. Same
substrate, same refs, same `audit` — the difference is who is pushing the refs: a
headless `worker` (the `QUICKSTART` path) versus a human-in-the-loop agent (this
path).

Two delivery mechanisms, one substrate:

- **The opencode plugin** (`.opencode/`, npm package `opencode-flockwork`) —
  marker-only, auto-activates in a wired workspace, injects the protocol into the
  system prompt, exposes the `fw_*` tools, and keeps live claims renewed. It is
  thin glue: every leg shells out to the `flockwork` CLI; the policy lives in the
  substrate (`l2/inrepo.py` + `l2/harness.py`).
- **The MCP server** (`l2/mcp_server.py`) — harness-**agnostic** stdio JSON-RPC.
  It exposes the same `fw_*` tools to *any* MCP client (codex, claude, an IDE, a
  script) without the opencode plugin. The client names the board on every call.

Both produce the same board state, legible by the same `audit` / `attribution` /
`debug` verbs.

## The marker — the sole activation source

A workspace is a flockwork board **iff** it has a local marker:

```
<workspace>/.flockwork/origin     # one line: the board origin path/url
```

`flockwork harness init <dir> <origin>` writes it. The marker is the **only**
on-switch. A stray `SWARM_ORIGIN` env var must **never** activate the plugin —
the env-trigger was the production-push hazard class the substrate exists to
prevent. When active, the marker's origin is passed to the CLI on the spawn env;
there is never a default. Re-pointing an existing marker to a different origin is
refused (a push-target change is not a quiet edit).

The marker is local and gitignored. Nothing about the substrate or the board is
written into the project's history by wiring it.

## Wiring a project (repeatable)

`demo/wire_board.sh <project> [board] [--force]` composes the two documented
steps into one safe, non-destructive helper:

1. create the board origin (bare, `HEAD -> main`) — the `docs/SCRATCH-BOARDS.md`
   authoring law (a plain `init --bare` leaves `HEAD` unborn; the symref is what
   makes a worker clone actually check out the tree);
2. mirror the project's current `HEAD` onto board `main` (the product baseline);
3. smoke-test the board — a fresh clone must contain one of the project's files
   (the `test -e` gate; a clone's `rc` is 0 on an unborn-HEAD board and proves
   nothing, so the *file* is the detector);
4. stamp the `.flockwork/origin` marker and keep it gitignored.

It only creates a board beside the project and a local marker; it never touches
the project's commits or working tree. It refuses to re-mirror a board that
already hosts live `refs/swarm/*` and refuses to re-point a marker at a different
origin, unless `--force`. The manual equivalent is `docs/SCRATCH-BOARDS.md`
(board) + `flockwork harness init` (marker).

## The `fw_*` tools

Both mechanisms expose the same legs. In opencode they are tools the agent calls;
over MCP they are `tools/call` names; each is a thin shell-out to a `flockwork`
CLI verb (the policy is Python, not duplicated in the harness).

| tool | what it does | CLI verb |
|---|---|---|
| `fw_brief <task>` | read the brief: what to do + the `verify` oracle | `brief` |
| `fw_claim <task>` | take the lease; returns the `att` (this attempt) | `claim` |
| `fw_work_started <task> <att>` | checkpoint that work began (crash-legible) | `work-started` |
| `fw_judge <task> <tree> [verify]` | run the `verify` oracle on the tree (**may block for minutes**); a failing oracle is an honest result, not a tool error | `judge` |
| `fw_land <task> <att> <tree> [paths…]` | commit the scoped change, push board `main` (one bounded rebase retry), push the return ref; a rejected push (behind main) is reported, never forced | `land` |
| `fw_verdict <task> <att> <fixed> <oc_rc> <pytest_rc> [oc_elapsed]` | write the terminal **create-once** verdict — the only tool that may write a verdict from an interactive session | `verdict` |
| `fw_renew <task> <att>` | renew the lease (fresh timestamp, same `att`) for long work | `renew` |
| `fw_work_done <task> <att>` | checkpoint that work completed (stops the idle backstop renewing) | `work-done` |

MCP-only additions: `fw_work_external` (the whole leg in one call:
`work_started → judge → land → verdict → work_done`) and `fw_protocol` (print the
canonical protocol). Over MCP every board op takes an explicit `origin`
(harness-agnostic: the client names the board; there is no default) — the only
optional one is `fw_judge`'s `origin` (for reading the spec's `verify`).

## The protocol

`flockwork harness protocol` prints the canonical 2-step protocol. It is the
single source of truth in `l2/harness.py`; the plugin **fetches** it from the CLI
and appends it to the system prompt (so the injected text can never drift from
the docs), and the bundled skill mirrors it. The loop:

1. **understand, then claim** — `fw_brief` then `fw_claim` (get the `att`). If the
   claim fails (already claimed / rejected), stop and say so — do not improvise.
2. **work, judge, land, close** — `fw_work_started`; do the work in the tree;
   `fw_judge`; `fw_land` (the scoped change only); `fw_verdict`; `fw_work_done`.

Invariants (non-negotiable, in the skill + injected protocol):

- A failing oracle is an honest `fixed: false`, not an error — do not weaken the
  `verify` to make it pass.
- Always write `fw_verdict` before you stop — a claimed task must end in a
  verdict, or it becomes an orphan claim only the slow TTL+heir path resolves.
- Never force-push. A rejected `fw_land` (behind main) is a typed refusal:
  re-read main, retry once, then stop and report.

## Attribution — replayable harness + model

Every attempt stamps the harness **and** model that actually did the work, on the
claim + verdict refs, so a verdict is replayable from the refs alone (never
`model: unknown`):

- **opencode plugin** — stamps `SWARM_HOST=opencode` + the session's captured
  model (recorded from the `chat.message` hook) on every leg.
- **MCP server** — the harness is, in order: an explicit `harness` tool arg → the
  MCP `clientInfo.name` captured at `initialize` (a known harness wins on a
  substring match) → an honest `unknown`. A codex/claude client therefore stamps
  its own name; it is never a wrong `opencode`. The server sets
  `SWARM_DISPATCH` to that name for the duration of the call and restores the
  env afterward.

Read it back: `flockwork attribution` (the per-task attempt table: model, kind,
harness, verdict, `in_progress` / `interrupted`) and `flockwork debug <task>`.

## Session state, legibility, and recovery

The plugin records a durable session→claim map and run/gate ledgers, out-of-tree
(XDG) — not in the project. Verbs: `flockwork harness session-map`,
`ledger-dir`, `read-ledger`, `session-put / session-drop`, `ledger-run /
ledger-gate`, `detect`.

- **Legibility:** `flockwork debug <task>` reconstructs the full run from the
  refs alone (spec, claim + its `work_started`/`work_done` checkpoints, the
  return ref, the verdict, the archive) augmented by the local ledgers.
- **Idle backstop:** a live session's claims are renewed on each turn boundary
  (a human-paced task is not reaped as stale). A session that goes quiet past the
  threshold is stamped with a **non-terminal** `stalled:` marker (a rescue) — the
  plugin never writes a terminal verdict; that stays the TTL+heir's job.
- **Recovery:** nothing auto-consumes the ledgers. If a session restarts
  mid-task, the durable map + ledgers survive, but recovery is a *human* reading
  `flockwork debug` and starting a fresh session — say so, do not imply it is
  automatic. The substrate's `reconcile` TTL + one-heir machinery also reaps a
  dead claim, bounded.

## Installing the opencode plugin

The CLI must be on `PATH`: `pip install -e .` (this repo) or `pip install
flockwork`. The plugin then loads from either location:

- **Global** (activates in every wired workspace): the plugin + its lib live in
  `~/.config/opencode/` (`plugins/flockwork.ts` + `lib/flockwork.ts`) and are
  named in `opencode.json`'s `plugin` array — by the npm name `opencode-flockwork`
  (the package source is `opencode-plugin/` here) or by a local path. A global
  `SWARM_ORIGIN` is still never consulted.
- **Repo-local** (activates only in that repo): `.opencode/plugins/flockwork.ts`
  + `.opencode/lib/flockwork.ts`.

The plugin must export exactly **one** `Plugin` function (the opencode loader
refuses a non-function export), so the logic lives in `lib/flockwork.ts` and the
plugin file wires only the single `Flockwork` function to `makeHooks`. It is
fail-safe: a missing CLI, a spawn failure, a timeout, or an unexpected exit
degrades to a no-op + a stderr warning; in a non-wired workspace it is silent.

## Wiring codex / claude (via the MCP server)

The server is harness-agnostic: `python3 -m l2.mcp_server` (newline-delimited
JSON-RPC on stdio). `l2` must be importable — the editable install makes that
true from any cwd; otherwise set `PYTHONPATH` to this repo.

- **codex** — `~/.codex/config.toml`:
  `[mcp_servers.flockwork]` with `command = "python3"`,
  `args = ["-m", "l2.mcp_server"]` (optional `[…env] PYTHONPATH = "<this repo>"`).
- **claude** — `~/.claude.json` (project-scoped or global): an
  `mcpServers.flockwork` entry with `type: "stdio"`, `command: "python3"`,
  `args: ["-m", "l2.mcp_server"]`.

The client then drives `fw_brief → fw_claim → fw_judge → fw_land → fw_verdict`
(plus `fw_work_external` to collapse the leg), always passing `origin` (the board
named by the marker) and, optionally, `harness`.

## The two end-to-end proofs (demo/)

- `demo/owner_test.sh` — the **headless** worker path: two workers race a scratch
  board; exactly one claim wins; the winner lands a `fixed: true` verdict.
- `demo/external_work_e2e.sh` — the **external-work / interactive** path (the
  `fw_*` legs, invoked here as CLI verbs — the same commands the plugin shells to):
  the board is truthful (fix on main + create-once verdict + return ref +
  `audit` `h1_pass`); exactly-once holds under a duplicate claim; and a simulated
  mid-work crash is **legible** (`flockwork debug`) and **bounded** (one heir, a
  fresh session, re-claims and produces the honest terminal verdict). Zero model
  spend, zero network.

## Legibility + health (read-only, on the board's refs)

`flockwork audit` (`h1_pass`: every live claim has a matching return + verdict),
`flockwork attribution` (per-task attempt table), `flockwork debug <task>` (the
full run timeline), `flockwork durations` (per-model `oc_elapsed`, flagging a
near-bound model). See `docs/REFS-MODEL.md` for what each ref means and
`docs/00-ORIENTATION.md` for the zero-context start.
