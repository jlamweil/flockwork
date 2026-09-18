#!/usr/bin/env python3
"""c9 — real-workload cross-host coordination (FREEZE-C9.md).

The coord repo (bare, on example-host-a) IS the substrate AND the workspace
history: main carries four broken tasks; a worker that wins the
claim-CAS fixes the task with opencode, commits the fix on top of
main, pushes it to refs/tasks/<task> (create-once CAS), then a
verdict commit to refs/verdicts/<task> (create-once CAS).

Driver (this file, run on example-host-c):
  seed     — build the broken-task tree locally, push to the bare repo
             on example-host-a (fresh path, NOT /tmp), write a worktree for the
             example-host-c worker
  worker <name> <model> <example-host-b?> — one worker loop: for each unclaimed
             task, push-CAS claim; on win, checkout main into a
             scratch worktree, run opencode with the task brief, host
             pytest, commit fix, push return + verdict via CAS
  audit    — ssh-exec git reads ONLY (c8 discipline) against the bare
             repo: H1 exactly-once, H2 verdict==host-pytest (re-run
             locally from fetched trees), H3 per-host win split;
             results_c9.json + refs_dump_c9.txt
"""
import json
import os
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))          # ~/swarmo
sys.path.insert(0, HERE)
from c9_fixtures import FIXTURES                        # noqa: E402

COORD_PATH = "home/you/swarmo-c9-coord.git"            # example-host-a, durable
COORD_URL = "ssh://example-host-a/home/you/swarmo-c9-coord.git"
SSH_T = ["ssh", "-o", "BatchMode=yes", "example-host-a"]
GITID = ["-c", "user.email=c9@twohost", "-c", "user.name=c9"]
TASKS = sorted(FIXTURES)


def sh(cmd, cwd=None, inp=None, timeout=300, env=None):
    return subprocess.run(cmd, cwd=cwd, input=inp, text=True,
                          capture_output=True, timeout=timeout, env=env)


def ok(r):
    return r.returncode == 0


# ----------------------------------------------------------------- seed

def seed() -> dict:
    R = {"phase": "seed"}
    work = tempfile_dir("c9-seed-")
    sh(["git", "init", "-q", "-b", "main", work])
    for t in TASKS:
        f = FIXTURES[t]
        os.makedirs(os.path.join(work, t), exist_ok=True)
        open(os.path.join(work, t, f["module"]), "w").write(f["broken"])
        open(os.path.join(work, t, f["testfile"]), "w").write(f["tests"])
    # sanity: every fixture must FAIL its own tests at seed time (H2 base)
    pre = {}
    for t in TASKS:
        f = FIXTURES[t]
        r = sh(["python3", "-m", "pytest", f["testfile"], "-q"],
               cwd=os.path.join(work, t))
        pre[t] = r.returncode != 0
    R["all_broken_at_seed"] = all(pre.values())
    assert R["all_broken_at_seed"], f"fixture not broken: {pre}"
    sh(["git", *GITID, "-C", work, "add", "-A"])
    assert ok(sh(["git", *GITID, "-C", work, "commit", "-qm",
                  "seed: four broken tasks"]))
    # bare repo on example-host-a, durable path (NOT /tmp — the c8 lesson)
    sh(SSH_T + ["rm", "-rf", COORD_PATH])
    assert ok(sh(SSH_T + ["git", "init", "-q", "--bare", COORD_PATH]))
    r = sh(["git", *GITID, "-C", work, "push", "-q", COORD_URL, "main"])
    assert ok(r), f"seed push failed: {r.stderr[:300]}"
    # bare HEAD must point at main (git init defaults to master)
    sh(SSH_T + ["git", "-C", COORD_PATH, "symbolic-ref", "HEAD",
                "refs/heads/main"])
    R["coord"] = COORD_PATH
    return R


def tempfile_dir(prefix):
    import tempfile
    return tempfile.mkdtemp(prefix=prefix)


# --------------------------------------------------------------- worker

