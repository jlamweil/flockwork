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
