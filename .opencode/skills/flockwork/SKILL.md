---
name: flockwork
description: Route a habitual chat request into a transparent flockwork swarm-substrate run (claim -> work -> judge -> land -> verdict). Use when a human asks to fix/implement a task on a flockwork board in chat ("fix X; verify: ...").
---

# flockwork — habitual chat, transparent swarm

A human says "fix X; verify: ..." in an ordinary chat. This skill routes that
into the flockwork substrate so the work is transparent + honest: every leg
lands in the board's git refs, a failing oracle is an honest `fixed:false`
(not a silent error), and the run is legible after the fact.

## When to use

- The human has framed a task as work to be done on a flockwork board
  (there is a board/origin + a task, or the human wants one seeded).
- The working directory declares a flockwork board (a `.flockwork/origin`
  marker), or the human names the origin.

**When NOT to use:** there is no board and the human just wants a local code
edit (use normal tools); or the task is pure Q&A.

## What to do

1. **Bootstrap (once, if needed).** If there is no `.flockwork/origin` marker,
   declare the origin:
   `flockwork harness init <dir> <origin>`
   The marker is local + gitignored; the ledgers are out-of-tree (XDG).
   Re-pointing to a different origin is refused (a push-target change).

2. **Seed the task — with seed integrity.** Seed a spec for the task, EXTRACTING
   the `verify:` command from the human's message. Never invent or loosen a
   verify (self-graded homework is the fake `fixed:true` vector). If the human
   did not give a verify, ask before seeding.

3. **Run the protocol.** The plugin has already injected the canonical protocol
   into the system prompt (and `flockwork harness protocol` prints it). Follow
   it exactly with the `fw_*` tools:

   - `fw_brief <task>` then `fw_claim <task>` (get the `att`).
   - Work in the tree; `fw_work_started <task> <att>`.
   - `fw_judge <task> <tree>` (the oracle — may block for minutes).
   - `fw_land <task> <att> <tree> [paths...]` (the scoped change only).
   - `fw_verdict <task> <att> <fixed> <oc_rc> <pytest_rc> [oc_elapsed]`.
   - `fw_work_done <task> <att>`.

## Invariants (non-negotiable)

- A failing oracle is an honest result: land what you have, write
  `fixed=false`, and say why. Do not weaken the verify to make it pass.
- Always write `fw_verdict` before you stop — a claimed task must end in a
  verdict, or it becomes an orphan claim only the slow TTL+heir path resolves.
- Never force-push. A rejected `fw_land` (behind main) is a typed refusal:
  re-read main, retry once, then stop and report.
- On long work, `fw_renew <task> <att>` keeps the lease alive.

## Debuggability + recovery

- `flockwork debug <task>` reconstructs the full run timeline from the refs
  alone (spec, claim + its checkpoints, return ref, verdict, archive) —
  augmented by the local run/gate ledgers.
- If a session restarts mid-task, the durable session map + ledgers survive,
  but nothing auto-consumes them: recovery is a human reading `flockwork
  debug` and starting a fresh session. Say so — do not imply it is automatic.