def worker(name: str, model: str, on_example-host-b: bool) -> None:
    """Worker loop: claim via CAS, fix for real, return + verdict."""
    scratch = tempfile_dir(f"c9-{name}-")
    # clone once; for each task, fetch main into a fresh branch
    r = sh(["git", "clone", "-q", "--no-checkout", COORD_URL, scratch])
    assert ok(r), r.stderr[:300]
    r = sh(["git", *GITID, "-C", scratch, "checkout", "-q", "main"])
    assert ok(r), r.stderr[:300]

    for task in TASKS:
        # crash-revive: skip tasks with a return ref; revive claimed-
        # without-return orphans (dead-marker the old claim, re-claim)
        lr = sh(["git", "ls-remote", COORD_URL,
                 f"refs/tasks/{task}", f"refs/claims/{task}"])
        have = {ln.split()[1] for ln in lr.stdout.splitlines() if ln.strip()}
        if f"refs/tasks/{task}" in have:
            continue                       # returned — someone finished it
        if f"refs/claims/{task}" in have:  # orphan claim (crashed holder)
            # make the orphan claim visible locally (scratch cloned before
            # it existed), then atomic copy+delete: push it to @orphan
            # AND remove it in ONE ref transaction (push copies; a bare
            # copy would leave the live ref and CAS-create would fail)
            r = sh(["git", "-C", scratch, "fetch", "-q", COORD_URL,
                    f"refs/claims/{task}:refs/claims/{task}"])
            if not ok(r):
                continue
            r = sh(["git", *GITID, "-C", scratch, "push", COORD_URL,
                    f"refs/claims/{task}:refs/claims/{task}@orphan",
                    f":refs/claims/{task}"])
            if not ok(r):
                continue                   # another reviver won
        att = f"att-{name}-{os.urandom(3).hex()}"
        # claim: CAS-create refs/claims/<task> at an empty commit
        r = sh(["git", *GITID, "-C", scratch, "commit", "-q",
                "--allow-empty", "-m", f"claim {att}"])
        if not ok(r):
            continue
        lease = f"--force-with-lease=refs/claims/{task}:"
        r = sh(["git", *GITID, "-C", scratch, "push", lease, COORD_URL,
                f"HEAD:refs/claims/{task}"])
        if not ok(r):
            continue                      # lost the race — clean
        # pull main's task dir into a work tree
        tree = tempfile_dir(f"c9-{name}-{task}-")
        r = sh(["git", "clone", "-q", COORD_URL, tree])
        if not ok(r):
            continue
        f = FIXTURES[task]
        # real model work (V4 recipe: opencode anchors by $PWD)
        env = dict(os.environ, PWD=tree, OLDPWD=tree)
        oc_bin = os.environ.get("OPENCODE_BIN", "opencode")
        try:
            oc = sh([oc_bin, "run", "--pure", "-m", model,
                     f["brief"]], cwd=tree, env=env, timeout=420)
        except subprocess.TimeoutExpired:
            oc = None
        # host pytest decides (H2: merit from tests, not the model).
        # cwd MUST be the task dir — tests are relative to it (the
        # common-mode trap the round caught before it poisoned H2).
        pr = sh(["python3", "-m", "pytest", f["testfile"], "-q"],
                cwd=os.path.join(tree, task))
        fixed = ok(pr)
        # commit the tree state (fix or no-op) with the attempt trailer
        sh(["git", *GITID, "-C", tree, "add", "-A"])
        msg = (f"fix {task}\n\nTask: {task}\nAttempt: {att}\n"
               f"Fixed: {'true' if fixed else 'false'}")
        sh(["git", *GITID, "-C", tree, "commit", "-qm", msg])
        # return: create-once CAS on refs/tasks/<task>
        lease = f"--force-with-lease=refs/tasks/{task}:"
        r1 = sh(["git", *GITID, "-C", tree, "push", lease, COORD_URL,
                 f"HEAD:refs/tasks/{task}"])
        # verdict: root commit in a throwaway repo, create-once CAS
        v = tempfile_dir(f"c9-{name}-v-")
        sh(["git", "init", "-q", "-b", "main", v])
        sh(["git", *GITID, "-C", v, "commit", "-q", "--allow-empty",
            "-m", (f"verdict\ntask: {task}\nattempt: {att}\n"
                    f"fixed: {'true' if fixed else 'false'}\n"
                    f"host: {name}")])
        lease = f"--force-with-lease=refs/verdicts/{task}:"
        r2 = sh(["git", *GITID, "-C", v, "push", lease, COORD_URL,
                 f"HEAD:refs/verdicts/{task}"])
        print(json.dumps({"event": "attempted", "worker": name,
                          "task": task, "att": att, "fixed": fixed,
                          "return_pushed": ok(r1),
                          "verdict_pushed": ok(r2),
                          "oc_rc": oc.returncode if oc else "timeout",
                          "oc_err": (oc.stderr or "")[-200:]
                          if oc and oc.returncode else ""}),
              flush=True)
    # observability: a silent clean exit (nothing left to claim) must be
    # visible — the example-host-c-v2 heir looked "dead" when it was merely done
    print(json.dumps({"event": "worker_done", "worker": name}),
          flush=True)


# -------------------------------------------------------------- rejudge

