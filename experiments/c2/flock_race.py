#!/usr/bin/env python3
"""C-flock probe (H-B1, DESIGNS.md §3): does flock(LOCK_EX|LOCK_NB) exclusive-
claim semantics hold across processes on THIS host?

Frozen threshold: 10/10 rounds — exactly 1 winner, 31 contenders get
BlockingIOError, lock released cleanly. Part (a): 2-proc holder/contender
x10. Part (b): 32-proc race x10.

Real subprocesses (flock is a per-open-file-description kernel primitive;
threads would not be a faithful test of the production singleton claim).
"""
import fcntl
import json
import os
import subprocess
import sys
import time

LOCK_PATH = "/tmp/c2-flock-probe.lock"
SELF = os.path.abspath(__file__)


def child(path: str, hold_s: float = 0.0) -> int:
    """Child mode: one nonblocking exclusive-flock attempt."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("BLOCKED", flush=True)
        return 3
    print("WIN", flush=True)  # first line = outcome; sync point while holding
    time.sleep(hold_s)  # winner holds the claim like a live worker would
    os.close(fd)
    print("RELEASED", flush=True)
    return 0


def spawn(mode: str, hold_s: float = 0.0) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, SELF, mode, LOCK_PATH, str(hold_s)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def read_outcome(p: subprocess.Popen) -> str:
    """First stdout line = the attempt's outcome (WIN or BLOCKED).

    readline only: draining to EOF would block until a holding winner
    exits, breaking the hold-time window the probe depends on.
    """
    first = p.stdout.readline().strip()
    if not first:
        p.wait(timeout=30)
        first = f"EXIT{p.returncode}"
    return first


def round_2proc() -> dict:
    os.close(os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644))  # reset
    holder = spawn("child", hold_s=1.0)
    held = read_outcome(holder) == "WIN"  # holder now holds the lock
    contender = spawn("child")
    cont_line = read_outcome(contender)
    contender.wait(timeout=30)
    holder.wait(timeout=30)
    released = "RELEASED" in holder.stdout.read()
    verifier = spawn("child")  # post-release: lock must be acquirable again
    ver_line = read_outcome(verifier)
    return {"held": held, "contender": cont_line, "released": released,
            "verifier": ver_line}


def round_32proc(n: int = 32, hold_s: float = 2.0) -> dict:
    os.close(os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644))  # reset
    procs = [spawn("child", hold_s=hold_s) for _ in range(n)]
    lines = [read_outcome(p) for p in procs]
    for p in procs:  # let the winner release before probing re-acquirability
        p.wait(timeout=30)
    wins = lines.count("WIN")
    blocked = lines.count("BLOCKED")
    verifier = spawn("child")
    ver_line = read_outcome(verifier)
    return {"wins": wins, "blocked": blocked, "other": n - wins - blocked,
            "verifier": ver_line}


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        sys.exit(child(sys.argv[2], float(sys.argv[3])))

    a = [round_2proc() for _ in range(10)]
    b = [round_32proc() for _ in range(10)]
    a_pass = all(r["contender"] == "BLOCKED" and r["released"] and r["verifier"] == "WIN" for r in a)
    b_pass = all(r["wins"] == 1 and r["blocked"] == 31 and r["other"] == 0
                 and r["verifier"] == "WIN" for r in b)
    result = {
        "probe": "C-flock", "hypothesis": "H-B1",
        "part_a_2proc": a, "part_b_32proc": b,
        "a_pass": a_pass, "b_pass": b_pass,
        "verdict": "PASS" if (a_pass and b_pass) else "FAIL",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    out = os.path.join(os.path.dirname(SELF), "results_flock.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({"a_pass": a_pass, "b_pass": b_pass, "verdict": result["verdict"]}))


if __name__ == "__main__":
    main()
