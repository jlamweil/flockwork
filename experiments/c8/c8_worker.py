#!/usr/bin/env python3
"""c8 — git-native claim-CAS coordination over a REAL two-host transport
(FREEZE-C8.md). One script, four modes: worker / victim / heir / sweep.

All coordination state lives in the bare repo on example-host-a; every mutation
is a server-side atomic ref update:
  claim  = push --force-with-lease=refs/claims/<task>: <sha>:<ref>
           (empty expect = must-not-exist = CAS-create; rc!=0 = lost)
  return = plumbing commit with `Attempt:` trailer, pushed with the same
           create-once CAS on refs/tasks/<task>
  note   = `notes --ref=verdicts add -F -` (server-side; bounded retry)
  sweep  = local on the repo host: reconcile-by-probe kills the tagged
           orphan ON example-host-b via ssh (the cross-host leg), preserves the
           dead attempt at refs/claims/<task>@<att>, CAS-deletes the
           live claim (c7 txn shape); run is idempotent.
No flock, no JSONL ledger — the only state is the git repo.
"""
import json
import os
import random
import subprocess
import sys
import time

CLEAN_TASKS = [f"t{i}" for i in range(1, 10)]
CRASH_TASK = "t-crash"
ALL_TASKS = CLEAN_TASKS + [CRASH_TASK]
ZERO = "0" * 40
GITID = ["-c", "user.email=c8@twohost", "-c", "user.name=c8"]
HOST = os.uname().nodename


def sh(cmd, inp=None, timeout=60, env=None, cwd=None):
    return subprocess.run(cmd, input=inp, text=True, capture_output=True,
                          timeout=timeout, env=env, cwd=cwd)


class Coord:
    """Server-side git ops. ssh:// URL -> ssh-exec (ControlMaster when a
    cm_dir is given); local path -> direct exec (the zero-transport lane)."""

    def __init__(self, url, cm_dir=None):
        self.url = url.rstrip("/")
        self.ssh = self.url.startswith("ssh://")
        if self.ssh:
            hostpath = self.url[len("ssh://"):]
            self.host, rest = hostpath.split("/", 1)
            self.path = "/" + rest  # server-side absolute path
            self.remote = (["ssh", "-o", "BatchMode=yes"]
                           + (["-o", f"ControlPath={cm_dir}/cm-%r@%h:%p",
                               "-o", "ControlMaster=auto",
                               "-o", "ControlPersist=600"] if cm_dir else [])
                           + [self.host, "git", "-C", self.path])
        else:
            self.path = self.url
            self.remote = ["git", "-C", self.path]
        self.ops = []  # [op-name, ms] for the H3 measurement

    def srv(self, *args, inp=None, timeout=60):
        t0 = time.perf_counter()
        r = sh(self.remote + list(args), inp=inp, timeout=timeout)
        self.ops.append([args[0], round((time.perf_counter() - t0) * 1000, 1)])
        return r

    def local(self, *args, inp=None, timeout=60, env=None):
        t0 = time.perf_counter()
        r = sh(["git", *GITID, *list(args)], inp=inp, timeout=timeout, env=env)
        self.ops.append([args[0] + ":local",
                         round((time.perf_counter() - t0) * 1000, 1)])
        return r

    def client(self, *args, cwd=None, inp=None, label=None, timeout=60):
        """Timed client-side git op (clone/ls-remote/push) — the H3 lanes."""
        t0 = time.perf_counter()
        r = sh(["git", *GITID, *list(args)], inp=inp, timeout=timeout, cwd=cwd)
        self.ops.append([label or args[0] + ":client",
                         round((time.perf_counter() - t0) * 1000, 1)])
        return r


def attempt_id(hosttag: str) -> str:
    return f"att-{hosttag}-{os.urandom(3).hex()}"


def setup_scratch(coord: Coord, scratch: str) -> float:
    t0 = time.perf_counter()
    sh(["rm", "-rf", scratch])
    r = sh(["git", "clone", "-q", "--no-checkout", coord.url, scratch])
    if r.returncode != 0:
        raise RuntimeError(f"clone failed: {r.stderr[:500]}")
    return round((time.perf_counter() - t0) * 1000, 1)


