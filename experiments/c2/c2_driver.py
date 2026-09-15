#!/usr/bin/env python3
"""C-worker driver (H-A1 + H-B2 + H-C2 in one run, DESIGNS.md §4.3).

Same five cases and same seeded bugs as experiments/c1, with E10's three
mechanical causes repaired (see tools/worker_loop.py docstring):
  (a) fixtures inside case-<n>/workspace/ BEFORE the mount;
  (b) workspace pre-created by uid 1004 on host (no root-owned mountpoint);
  (c) auth ro-mounted directly into container HOME (no /secrets probe).

H-A1 (fizzbuzz, frozen threshold): non-empty diff AND host tests all pass
AND container exit 0, wall <= 240s.
H-B2: row appended with attemptId att-* AND claim exactly-once (a canary
process must FAIL to take the ledger lock while a dispatch is in flight)
AND the fizzbuzz loop (claim->verdict) <= 300s.
H-C2: container commit with Attempt: trailer visible from host AND commit
patch matches the harvested patch.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "tools"))
import worker_loop  # noqa: E402

HOME = os.path.expanduser("~")
IMAGE = "swarmo-worker:c1"
MODEL = "google/gemini-3.5-flash-lite"
LEDGER = os.path.join(HERE, "results_worker_ledger.jsonl")
OPENCODE = f"{HOME}/.opencode/bin/opencode"
AUTH = f"{HOME}/.local/share/opencode/auth.json"

BUGS = {
    "fizzbuzz": (
        "fizzbuzz.py",
        'def classify(n: int) -> str:\n'
        '    """Return FizzBuzz classification for a single integer."""\n'
        '    if n % 3 == 0:\n'
        '        return "Fizz"\n'
        '    if n % 5 == 0:\n'
        '        return "Buzz"\n'
        '    if n % 15 == 0:\n'
        '        return "FizzBuzz"\n'
        '    return str(n)\n',
        {"test_fizzbuzz.py":
         'from fizzbuzz import classify\n\n'
         'def test_fifteen_is_fizzbuzz():\n'
         '    assert classify(15) == "FizzBuzz"\n\n'
         'def test_three_is_fizz():\n'
         '    assert classify(3) == "Fizz"\n\n'
         'def test_five_is_buzz():\n'
         '    assert classify(5) == "Buzz"\n\n'
         'def test_seven_is_plain():\n'
         '    assert classify(7) == "7"\n'},
    ),
    "sumto": (
        "sumto.py",
        'def sum_to(n: int) -> int:\n'
        '    """Sum the integers 1..n inclusive."""\n'
        '    total = 0\n'
        '    for i in range(1, n):\n'
        '        total += i\n'
        '    return total\n',
        {"test_sumto.py":
         'from sumto import sum_to\n\n'
         'def test_one():\n    assert sum_to(1) == 1\n\n'
         'def test_ten():\n    assert sum_to(10) == 55\n\n'
         'def test_zero():\n    assert sum_to(0) == 0\n'},
    ),
    "initials": (
        "initials.py",
        'def initials(full_name: str) -> str:\n'
        '    """First letters of the first two words, uppercase, dot-separated."""\n'
        '    parts = full_name.strip().split()\n'
        '    return (parts[0][0] + "." + part[1][0] + ".").upper()\n',
        {"test_initials.py":
         'from initials import initials\n\n'
         'def test_ada():\n    assert initials("ada lovelace") == "A.L."\n\n'
         'def test_grace():\n    assert initials("grace hopper") == "G.H."\n'},
    ),
    "addtag": (
        "tags.py",
        'def add_tag(tag: str, bucket=[]) -> list:\n'
        '    """Append tag to bucket, defaulting to a fresh list."""\n'
        '    bucket.append(tag)\n'
        '    return bucket\n',
        {"test_tags.py":
         'from tags import add_tag\n\n'
         'def test_first_call_fresh():\n    assert add_tag("a") == ["a"]\n\n'
         'def test_second_call_fresh():\n    assert add_tag("b") == ["b"]\n'},
    ),
    "dates": (
        "dates.py",
        'import re\n\n'
        'def is_iso_date(s: str) -> bool:\n'
        '    """True if s looks like YYYY-MM-DD."""\n'
        '    return bool(re.fullmatch(r"\\d{2}-\\d{2}-\\d{2}", s))\n',
        {"test_dates.py":
         'from dates import is_iso_date\n\n'
         'def test_full_year():\n    assert is_iso_date("2026-09-15") is True\n\n'
         'def test_short_year_rejected():\n    assert is_iso_date("26-09-15") is False\n\n'
         'def test_slashes_rejected():\n    assert is_iso_date("2026/09/15") is False\n'},
    ),
}

GITIGNORE = ".pytest_cache/\n__pycache__/\n"


def make_fixture(name: str) -> str:
    """(a)+(b): workspace pre-built on host, owned by uid 1004, git-inited."""
    ws = os.path.join(HERE, f"case-{name}", "workspace")
    subprocess.run(["rm", "-rf", os.path.join(HERE, f"case-{name}")],
                   check=True)
    os.makedirs(ws)  # uid 1004, before docker ever sees it
    target, bug_src, tests = BUGS[name]
    with open(os.path.join(ws, target), "w") as f:
        f.write(bug_src)
    for fn, src in tests.items():
        with open(os.path.join(ws, fn), "w") as f:
            f.write(src)
    with open(os.path.join(ws, ".gitignore"), "w") as f:
        f.write(GITIGNORE)
    env = dict(os.environ,
               GIT_AUTHOR_NAME="c", GIT_AUTHOR_EMAIL="c@c",
               GIT_COMMITTER_NAME="c", GIT_COMMITTER_EMAIL="c@c")
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=ws,
                   check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", f"fixture: {name}"], cwd=ws,
                   check=True, env=env)
    # baseline: tests must FAIL before the worker runs
    base = subprocess.run(["python3", "-m", "pytest", "-q"], cwd=ws,
                          capture_output=True, text=True)
    assert base.returncode != 0, f"{name}: fixture must start failing"
    return ws, target


def run_one(name: str) -> dict:
    ws, target = make_fixture(name)
    brief = (f"Fix the bug in {target} so that `python3 -m pytest` "
             f"passes. Do not modify the test file. Work only inside "
             f"/work.")
    return worker_loop.run_case(
        task=name, ws=ws, ledger_path=LEDGER, image=IMAGE, model=MODEL,
        brief=brief, opencode_bin=OPENCODE, auth_json=AUTH, timeout_s=240)


def canary_rc(ledger: str) -> int:
    """0 = a second claim WOULD win (bad); 1 = AlreadyClaimed (correct).

    An acquired claim MUST be explicitly released: flock is bound to the
    open file description, and dropping the Claim object does not close
    the raw fd (found live: an unreleased canary made the main process
    block itself on its next claim).
    """
    claim = None
    try:
        claim = worker_loop.Claim(ledger)
        return 0
    except worker_loop.AlreadyClaimed:
        return 1
    finally:
        if claim is not None:
            claim.release()


def audit_ledger() -> dict:
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    claims = [r for r in rows if r["event"] == "claim"]
    verdicts = [r for r in rows if r["event"] == "verdict"]
    per_task = {}
    for c in claims:
        per_task.setdefault(c["task"], []).append(c["attemptId"])
    return {
        "n_rows": len(rows), "n_claims": len(claims),
        "n_verdicts": len(verdicts),
        "all_verdicts_have_att_attempt": all(
            r.get("attemptId", "").startswith("att-") for r in verdicts),
        "claim_attempt_ids_unique_per_task": all(
            len(v) == len(set(v)) for v in per_task.values()),
        "every_verdict_has_claim": all(
            any(c["attemptId"] == v["attemptId"] for c in claims)
            for v in verdicts),
    }


def main() -> None:
    rows = []

    # --- fizzbuzz via subprocess so the exactly-once canary can probe the
    # ledger lock WHILE a real dispatch is in flight (H-B2).
    proc = subprocess.Popen(
        [sys.executable, __file__, "run-case", "fizzbuzz"],
        stdout=subprocess.PIPE, text=True)

    def fizzbuzz_in_flight() -> bool:
        """True iff the LATEST fizzbuzz claim has no verdict row yet."""
        claim_att = None
        finished = set()
        if os.path.exists(LEDGER):
            for l in open(LEDGER):
                r = json.loads(l)
                if r.get("task") != "fizzbuzz":
                    continue
                if r["event"] == "claim":
                    claim_att = r["attemptId"]
                elif r["event"] == "verdict":
                    finished.add(r["attemptId"])
        return claim_att is not None and claim_att not in finished

    t0 = time.perf_counter()
    while not fizzbuzz_in_flight():
        time.sleep(0.2)
        if time.perf_counter() - t0 > 120:
            raise RuntimeError("fizzbuzz claim row never appeared")
    time.sleep(2)  # docker run now in flight, lock held by the child
    canary_while_held = canary_rc(LEDGER)  # expect 1 = AlreadyClaimed
    out, _ = proc.communicate(timeout=600)
    loop_wall = round(time.perf_counter() - t0, 1)
    rows.append(json.loads(out.strip().splitlines()[-1]))
    canary_after_release = canary_rc(LEDGER)  # expect 0 = claimable again

    for name in ("sumto", "initials", "addtag", "dates"):
        rows.append(run_one(name))
        print(json.dumps({k: rows[-1][k] for k in
                          ("task", "container_exit", "wall_s", "commit_sha",
                           "trailer_ok", "patch_bytes", "host_passed",
                           "host_failed", "fixed")}))

    result = {
        "probe": "C-worker", "hypotheses": ["H-A1", "H-B2", "H-C2"],
        "rows": rows,
        "h_b2": {
            "canary_while_held_rc": canary_while_held,  # expect 1
            "canary_after_release_rc": canary_after_release,  # expect 0
            "fizzbuzz_loop_wall_s": loop_wall,
        },
        "ledger_audit": audit_ledger(),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(os.path.join(HERE, "results_worker.json"), "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({
        "h_b2": result["h_b2"], "ledger_audit": result["ledger_audit"]}))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "run-case":
        print(json.dumps(run_one(sys.argv[2])))
    elif len(sys.argv) > 1 and sys.argv[1] == "canary":
        sys.exit(canary_rc(sys.argv[2]))
    else:
        main()
