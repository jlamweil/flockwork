#!/usr/bin/env python3
"""Design B minimal worker loop (DESIGNS.md H-B2): the one missing leg of
production, as a single file with no server component.

claim (flock, singleton) -> dispatch (docker opencode) -> harvest (git)
-> host-verify (pytest) -> ledger rows (attemptId att-*).

Semantics carried over from production (SURVEY §A):
- claim = send-intent: acquiring the lock mints a fresh attemptId, so a
  retry-of-task is distinguishable from a duplicate-task (E2).
- singleton: second holder of the ledger lock gets AlreadyClaimed (E3,
  portability proven by C-flock/H-B1).
- ledger: JSONL append + fsync; rows are audit + recovery input (E1).
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
import uuid


class AlreadyClaimed(Exception):
    """Another loop instance holds the ledger lock (E3 semantics)."""


class Claim:
    """Exclusive ledger claim; held fd IS the claim (release = close)."""

    def __init__(self, ledger_path: str):
        self.lock_path = ledger_path + ".lock"
        self.fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise AlreadyClaimed(self.lock_path)

    def release(self):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def append_row(ledger_path: str, row: dict) -> None:
    """JSONL append + fsync (E1: the ledger survives this process dying)."""
    with open(ledger_path, "a") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def dispatch_and_harvest(
    task: str,
    attempt_id: str,
    ws: str,
    ledger_path: str,
    image: str,
    model: str,
    brief: str,
    opencode_bin: str,
    auth_json: str,
    timeout_s: int = 240,
) -> dict:
    """Run the containerized worker on `ws`, harvest its git commit.

    Repairs applied versus experiments/c1 (E10 a/b/c, plus d found live):
    (a) fixtures exist INSIDE ws before the mount;
    (b) ws is pre-created on host by uid 1004, so docker never
        auto-creates a root-owned mountpoint;
    (c) auth is ro-mounted at $HOME/auth.json and staged by cp into
        $HOME/.local/share/opencode/ — docker root-creates intermediate
        dirs of a mount target (E10-d: mounting auth at
        .local/share/opencode/ made ~/.local root-owned and opencode
        died on `mkdir ~/.local/state` EACCES), so the only root-free
        mountpoint is $HOME itself; the cp line is c1's proven path;
    (e) no /secrets path exists for opencode's external_directory
        auto-reject to trip on (E10-c).
    """
    home = "/home/worker"
    container_name = f"c2-{task}-{attempt_id}"
    container_script = (
        'mkdir -p ~/.local/share/opencode '
        '&& cp ~/auth.json ~/.local/share/opencode/auth.json\n'
        'opencode run --pure -m "$MODEL" "$BRIEF"; oc=$?\n'
        'if [ -n "$(git status --porcelain)" ]; then\n'
        '  git add -A\n'
        '  git -c user.email=worker@c2.local -c user.name=worker '
        'commit -q -m "fix: $TASK" -m "Attempt: $ATT"\n'
        '  echo "COMMIT_SHA $(git rev-parse HEAD)"\n'
        "else\n"
        '  echo "COMMIT_SHA -"\n'
        "fi\n"
        "exit $oc\n"
    )
    cmd = [
        "docker", "run", "--rm", "--name", container_name,
        "--user", "1004:1004",
        "-v", f"{ws}:/work", "-w", "/work",
        "-v", f"{opencode_bin}:/usr/local/bin/opencode:ro",
        "-v", f"{auth_json}:{home}/auth.json:ro",
        "-e", f"HOME={home}",
        "-e", f"MODEL={model}",
        "-e", f"BRIEF={brief}",
        "-e", f"TASK={task}",
        "-e", f"ATT={attempt_id}",
        image, "bash", "-c", container_script,
    ]
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout_s + 10)
    except subprocess.TimeoutExpired:
        # kill the CONTAINER, not just the client (a killed client leaves
        # the container running — c1's 429s/124-exit rows, E10 postscript)
        subprocess.run(["docker", "kill", container_name], capture_output=True)
        r = subprocess.run(["docker", "wait", container_name],
                           capture_output=True, text=True)
        r.returncode = 124
        r.stdout = ""
        r.stderr = f"timeout after {timeout_s}s; container killed"
    wall = time.perf_counter() - t0

    stdout_path = f"{ws}.stdout.txt"
    with open(stdout_path, "w") as f:
        f.write(r.stdout)
    stderr_path = f"{ws}.stderr.txt"
    with open(stderr_path, "w") as f:
        f.write(r.stderr)

    sha = None
    for line in r.stdout.splitlines():
        if line.startswith("COMMIT_SHA "):
            v = line.split(" ", 1)[1].strip()
            sha = None if v == "-" else v

    trailer_ok = False
    patch = ""
    if sha:
        msg = subprocess.run(["git", "log", "-1", "--format=%B", sha],
                             cwd=ws, capture_output=True, text=True).stdout
        trailer_ok = any(
            line.strip().startswith("Attempt: att-")
            for line in msg.splitlines())
        patch = subprocess.run(["git", "show", "--format=", sha],
                               cwd=ws, capture_output=True, text=True).stdout
    else:
        patch = subprocess.run(["git", "diff"], cwd=ws,
                               capture_output=True, text=True).stdout
    patch_path = f"{ws}.patch.diff"
    with open(patch_path, "w") as f:
        f.write(patch)
    # non-vacuous H-C2 check: the commit must have captured the whole fix
    worktree_clean = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ws,
        capture_output=True, text=True).stdout.strip() == ""

    return {
        "container_exit": r.returncode,
        "wall_s": round(wall, 1),
        "commit_sha": sha,
        "trailer_ok": trailer_ok,
        "patch_bytes": len(patch.encode()),
        "worktree_clean": worktree_clean,
    }


def host_verify(ws: str) -> dict:
    r = subprocess.run(["python3", "-m", "pytest", "-q"], cwd=ws,
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    import re
    passed = re.findall(r"(\d+) passed", out)
    failed = re.findall(r"(\d+) failed", out)
    return {
        "host_exit": r.returncode,
        "host_passed": int(passed[-1]) if passed else 0,
        "host_failed": int(failed[-1]) if failed else 0,
    }


def run_case(
    task: str,
    ws: str,
    ledger_path: str,
    image: str,
    model: str,
    brief: str,
    opencode_bin: str,
    auth_json: str,
    timeout_s: int = 240,
    attempt_id: str | None = None,
) -> dict:
    """One full claim->dispatch->harvest->verify->ledger cycle.

    attempt_id: pass one when the claim already happened upstream (c3
    queue) so ledger rows join to the upstream claim; mints one otherwise.
    """
    claim = Claim(ledger_path)  # raises AlreadyClaimed if singleton held
    try:
        if attempt_id is None:
            attempt_id = "att-" + uuid.uuid4().hex[:8]
        append_row(ledger_path, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": "claim", "task": task, "attemptId": attempt_id,
        })
        d = dispatch_and_harvest(
            task, attempt_id, ws, ledger_path, image, model, brief,
            opencode_bin, auth_json, timeout_s)
        v = host_verify(ws)
        fixed = (d["patch_bytes"] > 0 and v["host_passed"] > 0
                 and v["host_failed"] == 0)
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": "verdict", "task": task, "attemptId": attempt_id,
            "model": model, "image": image,
            **d, **v, "fixed": fixed,
        }
        append_row(ledger_path, row)
        return row
    finally:
        claim.release()
