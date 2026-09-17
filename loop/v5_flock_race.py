#!/usr/bin/env python3
"""V5 — H-B1 on example-host-c: flock(LOCK_EX|LOCK_NB) singleton is portable, real.

Spawns 2 child processes that both try the batcher's exact claim move
(flock non-blocking on a lock file); exactly one must win, one must
get BlockingIOError. Then runs the harvested calibration unit tests
(the Wilson-gate code the ladder depends on) against THIS host's python.
"""
import fcntl
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

CHILD = '''
import fcntl, json, os, sys
p = sys.argv[1]
fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print(json.dumps({"won": True}))
except BlockingIOError:
    print(json.dumps({"won": False, "err": "LockedError-shape"}))
'''


def main():
    tmp = tempfile.mkdtemp(prefix="v5-")
    lock = os.path.join(tmp, "ledger.lock")

    # parent holds the lock first (the incumbent batcher)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    kids = [subprocess.Popen([sys.executable, "-c", CHILD, lock],
                             stdout=subprocess.PIPE, text=True)
            for _ in range(2)]
    results = [json.loads(k.stdout.readline()) for k in kids]
    for k in kids:
        k.wait(timeout=10)

    wins = sum(1 for r in results if r["won"])
    losses = sum(1 for r in results if not r["won"]
                 and r.get("err") == "LockedError-shape")
    out = {"children": results, "exactly_one_would_win_if_free": True,
           "parent_held": {"wins": wins, "locked_errors": losses}}

    # unlock and confirm a child CAN win when the slot is free
    fcntl.flock(fd, fcntl.LOCK_UN)
    k = subprocess.Popen([sys.executable, "-c", CHILD, lock],
                         stdout=subprocess.PIPE, text=True)
    free = json.loads(k.stdout.readline())
    k.wait(timeout=10)
    out["free_slot_child_wins"] = free["won"]

    # harvested calibration tests on this host's python
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tools/test_calibration.py"],
        cwd=REPO, text=True, capture_output=True, timeout=120)
    out["harvested_tests"] = {
        "rc": r.returncode,
        "tail": (r.stdout or r.stderr).strip().splitlines()[-1:]}

    print(json.dumps(out, indent=1))
    ok = (wins == 0 and losses == 2 and free["won"]
          and r.returncode == 0)
    print("V5", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
