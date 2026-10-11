# Changelog

## v0.3.0

- **Model attribution**: the dispatched `SWARM_MODEL` + its honest
  `chain`/`direct` flag are recorded in the claim + verdict ref bodies (the
  durable source, readable for every attempt — archived, live, final), stamped
  on every attempted event + the metrics stream, and surfaced by a read-only
  `attribution` subcommand. The legacy `mm_model_of` derivation is retired in
  favor of the body-derived model.

- **Harness-integration layer**: the inlined opencode dispatch is now a seam
  (`_dispatch`) selectable via `SWARM_DISPATCH` (default `opencode`), with the
  dispatch harness stamped on every attempt so attribution is harness-aware.
  A second, proven harness ships behind the same seam: `SWARM_DISPATCH=freebuff`
  routes to the remote PTY adapter (`fbconn.run_prompt`), imported on demand so
  the default path never needs the remote dependency.

- **Fail-closed model (behavior change)**: the stale `zai-org/GLM-5.3-Flash`
  default is gone. `SWARM_MODEL` is now **required** — a worker started without
  it refuses at a typed seat preflight (`model_refusal`, exit 1, no claim
  consumed) instead of dispatching a stale id into a misattributed env-death
  loop. An unknown `SWARM_DISPATCH` is likewise refused (`dispatch_refusal`);
  the opt-in `SWARM_MODEL_STRICT=1` adds an alias-resolvability gate.

- **Run-duration checkpointing + debuggability**: a best-effort CAS push at
  dispatch start marks the claim with `dispatch_started`, resetting its lease
  age (a bounded run is sweep-proof) and making a crash mid-run legible from
  the refs alone. New surfaces: a `durations` subcommand (per-model
  `oc_elapsed` distribution from the verdict refs, with a near-bound flag),
  `attribution` `in_progress`/`interrupted` reports, a `dispatch_started`
  metrics kind, a lease-safety invariant warning, a reconcile `aging` flag
  (a claim approaching its lease), and board-scoped `FLOCKWORK_DEBUG` dumps
  (raw bodies + claim age). Opt-in streaming dispatch (`SWARM_DISPATCH_STREAM=1`)
  captures the JSON event stream with a real SIGKILL deadline + the error
  recovered from the `error` event.

## v0.2.3

- Complete source distribution: added `MANIFEST.in` so the sdist ships the
  full tree — `docs/`, `demo/`, `CHANGELOG.md`, `QUICKSTART.md`, and the CI
  pipeline — not just the package. `pip install` (the wheel) is unchanged.

## v0.2.2

- Single name: the product is `flockwork` throughout. The internal working
  name `swarmo` is gone from the docs, the demo kit, the scratch-board paths,
  and the lane's git commit identity (`flockwork refs-CAS` /
  `flockwork-lane@refs-cas.local`). No behavior change; suite unchanged.

## v0.2.1

- Public-source hygiene: removed internal development-process references
  (wave/work-item/integration labels, a commit hash, internal dates) from
  comments, docstrings, and two user-facing error messages. No behavior or
  API change — the suite is unchanged.

## v0.2.0

- **Oracle-leg timeout** (`SWARM_ORACLE_TIMEOUT`, default 600s): the verify
  command is now run under coreutils `timeout`, so a hung oracle (a network
  wait, an interactive prompt, an infinite loop in the fixed code) degrades to
  an honest measured result — `pytest_rc: 124`, `oracle_timeout: true` —
  instead of leaking a `TimeoutExpired` out of the worker. A completed
  dispatch with a hung oracle is a merit final, not an env death: the claim
  stays live for the reconcile TTL re-lease.
- CI: a GitLab pipeline runs the test suite on push and build/twine-checks
  the artifacts.
- Added `CHANGELOG.md`.

## v0.1.0

- First public release: the git-native swarm that runs like clockwork.
- In-repo `worker` loop (claim → dispatch → oracle verify → return →
  verdict) with the dispatch legs (opencode headless + the generic `sh`
  leg), the L1–L4 oracle stack, and the honest verdict/return/claim refs.
- The `flockwork` CLI (seed / worker / claim / returns / sweep /
  reconcile / reconcile-loop / inspect / audit / audit-metrics) and the
  DORA metrics layer.
- Schema gates (spec / review / verdict) so a bad seed is an env death
  before dispatch, never a false green.
- `demo/` (one command) and `docs/` (QUICKSTART + OPERATIONS).
- License: Apache-2.0.
