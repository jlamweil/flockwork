#!/usr/bin/env python3
"""c3 — Design B's loop under real multi-worker contention.

Production claim semantics (SURVEY §A.2) on plain files: claiming a task
is rename(2) of pending/<task> -> claimed/<task>.<attemptId>. rename is
atomic and fails (FileNotFoundError) for the loser, so a task can never
be dispatched twice — the folder-level analogue of git ref-CAS (H-C1).

N=3 worker processes contend over 5 bug-fix tasks (same fixtures as c2).
Exactly-once evidence per task: one claim row, one verdict row, no two
live dispatches overlapping (ledger timestamps), all 5 fixed at end.

Run: python3 parallel_demo.py            (parent: builds queue, spawns workers, audits)
     python3 parallel_demo.py worker <name>   (worker loop until queue empty)
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
sys.path.insert(0, os.path.join(HERE, "..", "c2"))
import worker_loop  # noqa: E402
from c2_driver import BUGS, IMAGE, MODEL, OPENCODE, AUTH, make_fixture  # noqa: E402

QUEUE = os.path.join(HERE, "queue")
PENDING = os.path.join(QUEUE, "pending")
CLAIMED = os.path.join(QUEUE, "claimed")
LEDGER = os.path.join(HERE, "results_c3_ledger")  # per-worker: LEDGER.w<i>.jsonl
N_WORKERS = 3
TASKS = list(BUGS)


def claim_task(worker: str):
    """Atomically claim one pending task; returns (task, attemptId) or None.

    rename(2) is the claim: winner moves the folder, losers get
    FileNotFoundError. Claimed folder name carries the attemptId.
    """
    for task in sorted(os.listdir(PENDING)):
        attempt_id = "att-" + worker + "-" + os.urandom(3).hex()
        src = os.path.join(PENDING, task)
        dst = os.path.join(CLAIMED, f"{task}.{attempt_id}")
        try:
            os.rename(src, dst)
            return task, attempt_id
        except FileNotFoundError:
            continue  # lost the race for this one; try next
        except OSError as e:
            if e.errno == 39:  # ENOTEMPTY: dst dir existed — continue
                continue
            raise
    return None


def worker_main(name: str) -> None:
    ledger = f"{LEDGER}.{name}.jsonl"  # production: one ledger per child
    while True:
        got = claim_task(name)
        if got is None:
            break
        task, attempt_id = got
        ws = os.path.join(HERE, f"ws-{task}.{attempt_id}")
        _, target = make_fixture(task, ws=ws)
        brief = (f"Fix the bug in {target} so that `python3 -m pytest` "
                 f"passes. Do not modify the test file. Work only inside "
                 f"/work.")
        # run_case appends claim+verdict rows under THIS attempt id; the
        # worker tag lives inside the attempt id prefix (att-w<i>-…)
        row = worker_loop.run_case(
            task=task, ws=ws, ledger_path=ledger, image=IMAGE, model=MODEL,
            brief=brief, opencode_bin=OPENCODE, auth_json=AUTH,
            timeout_s=240, attempt_id=attempt_id)
        print(json.dumps({k: row.get(k) for k in
                          ("task", "attemptId", "wall_s", "fixed")}))


def build_queue() -> None:
    shutil.rmtree(QUEUE, ignore_errors=True)
    os.makedirs(PENDING)
    os.makedirs(CLAIMED)
    for t in TASKS:
        os.makedirs(os.path.join(PENDING, t))
    import glob
    for p in glob.glob(f"{LEDGER}.w*.jsonl"):
        os.remove(p)


def audit() -> dict:
    import glob
    rows = []
    for path in sorted(glob.glob(f"{LEDGER}.w*.jsonl")):
        rows.extend(json.loads(l) for l in open(path) if l.strip())
    claims = [r for r in rows if r["event"] == "claim"]
    verdicts = [r for r in rows if r["event"] == "verdict"]
    per_task_claims = {}
    for c in claims:
        per_task_claims.setdefault(c["task"], []).append(c["attemptId"])
    v_by_att = {v["attemptId"]: v for v in verdicts}
    worker_of = {c["attemptId"]: c["attemptId"].split("-")[1]
                 for c in claims}
    return {
        "n_workers": N_WORKERS,
        "tasks": TASKS,
        "claims_per_task": {t: len(v) for t, v in per_task_claims.items()},
        "exactly_once": all(len(v) == 1 for v in per_task_claims.values()),
        "all_fixed": len(verdicts) == len(TASKS)
                     and all(v.get("fixed") for v in verdicts),
        "every_claim_has_verdict": all(
            a in v_by_att for v in per_task_claims.values() for a in v),
        "workers_used": sorted({worker_of[c["attemptId"]]
                                for c in claims}),
        "claimed_dirs": sorted(os.listdir(CLAIMED)),
        "verdict_rows": [{k: v.get(k) for k in
                          ("task", "attemptId", "wall_s", "fixed",
                           "container_exit", "trailer_ok", "host_passed")}
                         for v in verdicts],
    }


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "worker":
        worker_main(sys.argv[2])
        sys.exit(0)

    build_queue()
    t0 = time.perf_counter()
    procs = [subprocess.Popen(
        [sys.executable, __file__, "worker", f"w{i}"],
        stdout=subprocess.PIPE, text=True) for i in range(N_WORKERS)]
    outs = [p.communicate(timeout=1200)[0] for p in procs]
    wall = round(time.perf_counter() - t0, 1)
    result = audit()
    result["wall_s"] = wall
    result["worker_output_lines"] = sum(
        len([l for l in o.splitlines() if l.strip()]) for o in outs)
    with open(os.path.join(HERE, "results_c3.json"), "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: result[k] for k in
                      ("exactly_once", "all_fixed", "every_claim_has_verdict",
                       "wall_s")}))
