# swarm-coordination-experiments

Designs, verifications, and verdicts for git-native multi-agent
coordination. See `DESIGNS.md` (frozen hypotheses), `VERDICTS.md`
(audit pair), `LOOP-*.md` (nightly loop records), `experiments/` (c1–c9
freeze files).

## Swarm lane

This repo doubles as its own coordination substrate (D-INREPO,
LOOP-2026-09-18.md). Workers coordinate via refs on this repo's own
origin: `refs/swarm/specs` declares the task specs, `refs/swarm/claims`
holds lease claims acquired by create-once CAS pushes — exactly-once,
a duplicate claim push is rejected — `refs/swarm/tasks` tracks live
task state, and `refs/swarm/verdicts` records the final verdict per
task, written only from independent host-side test runs. Claim and
verdict commits are ROOT commits so their objects never leak into
consumer fetches; eviction is a one-transaction namespace delete.