def rejudge(task: str) -> None:
    """Disclosed repair for v1-poisoned verdicts (worker pytest ran with
    cwd=repo-root instead of the task dir -> file-not-found -> always
    'not fixed'). The frozen H2 oracle is independent host pytest on the
    LIVE return commit — re-run it here, and only if it passes, swap the
    verdict ref in ONE guarded server transaction: update with old-sha
    CAS (true create-once successor) + create the @v1-poisoned preserve.
    Thresholds untouched; the poisoned verdict stays readable at
    @v1-poisoned; disclosed in LOOP notes + VERDICTS."""
    q = ["git", "-C", COORD_PATH]

    def srv(*args):
        cmd = " ".join(shlex.quote(a) for a in list(q) + list(args))
        r = sh(SSH_T + [cmd])
        if r.returncode not in (0, 1):
            raise RuntimeError(f"srv {args[:2]}: {r.stderr[:300]}")
        return r.stdout

    vref = f"refs/verdicts/{task}"
    old = srv("rev-parse", "--verify", vref).strip()
    if "@" in old:
        raise SystemExit(f"{vref}: unexpected annotated sha")
    lclaim = srv("rev-parse", "--verify", f"refs/claims/{task}").strip()
    att = srv("log", "-1", "--format=%B", lclaim).strip().split()[-1]
    # fresh oracle run on the LIVE return commit (frozen H2 semantics)
    tmp = tempfile_dir("c9-rejudge-")
    assert ok(sh(["git", "clone", "-q", "--no-checkout", COORD_URL, tmp]))
    r = sh(["git", "-C", tmp, "fetch", "-q", COORD_URL,
            f"refs/tasks/{task}:refs/heads/rt"])
    assert ok(r), r.stderr[:200]
    assert ok(sh(["git", "-C", tmp, "checkout", "-q", "rt"]))
    f = FIXTURES[task]
    pr = sh(["python3", "-m", "pytest", f["testfile"], "-q"],
            cwd=os.path.join(tmp, task))   # task dir — the whole point
    if not ok(pr):
        raise SystemExit(f"refusing to rejudge {task}: oracle says NOT fixed")
    # new verdict commit (root, same schema as the worker's)
    v = tempfile_dir("c9-rejudge-v-")
    sh(["git", "init", "-q", "-b", "main", v])
    sh(["git", *GITID, "-C", v, "commit", "-q", "--allow-empty", "-m",
        (f"verdict\ntask: {task}\nattempt: {att}\nfixed: true\n"
         f"host: example-host-b-rejudge\n"
         f"note: v1 worker pytest-cwd bug disclosed; oracle re-run on "
         f"live return commit")])
    staging = f"refs/rejudge-staging/{task}"
    r = sh(["git", *GITID, "-C", v, "push", "-q", COORD_URL,
            f"HEAD:{staging}"])
    assert ok(r), r.stderr[:300]
    new = srv("rev-parse", "--verify", staging).strip()
    # ONE atomic, guarded transaction: CAS-update the live ref, preserve
    # the poisoned verdict, drop the staging ref
    txn = ("start\n"
           f"update {vref} {new} {old}\n"
           f"create {vref}@v1-poisoned {old}\n"
           f"delete {staging}\n"
           "prepare\ncommit\n")
    r = sh(SSH_T + ["git", "-C", COORD_PATH, "update-ref", "--stdin"],
           inp=txn)
    print(json.dumps({"event": "rejudge", "task": task, "att": att,
                      "fixed": True, "old_verdict": old[:12],
                      "new_verdict": new[:12], "txn_rc": r.returncode,
                      "txn_out": (r.stdout + r.stderr)[-200:]}),
          flush=True)
    assert r.returncode == 0, "guarded swap failed"


# ---------------------------------------------------------------- audit

