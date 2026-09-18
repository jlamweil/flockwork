#!/usr/bin/env python3
"""V6 — H-B3 end-to-end at the LEDGER level (the batcher-side contract).

Uses the harvested worker_loop primitives — the REAL production code
path Design B mandates — around a SECOND real opencode case on example-host-c:

  1. Claim(ledger) mints a fresh att-* (claim = send-intent, E2).
  2. dispatch_and_harvest-equivalent runs via loop.v4_worker_backend
     (host opencode, the two new V4 constraints applied) on a DIFFERENT
     real bug (sum.py off-by-one) — model edits, host pytest decides.
  3. Ledger rows land in the production row shape:
     claim row (attemptId) -> verdict row (att, ok, task, wall) —
     appended + fsync'd via worker_loop.append_row.
  4. Crash-recovery semantics, REAL: simulate a dead claimant by
     releasing the flock without a verdict row, re-claim (new att),
     verify the ledger now carries BOTH attempts (retry distinguishable
     from duplicate, E2) and that the att ledger row count matches.
  5. Orphan sweep row: kill nothing here; assert the first attempt is
     recorded with claim-only state (the exact shape the c7/c8 sweep
     reconciles), so the git-side sweep has the same input contract.

Exit 0 iff: case 2 fixes + pytest green, rows exact, recovery shows
two atts for one task with the second completing.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
from tools.worker_loop import Claim, append_row, AlreadyClaimed  # noqa: E402
from v4_worker_backend import dispatch  # the V4-verified adapter  # noqa: E402

BROKEN = '''def sum_to(n: int) -> int:
    """Sum integers 1..n inclusive."""
    total = 0
    for i in range(1, n):        # bug: excludes n
        total += i
    return total
'''

TESTS = '''from sum_to import sum_to

def test_sum_to_1():
    assert sum_to(1) == 1

def test_sum_to_10():
    assert sum_to(10) == 55

def test_sum_to_100():
    assert sum_to(100) == 5050
'''

PROMPT = ("Fix the bug in sum_to.py: sum_to(10) must be 55 but currently "
          "returns 45 — the range excludes n. Fix sum_to.py only. When "
          "done run: python3 -m pytest test_sum_to.py -q")


def sh(cmd, cwd=None, timeout=90):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True,
                          timeout=timeout)


def pytest_verdict(ws):
    r = sh(["python3", "-m", "pytest", "test_sum_to.py", "-q"], cwd=ws)
    return r.returncode == 0, (r.stdout or "")[-120:]


def main():
    tmp = tempfile.mkdtemp(prefix="v6-")
    ledger = os.path.join(tmp, "ledger.jsonl")
    ws = os.path.join(tmp, "ws")

    # workspace: own git root (V4 constraint 1)
    os.makedirs(ws)
    open(os.path.join(ws, "sum_to.py"), "w").write(BROKEN)
    open(os.path.join(ws, "test_sum_to.py"), "w").write(TESTS)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.email=v6@t", "-c", "user.name=v6",
                 "commit", "-qm", "base"]):
        assert sh(cmd, cwd=ws).returncode == 0

    out = {"att1_rows": [], "recovery": {}, "case2": {}}

    # --- attempt 1: claim, dispatch, record — then "die" pre-verdict ----
    claim1 = Claim(ledger)
    att1 = f"att-example-host-c-v6-{os.urandom(3).hex()}"
    append_row(ledger, {"id": "task-sum", "status": "running",
                        "attemptId": att1, "ts": time.time()})
    out["att1_rows"].append("claim")
    status, text = dispatch(ws, PROMPT, timeout=420)
    fixed_1, _ = pytest_verdict(ws)
    # simulate crash: claim released WITHOUT verdict row
    claim1.release()

    # --- recovery: re-claim mints a NEW att (E2: retry != duplicate) ----
    try:
        claim2 = Claim(ledger)
        att2 = f"att-example-host-c-v6-{os.urandom(3).hex()}"
        assert att2 != att1
        append_row(ledger, {"id": "task-sum", "status": "running",
                            "attemptId": att2, "ts": time.time(),
                            "resumed": True})
        # the resumed attempt verifies the workspace state on disk
        # (resume+read probe, never blind re-send — c7/c8 sweep shape)
        fixed_2, tail = pytest_verdict(ws)
        append_row(ledger, {"id": "task-sum", "status": "done",
                            "attemptId": att2, "verdict": fixed_2,
                            "wall": None})
        claim2.release()
    except AlreadyClaimed:
        raise

    # --- audit the ledger like the batcher does -------------------------
    rows = [json.loads(ln) for ln in open(ledger)]
    atts = [r["attemptId"] for r in rows if r["id"] == "task-sum"]
    done_rows = [r for r in rows if r.get("status") == "done"
                 and r.get("verdict") is True]
    claim_only = [a for a in atts
                  if not any(r.get("attemptId") == a
                             and r.get("status") == "done" for r in rows)]
    out["recovery"] = {
        "two_atts_one_task": sorted(set(atts)) == sorted([att1, att2]),
        "exactly_one_done_verdict": len(done_rows) == 1,
        "attempt1_claim_only_sweepable": claim_only == [att1],
        "final_case_fixed": fixed_2}
    out["case2"] = {"dispatch_status": status, "pytest_tail": tail,
                    "text_head": text[:150]}

    print(json.dumps(out, indent=1))
    ok = (out["recovery"]["two_atts_one_task"]
          and out["recovery"]["exactly_one_done_verdict"]
          and out["recovery"]["attempt1_claim_only_sweepable"]
          and out["recovery"]["final_case_fixed"]
          and status == "done" and fixed_2)
    print("V6", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
