#!/usr/bin/env python3
"""l2/inrepo.py — the in-repo lane (LOOP round 4, V9-V12 verified).

The project's OWN origin is the coordination substrate:
  refs/swarm/specs/<task>     task brief (root commit; fetched by workers)
  refs/swarm/claims/<task>    claim    (root commit, msg = claim <att>)
  refs/swarm/tasks/<task>     return   (fix commit on main lineage,
                                        trailer `Attempt: <att>`)
  refs/swarm/verdicts/<task>  verdict  (root commit, msg = verdict
                                        task/attempt/fixed/host block)

Laws encoded (from V9-V12):
  L1  claims/verdicts are ROOT commits — never on main lineage.
  L2  CAS via --force-with-lease=refs/swarm/<kind>/<task>:
      (empty base = create-once; known sha = own-record renewal).
      Workers never read remote state to form expectations — the sha
      comes from their own records (V12: works under hiding).
  L3  dead attempts preserved at refs/swarm/archive/<kind>/<task>@<att>
      in the SAME transaction that frees the live ref (c8 @-law).
  L4  host pytest at the task dir is the verdict oracle (c9 law).

Usage (run from a worker clone of the origin):
  python3 l2/inrepo.py seed SPEC.json     # push spec refs for tasks
  python3 l2/inrepo.py worker NAME [tasks...]   # claim-fix-verify loop
  python3 l2/inrepo.py audit [tasks...]   # read-only H1/H2 over refs
  python3 l2/inrepo.py sweep ORIGIN TASK [ATT]  # archive+free swarm refs
  python3 l2/inrepo.py reconcile [ORIGIN] [TTL_S]  # sweep stale claims
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

ORIGIN = os.environ.get("SWARM_ORIGIN", "ssh://example-host-a/home/you/swarmo-origin.git")


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


def remote(*args, cwd=None):
    orig = os.environ.get("SWARM_ORIGIN", ORIGIN)
    return git("ls-remote", orig, *args, cwd=cwd)


def empty_tree():
    return git("hash-object", "-t", "tree", "/dev/null").stdout.strip()


def verdict_body(
    task: str, att: str, fixed: bool, host: str, oc_rc: int, pytest_rc: int
) -> str:
    """Verdict commit message. oc_rc carries the dispatch exit code so the
    error table applies downstream (124=timeout → environmental/requeue,
    model-death → requeue+exit, 0 with pytest fail → merit): the refs
    alone must separate environmental deaths from honest merit failures
    without hunting the worker's stdout."""
    return (
        f"verdict\ntask: {task}\nattempt: {att}\n"
        f"fixed: {'true' if fixed else 'false'}\nhost: {host}\n"
        f"oc_rc: {oc_rc}\npytest_rc: {pytest_rc}"
    )


# --------------------------------------------------------------- seed


def seed(spec_path: str) -> dict:
    """Push refs/swarm/specs/<task> = root commit carrying the brief."""
    spec = json.load(open(spec_path))
    et = empty_tree()
    R = {}
    for task, brief in sorted(spec.items()):
        c = git("commit-tree", et, "-m", f"spec {task}\n\n{brief}")
        r = git("push", "-q", ORIGIN, f"{c.stdout.strip()}:refs/swarm/specs/{task}")
        R[task] = ok(r)
    print(json.dumps({"event": "seeded", "tasks": R}, indent=1))
    return R


# -------------------------------------------------------------- claim


