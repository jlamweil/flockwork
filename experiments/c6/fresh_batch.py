#!/usr/bin/env python3
"""c6 — fresh-task batch for the worker lane's ladder (see FREEZE.md).

c3's proven contention machinery (rename(2) claims, per-child ledgers,
N=3 workers) over EIGHT NEW tasks from c6_driver.FRESH_BUGS — no task
here ran earlier tonight, so every attempt is independent evidence.

Pre-registered retry policy (FREEZE.md, mechanical, ledger-only):
- environmental death := verdict row with commit_sha None AND
  patch_bytes 0 (timeout-124 kill, model/provider death, EACCES at
  work start — the leg produced nothing observable);
- such a task is requeued ONCE (claimed/<task>.<att> -> pending/<task>)
  and an heir worker drains it (c4 semantics, fresh att-heir6-*);
- merit failure (a patch was produced but host tests failed) is FINAL —
  no same-task retry, that is the inflation path the ladder must not
  reward.

Every attempt, retried or not, stays in the ledger and counts.

Run: python3 fresh_batch.py            (parent: queue, workers, requeue, audit)
     python3 fresh_batch.py worker <name>   (worker loop until queue empty)
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
sys.path.insert(0, os.path.join(HERE, "..", "c2"))
sys.path.insert(0, HERE)
import worker_loop  # noqa: E402
from c2_driver import IMAGE, MODEL, OPENCODE, AUTH  # noqa: E402
from c6_driver import FRESH_BUGS, make_fixture  # noqa: E402

QUEUE = os.path.join(HERE, "queue")
PENDING = os.path.join(QUEUE, "pending")
CLAIMED = os.path.join(QUEUE, "claimed")
LEDGER = os.path.join(HERE, "results_c6_ledger")  # per-worker: LEDGER.<w>.jsonl
N_WORKERS = 3
TASKS = list(FRESH_BUGS)


def claim_task(worker: str):
    """rename(2) claim — winner moves the folder, losers FileNotFoundError."""
    for task in sorted(os.listdir(PENDING)):
        attempt_id = "att-" + worker + "-" + os.urandom(3).hex()
        src = os.path.join(PENDING, task)
        dst = os.path.join(CLAIMED, f"{task}.{attempt_id}")
        try:
            os.rename(src, dst)
            return task, attempt_id
        except FileNotFoundError:
            continue
        except OSError as e:
            if e.errno == 39:  # ENOTEMPTY: dst dir existed
                continue
            raise
    return None


def worker_main(name: str) -> None:
    ledger = f"{LEDGER}.{name}.jsonl"
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
        row = worker_loop.run_case(
            task=task, ws=ws, ledger_path=ledger, image=IMAGE, model=MODEL,
            brief=brief, opencode_bin=OPENCODE, auth_json=AUTH,
            timeout_s=240, attempt_id=attempt_id)
        print(json.dumps({k: row.get(k) for k in
                          ("task", "attemptId", "wall_s", "fixed",
                           "container_exit", "patch_bytes")}))


def load_rows() -> list[dict]:
    rows = []
    for path in sorted(glob.glob(f"{LEDGER}.*.jsonl")):
        rows.extend(json.loads(l) for l in open(path) if l.strip())
    return rows


def final_verdicts(rows: list[dict]) -> dict:
    """Latest verdict per task (retries append, never overwrite)."""
    by_task = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        if r.get("event") == "verdict":
            by_task[r["task"]] = r
    return by_task


def environmental(r: dict) -> bool:
    return r.get("commit_sha") is None and r.get("patch_bytes", 0) == 0


def requeue_environmental_deaths(rows: list[dict], retried: set) -> list[str]:
    """One requeue per task, environmental deaths only (FREEZE rule)."""
    requeued = []
    for task, v in final_verdicts(rows).items():
        if task in retried or not environmental(v):
            continue
        src = os.path.join(CLAIMED, f"{task}.{v['attemptId']}")
        dst = os.path.join(PENDING, task)
        if os.path.isdir(src):
            os.rename(src, dst)
            requeued.append(f"{task}.att={v['attemptId']}")
            retried.add(task)
    return requeued


def audit(requeued: list[str]) -> dict:
    rows = load_rows()
    claims = [r for r in rows if r["event"] == "claim"]
    verdicts = [r for r in rows if r["event"] == "verdict"]
    fin = final_verdicts(rows)
    v_by_att = {}
    for v in verdicts:
        assert v["attemptId"] not in v_by_att, "duplicate verdict per attempt"
        v_by_att[v["attemptId"]] = v
    c_atts = [c["attemptId"] for c in claims]
    return {
        "n_workers": N_WORKERS,
        "tasks_frozen": TASKS,
        "n_claims": len(claims), "n_verdicts": len(verdicts),
        "claim_ids_unique": len(c_atts) == len(set(c_atts)),
        "every_claim_has_verdict": all(a in v_by_att for a in c_atts),
        "exactly_once_per_generation": all(
            sum(1 for c in claims if c["task"] == t) ==
            1 + (1 if any(t == r.split(".")[0] for r in requeued) else 0)
            for t in TASKS),
        "requeued": requeued,
        "final": {t: {"fixed": v.get("fixed"),
                      "environmental_death": environmental(v),
                      "attemptId": v["attemptId"],
                      "wall_s": v.get("wall_s"),
                      "host_passed": v.get("host_passed"),
                      "container_exit": v.get("container_exit")}
                  for t, v in sorted(fin.items())},
        "n_fixed_final": sum(1 for v in fin.values() if v.get("fixed")),
        "n_environmental_final": sum(
            1 for v in fin.values() if environmental(v)),
        "n_merit_failed_final": sum(
            1 for v in fin.values()
            if not v.get("fixed") and not environmental(v)),
    }


def drain_with_heir(tag: str) -> None:
    if not os.listdir(PENDING):
        return
    p = subprocess.Popen([sys.executable, __file__, "worker", tag],
                         stdout=subprocess.PIPE, text=True)
    p.communicate(timeout=1200)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "worker":
        worker_main(sys.argv[2])
        sys.exit(0)

    shutil.rmtree(QUEUE, ignore_errors=True)
    os.makedirs(PENDING)
    os.makedirs(CLAIMED)
    for t in TASKS:
        os.makedirs(os.path.join(PENDING, t))
    for p in glob.glob(f"{LEDGER}.*.jsonl"):
        os.remove(p)

    t0 = time.perf_counter()
    procs = [subprocess.Popen([sys.executable, __file__, "worker", f"w{i}"],
                              stdout=subprocess.PIPE, text=True)
             for i in range(N_WORKERS)]
    for p in procs:
        p.communicate(timeout=1200)

    retried: set = set()
    requeued = requeue_environmental_deaths(load_rows(), retried)
    drain_with_heir("heir6")

    wall = round(time.perf_counter() - t0, 1)
    result = audit(requeued)
    result["wall_s"] = wall
    with open(os.path.join(HERE, "results_c6.json"), "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: result[k] for k in
                      ("n_fixed_final", "n_environmental_final",
                       "n_merit_failed_final", "requeued", "wall_s",
                       "every_claim_has_verdict")}))
