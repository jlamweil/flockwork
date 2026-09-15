#!/usr/bin/env python3
"""C-flock: H-B1 — flock exclusive-claim semantics across processes on this host.

(a) 10 rounds: parent holds LOCK_EX, child (separate process) must get
    BlockingIOError on LOCK_EX|LOCK_NB; after parent releases, child must win.
(b) 10 rounds: 32 concurrent contender processes race one lock; expect exactly
    1 winner (the claimant) and 31 BlockingIOError losers, matching the batcher's
    flock(LOCK_EX|LOCK_NB) singleton protocol (SURVEY E3).
"""
import fcntl, json, os, subprocess, sys, time

LOCK = "/tmp/swarmo_c2_flock_probe.lock"
CHILD = r"""
import fcntl, sys
f = open(sys.argv[1], "a+")
try:
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("WON")
except BlockingIOError:
    print("LOCKED")
"""

def child_attempt():
    out = subprocess.run([sys.executable, "-c", CHILD, LOCK],
                         capture_output=True, text=True).stdout.strip()
    return out

def round_a():
    f = open(LOCK, "a+")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    blocked = child_attempt()
    fcntl.flock(f, fcntl.LOCK_UN)
    after = child_attempt()  # separate process wins once lock is released
    f.close()
    return {"blocked_while_held": blocked, "won_after_release": after}

def round_b(n=32):
    f = open(LOCK, "a+")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # simulate active claimant
    procs = [subprocess.Popen([sys.executable, "-c", CHILD, LOCK],
                              stdout=subprocess.PIPE, text=True)
             for _ in range(n)]
    results = [p.communicate()[0].strip() for p in procs]
    fcntl.flock(f, fcntl.LOCK_UN)
    f.close()
    return {"winners": results.count("WON"),
            "locked_errors": results.count("LOCKED"),
            "other": n - results.count("WON") - results.count("LOCKED")}

if __name__ == "__main__":
    res = {"a_holder contender_rounds": [round_a() for _ in range(10)],
           "b_32way_races": [round_b() for _ in range(10)]}
    print(json.dumps(res, indent=1))
    ok_a = all(r["blocked_while_held"] == "LOCKED" and r["won_after_release"] == "WON"
               for r in res["a_holder contender_rounds"])
    ok_b = all(r["winners"] == 1 and r["locked_errors"] == 31 and r["other"] == 0
               for r in res["b_32way_races"])
    print(f"H-B1 verdict: {'PASS' if ok_a and ok_b else 'FAIL'} "
          f"(a_all_10_ok={ok_a}, b_all_10_ok={ok_b})")
    sys.exit(0 if ok_a and ok_b else 1)