def cas_claim(coord: Coord, scratch: str, task: str, att: str) -> bool:
    """CAS-create the claim ref at a claim commit (subject = att)."""
    c = coord.client("-C", scratch, "commit", "-q", "--allow-empty",
                     "-m", f"claim {att}", label="claim-commit")
    if c.returncode != 0:
        raise RuntimeError(f"claim commit failed: {c.stderr[:500]}")
    r = coord.client("-C", scratch, "push",
                     f"--force-with-lease=refs/claims/{task}:",
                     coord.url, f"HEAD:refs/claims/{task}", label="push-claim")
    return r.returncode == 0


def do_task(coord: Coord, scratch: str, task: str, att: str) -> dict:
    """Return commit (create-once CAS) + verdict note (bounded retry)."""
    base_tree = sh(["git", "-C", scratch, "rev-parse",
                    "origin/main^{tree}"]).stdout.strip()
    blob = sh(["git", *GITID, "-C", scratch, "hash-object", "-w", "--stdin"],
              inp=f"fix for {task} by {att}\n").stdout.strip()
    idx = os.path.join(scratch, ".git", f"idx-{task}-{att}")
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    sh(["git", "-C", scratch, "read-tree", base_tree], env=env)
    sh(["git", "-C", scratch, "update-index", "--add", "--cacheinfo",
        f"100644,{blob},fix-{task}.txt"], env=env)
    tree = sh(["git", "-C", scratch, "write-tree"], env=env).stdout.strip()
    os.unlink(idx)
    sha = sh(["git", *GITID, "-C", scratch, "commit-tree", tree,
              "-p", "origin/main", "-m",
              f"fix {task}\n\nTask: {task}\nAttempt: {att}\n"]).stdout.strip()
    rejected, pushed = False, False
    for attempt in range(3):
        r = coord.client("-C", scratch, "push",
                         f"--force-with-lease=refs/tasks/{task}:",
                         coord.url, f"{sha}:refs/tasks/{task}",
                         label="push-return")
        if r.returncode == 0:
            pushed = True
            break
        if "stale info" in r.stderr:
            rejected = True  # someone else closed this task: never retry
            break
        time.sleep(0.3)
    note_retries = 0
    for attempt in range(3):
        r = coord.srv("notes", "--ref=verdicts", "add", "-F", "-", sha,
                      inp=f"task: {task}\nattempt: {att}\nfixed: true\n")
        if r.returncode == 0:
            break
        note_retries += 1
        time.sleep(0.2)
    return {"return": sha, "return_pushed": pushed,
            "return_rejected": rejected, "note_retries": note_retries}


def scan(coord: Coord) -> tuple:
    r = coord.client("ls-remote", coord.url,
                     "refs/claims/*", "refs/tasks/*", label="ls-remote")
    claims, returns = set(), set()
    for line in r.stdout.splitlines():
        ref = line.split("\t", 1)[1]
        if ref.startswith("refs/claims/"):
            name = ref[len("refs/claims/"):]
            if "@" not in name:
                claims.add(name)
        elif ref.startswith("refs/tasks/"):
            returns.add(ref[len("refs/tasks/"):])
    return claims, returns


def worker_mode(coord: Coord, idx: int, cm_dir) -> None:
    hosttag = f"{'example-host-b' if coord.ssh else 'example-host-a'}{idx}"
    scratch = f"/tmp/c8/w-{hosttag}"
    startup_ms = setup_scratch(coord, scratch)
    wins, losses, deadline = [], 0, time.perf_counter() + 110
    stuck = True
    while time.perf_counter() < deadline:
        claims, returns = scan(coord)
        free = [t for t in CLEAN_TASKS if t not in claims and t not in returns]
        if not free:
            if len(returns) >= len(CLEAN_TASKS):
                stuck = False
                break
            time.sleep(0.25)
            continue
        task = free[idx % len(free)]
        att = attempt_id(hosttag)
        if cas_claim(coord, scratch, task, att):
            res = do_task(coord, scratch, task, att)
            wins.append({"task": task, "att": att, **res})
        else:
            losses += 1
            time.sleep(random.uniform(0.03, 0.15))
    print(json.dumps({
        "event": "done" if not stuck else "stuck", "host": HOST,
        "lane": "ssh" if coord.ssh else "local", "idx": idx,
        "wins": wins, "losses": losses, "startup_ms": startup_ms,
        "ops": coord.ops}), flush=True)


