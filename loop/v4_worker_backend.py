#!/usr/bin/env python3
"""V4 — H-B3: worker leg behind the batcher spawn step (REAL run on example-host-c).

(a) Implements dispatch(folder, prompt, timeout) -> (status, text) with
    the contract the batcher's spawn step needs (send prompt, wait for
    turn end, return transcript text), backed by opencode headless —
    the c0/c2/c6 recipe on example-host-c via the hpc-glm provider.
(b) Runs ONE REAL case: recreates c0's broken fizzbuzz (order-of-checks
    bug), dispatches the fix prompt, then verifies on the HOST with
    pytest — the same evidence shape as c0/c2/c6 (patch + tests).
(c) Signature check: the adapter satisfies the batcher spawn-step
    interface (inspect.signature comparison against the send-step's
    needs: folder, prompt, timeout, status, text).

Exit 0 iff: pre pytest fails (bug present), post pytest 4/4 (fixed),
diff shows a real edit, signature check passes.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "hpc-glm/zai-org/GLM-5.3-Flash"

BROKEN = '''def classify(n: int) -> str:
    """Return FizzBuzz classification for a single integer."""
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    if n % 15 == 0:
        return "FizzBuzz"
    return str(n)
'''

TESTS = '''from fizzbuzz import classify

def test_fifteen_is_fizzbuzz():
    assert classify(15) == "FizzBuzz"

def test_three_is_fizz():
    assert classify(3) == "Fizz"

def test_five_is_buzz():
    assert classify(5) == "Buzz"

def test_seven_is_plain():
    assert classify(7) == "7"
'''

PROMPT = ("Fix the bug in fizzbuzz.py: classify(15) must return "
          "\"FizzBuzz\" but currently returns \"Fizz\". The modulo "
          "checks are in the wrong order. Fix fizzbuzz.py only — do "
          "not touch test_fizzbuzz.py. When done, run: python3 -m "
          "pytest test_fizzbuzz.py -q")


def sh(cmd, cwd=None, timeout=120):
    r = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True,
                       timeout=timeout)
    return r


# --- (a) the dispatch contract -------------------------------------------

def dispatch(folder: str, prompt: str, timeout: int = 600):
    """Batcher spawn-step contract, opencode backend (c0/c2/c6 recipe).

    Returns (status, text): status in {"done", "timeout"}; text is the
    model's final answer (transcript). Mirrors fbconn/batcher semantics:
    never raise on model failure — classify and return status.
    """
    t0 = time.time()
    # V4-run2 root cause: opencode (Bun) resolves its project anchor from
    # $PWD, which the parent shell set to ITS cwd — subprocess cwd alone
    # was ignored (log: tracking cwd=/home/you/swarmo). Pin PWD/OLDPWD.
    env = dict(os.environ, PWD=folder, OLDPWD=folder)
    try:
        r = subprocess.run(
            ["opencode", "run", "--model", MODEL, prompt],
            cwd=folder, env=env, text=True, capture_output=True,
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    wall = round(time.time() - t0, 1)
    text = (r.stdout or "").strip()
    # opencode prints banner lines; keep the tail (the actual answer)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    body = "\n".join(lines[-8:]) if lines else ""
    status = "done" if r.returncode == 0 else f"error:{r.returncode}"
    return status, f"{body}\n[wall {wall}s]"


def signature_check() -> dict:
    """(c) the adapter satisfies the batcher spawn-step interface."""
    import inspect
    sig = inspect.signature(dispatch)
    params = list(sig.parameters)
    needs = {"folder", "prompt", "timeout"}
    return {"params": params,
            "covers_spawn_step_needs": needs.issubset(params),
            "returns_status_text": True}  # asserted by V4's own flow


def pytest_run(folder: str) -> tuple:
    r = sh(["python3", "-m", "pytest", "test_fizzbuzz.py", "-q"],
           cwd=folder, timeout=60)
    passed = "4 passed" in r.stdout
    return passed, (r.stdout or r.stderr)[-200:]


def main():
    out = {"pre_bug_present": None, "dispatch": None, "post_fixed": None,
           "diff_real_edit": None, "signature": signature_check()}

    # (b) recreate c0's broken case — as its OWN git root (E10-class
    # recipe fix, discovered by V4's first failed run: opencode's
    # project discovery walks up from cwd; a non-repo dir lets it
    # anchor elsewhere and edit a different copy of the bug)
    ws = tempfile.mkdtemp(prefix="v4-fizz-")
    with open(os.path.join(ws, "fizzbuzz.py"), "w") as f:
        f.write(BROKEN)
    with open(os.path.join(ws, "test_fizzbuzz.py"), "w") as f:
        f.write(TESTS)
    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "add", "-A"],
                ["git", "-c", "user.email=v4@test", "-c",
                 "user.name=v4", "commit", "-qm", "base"]):
        r = sh(cmd, cwd=ws)
        assert r.returncode == 0, f"{cmd}: {r.stderr[:200]}"

    pre_ok, pre_tail = pytest_run(ws)
    out["pre_bug_present"] = (pre_ok is False)

    # real dispatch through the adapter
    status, text = dispatch(ws, PROMPT, timeout=420)
    out["dispatch"] = {"status": status,
                       "text_head": text[:300]}

    post_ok, post_tail = pytest_run(ws)
    out["post_fixed"] = {"passed_4_of_4": post_ok, "tail": post_tail}

    # diff against the broken original = a real edit happened
    fixed = open(os.path.join(ws, "fizzbuzz.py")).read()
    out["diff_real_edit"] = {"changed": fixed != BROKEN,
                             "order_fixed": ("n % 15" in fixed
                                             and fixed.index("n % 15")
                                             < fixed.index("n % 3"))
                             if "n % 15" in fixed else False,
                             "tests_untouched": open(os.path.join(
                                 ws, "test_fizzbuzz.py")).read() == TESTS}
    out["workspace"] = ws  # evidence preserved on disk

    print(json.dumps(out, indent=1))
    ok = (out["pre_bug_present"] and status == "done" and post_ok
          and out["diff_real_edit"]["changed"]
          and out["diff_real_edit"]["order_fixed"]
          and out["diff_real_edit"]["tests_untouched"]
          and out["signature"]["covers_spawn_step_needs"])
    print("V4", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
