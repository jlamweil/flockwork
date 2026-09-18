# SURVEY-FOSS — 2026-09-18 (HQ/example-host-c)

Question: does existing FOSS scaffold any load-bearing swarmo component?
Method: live web survey (agent-orchestrators index, 2026 comparison
roundups, awesome-cli-coding-agents, GitHub topics), not memory.

## Landscape map (categories × best examples)

| Category | FOSS examples | Relation to swarmo |
|---|---|---|
| Session supervision (N agents, diff review) | claude-squad, dmux, agent-deck, termany, herdr, repomon | none for core; batcher already headless. UX pattern worth stealing: agent-manager "prompt lands in pane without attaching" (= freebuff-connector) |
| Task intake (kanban/issues → agents) | Vibe Kanban, openkanban, code-conductor (GitHub-Issue labels as claim queue), AI4Kanban | front-ends only; ledger already provides claims |
| Worktree-per-agent isolation | claude-squad, amux, superset, YYLO | pattern industry-standard; swarmo's docker/workdir isolation is the stronger variant |
| Sandboxed backends | agentbox (per-agent Docker/VM), intentic (persistent sandbox + outbound tunnel), Fletch (Seatbelt/Docker) | validates E10 recipe shape; intentic's tunnel model noted for multi-host |
| Harness/runtimes | opencode (swarmo's worker already), Archon, OpenClaw | opencode confirmed as the right worker CLI |
| Verification-gated merges | YYLO (typed task/validation/merge boundaries, receipt-backed merge queue) | closest cousin to H2 "host pytest decides" |
| Coordination core | clu (SQLite tracker, atomic task claim, dep graph) | nearest analog; single-host SQLite = B-design shape without the production mileage |

## What FOSS does NOT cover (swarmo's differentiators)

1. **Cross-host exactly-once claims + crash-revive** — the field is
   single-machine; multi-machine tools supervise *sessions*, not *work*.
   The c8/c9 git ref-CAS substrate (claim-CAS, sweep/heir, orphan
   handling) has no found equivalent.
2. **Git-provable audit discipline** — frozen preregistrations, verdict
   refs, `att-*` lineage, disclosed-discard, `@`-preserved history. A
   measurement instrument; no productized equivalent found.
3. **Error-class policy as tested code** — classify_dispatch
   (timeout→requeue, model-death→requeue+exit, EACCES→quarantine,
   recipe-bug→not-requeueable) exists nowhere reusable.
4. **Ladder + Wilson-gate model ranking** — absent from the ecosystem.
5. **First-party absorption** (Claude Agent Teams, Codex loops) serves
   the simple segment (same-machine, same-subscription) — the segment
   VERDICTS already declines to build for.

## Decision

**No scaffold to adopt.** FOSS validates the architecture (worktree/
sandbox isolation + host-verify gates are the field consensus) but no
project covers the coordination core or the audit discipline. L2
proceeded on swarmo's own proven components (same day):
- `l2/backend.py` — DispatchBackend interface + Docker/Headless
  implementations (both previously proven; one row schema),
- `l2/rehearse.py` — 5/5 PASS through the real claim path (LOOP round
  3 / V8), which also discovered a production error-table gap
  (`docker_environment` → quarantine; upstream the-queue-driver
  commit <scrubbed-sha>),
- flip remains owner-gated (HQ-INTEGRATION.md).

Watchlist: clu (claim-core ideas), YYLO (merge-queue semantics),
intentic (tunnel transport for cross-host lanes).
