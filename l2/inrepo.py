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
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ORIGIN = os.environ.get(
    "SWARM_ORIGIN", "ssh://example-host-a/home/you/swarmo-origin.git")


def sh(cmd, cwd=None, inp=None, timeout=600, env=None):
    return subprocess.run(cmd, cwd=cwd, input=inp, text=True, env=env,
                          capture_output=True, timeout=timeout)


def ok(r):
    return r.returncode == 0


def git(*args, cwd=None, inp=None):
    return sh(["git", *args], cwd=cwd, inp=inp)


def remote(*args, cwd=None):
    return git("ls-remote", ORIGIN, *args, cwd=cwd)


def empty_tree():
    return git("hash-object", "-t", "tree", "/dev/null").stdout.strip()


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
    r = git("push", "-q", lease, ORIGIN,
            f"{c.stdout.strip()}:refs/swarm/claims/{task}")
    return att if ok(r) else None


def open_tasks() -> list:
    """Tasks with a spec but no live claim and no return."""
    have = remote().stdout
    specs, claimed, returned = set(), set(), set()
    for ln in have.splitlines():
        ref = ln.split()[1] if ln.strip() else ""
        for bucket, prefix in ((specs, "refs/swarm/specs/"),
                               (claimed, "refs/swarm/claims/"),
                               (returned, "refs/swarm/tasks/")):
            if ref.startswith(prefix) and "@" not in ref:
                bucket.add(ref[len(prefix):])
    return sorted(specs - claimed - returned)


# ------------------------------------------------------------- worker

def work_task(worker: str, task: str, att: str) -> dict:
    """Fix + verify + return + verdict. Only called by the claim winner."""
    tree = tempfile_tree(task)
    r = git("clone", "-q", ORIGIN, tree)
    if not ok(r):
        return {"task": task, "att": att, "error": f"clone: {r.stderr[:200]}"}
    git("-C", tree, "config", "user.email", f"{worker}@swarm")
    git("-C", tree, "config", "user.name", worker)
    # fetch the spec ref, read the brief (verify: line = task oracle)
    git("-C", tree, "fetch", "-q", ORIGIN, f"refs/swarm/specs/{task}")
    brief = git("-C", tree, "log", "-1", "--format=%B",
                "FETCH_HEAD").stdout.strip()
    verify = next((ln[len("verify:"):].strip()
                   for ln in brief.splitlines()
                   if ln.startswith("verify:")), None)
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
        sh(["bash", "-c",
            "pkill -f 'the-freebuff-runtime --continue' 2>/dev/null; "
            "sleep 2; "
            "rm -f ~/.config/the-freebuff-runtime/freebuff-instance-owner.json; true"])
        # unconditional rm: a zombie TUI (defunct, kill-0 alive) defeats
        # any pgrep-based conditional — found live on example-host-d
        try:
            sys.path.insert(0, os.environ.get(
                "FBCONN_HOME", "/home/you/freebuff-connector"))
            from fbconn.api import run_prompt
            res = run_prompt(tree, brief, takeover=True)
            oc_rc = 0 if res and res.get("text") else 1
        except Exception as e:  # noqa: BLE001 — record, never crash
            print(json.dumps({"event": "dispatch_error",
                              "task": task, "err": repr(e)[:200]}),
                  flush=True)
            oc_rc = 1
    else:
        model = os.environ.get("SWARM_MODEL",
                               "hpc-glm/zai-org/GLM-5.3-Flash")
        oc_bin = os.environ.get("OPENCODE_BIN", "opencode")
        env = dict(os.environ, PWD=tree, OLDPWD=tree)
        try:
            oc = sh([oc_bin, "run", "--pure", "-m", model, brief],
                    cwd=tree, env=env, timeout=420)
            oc_rc = oc.returncode
        except subprocess.TimeoutExpired:
            oc_rc = 124
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
    fixed = (pr.returncode == 0)
    # return commit on main lineage with the att trailer; concurrent
    # workers push main too — on non-FF, rebase onto origin and retry
    git("-C", tree, "add", "-A")
    git("-C", tree, "commit", "-q", "--allow-empty", "-m",
        f"fix {task}\n\nAttempt: {att}")
    r1 = git("-C", tree, "push", "-q", ORIGIN, "HEAD:main")
    if not ok(r1):
        rr = git("-C", tree, "pull", "-q", "--rebase", ORIGIN, "main")
        if ok(rr):
            r1 = git("-C", tree, "push", "-q", ORIGIN, "HEAD:main")
    lease = f"--force-with-lease=refs/swarm/tasks/{task}:"
    r2 = git("-C", tree, "push", "-q", lease, ORIGIN,
             f"HEAD:refs/swarm/tasks/{task}")
    # L1 verdict: ROOT commit
    et = empty_tree()
    v = git("commit-tree", et, "-m",
            f"verdict\ntask: {task}\nattempt: {att}\n"
            f"fixed: {'true' if fixed else 'false'}\nhost: {worker}")
    lease = f"--force-with-lease=refs/swarm/verdicts/{task}:"
    r3 = git("push", "-q", lease, ORIGIN,
             f"{v.stdout.strip()}:refs/swarm/verdicts/{task}")
    oc_err = ""
    try:
        oc_err = (oc.stderr or "")[-200:] if oc.returncode else ""
    except Exception:  # noqa: BLE001 — freebuff path has no oc object
        oc_err = ""
    return {"event": "attempted", "worker": worker, "task": task,
            "att": att, "fixed": fixed, "pytest_rc": pr.returncode,
            "oc_rc": oc_rc, "oc_err": oc_err, "main_push": ok(r1),
            "return_pushed": ok(r2), "verdict_pushed": ok(r3)}


