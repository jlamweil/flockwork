# DOCS-NODE — swarm node operations note

Transport: example-host-c -> example-host-a -> example-host-b over tailscale; ssh user is `you` on example-host-a
and example-host-b (never `jlam`); example-host-b is NOT directly reachable from example-host-c. Substrate:
the bare coord repo `~/swarmo-c9-coord.git` on example-host-a — every claim, return,
and verdict is a git push over ssh to that path.
Claim-CAS semantics: claim = push-CAS create-once (`--force-with-lease=refs/
claims/<task>:` empty-expect); renew by comparing to your OWN recorded sha;
wrong-sha takeover is rejected server-side. Fix commits ride main's lineage;
claim/verdict commits must be ROOT commits or they leak into every fetch.
Crash-revive: a dead attempt is CAS-deleted from the live claim and preserved
at `refs/claims/<task>@<att>` (survives even `gc.pruneExpire=now`); the sweep
kills cross-host orphans via `ssh you@example-host-b`, then an heir claims fresh.
Wallet refill: provider credit refills daily at ~09:00 CEST; a billing death
("Insufficient balance") means REQUEUE + session exit — batch after the refill.

inrepo.py CLI arg order (round-6 finding 4, do not "fix" into a bug):
`worker <LABEL> [tasks...]` — argv[2] is the worker LABEL, argv[3:] is
the optional task filter. `worker T5-audit-host-reads` names the WORKER
"T5-audit-host-reads" and claims sorted-first, it does NOT target that
task; to target, `worker my-label T5-audit-host-reads`. Untargeted
workers claim sorted-first; the att is the real identity either way.

Substrate hygiene: `python3 l2/inrepo.py divergence [ORIGIN]` — compare
this clone's main to the substrate's (read-only; exact counts when the
objects are local, honest `unknown` + fetch note otherwise). A node
that commits locally without pushing runs ahead silently — example-host-b ran 8
commits ahead unnoticed (2026-09-19); check before dispatching work.

Law freshness (2026-09-20): the worker now ENFORCES what divergence
only reports. Before claiming, `worker` compares the RUNNING
`l2/inrepo.py` blob to origin main's published copy (read on the origin
host) and refuses with `law_freshness_refusal` + exit 1 on a mismatch —
pull (fetch+reset) and rerun. Uncommitted law edits count as stale (an
unverified law is an unverified law). Origins that publish no
`l2/inrepo.py` on main (cross-repo task satellites like
solve-metrics-origin) pass through. `SWARM_ALLOW_DIVERGED=1` bypasses,
recorded as `law_freshness_bypass` in the event stream — never silent.
