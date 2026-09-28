# CORRECTION-GRAPH SPEC — seed (2026-09-28, INT-016)

Trigger row (DESIGN-NEXT §4, deferred pillar): **correction graph +
RAEE/PPR leaderboard** — build when "≥2 models compete on the same task
stream and scoped reviews can no longer separate them (MIN_N evidence
exhausted)". This commit seeds the substrate that trigger builds on.
It deliberately does NOT execute a live multi-model run (INT-016
constraint): nothing here dispatches a model, seeds refs to any live
origin, or pushes anywhere.

## What is committed

- `loop/specs-correction-graph-2026-09-28.json` — six fresh micro-task
  briefs (cg-* names; none dispatched before, first-execution
  independence per the c6 doctrine), each self-contained with a
  deterministic `verify:` oracle. Seed-compatible with the lane CLI:
  `SWARM_ORIGIN=<origin> python3 l2/inrepo.py seed
  loop/specs-correction-graph-2026-09-28.json`.
- `l2/inrepo.py` — `mm_model_of()` (attribution grammar) and
  `correction_graph(origin)` + the read-only `correction-graph` CLI
  mode: vertices, 'corrects' edges, and the per-model census,
  reconstructed from refs + commit objects ALONE (the c7/c8 law: the
  repo alone shows it — no ledger, no env, no worker log).
- `tools/test_correction_graph_spec.py` — the contract tests (below).

## Competitor protocol (frozen now, before any run)

1. One task stream, both competitors draw from it concurrently — no
   per-model queues (the trigger is competition on the SAME stream).
2. Model identity lives in the WORKER LABEL, nowhere else: every seat
   runs `python3 l2/inrepo.py worker mm-<model>-<host>` with
   `SWARM_MODEL` carrying the real model id. att tokens then embed the
   model (`att-mm-<model>-<host>-<hex>`) and archive refs preserve it
   (L3 @-law), so `correction_graph` attributes attempts from refs
   alone. Constraint: `<model>` must not contain `-` (use `.`/`_`,
   e.g. `mm-glm.5.3f-example-host-a`) — the grammar splits on `-`.
3. No new write machinery: correction edges are whatever the c6 heir
   law, reconcile TTL re-leases, and claim races already produce.
   `correction_graph` is read-only (ls-remote + object reads).
4. Model ids without a run to attach to (pre-protocol attempts,
   `att-<worker>-<hex>` labels) attribute as `unknown` — a vertex
   without a model, never a silently misfiled one.

## Hypotheses (falsifiable, carried into any future run)

- **H-CG1** refs-alone reconstruction: `correction_graph` output
  matches driven ground truth on a synthetic substrate. Falsified if
  any vertex/edge needs data outside refs + commit objects.
  (Pinned now by test_correction_graph_spec.py.)
- **H-CG2** the label grammar survives real att minting: worker labels
  become att tokens become archive ref names, model intact.
  (Pinned now by the grammar tests.)
- **H-CG3** edges emerge from existing law with zero new writes: a
  two-model free-for-all over the cg-* stream produces ≥1 'corrects'
  edge via heir/reconcile paths. NOT proven here — this is the first
  thing a live run must show (falsified if all attempts are
  single-vertex chains, i.e. competition produced no corrections).

## Decision rule for any future run

House discipline: freeze a FREEZE-CG.md (predictions, thresholds,
batch composition) BEFORE dispatch — this seed fixes only the
substrate contract, not the statistics. The graph feed is ready when
the census shows ≥2 models with ≥ MIN_N (5) attempts each; RAEE/PPR
ranking reads `models` + `edges` from `correction-graph` output.