def tempfile_tree(task):
    import tempfile
    return tempfile.mkdtemp(prefix=f"inrepo-{task}-")


def worker(name: str, only: list | None = None) -> None:
    done = 0
    while True:
        tasks = [t for t in open_tasks() if not only or t in only]
        if not tasks:
            break
        task = tasks[0]
        att = claim(name, task)
        if att is None:
            time.sleep(1)          # lost race; re-scan
            continue
        out = work_task(name, task, att)
        done += 1
        print(json.dumps(out), flush=True)
    print(json.dumps({"event": "worker_done", "worker": name,
                      "completed": done}), flush=True)


# -------------------------------------------------------------- audit

def audit(only: list | None = None) -> None:
    have = {}
    for ln in remote().stdout.splitlines():
        if not ln.strip():
            continue
        sha, ref = ln.split()
        have[ref] = sha

    def body(ref):
        if ref not in have:
            return ""
        r = git("cat-file", "-p", have[ref])
        return r.stdout if ok(r) else ""

    tasks = only or sorted(t[len("refs/swarm/specs/"):]
                           for t in have if t.startswith("refs/swarm/specs/"))
    A = {}
    for t in tasks:
        att_c = (body(f"refs/swarm/claims/{t}").strip().split()[-1]
                 if f"refs/swarm/claims/{t}" in have else None)
        vbody = body(f"refs/swarm/verdicts/{t}")
        vd = {}
        for ln in vbody.splitlines():
            if ":" in ln:
                k, _, val = ln.partition(":")
                vd[k.strip()] = val.strip()
        A[t] = {"claimed": att_c,
                "returned": f"refs/swarm/tasks/{t}" in have,
                "verdict": vd.get("fixed"),
                "verdict_att": vd.get("attempt")}
    # H1: every claimed task has return+verdict with matching att
    h1 = all(d["claimed"] and d["returned"]
             and d["verdict"] in ("true", "false")
             and d["verdict_att"] == d["claimed"]
             for d in A.values()) if A else False
    print(json.dumps({"h1_pass": h1, "tasks": A}, indent=1))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if mode == "seed":
        seed(sys.argv[2])
    elif mode == "worker":
        worker(sys.argv[2], sys.argv[3:] or None)
    elif mode == "audit":
        audit(sys.argv[2:] or None)