def audit() -> None:
    R = {"probe": "c9-real-workload-cross-host",
         "freeze": "experiments/c9/FREEZE-C9.md",
         "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    q = ["git", "-C", COORD_PATH]

    def srv(*args):
        cmd = " ".join(shlex.quote(a) for a in list(q) + list(args))
        r = sh(SSH_T + [cmd])
        if r.returncode not in (0, 1):
            raise RuntimeError(f"srv {args[:2]}: {r.stderr[:300]}")
        return r.stdout

    refs = {}
    for ln in srv("for-each-ref",
                  "--format=%(refname) %(objectname)").splitlines():
        n, sha_ = ln.split()
        refs[n] = sha_
    R["refs"] = sorted(refs)

    def body(sha_):
        return srv("log", "-1", "--format=%B", sha_)

    claim_refs = [n for n in refs if n.startswith("refs/claims/")]
    live_claims = [n for n in claim_refs if "@" not in n]
    dead_claims = [n for n in claim_refs if "@" in n]
    claims = {}
    for n in live_claims:
        sha_ = refs[n]
        task = n[len("refs/claims/"):]
        att = body(sha_).strip().split()[-1]
        claims[task] = {"att": att, "state": "live"}
    returns = {}
    for n, sha_ in refs.items():
        if n.startswith("refs/tasks/") and "@" not in n:
            t = n[len("refs/tasks/"):]
            b = body(sha_)
            att = next((ln.split("Attempt: ", 1)[1].strip()
                        for ln in b.splitlines()
                        if ln.startswith("Attempt: ")), None)
            returns[t] = {"att": att, "sha": sha_}
    verdicts = {}
    for n, sha_ in refs.items():
        if n.startswith("refs/verdicts/") and "@" not in n:
            t = n[len("refs/verdicts/"):]
            d = {}
            for ln in body(sha_).splitlines():
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    d[k.strip()] = v.strip()
            verdicts[t] = d

    # H1 exactly-once (raw ref count: duplicates can never collapse;
    # @-suffixed dead markers are history, like c8 — never counted)
    h1 = {
        "one_live_claim_per_task": len(live_claims) == len(TASKS),
        "all_tasks_claimed_once": set(claims) == set(TASKS),
        "all_tasks_returned_once": set(returns) == set(TASKS),
        "return_att_matches_claim": all(
            returns.get(t, {}).get("att") == claims[t]["att"]
            for t in TASKS),
        "verdicts_one_per_task": set(verdicts) == set(TASKS),
        "verdict_att_matches_claim": all(
            verdicts.get(t, {}).get("attempt") == claims[t]["att"]
            for t in TASKS),
    }

    # H2 verdict == independent host pytest (fetch each return, re-run)
    h2 = {}
    tmp = tempfile_dir("c9-audit-")
    for t in TASKS:
        f = FIXTURES[t]
        w = os.path.join(tmp, t)
        sha_ = returns.get(t, {}).get("sha")
        if not sha_:
            h2[t] = {"error": "no return"}
            continue
        r = sh(["git", "clone", "-q", "--no-checkout", COORD_URL, w])
        # fetch by REF (raw-sha fetch needs uploadpack.allow* on server)
        r = sh(["git", "-C", w, "fetch", "-q", COORD_URL,
                f"refs/tasks/{t}:refs/heads/rt"])
        r = sh(["git", "-C", w, "checkout", "-q", "rt"])
        pr = sh(["python3", "-m", "pytest", f["testfile"], "-q"],
                cwd=os.path.join(w, t))
        host_fixed = ok(pr)
        h2[t] = {"host_fixed": host_fixed,
                 "verdict_says": verdicts.get(t, {}).get("fixed"),
                 "match": (verdicts.get(t, {}).get("fixed")
                           == ("true" if host_fixed else "false"))}
    h1["h2_all_match"] = all(v.get("match") for v in h2.values())

    # H3 split (measured, not gated)
    hosts = {}
    for t, d in claims.items():
        host = d["att"].split("-")[1]
        hosts[host] = hosts.get(host, 0) + 1
    R["h1"] = h1
    R["h2"] = h2
    R["h3_host_split"] = hosts
    R["dead_claim_markers"] = dead_claims
    R["h1_pass"] = all(h1.values())
    R["verdict"] = ("c-real-workload-cross-host-verified"
                    if R["h1_pass"] else "H1FAIL-c9")
    R["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(os.path.join(HERE, "results_c9.json"), "w") as f:
        json.dump(R, f, indent=1)
    with open(os.path.join(HERE, "refs_dump_c9.txt"), "w") as f:
        for n in sorted(refs):
            f.write(f"{n} {refs[n]}\n")
            if not n.startswith("refs/heads/"):
                f.write("  :: " + body(refs[n]).strip()
                        .replace("\n", " | ") + "\n")
    print(json.dumps({"h1_pass": R["h1_pass"], "h1": h1,
                      "h2": h2, "h3": hosts,
                      "verdict": R["verdict"]}, indent=1))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if mode == "seed":
        print(json.dumps(seed()))
    elif mode == "worker":
        worker(sys.argv[2], sys.argv[3],
               on_example-host-b=(len(sys.argv) > 4 and sys.argv[4] == "example-host-b"))
    # NOTE: on_example-host-b only tags the host split; opencode providers are
    # host-local config (hpc-glm on example-host-c, example-host-b model from c6 recipe).
    elif mode == "audit":
        audit()
    elif mode == "rejudge":
        for t in (sys.argv[2:] or TASKS):
            rejudge(t)
    elif mode == "srv-stdin":
        # sweep helper: run `git update-ref --stdin` ON the repo host
        # (one atomic transaction); input arrives on our stdin
        data = sys.stdin.read()
        r = sh(SSH_T + ["git", "-C", COORD_PATH, "update-ref",
                        "--stdin"], inp=data)
        print(r.stdout, r.stderr, "RC", r.returncode)
        sys.exit(r.returncode)
