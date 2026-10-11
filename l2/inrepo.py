#!/usr/bin/env python3
"""l2/inrepo.py — the in-repo lane.

The project's OWN origin is the coordination substrate:
  refs/swarm/specs/<task>     task brief (root commit; fetched by workers)
  refs/swarm/claims/<task>    claim    (root commit, msg = claim <att>)
  refs/swarm/tasks/<task>     return   (fix commit on main lineage,
                                         trailer `Attempt: <att>`)
  refs/swarm/verdicts/<task>  verdict  (root commit, msg = verdict
                                         task/attempt/fixed/host block)

Laws encoded:
  L1  claims/verdicts are ROOT commits — never on main lineage.
  L2  CAS via --force-with-lease=refs/swarm/<kind>/<task>:
       (empty base = create-once; known sha = own-record renewal).
       Workers never read remote state to form expectations — the sha
       comes from their own records (works under hiding).
  L3  dead attempts preserved at refs/swarm/archive/<kind>/<task>@<att>
       in the SAME transaction that frees the live ref (the @-marker
       archive law).
  L4  host pytest at the task dir is the verdict oracle.

Usage (run from a worker clone of the origin):
  python3 l2/inrepo.py seed SPEC.json     # push spec refs for tasks
  python3 l2/inrepo.py worker NAME [tasks...]   # claim-fix-verify loop
      # the worker refuses to claim while the RUNNING law (this file)
      # differs from origin main's published copy (law-freshness gate;
      # SWARM_ALLOW_DIVERGED=1 bypasses, recorded in the event stream)
  python3 l2/inrepo.py audit [tasks...]   # read-only H1/H2 over refs
  python3 l2/inrepo.py review <task> <reviewer> <agree|veto>
      [evidence-ref]   # reviewer verdict, create-once (the gate
      # module l2/gates.py through the law's CLI surface;
      # docs/GATES.md — n-of-m verdicts flip refs/swarm/integrated/<task>)
  python3 l2/inrepo.py gate <task>   # verdict-count gate: fires iff
      # count(agree)==n and count(veto)==0 with evidence resolving;
      # idempotent after a flip
  python3 l2/inrepo.py sweep ORIGIN TASK [ATT]  # archive+free swarm refs
  python3 l2/inrepo.py reconcile [ORIGIN] [TTL_S]  # sweep stale claims
  python3 l2/inrepo.py correction-graph [ORIGIN]  # multi-model attempt
      # graph from refs alone (read-only)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

try:
    import metrics
except ImportError:  # loaded by path (tests, drivers): resolve the sibling
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import metrics
    except ImportError:
        # the law file travels without its sibling (staged nodes, lone
        # inrepo.py copies — the lawgate pattern): the lane runs with NO
        # metrics. Observe-only means the instrumentation can never
        # become a dependency of the wire.
        class _MetricsNoop:
            def enabled(self):
                return False

            def emit(self, *a, **k):
                return None

            def observe_attempt(self, out):
                return None

        metrics = _MetricsNoop()

try:
    import gates
except ImportError:  # loaded by path (tests, drivers): resolve the sibling
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import gates
    except ImportError:
        # the law file travels without its sibling (staged nodes, lone
        # inrepo.py copies — the lawgate pattern): the lane BOOTS with
        # NO gate. Unlike metrics (observe-only), the gate verbs MUTATE
        # the board, so their fallback is an honest rc-2 refusal at use
        # — never a success-shaped no-op (a mutating operation that
        # did not happen never reports success).
        gates = None

try:
    import refschema
except ImportError:  # loaded by path (tests, drivers): resolve the sibling
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import refschema
    except ImportError:
        # staged alone (the lawgate pattern): the lane boots with NO
        # schema enforcement — the pre-schema law exactly (the
        # empty-spec check only). Adoption is additive: the schemas
        # (l2/refschema.py) refuse malformed refs at claim/gate
        # time, well-formed refs behave byte-identically, and the schema
        # can never become a dependency of the wire.
        refschema = None

# NO default origin. The default here used to be
# a production board, so any seat that forgot SWARM_ORIGIN would have
# pushed seed/verdict/spec refs straight into production. Every origin
# resolution goes through resolve_origin(): explicit arg > SWARM_ORIGIN
# at CALL time > this import-time capture > OriginUnsetError naming the
# fix. The module attribute stays settable (the suite's per-file-module
# monkeypatch pattern is a legal resolution source).
ORIGIN = os.environ.get("SWARM_ORIGIN")


class OriginUnsetError(RuntimeError):
    """No origin to talk to: no explicit origin, SWARM_ORIGIN unset,
    and no import-time ORIGIN. A config error, never a silent write."""


def resolve_origin(explicit: str | None = None) -> str:
    o = explicit or os.environ.get("SWARM_ORIGIN") or ORIGIN
    if not o:
        raise OriginUnsetError(
            "no origin: export SWARM_ORIGIN=<board-url> (or pass the "
            "origin explicitly) — there is no default origin; the old "
            "production default would have pushed writes straight to "
            "the production board"
        )
    return o


def sh(cmd, cwd=None, inp=None, timeout=600, env=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=inp,
        text=True,
        env=env,
        capture_output=True,
        timeout=timeout,
    )


def ok(r):
    return r.returncode == 0


def git(*args, cwd=None, inp=None):
    return sh(["git", *args], cwd=cwd, inp=inp)


# Ref-CAS identity + no-phantom-push law.
# The claim/verdict/spec writes used ambient git identity; on a node
# without user.email/user.name (first seen from a throwaway-HOME driver)
# `commit-tree` fails and the EMPTY sha flowed into the refspec —
# `<empty>:refs/...` deletes a non-existent ref and git exits 0, so the
# event reported verdict_pushed true with no verdict ever written. The
# identity is now always carried (-c), the commit result is checked
# before any push, and a failed commit NEVER becomes a push.
LANE_EMAIL = "flockwork-lane@refs-cas.local"
LANE_NAME = "flockwork refs-CAS"


def _commit_tree_id_args():
    return "-c", f"user.email={LANE_EMAIL}", "-c", f"user.name={LANE_NAME}"


def commit_tree(*args, **kw):
    """git commit-tree with the lane identity carried. Returns
    (sha_or_None, proc): sha None means the commit failed and `proc`
    carries the REAL rc/stderr (classification + honest events need the
    actual failure, never a synthesized one)."""
    proc = git(*_commit_tree_id_args(), "commit-tree", *args, **kw)
    sha = proc.stdout.strip() if ok(proc) else ""
    return (sha or None), proc


def push_sha_ref(sha, ref, lease=True, failed=None, origin=None, expected=None):
    """Push <sha>:<ref> with force-with-lease. A missing sha FAILS the
    push — returning the commit's own failed proc when the caller
    supplied it (real stderr preserved), else a synthetic rc-128 result.
    Spawning git with an empty refspec is forbidden: git reads it as a
    ref DELETION and exits 0 (the phantom-verdict bug). `expected=None` is
    the create-once CAS (the ref must be absent); a concrete sha is the
    update-CAS (the ref must be at that value) — the dispatch-start
    checkpoint uses the latter to re-lease an already-living claim."""
    if not sha:
        if failed is not None:
            return failed
        return subprocess.CompletedProcess(
            args=["git", "push", ref], returncode=128,
            stdout="", stderr="no sha: commit-tree failed (push withheld)")
    lease_arg = (
        f"--force-with-lease={ref}:{expected}" if expected
        else f"--force-with-lease={ref}:"
    )
    return git("push", "-q", *( [lease_arg] if lease else [] ),
               resolve_origin(origin), f"{sha}:{ref}")


def remote(*args, cwd=None, origin=None):
    orig = resolve_origin(origin)
    return git("ls-remote", orig, *args, cwd=cwd)


def _ssh_cmd(host: str, port, *argv: str) -> list:
    """ssh command whose remote side is a SHELL string: ssh concatenates
    the command argv into one string and hands it to the login shell, so
    every argv element is joined shlex.quote'd ('ssh joins argv and the
    remote shell eats the parens'; a contract test extends the class to
    remote-data
    REFNAMES: the claim-CAS namespace accepts shell-active names, and
    origin reads pre-fix executed them on the origin host)."""
    cmd = ["ssh", "-o", "BatchMode=yes"]
    if port:
        cmd += ["-p", str(port)]
    cmd += [host, " ".join(shlex.quote(a) for a in argv)]
    return cmd


def empty_tree():
    return git("hash-object", "-t", "tree", "/dev/null").stdout.strip()


# The archive-marker law embeds @att in
# refnames, so every lane read (open_tasks, sweep, audit) excludes
# @-carrying refs BY DESIGN. A task name carrying @ would seed/claim a
# ref no lane machinery can ever see or sweep — a permanent zombie
# (seeded:true, invisible to open_tasks, un-sweepable). Refuse at the
# entry points instead of quietly creating invisible state.


def task_name_is_safe(task: str) -> bool:
    """Task names become refname components; '@' is the archive-marker
    separator (refs/swarm/.../task@att) and must never appear in a LIVE
    task name."""
    return isinstance(task, str) and "@" not in task


_BAD_TASK_MSG = (
    "refused: task names carry no '@' (archive-marker law — an "
    "@-named task would be invisible to every lane read and un-sweepable)"
)


def _swarm_model() -> str:
    """The dispatched model: `SWARM_MODEL`, the exact `-m <model>` the
    worker hands the harness. Read at claim time so the model is on the
    pre-dispatch checkpoint; identical to the dispatch-site read (same env
    var). No default: an unset model yields the empty string, which the
    worker-start preflight refuses — the stale GLM default that dispatched
    into a misattributed env-death loop is gone (L1 fail-closed)."""
    return os.environ.get("SWARM_MODEL", "").strip()


_KNOWN_HARNESSES = ("opencode",)


def _dispatch_harness() -> str:
    """The dispatch harness: `SWARM_DISPATCH` (default opencode). Selects the
    adapter in the dispatch seam; the worker-start preflight refuses a value
    it does not know (a config error must not dispatch)."""
    return (os.environ.get("SWARM_DISPATCH", "opencode").strip().lower()
            or "opencode")


def _opencode_model_list(oc_bin: str) -> list | None:
    """The opencode model listing (best-effort, ~seconds). Returns the model
    ids on a successful non-empty listing; None on a failed or empty listing
    — 'cannot see' is not 'empty', so a missing listing is never a refusal
    (the house cannot-see law)."""
    try:
        r = subprocess.run([oc_bin, "models"], capture_output=True,
                           text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    ids = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    return ids or None


def _seat_refuse(name: str, event: str, reason: str) -> None:
    """Emit a typed seat-preflight refusal (distinct from a claim/env death):
    the worker exits 1 before it touches the origin, so no claim + heir
    budget is consumed."""
    print(json.dumps({
        "event": event, "worker": name, "model": _swarm_model() or None,
        "harness": _dispatch_harness(), "reason": reason,
    }), flush=True)


def _model_preflight(name: str) -> bool:
    """Worker-start seat preflight (L1/L2, fail-closed). Returns True when
    the worker must refuse to start: no dispatched model (L1 — the stale
    default is gone, an unset model must not dispatch), an unknown dispatch
    harness (L2 — a config error), or a model absent from a successful,
    non-empty harness model listing (the alias check: a stale id must not
    spend a dispatch). A failed/empty listing proceeds (cannot-see law)."""
    model = _swarm_model()
    if not model:
        _seat_refuse(name, "model_refusal",
                     "no dispatched model set (SWARM_MODEL)")
        return True
    harness = _dispatch_harness()
    if harness not in _KNOWN_HARNESSES:
        _seat_refuse(name, "dispatch_refusal",
                     f"unknown SWARM_DISPATCH {harness!r} "
                     f"(known: {', '.join(_KNOWN_HARNESSES)})")
        return True
    # Alias resolvability (opt-in, SWARM_MODEL_STRICT=1): a stale id must not
    # spend a dispatch. Opt-in because the check invokes the harness binary
    # with `models`, and a dispatch shim with side effects on any invocation
    # would be tripped by a default-on probe; a real stale model still fails
    # fast at dispatch (the harness rejects it → env_death → requeue).
    if harness == "opencode" and os.environ.get("SWARM_MODEL_STRICT", "") == "1":
        listing = _opencode_model_list(os.environ.get("OPENCODE_BIN", "opencode"))
        if listing is not None and model not in listing:
            _seat_refuse(name, "model_refusal",
                         f"model {model!r} not in opencode model list")
            return True
    return False


def _dispatch(harness: str, model: str, brief: str, tree: str):
    """The dispatch seam (L2): select the adapter by `harness` and run the
    brief in a scratch work tree. Returns (rc, elapsed, err) — the same
    contract the inlined opencode leg had, so the downstream classification
    (no_changes / env_death / heir) is untouched. opencode is the default;
    freebuff (the proven swarmo adapter) is added behind this seam. `err` is
    captured at the source so a timeout (no CompletedProcess) still yields a
    clean empty tail instead of an unbound reference."""
    env = dict(os.environ, PWD=tree, OLDPWD=tree)
    t0 = time.monotonic()
    if harness == "opencode":
        oc_bin = os.environ.get("OPENCODE_BIN", "opencode")
        try:
            oc = sh(
                [oc_bin, "run", "--pure", "-m", model, brief],
                cwd=tree,
                env=env,
                timeout=int(os.environ.get("SWARM_DISPATCH_TIMEOUT", "600")),
            )
            rc, err = oc.returncode, oc.stderr or ""
        except subprocess.TimeoutExpired:
            rc, err = 124, ""
    else:  # unreachable: the seat preflight gates the harness value
        raise ValueError(f"unknown dispatch harness: {harness}")
    return rc, round(time.monotonic() - t0, 2), err


def _push_dispatch_started(origin: str, task: str, att: str,
                           iso: str) -> str | None:
    """L5.1 checkpoint: push a `dispatch_started: <iso>` marker onto the claim
    ref so a dispatch death (or a crash mid-dispatch) is legible from the refs
    alone. Reads the current claim body, inserts the whitespace-free ISO token
    BEFORE the att (the att stays the last body token, so the four att-readers
    still recover it), and CAS-pushes with force-with-lease. Best-effort: any
    failure proceeds (the dispatch still runs) and returns None. The fresh
    committer ts also resets the claim's lease age to now — a bounded run is
    sweep-proof."""
    ref = f"refs/swarm/claims/{task}"
    raw = origin_raw(origin, ref)
    if not raw:
        return None
    body = raw.partition("\n\n")[2] if raw.startswith("tree ") else raw
    lines = body.rstrip("\n").split("\n")
    if not lines or lines[-1].strip() != att:
        return None  # the att must be last; an unexpected body is skipped
    cur = (remote(ref, origin=origin).stdout or "").strip()
    cur_sha = cur.split()[0] if cur else ""
    if not cur_sha:
        return None
    lines.insert(len(lines) - 1, f"dispatch_started: {iso}")
    c_sha, c_proc = commit_tree(empty_tree(), "-m", "\n".join(lines))
    if not ok(c_proc):
        return None
    # update-CAS: lease against the sha we just read (a concurrent change
    # fails the push -> the best-effort checkpoint is skipped, dispatch runs)
    r = push_sha_ref(c_sha, ref, origin=origin, expected=cur_sha)
    return iso if ok(r) else None


_MODEL_KIND_CACHE: dict = {}


def _classify_kind_from_config(cfg, model: str) -> str:
    """'chain' if the requested alias maps to >1 backend (a litellm
    fallback/model group), 'direct' if exactly one, 'unknown' if the alias
    is absent or the payload shape is unrecognized. The litellm /model_info
    payload is {"model_list": [{"model_name":..., "litellm_params": {...}},
    ...]}; a group is >1 entry sharing the same model_name."""
    ml = cfg.get("model_list") if isinstance(cfg, dict) else None
    if not isinstance(ml, list):
        return "unknown"
    backends = [e for e in ml if isinstance(e, dict) and e.get("model_name") == model]
    if not backends:
        return "unknown"
    return "chain" if len(backends) > 1 else "direct"


def _query_router_kind(model: str) -> str:
    """Fetch the router's litellm config and classify the model. Best-effort
    + bounded (2s): any timeout/error -> 'unknown'. SWARM_ROUTER_URL (default
    the 127.0.0.1:4000 tunnel); an optional SWARM_ROUTER_KEY is sent as a
    Bearer header. Most worker seats lack the tunnel and degrade to
    'unknown' fast (connection refused)."""
    url = os.environ.get("SWARM_ROUTER_URL", "http://127.0.0.1:4000")
    req = urllib.request.Request(url + "/model_info")
    key = os.environ.get("SWARM_ROUTER_KEY", "").strip()
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return _classify_kind_from_config(json.load(r), model)
    except Exception:
        return "unknown"


def _model_kind(model: str) -> str:
    """The honest chain/direct flag for a requested model. Source, in order:
    (1) operator env SWARM_MODEL_KIND (chain|direct) — an explicit assertion,
    used without touching the router; (2) the router's litellm config, fetched
    best-effort under a 2s bound. Cached per distinct model for the worker's
    life (a worker handling many claims under one router pays one query, not
    one per claim). Never gates the dispatch or grows an attempt: it is a
    checkpoint annotation, best-effort by design."""
    if model in _MODEL_KIND_CACHE:
        return _MODEL_KIND_CACHE[model]
    k = os.environ.get("SWARM_MODEL_KIND", "").strip().lower()
    kind = k if k in ("chain", "direct") else _query_router_kind(model)
    _MODEL_KIND_CACHE[model] = kind
    return kind


def verdict_body(
    task: str, att: str, fixed: bool, host: str, oc_rc: int, pytest_rc: int
) -> str:
    """Verdict commit message. oc_rc carries the dispatch exit code so the
    error table applies downstream (124=timeout → environmental/requeue,
    model-death → requeue+exit, 0 with pytest fail → merit): the refs
    alone must separate environmental deaths from honest merit failures
    without hunting the worker's stdout. Carries the dispatched model as a
    structured `model:` field (the correction graph reads it from here)."""
    model = _swarm_model()
    model_line = (
        f"\nmodel: {model}\nkind: {_model_kind(model)}" if model.strip() else ""
    )
    return (
        f"verdict\ntask: {task}\nattempt: {att}\n"
        f"fixed: {'true' if fixed else 'false'}\nhost: {host}\n"
        f"oc_rc: {oc_rc}\npytest_rc: {pytest_rc}\n"
        f"harness: {_dispatch_harness()}" + model_line
    )


# --------------------------------------------------------------- seed


def seed(spec_path: str, origin=None) -> dict:
    """Push refs/swarm/specs/<task> = root commit carrying the brief."""
    origin = resolve_origin(origin)
    spec = json.load(open(spec_path))
    et = empty_tree()
    R = {}
    for task, brief in sorted(spec.items()):
        if not task_name_is_safe(task):
            R[task] = "refused: " + _BAD_TASK_MSG
            continue
        c_sha, c_proc = commit_tree(et, "-m", f"spec {task}\n\n{brief}")
        r = push_sha_ref(c_sha, f"refs/swarm/specs/{task}",
                         lease=False, failed=c_proc, origin=origin)
        R[task] = ok(r)
    if metrics.enabled():
        # the DORA lead-time anchor (map: 'extend the join to the seed
        # event'); refused specs are data too — ok carries the push result
        for task, r in R.items():
            metrics.emit("seed", task=task, ok=(r is True))
    print(json.dumps({"event": "seeded", "tasks": R}, indent=1))
    return R


# -------------------------------------------------------------- claim


def claim_detail(worker: str, task: str, origin=None) -> dict:
    """Create-once CAS claim with the failure classified. att is None on
    any failure; rc/stderr let the caller tell a lost race (rc 1,
    `! [rejected] … (stale info)` — healthy contention, re-scan) from a
    structurally broken environment (rc 128 not-a-repo / unreachable …
    — the same honesty the scan side owes)."""
    origin = resolve_origin(origin)
    att = f"att-{worker}-{os.urandom(3).hex()}"
    if not task_name_is_safe(task):
        # Defense in depth: classify_claim_failure maps rc 128 to
        # 'structural', so the worker stops honestly instead of spinning.
        if metrics.enabled():
            metrics.emit("claim", task=task, worker=worker, att=None, rc=128,
                         outcome=classify_claim_failure(128, _BAD_TASK_MSG))
        return {"att": None, "rc": 128, "stderr": _BAD_TASK_MSG}
    # The dispatched model is recorded on the claim body (at claim time —
    # the pre-dispatch checkpoint) so a dispatch death still carries it.
    # `model:` precedes the att: the att stays the LAST body token, which
    # every att reader (sweep/relabel/graph/audit) extracts via split()[-1].
    model = _swarm_model()
    kind = _model_kind(model) if model.strip() else "unknown"
    h = _dispatch_harness()
    c_body = f"claim {task}\nharness: {h}\n{att}"
    if model.strip():
        c_body = f"claim {task}\nharness: {h}\nmodel: {model}\nkind: {kind}\n{att}"
    c_sha, c_proc = commit_tree(empty_tree(), "-m", c_body)
    lease = f"--force-with-lease=refs/swarm/claims/{task}:"
    r = push_sha_ref(c_sha, f"refs/swarm/claims/{task}", failed=c_proc,
                     origin=origin)
    res = {"att": att if ok(r) else None, "rc": r.returncode,
           "stderr": r.stderr or ""}
    # metrics (observe-only): the attempt AND its CAS verdict
    # (won / race = duplicate-rejection / structural) are data; recorded
    # only after the lane's own result exists, which it never changes.
    if metrics.enabled():
        outcome = (
            "won" if res["att"]
            else classify_claim_failure(res["rc"], res["stderr"])
        )
        metrics.emit(
            "claim", task=task, worker=worker, att=res["att"], rc=res["rc"],
            outcome=outcome,
        )
    return res


def claim(worker: str, task: str) -> str | None:
    """Create-once CAS claim. Returns att id or None (lost/claimed)."""
    return claim_detail(worker, task)["att"]


def classify_claim_failure(rc: int, stderr: str) -> str:
    """'race' = healthy contention (re-scan is correct); 'structural' =
    the environment is broken (a re-scan spins forever). Two measured
    race shapes: `! [rejected] … (stale info)` when the
    ref existed before the push, and `! [remote rejected]` +
    `cannot lock ref … reference already exists` when it lands
    concurrently — the live 2-worker probe caught the second shape
    minutes after the first shipped. Every unclassified failure is
    structural, the safe side."""
    if rc == 1 and (
        "[rejected]" in stderr
        or "[remote rejected]" in stderr
        or "stale info" in stderr
        or "reference already exists" in stderr
    ):
        return "race"
    return "structural"


def open_tasks() -> list:
    """Tasks with a spec but no live claim and no return.

    Raises RuntimeError when the origin is unreachable — 'queue empty'
    and 'cannot see the queue' must not look alike (a worker that
    can't tell exits silently exactly when it's most needed)."""
    r = remote()
    if not ok(r):
        raise RuntimeError(
            f"origin unreachable: {(r.stderr or r.stdout or '').strip()[:200]}"
        )
    have = r.stdout
    specs, claimed, returned = set(), set(), set()
    for ln in have.splitlines():
        ref = ln.split()[1] if ln.strip() else ""
        for bucket, prefix in (
            (specs, "refs/swarm/specs/"),
            (claimed, "refs/swarm/claims/"),
            (returned, "refs/swarm/tasks/"),
        ):
            if ref.startswith(prefix) and "@" not in ref:
                bucket.add(ref[len(prefix) :])
    return sorted(specs - claimed - returned)


# ------------------------------------------------------------- worker


def heirs_count(origin: str, task: str) -> int:
    """Archived dead attempts for a task — each archive/claims ref is one
    environmental death already recorded (by reconcile or self-requeue).

    The read is rc-CHECKED. It feeds the one-heir gate (every requeue
    decision in _work_task); the old unchecked git() returned 0 on an
    unreachable origin — a lie that silently OPENED the heir budget
    (every fresh requeue looks budget-fresh) exactly when the lane
    cannot see its own archive. A reachable empty archive still
    counts 0."""
    r = git("ls-remote", origin, f"refs/swarm/archive/claims/{task}@*")
    if not ok(r):
        raise RuntimeError(
            f"origin unreachable: {(r.stderr or r.stdout or '').strip()[:200]}"
        )
    return sum(1 for ln in r.stdout.splitlines() if ln.strip())


def _heir_gate(task: str) -> tuple[bool, str | None]:
    """The one-heir gate, origin-failure-safe. Returns (budget_fresh,
    error): error non-None means the origin died mid-attempt and the
    gate could not be evaluated — the caller records the death honestly
    (the claim stays live for reconcile's TTL) instead of acting on a
    gate value it does not know, and never crashes into the worker's
    work_task_error catch-all with zero heir accounting."""
    try:
        return (
            heirs_count(resolve_origin(), task)
            < int(os.environ.get("SWARM_HEIR_MAX", "1")),
            None,
        )
    except RuntimeError as e:
        return False, str(e)[:200]


def _guarded_sweep(task: str) -> tuple[dict | None, str | None]:
    """sweep() with the origin-death recorded, never raised:
    the requeue legs run exactly when the substrate is flapping — a
    failure returns (None, error) so the attempt's event stays
    structured (requeued False + sweep_error) instead of crashing."""
    try:
        return sweep(resolve_origin(), task), None
    except RuntimeError as e:
        return None, str(e)[:200]


def keep_tree_enabled() -> bool:
    """FLOCKWORK_KEEP_TREE=1: preserve a merit-failed attempt's
    tree so a failure is auditable — the metrics layer's contract shape
    (env-switched, observe-only; off = byte-identical wire behavior)."""
    return os.environ.get("FLOCKWORK_KEEP_TREE") == "1"


def keep_tree_root() -> str:
    return (
        os.environ.get("FLOCKWORK_KEEP_DIR")
        or os.path.join(os.getcwd(), "kept-attempts")
    )


def keep_attempt_tree(task: str, att: str, tree: str):
    """MOVE a merit-failed attempt tree to the keep dir (never delete —
    the rmtree in work_task's finally becomes a no-op on this path).
    Returns (kept_path, None) on success, (None, error) otherwise; a
    failed keep must never break the attempt (observe-only law). The
    leaf is sanitized to tempfile_tree's alphabet: task names are
    remote data (a '/' is a legal refname component) and the '@' join
    is the archive-marker convention, never user input."""
    safe = lambda s: re.sub(r"[^A-Za-z0-9._-]", "_", s)
    dst = os.path.join(keep_tree_root(), f"{safe(task)}@{safe(att)}")
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        final, n = dst, 0
        while os.path.exists(final):  # a colliding keep never overwrites
            n += 1
            final = f"{dst}.{n}"
        shutil.move(tree, final)
        return final, None
    except OSError as e:
        return None, str(e)[:200]


def work_task(worker: str, task: str, att: str) -> dict:
    """Fix + verify + return + verdict. Only called by the claim winner.

    Owns the attempt's scratch tree: created here, removed when the
    attempt ends (any path) — the tree is never read after a return,
    and an abandoned clone per attempt littered the host. The one
    exception is FLOCKWORK_KEEP_TREE's merit-fail keep: the tree is
    MOVED to the keep dir inside _work_task before the verdict is
    written, so the finally-rmtree below finds nothing and removes
    nothing. The requeue rule lives in _work_task.

    Carries the frozen requeue rule: an environmental death
    (dispatch failed leaving no work in the
    tree — timeout, model/provider death, clone failure) with no heir
    yet ARCHIVES the dead attempt and frees the claim in the same run,
    so the task re-enters open_tasks immediately instead of waiting out
    the reconcile TTL; one heir attempt max, then the honest
    fixed:false verdict is FINAL. Merit failures (dispatch completed,
    oracle failed) were always final."""
    tree = tempfile_tree(task)
    try:
        out = _work_task(worker, task, att, tree)
        # The dispatched model + its chain/direct flag on EVERY attempted
        # event (all emitters return through here) — read at attempt entry,
        # so even a pre-dispatch death reports which model the seat ran.
        model = _swarm_model()
        out["model"] = model
        out["kind"] = _model_kind(model) if model.strip() else "unknown"
        out["harness"] = _dispatch_harness()
        # metrics (observe-only): the attempt's judged outcome
        # (verdict) and any requeue decision (heir) as the event carries
        # them — recorded after the fact, never instead of the return.
        if metrics.enabled():
            metrics.observe_attempt(out)
        return out
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def _work_task(worker: str, task: str, att: str, tree: str) -> dict:
    # ONE origin resolved at attempt entry — clone, spec fetch,
    # main publish, return ref and every verdict push touch exactly it
    # (the import-time split-brain class is dead).
    origin = resolve_origin()
    # The dispatched model (operator-configured via SWARM_MODEL), read at
    # attempt entry for the dispatch leg. work_task stamps model + kind on
    # the returned event for every emitter.
    model = _swarm_model()
    r = git("clone", "-q", origin, tree)
    if not ok(r):
        # The one-heir budget gates the CLONE leg exactly as it gates
        # the spec-death legs and the post-dispatch legs. The old
        # unconditional sweep was an asymmetry: a permanently unclonable
        # task (disk-full/quota on the worker host, a tree carrying a
        # path the local fs rejects, a corrupted object) churned
        # claim→clone-fail→sweep→requeue FOREVER — no TTL backstop (the
        # claim is freed at once, so reconcile never sees a stale claim),
        # no final verdict ever surfacing, one archive ref per cycle.
        # Only the TRANSIENT half of the class self-heals — so the first
        # death still requeues (the environment may heal), and under a
        # spent budget the path writes the honest final fixed:false
        # verdict (oc_rc 1 — no usable dispatch leg, never 0 which reads
        # as merit; pytest_rc plain 1 — the oracle never ran; main +
        # tasks ref untouched per the heir-exhausted law). An origin
        # death MID-CLONE keeps the same structured shape — the gate
        # itself unevaluatable is recorded (claim live for reconcile),
        # never a crash, never the lie-0.
        budget_fresh, gate_err = _heir_gate(task)
        if gate_err is not None:
            # Shape parity: the sibling requeue legs carry
            # requeued/swept/sweep_error keys; this leg's origin-death
            # event lacked them.
            return {
                "event": "attempted",
                "worker": worker,
                "task": task,
                "att": att,
                "env_death": True,
                "reason": f"clone: {r.stderr[:200]}",
                "requeued": False,
                "swept": None,
                "sweep_error": gate_err,
            }
        if budget_fresh:
            ev = {
                "event": "attempted",
                "worker": worker,
                "task": task,
                "att": att,
                "env_death": True,
                "reason": f"clone: {r.stderr[:200]}",
            }
            swept, sweep_err = _guarded_sweep(task)
            if sweep_err is None:
                ev["requeued"] = bool(swept["archived"])
                ev["swept"] = swept["att"] if swept["archived"] else None
            else:
                ev["requeued"] = False
                ev["swept"] = None
                ev["sweep_error"] = sweep_err
            return ev
        # budget spent: ONLY the final verdict is written — the claim
        # stays LIVE (sweeping here would re-open the churn at full
        # speed — claim+verdict archived → the task instantly re-enters
        # open_tasks → claim again, forever; the live claim is exactly
        # what keeps the task out of the queue until reconcile's TTL
        # re-lease, the lane's deliberate slow path), main and the tasks
        # ref stay untouched.
        et = empty_tree()
        v_sha, v_proc = commit_tree(
            et,
            "-m",
            verdict_body(task, att, False, worker, 1, 1),
        )
        r3 = push_sha_ref(v_sha, f"refs/swarm/verdicts/{task}", failed=v_proc, origin=origin)
        return {
            "event": "attempted",
            "worker": worker,
            "task": task,
            "att": att,
            "fixed": False,
            "pytest_rc": 1,
            "oc_rc": 1,
            "oc_err": "",
            "env_death": True,
            "heir_exhausted": True,
            "main_push": False,
            "return_pushed": False,
            "verdict_pushed": ok(r3),
            "requeued": None,
            "swept": None,
            "reason": "clone_heir_exhausted",
        }

    def _env_death_before_dispatch(reason: str, err: str) -> dict:
        """The attempt died between claim and dispatch (clone failure or
        a missing/empty spec). Nothing was spent, so there is no
        verdict, no return, and — unless the heir budget is spent — no
        final judgment: the claim is archived under its true att (the
        sweep law) and the task re-enters the queue.

        The sweep is NOT unconditional. The one-heir law gates here
        exactly as it gates post-dispatch deaths — a PERMANENTLY broken
        spec (an empty brief that nothing repairs) otherwise churns
        claim→death→sweep forever: a fast loop with no TTL, no reconcile
        backstop (the claim is freed at once, so reconcile never sees a
        stale claim), no final verdict ever surfacing, and one archive
        ref added per cycle. First death requeues (the spec may be
        mid-repair); under a spent budget the path writes the honest
        final fixed:false verdict instead — the oracle never ran, so
        pytest_rc stays a plain 1 and oc_rc is 1 (no usable dispatch
        leg — never 0, which reads as merit)."""
        budget_fresh, gate_err = _heir_gate(task)
        if gate_err is not None:
            # The origin died between the spec read and the heir gate —
            # record the death honestly (the claim stays live for
            # reconcile's TTL); never the rc-blind lie-0, never a crash.
            return {
                "event": "attempted",
                "worker": worker,
                "task": task,
                "att": att,
                "env_death": True,
                "reason": reason,
                "err": err[:200],
                "requeued": False,
                "swept": None,
                "sweep_error": gate_err,
            }
        if budget_fresh:
            ev = {
                "event": "attempted",
                "worker": worker,
                "task": task,
                "att": att,
                "env_death": True,
                "reason": reason,
                "err": err[:200],
            }
            swept, sweep_err = _guarded_sweep(task)
            if sweep_err is None:
                ev["requeued"] = bool(swept["archived"])
                ev["swept"] = swept["att"] if swept["archived"] else None
            else:
                ev["requeued"] = False
                ev["sweep_error"] = sweep_err
            return ev
        # budget spent: ONLY the final verdict is written — main and the
        # tasks ref stay untouched (the heir-exhausted law).
        et = empty_tree()
        v_sha, v_proc = commit_tree(
            et,
            "-m",
            verdict_body(task, att, False, worker, 1, 1),
        )
        r3 = push_sha_ref(v_sha, f"refs/swarm/verdicts/{task}", failed=v_proc, origin=origin)
        return {
            "event": "attempted",
            "worker": worker,
            "task": task,
            "att": att,
            "fixed": False,
            "pytest_rc": 1,
            "oc_rc": 1,
            "oc_err": "",
            "env_death": True,
            "heir_exhausted": True,
            "main_push": False,
            "return_pushed": False,
            "verdict_pushed": ok(r3),
            "requeued": None,
            "swept": None,
            "reason": f"{reason}_heir_exhausted",
            # The typed refusal detail rides the FINAL event too
            # (a schema refusal that exhausts the heir budget must still
            # name the missing field where the story ends, not only in
            # the requeued first attempt).
            "err": err[:200],
        }

    git("-C", tree, "config", "user.email", f"{worker}@swarm")
    git("-C", tree, "config", "user.name", worker)
    # fetch the spec ref, read the brief (verify: line = task oracle)
    # The fetch was UNCHECKED. A missing or unreadable spec ref (a
    # lost-ref transient — the same class documented for return refs,
    # or origin-host surgery like a plain `update-ref -d`; a spec body
    # that is empty) left brief="" and the attempt DISPATCHED ON AN
    # EMPTY BRIEF — a real backend leg (a real model session, an
    # opencode run) on nothing — and then judged whatever the tree
    # happened to do: the measured falsifier saw the default pytest
    # oracle's cache artifacts become a diff, get committed, and
    # PUBLISH to main with a green verdict (fixed:true) for an attempt
    # that never saw its task — the substrate-lie family through a new
    # route. An attempt without its brief is an ENVIRONMENTAL DEATH
    # BEFORE DISPATCH: no dispatch, no oracle, no verdict, no return —
    # the claim is archived under its true att (the sweep law) and the
    # task re-enters open_tasks when its spec is healthy again. No heir
    # cost (the clone-failure shape: the attempt died before anything
    # was spent).
    fr = git("-C", tree, "fetch", "-q", origin, f"refs/swarm/specs/{task}")
    if not ok(fr):
        return _env_death_before_dispatch(
            "spec-fetch-failed", fr.stderr.strip()
        )
    brief = git("-C", tree, "log", "-1", "--format=%B", "FETCH_HEAD").stdout.strip()
    if not brief:
        return _env_death_before_dispatch(
            "spec-empty",
            "spec ref fetched but its brief body is empty — "
            "nothing to dispatch on",
        )
    # The schema IS the instruction: a spec the schema refuses is an
    # environmental death BEFORE dispatch, the empty-spec class finer
    # grained — zero work spent on a seed no worker can act on, the
    # refusal typed with the missing field's name, the heir gate
    # keeping it from churning forever. A lone-law copy without the
    # schema sibling skips this entirely.
    if refschema is not None:
        schema_errs = refschema.validate_spec(brief)
        if schema_errs:
            return _env_death_before_dispatch(
                "spec-schema-refused",
                "; ".join(schema_errs),
            )
    verify = next(
        (
            ln[len("verify:") :].strip()
            for ln in brief.splitlines()
            if ln.startswith("verify:")
        ),
        None,
    )
    # real work: dispatch the brief to the model via the seam. The harness,
    # model, and binary are operator-configured (SWARM_DISPATCH / SWARM_MODEL
    # / OPENCODE_BIN); SWARM_DISPATCH_TIMEOUT bounds the dispatch leg.
    harness = _dispatch_harness()
    # L5.1 checkpoint: mark the dispatch start on the claim ref (best-effort
    # CAS) + emit the metrics event (gated). The fresh claim committer ts
    # resets the lease age to now — a bounded run is sweep-proof.
    iso = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    _push_dispatch_started(origin, task, att, iso)
    if metrics.enabled():
        metrics.emit("dispatch_started", task=task, worker=worker, att=att,
                     harness=harness, model=model, dispatch_started=iso)
    # oc_rc: dispatch exit code; oc_elapsed: wall-clock of the dispatch leg
    # (the bound when it timed out); oc_stderr: captured at the source so a
    # timeout (no CompletedProcess) still yields a clean empty tail.
    oc_rc, oc_elapsed, oc_stderr = _dispatch(harness, model, brief, tree)
    # The classification, BEFORE any ref is written: an environmental
    # death is a dispatch that failed leaving no work in the tree
    # (124=timeout counts even with partial work — partial orphan work
    # was requeued too). oc_rc!=0 with a populated tree is recorded
    # honestly and stays merit-final: the conservative side of the
    # error table (no requeue storms from ambiguous exits).
    no_changes = git("-C", tree, "status", "--porcelain").stdout.strip() == ""
    env_death = oc_rc == 124 or (oc_rc != 0 and no_changes)
    if env_death:
        budget_fresh, gate_err = _heir_gate(task)
        if gate_err is not None:
            # The origin died mid-attempt (the dispatch leg genuinely
            # ran — oc_rc is real) and the heir gate is unevaluatable.
            # The death is RECORDED with the failure (the claim stays
            # live; reconcile's TTL requeues on heal) — never the
            # rc-blind lie-0 that silently OPENED the heir budget, and
            # never a crash into the worker's catch-all.
            return {
                "event": "attempted",
                "worker": worker,
                "task": task,
                "att": att,
                "oc_rc": oc_rc,
                "oc_elapsed": oc_elapsed,
                "env_death": True,
                "requeued": False,
                "swept": None,
                "sweep_error": gate_err,
            }
        if budget_fresh:
            swept, sweep_err = _guarded_sweep(task)
            ev = {
                "event": "attempted",
                "worker": worker,
                "task": task,
                "att": att,
                "oc_rc": oc_rc,
                "oc_elapsed": oc_elapsed,
                "env_death": True,
            }
            if sweep_err is None:
                ev["requeued"] = bool(swept["archived"])
                ev["swept"] = swept["att"] if swept["archived"] else None
            else:
                ev["requeued"] = False
                ev["swept"] = None
                ev["sweep_error"] = sweep_err
            return ev
    if env_death:
        # The requeue law, second half: with the heir budget spent, an
        # environmental death is never a fix — and never a commit
        # either. This path used to fall through and publish an empty
        # --allow-empty "fix" to main and point refs/swarm/tasks at it
        # (measured live). The oracle is not run at all here — the
        # dispatch never worked — so pytest_rc is recorded as a plain 1
        # (not a pass), never as a measured result. ONLY the final
        # verdict is written; main and the tasks ref stay untouched.
        et = empty_tree()
        v_sha, v_proc = commit_tree(
            et,
            "-m",
            verdict_body(task, att, False, worker, oc_rc, 1),
        )
        r3 = push_sha_ref(v_sha, f"refs/swarm/verdicts/{task}", failed=v_proc, origin=origin)
        return {
            "event": "attempted",
            "worker": worker,
            "task": task,
            "att": att,
            "fixed": False,
            "pytest_rc": 1,
            "oc_rc": oc_rc,
            "oc_elapsed": oc_elapsed,
            "oc_err": "",
            "env_death": True,
            "heir_exhausted": True,
            "main_push": False,
            "return_pushed": False,
            "verdict_pushed": ok(r3),
            "requeued": None,
            "swept": None,
            "reason": "heir_exhausted_env_death",
        }
    # L4 oracle: task-specified verify command, else host pytest in the
    # task dir (first dir under tasks/). bash -lc so the host's pyenv
    # pytest resolves. The oracle leg carries its own bound —
    # SWARM_ORACLE_TIMEOUT (default 600s) — enforced with coreutils
    # `timeout` around the verify command, so a hung verify (a network
    # wait, an interactive prompt, an infinite loop in the fixed code)
    # degrades to an honest measured result (rc 124) instead of sitting
    # the worker down for the full floor or leaking a TimeoutExpired out
    # of _work_task into the catch-all, which would misclassify a
    # completed dispatch as a failed one.
    oracle_timeout = int(os.environ.get("SWARM_ORACLE_TIMEOUT", "600"))
    if verify:
        pr = sh(["timeout", str(oracle_timeout), "bash", "-lc", verify], cwd=tree)
    else:
        tdir = os.path.join(tree, "tasks", task)
        if not os.path.isdir(tdir):
            tdir = tree
        pr = sh(
            ["timeout", str(oracle_timeout), sys.executable, "-m", "pytest", "-q"],
            cwd=tdir,
        )
    # rc 124 is coreutils `timeout`'s verdict on the leg's own bound: the
    # verify command died at the oracle timeout, not a measured exit. The
    # flag marks exactly that shape (present only there — healthy rows
    # carry no oracle_timeout key).
    oracle_timed_out = pr.returncode == 124
    # an environmental death is never a fix, whatever the oracle says
    # (heir-exhausted path: the oracle may pass on an unchanged tree)
    fixed = pr.returncode == 0 and not env_death
    if not fixed:
        # A change that FAILS its oracle is never a fix and never
        # lands. Publishing used to be unconditional — a merit failure
        # add'ed + committed + pushed its tree and THEN wrote
        # fixed:false, so main could carry changes no verdict claims
        # (the inverse of the empty commit found live on the env-death
        # path). Merit failure is FINAL: main and refs/swarm/tasks
        # stay untouched, ONLY the honest verdict is written, with the
        # real pytest_rc. With FLOCKWORK_KEEP_TREE=1 the tree is moved
        # to the keep dir BEFORE the verdict so both the event and the
        # verdict body can carry the kept path — the dispatch RAN here
        # (env-death legs all returned above), which is exactly the
        # artifact worth auditing. A failed keep changes nothing: the
        # finally-rmtree still cleans up, the verdict stays honest.
        kept, kept_err = (None, None)
        if keep_tree_enabled():
            kept, kept_err = keep_attempt_tree(task, att, tree)
        v_body = verdict_body(task, att, False, worker, oc_rc, pr.returncode)
        if kept:
            v_body += f"\nkept: {kept}"
        et = empty_tree()
        v_sha, v_proc = commit_tree(et, "-m", v_body)
        r3 = push_sha_ref(v_sha, f"refs/swarm/verdicts/{task}", failed=v_proc, origin=origin)
        out = {
            "event": "attempted",
            "worker": worker,
            "task": task,
            "att": att,
            "fixed": False,
            "pytest_rc": pr.returncode,
            "oc_rc": oc_rc,
            "oc_elapsed": oc_elapsed,
            "oc_err": "",
            "env_death": False,
            "heir_exhausted": False,
            "main_push": False,
            "return_pushed": False,
            "verdict_pushed": ok(r3),
            "requeued": None,
            "swept": None,
            "reason": "merit_failure_no_publish",
        }
        if kept:
            out["kept"] = kept
        if kept_err:
            out["kept_error"] = kept_err
        if oracle_timed_out:
            out["oracle_timeout"] = True
        return out
    # return commit on main lineage with the att trailer; concurrent
    # workers push main too — on non-FF, rebase onto origin and retry
    # (reached only when the oracle PASSED — the publish is earned)
    git("-C", tree, "add", "-A")
    # The oracle may PASS on an unchanged tree. The rediscovered SETTLED
    # task shape (fixed:true verdict whose return ref was lost;
    # reconcile's TTL re-lease freed the claim) re-runs with the fix
    # ALREADY on main, and any oracle-true no-op attempt lands here too.
    # The old unconditional --allow-empty commit published a NEW empty
    # "fix" commit to main: the substrate-lie class (main claiming a
    # fix the tree never had) reopened through the re-lease route.
    # Never CREATE a commit in this shape: main already satisfies the
    # task's success criterion, so the return ref is pointed at main's
    # tip and the verdict records the attempt honestly.
    # The tip comes from the attempt's OWN clone
    # (refs/remotes/origin/main, fetched at clone time — the sha comes
    # from the worker's own records, never a fresh remote read). The
    # first cut ls-remote'd main and pushed that sha from the clone,
    # which never fetched it: when main advanced during the attempt (a
    # concurrent rediscoverer), the return push failed (`not our ref`),
    # return_pushed came back false for work that IS on main, and the
    # settled task re-entered the re-lease treadmill. The clone's view
    # is deterministic, locally resolvable, and the honest "as verified
    # by this attempt" tip.
    no_diff = git("-C", tree, "status", "--porcelain").stdout.strip() == ""
    tip = None
    if no_diff:
        r1 = subprocess.CompletedProcess(
            args=["git", "push"], returncode=0, stdout="",
            stderr="(no-diff oracle pass) main untouched; no commit created",
        )
        rv = git("-C", tree, "rev-parse", "refs/remotes/origin/main")
        cand = rv.stdout.strip()
        if ok(rv) and cand:
            tip = cand
    else:
        git(
            "-C",
            tree,
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            f"fix {task}\n\nAttempt: {att}",
        )
        r1 = git("-C", tree, "push", "-q", origin, "HEAD:main")
    if not ok(r1):
        rr = git("-C", tree, "pull", "-q", "--rebase", origin, "main")
        if ok(rr):
            r1 = git("-C", tree, "push", "-q", origin, "HEAD:main")
    if not ok(r1):
        # the lane's product is main: a fix that never landed is never
        # a fix, whatever the oracle says (the collision was seen
        # live; one rebase retry covers one collision, not two).
        # Requeue (heir bounded) instead of pushing a fixed:true
        # verdict the substrate contradicts.
        budget_fresh, gate_err = _heir_gate(task)
        if gate_err is not None:
            # The origin died between the dispatch and the return
            # push — the same honest record (claim stays live for
            # reconcile's TTL), never a crash with no accounting.
            return {
                "event": "attempted",
                "worker": worker,
                "task": task,
                "att": att,
                "fixed": False,
                "pytest_rc": pr.returncode,
                "oc_rc": oc_rc,
                "oc_elapsed": oc_elapsed,
                "oc_err": "",
                "env_death": False,
                "heir_exhausted": False,
                "main_push": False,
                "return_pushed": False,
                "verdict_pushed": False,
                "requeued": False,
                "swept": None,
                "sweep_error": gate_err,
                "reason": "main_push_rejected",
            }
        if budget_fresh:
            swept, sweep_err = _guarded_sweep(task)
            ev = {
                "event": "attempted",
                "worker": worker,
                "task": task,
                "att": att,
                "fixed": False,
                "pytest_rc": pr.returncode,
                "oc_rc": oc_rc,
                "oc_elapsed": oc_elapsed,
                "oc_err": "",
                "env_death": False,
                "heir_exhausted": False,
                "main_push": False,
                "return_pushed": False,
                "verdict_pushed": False,
            }
            if sweep_err is None:
                ev["requeued"] = bool(swept["archived"])
                ev["swept"] = swept["att"] if swept["archived"] else None
            else:
                ev["requeued"] = False
                ev["swept"] = None
                ev["sweep_error"] = sweep_err
            ev["reason"] = "main_push_rejected"
            return ev
        fixed = False  # heirs exhausted: final honest verdict below
    if fixed:
        lease = f"--force-with-lease=refs/swarm/tasks/{task}:"
        if no_diff and not tip:
            # no resolvable main tip in the attempt's view (origin main
            # absent/unborn at clone): withhold the return ref — never
            # an empty-refspec push (the phantom-deletion class
            # push_sha_ref guards).
            r2 = subprocess.CompletedProcess(
                args=["git", "push"], returncode=128, stdout="",
                stderr="no main tip on origin: return ref withheld",
            )
        else:
            src = tip if tip is not None else "HEAD"
            r2 = git(
                "-C", tree, "push", "-q", lease, origin,
                f"{src}:refs/swarm/tasks/{task}",
            )
    else:
        # Heirs exhausted on a rejected main push → ONLY the final
        # honest verdict. The tasks ref is a RETURN: advancing it with
        # content main contradicts beside a fixed:false verdict is the
        # substrate-lie shape the merit-fail and heir-exhausted
        # env-death paths never commit. The live claim keeps the task
        # out of open_tasks; reconcile's TTL may re-lease it later —
        # oracle-passing work legitimately retries on a fresh lease.
        r2 = None
    # L1 verdict: ROOT commit
    et = empty_tree()
    v_sha, v_proc = commit_tree(
        et,
        "-m", verdict_body(task, att, fixed, worker, oc_rc, pr.returncode)
    )
    r3 = push_sha_ref(v_sha, f"refs/swarm/verdicts/{task}", failed=v_proc, origin=origin)
    # oc_err: the dispatch stderr tail on a non-zero rc (captured at the
    # dispatch seam; a timeout yields the clean empty tail).
    oc_err = (oc_stderr or "")[-200:] if oc_rc else ""
    out = {
        "event": "attempted",
        "worker": worker,
        "task": task,
        "att": att,
        "fixed": fixed,
        "pytest_rc": pr.returncode,
        "oc_rc": oc_rc,
        "oc_elapsed": oc_elapsed,
        "oc_err": oc_err,
        "env_death": env_death,
        "heir_exhausted": env_death,
        "main_push": ok(r1) and not no_diff,
        "already_on_main": no_diff,
        "return_pushed": r2 is not None and ok(r2),
        "verdict_pushed": ok(r3),
    }
    if not fixed:
        out["reason"] = "main_push_rejected_heir_exhausted"
    return out


def tempfile_tree(task):
    import tempfile

    # task names are REMOTE DATA (the claim-CAS namespace accepts any
    # legal refname): a name carrying '/' would point mkdtemp under a
    # nonexistent directory (found live — sweep crashed on exactly that
    # shape, the local twin of the ssh-shell-injection class).
    # Sanitize to the refname-safe alphabet; collisions are
    # impossible anyway (mkdtemp appends random chars).
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", task)[:64]
    return tempfile.mkdtemp(prefix=f"inrepo-{safe}-")


def worker(name: str, only: list | None = None) -> bool:
    """Claim-fix-verify loop. Returns True when it gave up on an
    unreachable origin (SWARM_WORKER_MAX_FAILS consecutive scans,
    default 3) — a supervised worker must exit honestly distinct from
    'queue empty, done'. Transient per-task failures are recorded and
    the loop continues; the failed claim is freed best-effort."""
    # seat preflight (L1): a worker with no dispatched model refuses before
    # it touches the origin — no claim consumed, no stale default dispatched.
    if _model_preflight(name):
        return True  # honest distinct exit (CLI: exit 1)
    done = 0
    fails = 0
    claim_fails = 0
    max_fails = int(os.environ.get("SWARM_WORKER_MAX_FAILS", "3"))
    origin = resolve_origin()
    while True:
        try:
            tasks = [t for t in open_tasks() if not only or t in only]
        except RuntimeError as e:
            fails += 1
            print(
                json.dumps(
                    {
                        "event": "origin_unreachable",
                        "worker": name,
                        "fails": fails,
                        "err": str(e)[:200],
                    }
                ),
                flush=True,
            )
            if fails >= max_fails:
                print(
                    json.dumps(
                        {
                            "event": "worker_gave_up",
                            "worker": name,
                            "completed": done,
                            "reason": "origin",
                        }
                    ),
                    flush=True,
                )
                return True
            time.sleep(2)
            continue
        fails = 0
        if not tasks:
            break
        task = tasks[0]
        # law-freshness gate: judge with the substrate's law or not at
        # all (a stale law recorded a false fixed:true; the claim CAS
        # cannot see code versions)
        refused = law_gate(origin, name, task)
        if refused is not None:
            print(json.dumps(refused), flush=True)
            if refused["event"] == "law_freshness_refusal":
                return True  # honest distinct exit (CLI: exit 1)
        res = claim_detail(name, task)
        if res["att"] is None:
            if classify_claim_failure(res["rc"], res["stderr"]) == "race":
                time.sleep(1)  # lost race; re-scan
                continue
            # structural (not a repo cwd, origin died mid-flight, …):
            # re-scanning cannot heal it — same contract as the scan
            # side, the worker stops honestly instead of spinning
            claim_fails += 1
            print(
                json.dumps(
                    {
                        "event": "claim_failed",
                        "worker": name,
                        "task": task,
                        "rc": res["rc"],
                        "err": res["stderr"].strip()[:200],
                        "claim_fails": claim_fails,
                    }
                ),
                flush=True,
            )
            if claim_fails >= max_fails:
                print(
                    json.dumps(
                        {
                            "event": "worker_gave_up",
                            "worker": name,
                            "completed": done,
                            "reason": "claim",
                        }
                    ),
                    flush=True,
                )
                return True
            time.sleep(1)
            continue
        att = res["att"]
        claim_fails = 0  # a healthy claim: the environment works again
        try:
            out = work_task(name, task, att)
        except Exception as e:  # noqa: BLE001 — an attempt must never
            # kill the loop holding its claim; free it best-effort
            out = {
                "event": "work_task_error",
                "worker": name,
                "task": task,
                "att": att,
                "err": repr(e)[:200],
            }
            try:
                sweep(origin, task)
                out["claim_freed"] = True
            except Exception:  # noqa: BLE001
                out["claim_freed"] = False
            if metrics.enabled():
                metrics.emit("crash", task=task, worker=name, att=att,
                             err=repr(e)[:200],
                             claim_freed=out.get("claim_freed"))
        done += 1
        print(json.dumps(out), flush=True)
    print(
        json.dumps({"event": "worker_done", "worker": name, "completed": done}),
        flush=True,
    )
    return False


# -------------------------------------------------------------- sweep


def require_remote(origin: str) -> dict:
    """ls-remote with the rc CHECKED. An unreachable origin RAISES —
    'queue empty' and 'cannot see the queue' must not look alike
    (open_tasks' law, extended to every automation read). Pre-fix,
    sweep/reconcile/correction_graph /relabel_orphan_archives parsed
    stdout without the rc: a dead origin yielded an empty dict and each
    returned a SUCCESS-SHAPED EMPTY result — sweep a silent no-op,
    reconcile a clean empty census, correction_graph an empty graph
    (decision-grade: the frozen rule reads 'FALSIFIED iff all chains
    single-vertex', so a network blip during the two-model window would
    have graded the substrate falsified), relabel a quiet no-op.
    Callers that genuinely tolerate an absent origin call git() directly
    and classify the failure themselves (origin_body's degraded-read
    law)."""
    r = git("ls-remote", origin)
    if not ok(r):
        raise RuntimeError(
            f"origin unreachable: {(r.stderr or r.stdout or '').strip()[:200]}"
        )
    have = {}
    for ln in r.stdout.splitlines():
        if ln.strip():
            sha, ref = ln.split()
            have[ref] = sha
    return have


def sweep(origin: str, task: str, att: str | None = None) -> dict:
    """Archive every live refs/swarm/{claims,tasks,verdicts}/<task> to
    refs/swarm/archive/<kind>/<task>@<att> and delete the live refs in
    ONE transaction (push --atomic; the L3 @-marker archive law).
    att defaults to the claim commit body's last token. Idempotent:
    nothing live -> empty lists, no refs written. Raises RuntimeError
    on an unreachable origin — never a success-shaped empty result."""
    have = require_remote(origin)
    live = [
        (kind, ref, have[ref])
        for kind in ("claims", "tasks", "verdicts")
        for ref in (f"refs/swarm/{kind}/{task}",)
        if ref in have and "@" not in ref
    ]
    if not live:
        return {"task": task, "att": att, "archived": [], "deleted": []}
    if att is None:
        # An archive marker must embed the true att. When the claim IS
        # live the att is recoverable in principle — read it, retry once
        # for a transient origin-host read failure, and REFUSE to
        # archive under a degraded marker (the same push would delete the
        # refs that carry it). Only the genuinely claim-less orphan
        # shape keeps @unknown: refusing there would leave zombie refs
        # blocking the task forever, and relabel_orphan_archives can
        # repair the marker later if the att ever surfaces.
        cref = f"refs/swarm/claims/{task}"
        att = "unknown"
        if cref in have:
            for _ in (1, 2):
                body = origin_body(origin, cref)
                cand = body.strip().split()[-1] if body.strip() else ""
                if cand.startswith("att-"):
                    att = cand
                    break
            if att == "unknown":
                raise RuntimeError(
                    f"sweep refused: claim {cref} is live but its body "
                    "yields no att-* token — archiving would delete the "
                    "live refs under a degraded marker"
                )
    refspecs = []
    for kind, ref, sha in live:
        refspecs.append(f":{ref}")
        refspecs.append(f"{sha}:refs/swarm/archive/{kind}/{task}@{att}")
    # push resolves <sha> in the SOURCE repo — sweep may run from a
    # checkout without the swarm objects, so push through a scratch
    # lens that fetched exactly the live refs (ssh:// and path origins
    # both work; the archive+delete itself stays ONE atomic push)
    scratch = tempfile_tree(f"sweep-{task}")
    try:
        git("init", "-q", "--bare", scratch)
        fr = git(
            "fetch",
            "-q",
            origin,
            *[f"+{ref}:{ref}" for _, ref, _ in live],
            cwd=scratch,
        )
        if not ok(fr):
            raise RuntimeError(f"sweep fetch failed: {fr.stderr.strip()[:300]}")
        r = git("push", "--atomic", "-q", origin, *refspecs, cwd=scratch)
        if not ok(r):
            raise RuntimeError(f"sweep push failed: {r.stderr.strip()[:300]}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    # metrics (observe-only): an eviction is what just happened
    # (archived dead attempts + freed live refs), recorded post-transaction.
    if metrics.enabled():
        metrics.emit("eviction", task=task, att=att,
                     archived=len(live), deleted=len(live))
    return {
        "task": task,
        "att": att,
        "archived": [f"refs/swarm/archive/{k}/{task}@{att}" for k, _, _ in live],
        "deleted": [ref for _, ref, _ in live],
    }


# ---------------------------------------------------------- reconcile


def reconcile(origin: str, ttl_s: float | None = None) -> dict:
    """Detect and recover stale claims: the automation layer over sweep().

    Early on, six environmental deaths were recovered by manual sweep;
    this closes that gap. A task with a live claim whose return+verdict
    are not both live is INCOMPLETE; if its claim commit is older than
    ttl_s (lease expiry — must exceed the worst-case dispatch+verify
    wall), sweep() archives+frees it so the task re-enters open_tasks.

    Safety is by construction, not by timing: return/verdict refs are
    create-once CAS (L2), so even a mis-timed sweep of a still-running
    worker's claim cannot produce two verdicts — the loser's pushes are
    rejected; the cost is one duplicate attempt, never a double write.

    Never touched: healthy tasks (claim+return+verdict live), fresh
    claims (age <= ttl), and shapes that should not exist (return or
    verdict with no live claim) or cannot be dated (undateable claim
    object) — the latter two are flagged as anomalies for the auditor.
    """
    if ttl_s is None:
        ttl_s = float(os.environ.get("SWARM_LEASE_TTL", "1800"))
    have = require_remote(origin)
    tasks = sorted(
        r[len("refs/swarm/claims/") :]
        for r in have
        if r.startswith("refs/swarm/claims/") and "@" not in r
    )
    now = time.time()
    out = {
        "origin": origin,
        "ttl_s": ttl_s,
        "stale": [],
        "swept": [],
        "fresh": [],
        "healthy": [],
        "anomalies": [],
        "errors": [],
    }
    for t in tasks:
        t_ref = f"refs/swarm/tasks/{t}"
        v_ref = f"refs/swarm/verdicts/{t}"
        has_t, has_v = t_ref in have, v_ref in have
        if has_t and has_v:
            out["healthy"].append(t)
            continue
        # The claim is board-visible (the require_remote census listed
        # it), so a failed object read is a broken board, never
        # "undateable". The strict read raises and the failure lands in
        # `errors` (retry-able — the next healthy reconcile sweeps)
        # instead of `anomalies` (the permanent misclassification that
        # silenced the lane's only TTL backstop while reads failed). A
        # genuinely malformed object (read succeeds, no committer line)
        # stays the honest anomaly.
        try:
            ts = _committer_ts_from_raw(
                _origin_raw_strict(origin, f"refs/swarm/claims/{t}")
            )
        except RuntimeError as e:
            out["errors"].append({"task": t, "err": str(e)[:200]})
            continue
        if ts is None:
            out["anomalies"].append(
                {"task": t, "reason": "claim object undateable; not swept"}
            )
            continue
        age = now - ts
        if age <= ttl_s:
            out["fresh"].append(t)
            continue
        shape = "+".join(
            k for k, present in (("claim", True), ("return", has_t), ("verdict", has_v)) if present
        )
        out["stale"].append({"task": t, "age_s": round(age, 1), "shape": shape})
        try:
            res = sweep(origin, t)
            out["swept"].append(res["att"])
            if metrics.enabled():
                metrics.emit("lease_expired", task=t, age_s=round(age, 1),
                             swept=res["att"])
        except RuntimeError as e:
            out["errors"].append({"task": t, "err": str(e)[:200]})
            if metrics.enabled():
                metrics.emit("lease_expired", task=t, age_s=round(age, 1),
                             swept=None, error=str(e)[:200])
    # orphan return/verdict refs (no live claim) — flag, never touch
    for ref in have:
        for kind in ("tasks", "verdicts"):
            prefix = f"refs/swarm/{kind}/"
            if ref.startswith(prefix) and "@" not in ref:
                t = ref[len(prefix) :]
                if f"refs/swarm/claims/{t}" not in have:
                    out["anomalies"].append(
                        {"task": t, "reason": f"live {kind} ref with no live claim"}
                    )
    return out


# ---------------------------------------------------------- divergence


def divergence(origin: str | None = None, cwd=None) -> dict:
    """Node-vs-substrate law: a node that commits to its local main
    while the substrate moved elsewhere is invisible until someone
    compares — one node ran 8 local commits ahead unnoticed.
    Read-only comparison: local main vs origin main by sha (ls-remote,
    never a fetch). ahead/behind are exact via rev-list when the
    objects are local; otherwise None + a reason that names the fetch —
    never a fabricated count."""
    origin = resolve_origin(origin)
    out = {
        "origin": origin,
        "local_main": None,
        "origin_main": None,
        "ahead": None,
        "behind": None,
        "reason": None,
    }
    r = git("rev-parse", "--verify", "main", cwd=cwd)
    if not ok(r):
        out["reason"] = f"no local main: {r.stderr.strip()[:120]}"
        return out
    out["local_main"] = r.stdout.strip()
    # NOT remote(): that helper reads the env-bound ORIGIN — a divergence
    # check must compare against the ORIGIN IT IS ASKED ABOUT (a unit test
    # with a tmp bare origin must never query the fleet substrate)
    r = git("ls-remote", origin, "main", cwd=cwd)
    if not ok(r) or not r.stdout.strip():
        out["reason"] = f"origin main unreadable: {(r.stderr or r.stdout or '')[:150]}"
        return out
    out["origin_main"] = r.stdout.split()[0]
    if out["local_main"] == out["origin_main"]:
        out["ahead"] = out["behind"] = 0
        return out
    r = git(
        "rev-list", "--left-right", "--count",
        f"{out['local_main']}...{out['origin_main']}", cwd=cwd,
    )
    if not ok(r):
        out["reason"] = (
            "origin commits not in local object store — fetch needed for "
            f"exact counts ({r.stderr.strip()[:120]})"
        )
        return out
    out["ahead"], out["behind"] = (int(x) for x in r.stdout.split())
    return out


# ------------------------------------------------- law freshness gate


LAW_PATH = "l2/inrepo.py"


def law_check(origin: str) -> dict:
    """Is the RUNNING lane law the law the origin publishes?

    Workers have repeatedly judged with stale law (a stale scp-dropped
    copy; a node with no git remote; a stale substrate — the receipt
    was a false fixed:true on an environmental death). The claim CAS
    cannot see code versions, so the check compares the running module
    file's blob sha against origin main's published copy, read on the
    ORIGIN host (the node's object store may not hold origin main).

    status:
      match  — identical bytes, claim allowed
      stale  — the origin publishes different law: refuse
      absent — origin main has no law file (cross-repo lanes: the task
               origin is a satellite; pass-through)
      error  — unreadable for any other reason: refuse (fail closed;
               reachability was already established by the scan)
    """
    origin = resolve_origin(origin)
    out = {
        "origin": origin,
        "law_path": LAW_PATH,
        "local_sha": None,
        "origin_sha": None,
        "status": "error",
        "reason": None,
    }
    r = sh(["git", "hash-object", __file__])
    if not ok(r):
        out["reason"] = f"local law hash failed: {r.stderr.strip()[:120]}"
        return out
    out["local_sha"] = r.stdout.strip()
    tgt = ssh_target(origin)
    try:
        if tgt is not None:
            _host, _port, _path = tgt
            cmd = _ssh_cmd(_host, _port,
                           "git", "-C", _path, "rev-parse", f"main:{LAW_PATH}")
            r = sh(cmd)
        else:
            path = origin
            if origin.startswith("file://"):
                path = urlparse(origin).path
            r = git("rev-parse", f"main:{LAW_PATH}", cwd=path)
    except Exception as e:  # noqa: BLE001 — an unreadable law is a
        # verdict for the gate to act on, never a crash (the scp-form
        # lesson: read failures degrade to classified states)
        out["reason"] = f"{type(e).__name__}: {e}"[:150]
        return out
    if ok(r):
        out["origin_sha"] = r.stdout.strip()
        out["status"] = "match" if out["origin_sha"] == out["local_sha"] else "stale"
        return out
    err = (r.stderr or "").strip()
    # absent law: origin main missing entirely (empty repo: "invalid
    # object name 'main'"), or the law path absent from main ("unknown
    # revision", "path ... does not exist in 'main'") — no law published
    if (
        "invalid object name" in err
        or "unknown revision" in err
        or "Not a valid object name" in err
        or "does not exist in" in err
    ):
        out["status"] = "absent"
        out["reason"] = err[:120]
        return out
    out["reason"] = err[:150] or "origin law read failed"
    return out


def law_gate(origin: str, worker_name: str, task: str) -> dict | None:
    """The claim-moment gate over law_check: None → proceed (match or
    absent); otherwise the event to print before an honest exit 1.
    SWARM_ALLOW_DIVERGED=1 proceeds but must be recorded."""
    law = law_check(origin)
    if law["status"] in ("match", "absent"):
        return None
    event = "law_freshness_bypass"
    ev = {"event": event, "worker": worker_name, "task": task, "law": law}
    if os.environ.get("SWARM_ALLOW_DIVERGED") != "1":
        ev["event"] = "law_freshness_refusal"
        ev["hint"] = (
            "nodes pull, never scp (round-6 f2/f6, 09-19 divergence): "
            "fetch+reset this checkout to origin main and rerun; "
            "SWARM_ALLOW_DIVERGED=1 proceeds and is recorded"
        )
    return ev


# ------------------------------------------------------------- relabel


def relabel_orphan_archives(origin: str) -> dict:
    """Rename archive refs whose @-marker is not att-* to the true att
    recovered from the object's own body (audit finding 4: the marker
    is the ref-level index of an attempt; a body-only att is not
    queryable). Same sha at the new name + delete the old, ONE atomic
    push per ref. Never overwrites an existing target and never guesses
    — a ref with no att-* token in its body is skipped and flagged, so
    a dry-minded caller can rerun relabel after fixing the bodies. An
    unreachable origin raises — never a quiet no-op."""
    have = require_remote(origin)
    out = {"origin": origin, "relabeled": [], "skipped": [], "errors": []}
    for ref, sha in sorted(have.items()):
        m = re.match(r"^(refs/swarm/archive/.+@)(.+)$", ref)
        if not m or m.group(2).startswith("att-"):
            continue
        body = origin_body(origin, ref)
        att = body.strip().split()[-1] if body.strip() else ""
        if not att.startswith("att-"):
            out["skipped"].append(
                {"ref": ref, "reason": "no att-* token recoverable from body"}
            )
            continue
        new_ref = m.group(1) + att
        if new_ref in have:
            out["skipped"].append(
                {"ref": ref, "reason": f"target {new_ref} already exists"}
            )
            continue
        scratch = tempfile_tree(f"relabel-{att}")
        try:
            git("init", "-q", "--bare", scratch)
            fr = git("fetch", "-q", origin, f"+{ref}:{ref}", cwd=scratch)
            if not ok(fr):
                out["errors"].append(
                    {"ref": ref, "err": f"fetch: {fr.stderr.strip()[:150]}"}
                )
                continue
            r = git(
                "push", "--atomic", "-q", origin, f"{sha}:{new_ref}", f":{ref}",
                cwd=scratch,
            )
            if not ok(r):
                out["errors"].append(
                    {"ref": ref, "err": f"push: {r.stderr.strip()[:150]}"}
                )
                continue
            out["relabeled"].append({"from": ref, "to": new_ref, "sha": sha})
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        have[new_ref] = sha
        del have[ref]
    return out


# -------------------------------------------------------------- audit


def ssh_target(origin: str):
    """(host, port, path) for ssh-origin reads, or None for non-ssh.

    git plumbing accepts both `ssh://[user@]host[:port]/path` and the
    scp-form `[user@]host:path` — but origin-host body reads build an
    ssh COMMAND from the URL, and only knowing ssh:// meant the
    documented scp-form
    (`SWARM_ORIGIN=board-host:solve-metrics-origin.git`) fell through to
    the local-path branch and crashed every origin_body/origin_commit_ts
    read with FileNotFoundError (cwd = the literal URL string). scp-form
    paths stay verbatim after the colon: relative to the remote HOME,
    where the non-interactive ssh shell starts."""
    if origin.startswith("ssh://"):
        u = urlparse(origin)
        host = u.hostname
        if u.username:
            host = f"{u.username}@{host}"
        path = u.path
        if not path.startswith("/"):
            path = "/" + path
        return host, u.port, path
    if origin.startswith("file://") or origin.startswith("/"):
        return None
    if "://" in origin:
        return None
    host, sep, path = origin.partition(":")
    if sep and host and path and "/" not in host:
        return host, None, path
    return None


def _origin_raw_proc(origin: str, ref_or_sha: str):
    """The `cat-file -p` read behind origin_raw, as the raw proc so the
    rc survives (the degraded wrapper discards it)."""
    origin = resolve_origin(origin)
    tgt = ssh_target(origin)
    if tgt is not None:
        _host, _port, _path = tgt
        cmd = _ssh_cmd(_host, _port,
                       "git", "-C", _path, "cat-file", "-p", ref_or_sha)
        return sh(cmd)
    path = origin
    if origin.startswith("file://"):
        path = urlparse(origin).path
    return git("cat-file", "-p", ref_or_sha, cwd=path)


def origin_raw(origin: str, ref_or_sha: str) -> str:
    """Full `cat-file -p` output of an object, read on the ORIGIN host
    (the auditor's clone may lack freshly pushed objects).

    Degraded-read law (frozen, test_inrepo_origin_body): anything
    unreadable — absent ref, failed read — returns "". Callers that
    have ALREADY established board visibility (the ref is in a
    require_remote census) and judge on the result use the strict
    reads (_origin_raw_strict/_origin_body_strict): for a board-visible
    object, "" from a failed read is a lie at every decision-grade
    consumer (correction_graph's frozen rule, audit's H1, reconcile's
    TTL dating)."""
    if not ref_or_sha:
        return ""
    r = _origin_raw_proc(resolve_origin(origin),
                         ref_or_sha)
    return r.stdout if ok(r) else ""


def _origin_raw_strict(origin: str, ref_or_sha: str) -> str:
    """Object read with the rc ENFORCED.
    Raises RuntimeError naming the failed read instead of returning
    "" — for a board-visible ref an unreadable object is a broken
    board, never an empty body. Callers pass refs they have already
    seen on the board (require_remote census); board-absence is the
    caller's design, handled by the `ref in have` guards (the frozen
    origin_body empty-for-missing-ref law is untouched)."""
    r = _origin_raw_proc(resolve_origin(origin),
                         ref_or_sha)
    if not ok(r):
        raise RuntimeError(
            f"origin object read failed: {ref_or_sha} on {origin}: "
            f"{(r.stderr or r.stdout or '').strip()[:200]}"
        )
    return r.stdout


def _origin_body_strict(origin: str, ref_or_sha: str) -> str:
    """origin_body semantics (tree-header stripped) with the strict rc
    law of _origin_raw_strict."""
    raw = _origin_raw_strict(origin, ref_or_sha)
    if raw.startswith("tree "):
        _, sep, message = raw.partition("\n\n")
        if sep:
            return message
    return raw


def _committer_ts_from_raw(raw: str) -> float | None:
    """Committer epoch from `cat-file -p` output; None when undateable."""
    for ln in raw.splitlines():
        if ln.startswith("committer "):
            parts = ln.split()
            try:
                return float(parts[-2])
            except (ValueError, IndexError):
                return None
    return None


def origin_body(origin: str, ref_or_sha: str) -> str:
    """Read object body (ref or sha) from origin (ssh://, file://, or local path)."""
    raw = origin_raw(origin, ref_or_sha)
    if raw.startswith("tree "):
        _, sep, message = raw.partition("\n\n")
        if sep:
            return message
    return raw


def origin_commit_ts(origin: str, ref_or_sha: str) -> float | None:
    """Committer epoch of a commit object, read on the origin host.
    None when the object is missing/undateable — callers must treat
    None as 'cannot judge age', never as 'old'. (A failed read also
    lands here as None — degraded; reconcile's dating read uses the
    strict path and records the failure instead.)"""
    return _committer_ts_from_raw(origin_raw(origin, ref_or_sha))


def audit(only: list | None = None) -> None:
    current_origin = resolve_origin()
    # The board read is rc-CHECKED — the UNCHECKED remote() let a dead
    # origin yield an empty have → empty A → h1_pass true. An empty
    # board read is now HEALTHY (correctly, for a reachable idle board),
    # so composed the two made audit the worst instrument on the wall:
    # blind → empty → healthy. The operator's primary health instrument
    # never certifies a board it cannot see (the same law, applied to
    # the instrument itself).
    have = require_remote(current_origin)

    tasks = only or sorted(
        t[len("refs/swarm/specs/") :] for t in have if t.startswith("refs/swarm/specs/")
    )
    A = {}
    for t in tasks:
        c_ref = f"refs/swarm/claims/{t}"
        v_ref = f"refs/swarm/verdicts/{t}"
        # Board-visible object reads are STRICT. Degraded, a failed
        # verdict read graded a HEALTHY claimed task VIOLATED (vbody=""
        # → verdict None) and a failed claim read crashed IndexError on
        # the att extraction — the instrument condemned (or crashed on)
        # a board it could not see. The rc-checked-read law covers the
        # instrument: audit never judges, either way, on a read it does
        # not know.
        try:
            att_c = (
                _origin_body_strict(current_origin, c_ref).strip().split()[-1]
                if c_ref in have
                else None
            )
            vbody = (
                _origin_body_strict(current_origin, v_ref)
                if v_ref in have
                else ""
            )
        except RuntimeError as e:
            raise RuntimeError(
                f"audit cannot see the board: {e}"
            ) from None
        vd = {}
        for ln in vbody.splitlines():
            if ":" in ln:
                k, _, val = ln.partition(":")
                vd[k.strip()] = val.strip()
        A[t] = {
            "claimed": att_c,
            "returned": f"refs/swarm/tasks/{t}" in have,
            "verdict": vd.get("fixed"),
            "verdict_att": vd.get("attempt"),
        }
        # The verdict-count gate's flip is visible when it exists —
        # integrated: true + the flip sha. Absence writes NOTHING:
        # visibility, never a new failure class (h1 and the orphan
        # census are untouched by this field).
        int_ref = f"refs/swarm/integrated/{t}"
        if int_ref in have:
            A[t]["integrated"] = True
            A[t]["integrated_sha"] = have[int_ref]
    # H1: every CLAIMED task has return+verdict with matching att.
    # Judged over LIVE CLAIMS, never over pending work. The old
    # quantification ran `all(...)` over ALL spec tasks with an
    # empty-board fallback of False, so any task with no live claim yet
    # — an idle board, or a settled one awaiting its next re-seed —
    # read h1_pass FALSE: a board where nothing is wrong reads
    # VIOLATED, the inverse of the law that 'queue empty' and 'cannot
    # see the queue' must not look alike. A task with no live claim is
    # PENDING, not violating; an empty board is HEALTHY.
    h1 = (
        all(
            d["returned"]
            and d["verdict"] in ("true", "false")
            and d["verdict_att"] == d["claimed"]
            for d in A.values()
            if d["claimed"]
        )
        if A
        else True
    )
    # Claim-orphan observability: the claim-CAS namespace accepts any
    # ref name, so a claim for a spec-less task can land via an
    # env-bind slip and sit invisible here (audit is spec-keyed).
    # REPORT-ONLY: h1 stays the per-spec invariant; reconcile remains
    # the TTL backstop, sweep the repair (marker carries the claim's
    # true att).
    spec_tasks = {
        t[len("refs/swarm/specs/") :]
        for t in have
        if t.startswith("refs/swarm/specs/")
    }
    claim_orphans = sorted(
        r[len("refs/swarm/claims/") :]
        for r in have
        if r.startswith("refs/swarm/claims/")
        and "@" not in r
        and r[len("refs/swarm/claims/") :] not in spec_tasks
    )
    # The schema IS the instruction: the schema-refusal census — how
    # many of the board's acting refs (task specs, reviewer verdicts)
    # fail l2/refschema.py, each refusal named. Observe-only: a count,
    # never a new failure class (h1 and the orphan census are untouched
    # by it; absence of the section means a lone-law copy staged without
    # the schema sibling, not a clean board — a clean board with the law
    # present reads measured zeros).
    schema_census = None
    if refschema is not None:
        spec_refusals, rv_refusals = [], []
        spec_total = rv_total = 0
        for ref in have:
            if ref.startswith("refs/swarm/specs/") and "@" not in ref:
                spec_total += 1
                try:
                    body = _origin_body_strict(current_origin, ref)
                except RuntimeError as e:
                    raise RuntimeError(
                        f"audit cannot see the board: {e}"
                    ) from None
                if refschema.validate_spec(body):
                    spec_refusals.append(ref[len("refs/swarm/specs/") :])
            elif ref.startswith("refs/swarm/verdicts/") and "@" in ref:
                rv_total += 1
                name = ref[len("refs/swarm/verdicts/") :]
                t, rev = name.rsplit("@", 1)
                try:
                    body = _origin_body_strict(current_origin, ref)
                except RuntimeError as e:
                    raise RuntimeError(
                        f"audit cannot see the board: {e}"
                    ) from None
                _, errs = refschema.validate_review_verdict(body, t, rev)
                if errs:
                    rv_refusals.append({"ref": name, "errors": errs})
        schema_census = {
            "specs": {"total": spec_total,
                      "refused": sorted(spec_refusals)},
            "reviewer_verdicts": {"total": rv_total,
                                  "refused": rv_refusals},
        }
    payload = {"h1_pass": h1, "claim_orphans": claim_orphans, "tasks": A}
    if schema_census is not None:
        payload["schema"] = schema_census
    print(json.dumps(payload, indent=1))


# --------------------------------------------------- correction graph


def _model_from_body(body: str) -> str:
    """The dispatched model recorded in a ref body (`model: <m>`) — the
    exact `-m <model>` the worker handed opencode. 'unknown' when absent
    (pre-protocol claims, env deaths that never reached a model-bearing
    claim): an honest blind vertex, never a guess. The att token alone can
    never carry the true model — deriving it from the att grammar was the
    mm misparse this replaces."""
    for ln in body.splitlines():
        k, _, val = ln.partition(":")
        if k.strip() == "model":
            return val.strip() or "unknown"
    return "unknown"


def _kind_from_body(body: str) -> str:
    """The chain/direct flag recorded in a ref body (`kind: <k>`), or
    'unknown' when absent (pre-protocol claims, or a claim whose router
    query degraded to unknown)."""
    for ln in body.splitlines():
        k, _, val = ln.partition(":")
        if k.strip() == "kind":
            return val.strip() or "unknown"
    return "unknown"


def correction_graph(origin: str) -> dict:
    """Multi-model correction graph, reconstructed from refs alone.

    The correction graph + RAEE/PPR leaderboard are deferred until
    '≥2 models compete on the same task stream' — this is the substrate
    read side that trigger builds on (no live multi-model run ships
    with it). Vertices are a task's attempts:
    every archived claim (@att refs, L3), the live claim, and — when a
    verdict exists — the verdict's attempt. Edges are 'corrects' links
    between consecutive attempts ordered by claim-commit time (the
    heir/reconcile law creates them; nothing here writes refs). Model
    attribution comes from the `model:` field in each attempt's ref body —
    the exact dispatched model, 'unknown' when absent (pre-protocol
    claims). Refs + commit objects must carry the whole story — no
    ledger, no env, no log.

    Read-only: ls-remote + origin object reads, never a ref write. An
    unreachable origin raises — an empty graph must mean an empty
    substrate, never a dead connection."""
    have = require_remote(origin)
    tasks = sorted(
        r[len("refs/swarm/specs/") :]
        for r in have
        if r.startswith("refs/swarm/specs/") and "@" not in r
    )
    G = {"origin": origin, "tasks": {}, "edges": [], "models": {}}
    for t in tasks:
        v_att = None
        v_model = "unknown"
        v_kind = "unknown"
        vref = f"refs/swarm/verdicts/{t}"
        if vref in have:
            # Strict reads — the verdict ref is board-visible, so a
            # failed object read is a broken board, never "no attempt
            # line". The DEGRADED read turned a read blip into a
            # success-shaped INCOMPLETE graph: the verdict vertex (and
            # its 'final' flag) silently dropped, and under the frozen
            # rule ("FALSIFIED iff all chains single-vertex") the
            # two-model window could grade the substrate falsified on a
            # dead read. The blind graph must not exist (the same law,
            # one layer down).
            vraw = _origin_body_strict(origin, vref)
            for ln in vraw.splitlines():
                k, _, val = ln.partition(":")
                if k.strip() == "attempt":
                    v_att = val.strip()
                elif k.strip() == "model":
                    v_model = val.strip() or "unknown"
                elif k.strip() == "kind":
                    v_kind = val.strip() or "unknown"
        # Each candidate attempt carries the model parsed from its own ref
        # body (the exact dispatched model; 'unknown' when absent). The
        # archived + live claims are strict reads (board-visible refs —
        # the same law as the verdict above).
        raw = []
        for ref in have:
            if ref.startswith(f"refs/swarm/archive/claims/{t}@"):
                abody = _origin_body_strict(origin, ref)
                raw.append((ref.split("@", 1)[1], ref,
                            _model_from_body(abody), _kind_from_body(abody)))
        cref = f"refs/swarm/claims/{t}"
        if cref in have:
            # Strict read (board-visible ref) — same law.
            cbody = _origin_body_strict(origin, cref)
            cand = cbody.strip().split()
            if cand and cand[-1].startswith("att-"):
                raw.append((cand[-1], cref,
                            _model_from_body(cbody), _kind_from_body(cbody)))
        if v_att and v_att not in [a for a, _, _, _ in raw]:
            # verdict-only vertex (claim gone)
            raw.append((v_att, vref, v_model, v_kind))
        attempts = []
        for att, ref, model, kind in raw:
            attempts.append(
                {
                    "att": att,
                    # Strict dating — an undateable ATTEMPT sorts last,
                    # deterministically by att (the honest degraded
                    # read for a malformed object); a FAILED read raises
                    # like every other object read here.
                    "ts": _committer_ts_from_raw(
                        _origin_raw_strict(origin, ref)
                    ),
                    "model": model,
                    "kind": kind,
                    "final": att == v_att,
                }
            )
        # undateable attempts sort last, deterministically by att
        attempts.sort(key=lambda a: (a["ts"] is None, a["ts"] or 0.0, a["att"]))
        G["tasks"][t] = {"attempts": attempts, "verdict_att": v_att}
        for a, b in zip(attempts, attempts[1:]):
            G["edges"].append(
                {
                    "task": t,
                    "from_att": a["att"],
                    "to_att": b["att"],
                    "from_model": a["model"],
                    "to_model": b["model"],
                }
            )
        for a in attempts:
            m = a["model"] or "unknown"
            g = G["models"].setdefault(m, {"attempts": 0, "fixed": 0})
            g["attempts"] += 1
            if a["final"]:
                g["fixed"] += 1
    return G


def attribution(origin: str | None = None) -> dict:
    """Read-only model-attribution table per task — the debugging entry
    point for 'is this board's attribution correct?'. Same law as
    correction_graph: ls-remote + origin object reads, never a ref write.
    Per task, a flat row per attempt:
      att | model | kind | verdict(fixed/oc_rc/pytest_rc) | archived | host.
    The verdict fields are read from the task's verdict body (the final
    attempt's; None for non-final attempts). FLOCKWORK_DEBUG=1 adds the raw
    claim/verdict bodies so a misfiled vertex is explainable without
    mutation (the derivation is body `model:` -> `kind:`, both read straight
    off the ref body by _model_from_body / _kind_from_body)."""
    origin = resolve_origin(origin)
    have = require_remote(origin)
    g = correction_graph(origin)
    out = {"origin": origin, "tasks": {}}
    for t, td in g["tasks"].items():
        vref = f"refs/swarm/verdicts/{t}"
        vfields = {}
        if vref in have:
            for ln in _origin_body_strict(origin, vref).splitlines():
                k, _, val = ln.partition(":")
                vfields[k.strip()] = val.strip()
        rows = []
        for a in td["attempts"]:
            final = a["final"] and a["att"] == td.get("verdict_att")
            archived = f"refs/swarm/archive/claims/{t}@{a['att']}" in have
            rows.append({
                "att": a["att"],
                "model": a["model"],
                "kind": a["kind"],
                "fixed": vfields.get("fixed") if final else None,
                "oc_rc": vfields.get("oc_rc") if final else None,
                "pytest_rc": vfields.get("pytest_rc") if final else None,
                "host": vfields.get("host") if final else None,
                "archived": archived,
            })
        out["tasks"][t] = rows
    if os.environ.get("FLOCKWORK_DEBUG", "").strip().lower() in (
            "1", "true", "yes"):
        out["debug"] = {
            t: {
                "claim_body": _origin_body_strict(
                    origin, f"refs/swarm/claims/{t}")
                if f"refs/swarm/claims/{t}" in have else None,
                "verdict_body": _origin_body_strict(
                    origin, f"refs/swarm/verdicts/{t}")
                if f"refs/swarm/verdicts/{t}" in have else None,
            }
            for t in out["tasks"]
        }
    return out


# ------------------------------------------------ verdict-count gate CLI
# The gate module (l2/gates.py) becomes reachable through the law's
# single CLI surface. THIN delegation only — the module owns the
# semantics (docs/GATES.md, pinned by its own suite); the law adds no
# second implementation to drift.


def review_cmd(task: str, reviewer: str, outcome: str,
               evidence: str | None = None) -> int:
    """`inrepo.py review <task> <reviewer> <agree|veto> [evidence-ref]`
    — l2.gates.review_verdict, the create-once reviewer verdict at
    refs/swarm/verdicts/<task>@<reviewer> (the @-law). Same JSON event
    as the module's own CLI; rc 0 iff the verdict pushed."""
    if gates is None:
        print(json.dumps(
            {"event": "review_verdict", "task": task, "reviewer": reviewer,
             "outcome": outcome, "pushed": False, "refused": True,
             "reason": "gate module absent (lone-law copy: l2/gates.py "
                       "not staged beside the law)"}), flush=True)
        return 2
    if evidence is None:
        evidence = f"refs/swarm/tasks/{task}"
    ev = gates.review_verdict(task, reviewer, outcome, evidence)
    return 0 if ev["pushed"] else 1


def gate_cmd(task: str) -> int:
    """`inrepo.py gate <task>` — l2.gates.gate: fires iff
    count(agree)==n and count(veto)==0 with every counted verdict's
    evidence ref resolving; the flip is create-once, a re-run after a
    flip is an honest idempotent no-op. Same JSON event as the module's
    own CLI; rc 0 iff fired or already integrated."""
    if gates is None:
        print(json.dumps(
            {"event": "gate", "task": task, "fired": False,
             "flipped": False, "already_integrated": False,
             "refused": True,
             "reason": "gate module absent (lone-law copy: l2/gates.py "
                       "not staged beside the law)"}), flush=True)
        return 2
    ev = gates.gate(task)
    return 0 if (ev["fired"] or ev["already_integrated"]) else 1


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if mode == "seed":
        seed(sys.argv[2])
    elif mode == "worker":
        sys.exit(1 if worker(sys.argv[2], sys.argv[3:] or None) else 0)
    elif mode == "audit":
        audit(sys.argv[2:] or None)
    elif mode == "sweep":
        print(json.dumps(
            sweep(sys.argv[2], sys.argv[3],
                  sys.argv[4] if len(sys.argv) > 4 else None),
            indent=1,
        ))
    elif mode == "reconcile":
        origin = (sys.argv[2] if len(sys.argv) > 2
                  else resolve_origin())
        ttl = float(sys.argv[3]) if len(sys.argv) > 3 else None
        print(json.dumps(reconcile(origin, ttl), indent=1))
    elif mode == "relabel":
        print(json.dumps(relabel_orphan_archives(sys.argv[2]), indent=1))
    elif mode == "divergence":
        print(json.dumps(
            divergence(sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
    elif mode == "open_tasks":
        # documented as the operator's queue view; an unknown mode falls
        # through to the usage error below (a documented mode must act).
        print(json.dumps(open_tasks(), indent=1))
    elif mode == "correction-graph":
        print(json.dumps(
            correction_graph(
                sys.argv[2] if len(sys.argv) > 2
                else resolve_origin()
            ),
            indent=1,
        ))
    elif mode == "attribution":
        print(json.dumps(
            attribution(
                sys.argv[2] if len(sys.argv) > 2
                else resolve_origin()
            ),
            indent=1,
        ))
    elif mode == "review":
        if len(sys.argv) < 5:
            print("usage: flockwork review <task> <reviewer> "
                  "<agree|veto> [evidence-ref]", file=sys.stderr)
            sys.exit(2)
        sys.exit(review_cmd(
            sys.argv[2], sys.argv[3], sys.argv[4],
            sys.argv[5] if len(sys.argv) > 5 else None))
    elif mode == "gate":
        if len(sys.argv) < 3:
            print("usage: flockwork gate <task>", file=sys.stderr)
            sys.exit(2)
        sys.exit(gate_cmd(sys.argv[2]))
    else:
        print(
            f"unknown mode: {mode}\n"
            "usage: flockwork {seed|worker|audit|review|gate|sweep"
            "|open_tasks|reconcile|relabel|divergence|correction-graph} ...",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