def claim(worker: str, task: str) -> str | None:
    """Create-once CAS claim. Returns att id or None (lost/claimed)."""
    att = f"att-{worker}-{os.urandom(3).hex()}"
    c = git("commit-tree", empty_tree(), "-m", f"claim {task} {att}")
    lease = f"--force-with-lease=refs/swarm/claims/{task}:"
    r = git("push", "-q", lease, ORIGIN, f"{c.stdout.strip()}:refs/swarm/claims/{task}")
    return att if ok(r) else None


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
    environmental death already recorded (by reconcile or self-requeue)."""
    r = git("ls-remote", origin, f"refs/swarm/archive/claims/{task}@*")
    return sum(1 for ln in r.stdout.splitlines() if ln.strip())


def work_task(worker: str, task: str, att: str) -> dict:
    """Fix + verify + return + verdict. Only called by the claim winner.

    Carries the frozen c6 requeue rule (experiments/c6/FREEZE.md):
    an environmental death (dispatch failed leaving no work in the
    tree — timeout, model/provider death, clone failure) with no heir
    yet ARCHIVES the dead attempt and frees the claim in the same run,
    so the task re-enters open_tasks immediately instead of waiting out
    the reconcile TTL; one heir attempt max, then the honest
    fixed:false verdict is FINAL. Merit failures (dispatch completed,
    oracle failed) were always final."""
    tree = tempfile_tree(task)
    r = git("clone", "-q", ORIGIN, tree)
    if not ok(r):
        ev = {
            "event": "attempted",
            "worker": worker,
            "task": task,
            "att": att,
            "env_death": True,
            "reason": f"clone: {r.stderr[:200]}",
        }
        try:
            swept = sweep(ORIGIN, task)
            ev["requeued"] = bool(swept["archived"])
            ev["swept"] = swept["att"] if swept["archived"] else None
        except RuntimeError as e:
            ev["requeued"] = False
            ev["sweep_error"] = str(e)[:200]
        return ev
    git("-C", tree, "config", "user.email", f"{worker}@swarm")
    git("-C", tree, "config", "user.name", worker)
    # fetch the spec ref, read the brief (verify: line = task oracle)
    git("-C", tree, "fetch", "-q", ORIGIN, f"refs/swarm/specs/{task}")
    brief = git("-C", tree, "log", "-1", "--format=%B", "FETCH_HEAD").stdout.strip()
    verify = next(
        (
            ln[len("verify:") :].strip()
            for ln in brief.splitlines()
            if ln.startswith("verify:")
        ),
        None,
    )
    # real work: dispatch backend selectable by env.
    #  freebuff — the fbconn PTY driver on the freebuff host (example-host-d):
    #             run_prompt spawns/continues a session and harvests the
    #             reply transcript (proven E2E chain).
    #  default  — opencode headless (V4/V6 laws: own git root, $PWD pin)
    oc_rc = 0
    if os.environ.get("SWARM_DISPATCH") == "freebuff":
        # fbconn recipe step 1 (HOWTO 'Proven takeover recipe'): the
        # dispatch owns the slot only if no live TUI holder exists —
        # clear it HERE, inside the recipe (found live: worker raced
        # a still-live picker TUI twice)
        sh(
            [
                "bash",
                "-c",
                "pkill -f 'the-freebuff-runtime --continue' 2>/dev/null; "
                "sleep 2; "
                "rm -f ~/.config/the-freebuff-runtime/freebuff-instance-owner.json; true",
            ]
        )
        # unconditional rm: a zombie TUI (defunct, kill-0 alive) defeats
        # any pgrep-based conditional — found live on example-host-d
        try:
            sys.path.insert(
                0, os.environ.get("FBCONN_HOME", "/home/you/freebuff-connector")
            )
            from fbconn.api import run_prompt

            res = run_prompt(tree, brief, takeover=True)
            oc_rc = 0 if res and res.get("text") else 1
        except Exception as e:  # noqa: BLE001 — record, never crash
            print(
                json.dumps(
                    {"event": "dispatch_error", "task": task, "err": repr(e)[:200]}
                ),
                flush=True,
            )
            oc_rc = 1
    else:
        model = os.environ.get("SWARM_MODEL", "hpc-glm/zai-org/GLM-5.3-Flash")
        oc_bin = os.environ.get("OPENCODE_BIN", "opencode")
        env = dict(os.environ, PWD=tree, OLDPWD=tree)
        try:
            # SWARM_DISPATCH_TIMEOUT: model-size must fit task-size; a pro
            # model on a contract test needs >420s (example-host-b finding, round 6)
            oc = sh(
                [oc_bin, "run", "--pure", "-m", model, brief],
                cwd=tree,
                env=env,
                timeout=int(os.environ.get("SWARM_DISPATCH_TIMEOUT", "600")),
            )
            oc_rc = oc.returncode
        except subprocess.TimeoutExpired:
            oc_rc = 124
    # c6 classification, BEFORE any ref is written: an environmental
    # death is a dispatch that failed leaving no work in the tree
    # (124=timeout counts even with partial work — c4 requeued partial
    # orphan work too). oc_rc!=0 with a populated tree is recorded
    # honestly and stays merit-final: the conservative side of the
    # error table (no requeue storms from ambiguous exits).
    no_changes = git("-C", tree, "status", "--porcelain").stdout.strip() == ""
    env_death = oc_rc == 124 or (oc_rc != 0 and no_changes)
    if env_death and heirs_count(ORIGIN, task) < int(
        os.environ.get("SWARM_HEIR_MAX", "1")
    ):
        swept = sweep(ORIGIN, task)
        return {
            "event": "attempted",
            "worker": worker,
            "task": task,
            "att": att,
            "oc_rc": oc_rc,
            "env_death": True,
            "requeued": bool(swept["archived"]),
            "swept": swept["att"] if swept["archived"] else None,
        }
    # L4 oracle: task-specified verify command, else host pytest in the
    # task dir (first dir under tasks/). bash -lc so example-host-b's pyenv
    # pytest resolves (c6 recipe).
    if verify:
        pr = sh(["bash", "-lc", verify], cwd=tree)
    else:
        tdir = os.path.join(tree, "tasks", task)
        if not os.path.isdir(tdir):
            tdir = tree
        pr = sh([sys.executable, "-m", "pytest", "-q"], cwd=tdir)
    # an environmental death is never a fix, whatever the oracle says
    # (heir-exhausted path: the oracle may pass on an unchanged tree)
    fixed = pr.returncode == 0 and not env_death
    # return commit on main lineage with the att trailer; concurrent
    # workers push main too — on non-FF, rebase onto origin and retry
    git("-C", tree, "add", "-A")
    git(
        "-C",
        tree,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        f"fix {task}\n\nAttempt: {att}",
    )
    r1 = git("-C", tree, "push", "-q", ORIGIN, "HEAD:main")
    if not ok(r1):
        rr = git("-C", tree, "pull", "-q", "--rebase", ORIGIN, "main")
        if ok(rr):
            r1 = git("-C", tree, "push", "-q", ORIGIN, "HEAD:main")
    lease = f"--force-with-lease=refs/swarm/tasks/{task}:"
    r2 = git("-C", tree, "push", "-q", lease, ORIGIN, f"HEAD:refs/swarm/tasks/{task}")
    # L1 verdict: ROOT commit
    et = empty_tree()
    v = git(
        "commit-tree", et, "-m", verdict_body(task, att, fixed, worker, oc_rc, pr.returncode)
    )
    lease = f"--force-with-lease=refs/swarm/verdicts/{task}:"
    r3 = git(
        "push", "-q", lease, ORIGIN, f"{v.stdout.strip()}:refs/swarm/verdicts/{task}"
    )
    oc_err = ""
    try:
        oc_err = (oc.stderr or "")[-200:] if oc.returncode else ""
    except Exception:  # noqa: BLE001 — freebuff path has no oc object
        oc_err = ""
    return {
        "event": "attempted",
        "worker": worker,
        "task": task,
        "att": att,
        "fixed": fixed,
        "pytest_rc": pr.returncode,
        "oc_rc": oc_rc,
        "oc_err": oc_err,
        "env_death": env_death,
        "heir_exhausted": env_death,
        "main_push": ok(r1),
        "return_pushed": ok(r2),
        "verdict_pushed": ok(r3),
    }


def tempfile_tree(task):
    import tempfile

    return tempfile.mkdtemp(prefix=f"inrepo-{task}-")


def worker(name: str, only: list | None = None) -> bool:
    """Claim-fix-verify loop. Returns True when it gave up on an
    unreachable origin (SWARM_WORKER_MAX_FAILS consecutive scans,
    default 3) — a supervised worker must exit honestly distinct from
    'queue empty, done'. Transient per-task failures are recorded and
    the loop continues; the failed claim is freed best-effort."""
    done = 0
    fails = 0
    max_fails = int(os.environ.get("SWARM_WORKER_MAX_FAILS", "3"))
    origin = os.environ.get("SWARM_ORIGIN", ORIGIN)
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
                        {"event": "worker_gave_up", "worker": name, "completed": done}
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
        att = claim(name, task)
        if att is None:
            time.sleep(1)  # lost race; re-scan
            continue
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
        done += 1
        print(json.dumps(out), flush=True)
    print(
        json.dumps({"event": "worker_done", "worker": name, "completed": done}),
        flush=True,
    )
    return False


# -------------------------------------------------------------- sweep


def sweep(origin: str, task: str, att: str | None = None) -> dict:
    """Archive every live refs/swarm/{claims,tasks,verdicts}/<task> to
    refs/swarm/archive/<kind>/<task>@<att> and delete the live refs in
    ONE transaction (push --atomic; L3/c8 @-law). att defaults to the
    claim commit body's last token. Idempotent: nothing live -> empty
    lists, no refs written."""
    have = {}
    for ln in git("ls-remote", origin).stdout.splitlines():
        if ln.strip():
            sha, ref = ln.split()
            have[ref] = sha
    live = [
        (kind, ref, have[ref])
        for kind in ("claims", "tasks", "verdicts")
        for ref in (f"refs/swarm/{kind}/{task}",)
        if ref in have and "@" not in ref
    ]
    if not live:
        return {"task": task, "att": att, "archived": [], "deleted": []}
    if att is None:
        # audit finding 4 revision: an archive marker must embed the
        # true att. When the claim IS live the att is recoverable in
        # principle — read it, retry once for a transient origin-host
        # read failure, and REFUSE to archive under a degraded marker
        # (the same push would delete the refs that carry it). Only the
        # genuinely claim-less orphan shape keeps @unknown: refusing
        # there would leave zombie refs blocking the task forever
        # (round-6 finding 5), and relabel_orphan_archives can repair
        # the marker later if the att ever surfaces.
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
    return {
        "task": task,
        "att": att,
        "archived": [f"refs/swarm/archive/{k}/{task}@{att}" for k, _, _ in live],
        "deleted": [ref for _, ref, _ in live],
    }


# ---------------------------------------------------------- reconcile


def reconcile(origin: str, ttl_s: float | None = None) -> dict:
    """Detect and recover stale claims: the automation layer over sweep().

    The dogfood batch recovered six environmental deaths by MANUAL sweep;
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
    have = {}
    for ln in git("ls-remote", origin).stdout.splitlines():
        if ln.strip():
            sha, ref = ln.split()
            have[ref] = sha
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
        ts = origin_commit_ts(origin, f"refs/swarm/claims/{t}")
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
        except RuntimeError as e:
            out["errors"].append({"task": t, "err": str(e)[:200]})
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
    """Node-vs-substrate law (09-19 finding 2): a node that commits to
    its local main while the substrate moved elsewhere is invisible
    until someone compares — example-host-b ran 8 local commits ahead unnoticed.
    Read-only comparison: local main vs origin main by sha (ls-remote,
    never a fetch). ahead/behind are exact via rev-list when the
    objects are local; otherwise None + a reason that names the fetch —
    never a fabricated count."""
    origin = origin or os.environ.get("SWARM_ORIGIN", ORIGIN)
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