def victim_mode(coord: Coord, task: str, cm_dir) -> None:
    hosttag = "example-host-bV"
    scratch = f"/tmp/c8/w-{hosttag}"
    setup_scratch(coord, scratch)
    att = attempt_id(hosttag)
    if not cas_claim(coord, scratch, task, att):
        print(json.dumps({"event": "claim-lost", "att": att}), flush=True)
        sys.exit(3)
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)",
                      f"c8-{task}-{att}"], start_new_session=True)
    print(json.dumps({"event": "claimed", "att": att,
                      "tag": f"c8-{task}-{att}"}), flush=True)
    time.sleep(300)  # hold the claim until the driver SIGKILLs us


def heir_mode(coord: Coord, task: str, cm_dir) -> None:
    hosttag = "example-host-aH"
    scratch = f"/tmp/c8/w-{hosttag}"
    setup_scratch(coord, scratch)
    att = attempt_id(hosttag)
    if not cas_claim(coord, scratch, task, att):
        print(json.dumps({"event": "claim-lost", "att": att}), flush=True)
        sys.exit(3)
    res = do_task(coord, scratch, task, att)
    print(json.dumps({"event": "done", "host": HOST, "att": att,
                      "task": task, **res, "ops": coord.ops}), flush=True)


def scan_tagged_local(tag: str) -> list:
    hits = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if any(tag.encode() in a for a in args):
            hits.append(int(pid))
    return hits


def verdict_text(coord: Coord) -> str:
    r = coord.srv("notes", "--ref=verdicts", "list")
    shas = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
    if not shas:
        return ""
    return coord.srv("cat-file", "--batch",
                     inp="".join(s + "\n" for s in shas)).stdout


def has_final_verdict(text: str, task: str) -> bool:
    return f"task: {task}\n" in text and "fixed: true" in text


def sweep_mode(coord: Coord, example-host-b_ssh: list) -> None:
    """Runs ON the repo host (example-host-a). c7's crash-safe order, cross-host:
    (1) kill the tagged orphan on example-host-b via ssh; (2) preserve the dead
    attempt at refs/claims/<task>@<att> (create-if-absent); (3) CAS-delete
    the live claim. Interrupted between (2) and (3) re-resolves on rerun."""
    text = verdict_text(coord)
    requeued, killed_example-host-b, killed_local = [], {}, []
    for task in ALL_TASKS:
        val = coord.srv("rev-parse", "--verify", "--quiet",
                        f"refs/claims/{task}").stdout.strip()
        if not val or has_final_verdict(text, task):
            continue
        subject = coord.srv("log", "-1", "--format=%s", val).stdout.strip()
        att = subject.split()[-1]
        tag = f"c8-{task}-{att}"
        r = sh(example-host-b_ssh + ["pgrep", "-f", tag])
        pids = [int(p) for p in r.stdout.split()]
        for pid in pids:
            sh(example-host-b_ssh + ["kill", "-9", str(pid)])
        verify = sh(example-host-b_ssh + ["pgrep", "-f", tag])
        killed_example-host-b[tag] = {"pids": pids, "gone": verify.returncode != 0}
        for pid in scan_tagged_local(tag):
            try:
                os.kill(pid, 9)
                killed_local.append(pid)
            except ProcessLookupError:
                pass
        coord.srv("update-ref", "--stdin",
                  inp=(f"start\nupdate refs/claims/{task}@{att} {val} {ZERO}"
                       "\nprepare\ncommit\n"))
        r = coord.srv("update-ref", "--stdin",
                      inp=(f"start\ndelete refs/claims/{task} {val}\n"
                           "prepare\ncommit\n"))
        if r.returncode == 0:
            requeued.append(f"{task}.{att}")
    print(json.dumps({"event": "swept", "requeued": requeued,
                      "killed_example-host-b": killed_example-host-b,
                      "killed_local": killed_local}), flush=True)


def main() -> None:
    mode = sys.argv[1]
    coord = Coord(sys.argv[2],
                  cm_dir=sys.argv[4] if len(sys.argv) > 4 else None)
    if mode == "worker":
        worker_mode(coord, int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None)
    elif mode == "victim":
        victim_mode(coord, sys.argv[3], None)
    elif mode == "heir":
        heir_mode(coord, sys.argv[3], None)
    elif mode == "sweep":
        sweep_mode(coord, ["ssh", "-o", "BatchMode=yes"] + sys.argv[3].split(","))
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
