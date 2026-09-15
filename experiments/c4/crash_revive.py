#!/usr/bin/env python3
"""c4 — crash-safety of the worker leg: victim killed mid-dispatch.

Production semantics under test (SURVEY §A): claim=send-intent means a
dead attempt leaves a claim row with no verdict; the revive sweep (E6)
reconciles by probe — here: kill the orphan container BY NAME (container
names embed task+attemptId) and requeue the folder; the next claim mints
a fresh attemptId (E2: retry-of-task, never duplicate-task).

Flow:
  1. victim worker claims sumto, starts docker dispatch
  2. parent waits until the container is up, then SIGKILLs the victim
     (docker client is orphaned; container keeps running = the leak)
  3. supervisor sweep: no verdict for claimed/<task>.<att> ->
     `docker kill c2-<task>-<att>` -> rename back to pending/
  4. heir worker re-claims (fresh att-*), runs to completion
  5. audit: exactly one final verdict, fixed=true, dead attempt recorded
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
LEDGER = os.path.join(HERE, "results_c4_ledger")  # per-worker .<name>.jsonl
TASK = "sumto"


def claim_task(worker: str):
    for task in sorted(os.listdir(PENDING)):
        attempt_id = "att-" + worker + "-" + os.urandom(3).hex()
        try:
            os.rename(os.path.join(PENDING, task),
                      os.path.join(CLAIMED, f"{task}.{attempt_id}"))
            return task, attempt_id
        except FileNotFoundError:
            continue
    return None


def runner(name: str) -> None:
    ledger = f"{LEDGER}.{name}.jsonl"
    got = claim_task(name)
    if got is None:
        print(json.dumps({"worker": name, "did": "nothing"}))
        return
    task, attempt_id = got
    ws = os.path.join(HERE, f"ws-{task}.{attempt_id}")
    _, target = make_fixture(task, ws=ws)
    row = worker_loop.run_case(
        task=task, ws=ws, ledger_path=ledger, image=IMAGE, model=MODEL,
        brief=(f"Fix the bug in {target} so that `python3 -m pytest` "
               f"passes. Do not modify the test file. Work only inside "
               f"/work."),
        opencode_bin=OPENCODE, auth_json=AUTH, timeout_s=240,
        attempt_id=attempt_id)
    print(json.dumps({k: row.get(k) for k in
                      ("task", "attemptId", "wall_s", "fixed")}))


def sweep_once() -> list:
    """One revive sweep: requeue claims with no verdict, kill orphans."""
    requeued = []
    for entry in sorted(os.listdir(CLAIMED)):
        task, _, att = entry.partition(".")
        owner_rows = []
        for name in ("victim", "heir"):
            p = f"{LEDGER}.{name}.jsonl"
            if os.path.exists(p):
                owner_rows += [json.loads(l) for l in open(p)]
        has_verdict = any(r.get("event") == "verdict" and r.get("attemptId") == att
                          for r in owner_rows)
        if has_verdict:
            continue
        # reconcile by probe: kill the named container if it still runs
        subprocess.run(["docker", "kill", f"c2-{task}-{att}"],
                       capture_output=True)
        os.rename(os.path.join(CLAIMED, entry),
                  os.path.join(PENDING, task))
        requeued.append(entry)
    return requeued


def wait_container(name_prefix: str, timeout: float = 60) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True)
        if any(n.startswith(name_prefix) for n in r.stdout.splitlines()):
            return True
        time.sleep(0.3)
    return False


def main() -> None:
    shutil.rmtree(QUEUE, ignore_errors=True)
    os.makedirs(PENDING)
    os.makedirs(CLAIMED)
    os.makedirs(os.path.join(PENDING, TASK))
    for name in ("victim", "heir"):
        p = f"{LEDGER}.{name}.jsonl"
        if os.path.exists(p):
            os.remove(p)

    # 1. victim claims and starts dispatch
    victim = subprocess.Popen([sys.executable, __file__, "runner", "victim"],
                              stdout=subprocess.PIPE, text=True)
    assert wait_container(f"c2-{TASK}-att-victim"), "victim never dispatched"
    # 2. SIGKILL mid-dispatch
    victim.kill()
    victim.wait(timeout=10)
    kill_t = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    # 3. revive sweep (immediate; production cadence is 10 min)
    requeued = sweep_once()

    # 4. heir completes the requeued task
    heir = subprocess.run([sys.executable, __file__, "runner", "heir"],
                          capture_output=True, text=True, timeout=600)

    # 5. audit
    def rows(name):
        p = f"{LEDGER}.{name}.jsonl"
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    victim_rows = rows("victim")
    heir_rows = rows("heir")
    verdicts = [r for r in heir_rows if r["event"] == "verdict"]
    victim_claims = [r for r in victim_rows if r["event"] == "claim"]
    result = {
        "probe": "c4-crash-revive", "task": TASK,
        "victim_killed_at": kill_t,
        "victim_claim_no_verdict": bool(victim_claims) and not any(
            r.get("event") == "verdict" for r in victim_rows),
        "sweep_requeued": requeued,
        "heir_fixed": any(v.get("fixed") for v in verdicts),
        "final_verdicts_for_task": len(verdicts),
        "exactly_one_final_verdict": len(verdicts) == 1,
        "heir_attempt_fresh": all(
            r.get("attemptId", "").startswith("att-heir-")
            for r in verdicts),
        "orphan_containers_left": subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True).stdout.split(),
        "victim_rows": victim_rows, "heir_rows": heir_rows,
    }
    with open(os.path.join(HERE, "results_c4.json"), "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: result[k] for k in
                      ("victim_claim_no_verdict", "sweep_requeued",
                       "heir_fixed", "exactly_one_final_verdict",
                       "orphan_containers_left")}))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "runner":
        runner(sys.argv[2])
    else:
        main()