# ------------------------------------------------------------- relabel


def relabel_orphan_archives(origin: str) -> dict:
    """Rename archive refs whose @-marker is not att-* to the true att
    recovered from the object's own body (audit finding 4: the marker
    is the ref-level index of an attempt; a body-only att is not
    queryable). Same sha at the new name + delete the old, ONE atomic
    push per ref. Never overwrites an existing target and never guesses
    — a ref with no att-* token in its body is skipped and flagged, so
    a dry-minded caller can rerun relabel after fixing the bodies."""
    have = {}
    for ln in git("ls-remote", origin).stdout.splitlines():
        if ln.strip():
            sha, ref = ln.split()
            have[ref] = sha
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
        have[new_ref] = sha
        del have[ref]
    return out


# -------------------------------------------------------------- audit


def ssh_target(origin: str):
    """(host, port, path) for ssh-origin reads, or None for non-ssh.

    git plumbing accepts both `ssh://[user@]host[:port]/path` and the
    scp-form `[user@]host:path` — but origin-host body reads build an
    ssh COMMAND from the URL, and only knowing ssh:// meant the
    documented scp-form (`SWARM_ORIGIN=you@example-host-a:solve-metrics-origin.git`,
    the 09-19 addendum's claim line) fell through to the local-path
    branch and crashed every origin_body/origin_commit_ts read with
    FileNotFoundError (cwd = the literal URL string). scp-form paths
    stay verbatim after the colon: relative to the remote HOME, where
    the non-interactive ssh shell starts."""
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


def origin_raw(origin: str, ref_or_sha: str) -> str:
    """Full `cat-file -p` output of an object, read on the ORIGIN host
    (T5 law: the auditor's clone may lack freshly pushed objects)."""
    if not ref_or_sha:
        return ""
    origin = origin or os.environ.get("SWARM_ORIGIN", ORIGIN)
    tgt = ssh_target(origin)
    if tgt is not None:
        _host, _port, _path = tgt
        cmd = ["ssh", "-o", "BatchMode=yes"]
        if _port:
            cmd += ["-p", str(_port)]
        cmd += [_host, "git", "-C", _path, "cat-file", "-p", ref_or_sha]
        r = sh(cmd)
        return r.stdout if ok(r) else ""
    path = origin
    if origin.startswith("file://"):
        path = urlparse(origin).path
    r = git("cat-file", "-p", ref_or_sha, cwd=path)
    return r.stdout if ok(r) else ""


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
    None as 'cannot judge age', never as 'old'."""
    for ln in origin_raw(origin, ref_or_sha).splitlines():
        if ln.startswith("committer "):
            parts = ln.split()
            try:
                return float(parts[-2])
            except (ValueError, IndexError):
                return None
    return None


def audit(only: list | None = None) -> None:
    current_origin = os.environ.get("SWARM_ORIGIN", ORIGIN)
    have = {}
    for ln in remote().stdout.splitlines():
        if not ln.strip():
            continue
        sha, ref = ln.split()
        have[ref] = sha

    def body(ref):
        if ref not in have:
            return ""
        return origin_body(current_origin, ref)

    tasks = only or sorted(
        t[len("refs/swarm/specs/") :] for t in have if t.startswith("refs/swarm/specs/")
    )
    A = {}
    for t in tasks:
        att_c = (
            body(f"refs/swarm/claims/{t}").strip().split()[-1]
            if f"refs/swarm/claims/{t}" in have
            else None
        )
        vbody = body(f"refs/swarm/verdicts/{t}")
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
    # H1: every claimed task has return+verdict with matching att
    h1 = (
        all(
            d["claimed"]
            and d["returned"]
            and d["verdict"] in ("true", "false")
            and d["verdict_att"] == d["claimed"]
            for d in A.values()
        )
        if A
        else False
    )
    print(json.dumps({"h1_pass": h1, "tasks": A}, indent=1))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if mode == "seed":
        seed(sys.argv[2])
    elif mode == "worker":
        sys.exit(1 if worker(sys.argv[2], sys.argv[3:] or None) else 0)
    elif mode == "audit":
        audit(sys.argv[2:] or None)
    elif mode == "sweep":
        sweep(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    elif mode == "reconcile":
        origin = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SWARM_ORIGIN", ORIGIN)
        ttl = float(sys.argv[3]) if len(sys.argv) > 3 else None
        print(json.dumps(reconcile(origin, ttl), indent=1))
    elif mode == "relabel":
        print(json.dumps(relabel_orphan_archives(sys.argv[2]), indent=1))
    elif mode == "divergence":
        print(json.dumps(
            divergence(sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
