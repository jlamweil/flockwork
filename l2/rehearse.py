#!/usr/bin/env python3
"""L2 rehearsal — the worker leg behind the batcher claim step, docker-free.

Drives the REAL production primitives through the new seam (l2/backend.py):
  worker_policy.claim_task   (rename(2) exactly-once claim, c3 semantics)
  worker_loop / worker_policy row schema (flock ledger, att-* keys)
  toy_docker shim            (plays the container side; the host-side
                              dispatch/harvest/verify/classify code runs
                              for real — HQ harness pattern)
  toy_fixtures               (c2's seeded bugs, baseline-FAIL gates)

Production shapes honored:
  A.4  each worker keeps its OWN ledger; contention lives on the shared
       queue (rename(2)), never on each other's ledger locks.
  E2   attemptIds are minted per claim; a requeued task's heir gets a
       fresh att; the dead attempt keeps its rows (never reused).

What it proves (exit 0 iff all pass):
  H1  exactly-once: 3 workers race for 2 pending tasks; each dispatched
      exactly once; both deliver with clean trailers (H-C2 shape)
  H2  verdict == independent host pytest on the delivered workspaces
      (the c9 oracle, now riding the L2 seam)
  H3  timeout -> requeue_fresh -> heir delivers; requeue after done is
      a no-op (no zombie requeue)
  H4  c4-shape worker death (container exit 137) requeues; the heir's
      attempt delivers; dead + heir attempts are distinct att-* rows
  H5  both backends produce the same row schema; headless classifies a
      model failure honestly (no real model needed for the rehearsal)

Run: python3 l2/rehearse.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                              # l2.backend
sys.path.insert(0, "/home/you/the-queue-driver")  # tools.*, tests.*
from backend import DockerBackend, HeadlessBackend   # noqa: E402
from tools import worker_policy                       # noqa: E402


def _load_module(name: str, path: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# `tests` collides with a site-packages package — load by path
toy_fixtures = _load_module(
    "toy_fixtures",
    "/home/you/the-queue-driver/tests/toy_fixtures.py")

TOY_DOCKER_SRC = "/home/you/the-queue-driver/tests/toy_docker.py"
FIX_JSON = {name: toy_fixtures.FIXED[name]
            for name in ("fizzbuzz", "sumto")}


def sh(cmd, cwd=None, env=None, timeout=60):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True,
                          capture_output=True, timeout=timeout)


def stage_toy_docker(bindir: str) -> None:
    """Put the shim on PATH as an executable `docker`."""
    os.makedirs(bindir, exist_ok=True)
    dst = os.path.join(bindir, "docker")
    shutil.copy(TOY_DOCKER_SRC, dst)
    os.chmod(dst, os.stat(dst).st_mode | stat.S_IXUSR
             | stat.S_IXGRP | stat.S_IXOTH)


def fresh_worker_env(bindir: str, mode: str, killlog: str,
                     tmp: str) -> dict:
    return dict(os.environ,
                PATH=f"{bindir}:{os.environ['PATH']}",
                TOY_WORKER_MODE=mode,
                TOY_FIX_JSON=json.dumps(FIX_JSON),
                TOY_DOCKER_KILLLOG=killlog,
                TOY_FLAKY_DIR=tmp)


def pytest_tail(ws) -> tuple:
    """H2 oracle, independent of the backend: host pytest at task root."""
    r = sh([sys.executable, "-m", "pytest", "-q"], cwd=ws, timeout=60)
    return r.returncode == 0, (r.stdout or r.stderr)[-200:]


def audit_ledgers(ledger_dir: str, pattern: str = "ledger-*.jsonl") -> list:
    rows = []
    for fn in sorted(os.listdir(ledger_dir)):
        if fn.startswith("ledger-") and fn.endswith(".jsonl"):
            with open(os.path.join(ledger_dir, fn)) as f:
                rows.extend(json.loads(ln) for ln in f if ln.strip())
    return rows


def write_queue(queue: str, tasks: list) -> None:
    os.makedirs(os.path.join(queue, "pending"), exist_ok=True)
    os.makedirs(os.path.join(queue, "claimed"), exist_ok=True)
    for t in tasks:
        os.makedirs(os.path.join(queue, "pending", t), exist_ok=True)


def spawn_worker(worker: str, queue: str, ledger_dir: str, ws_of: dict,
                 env: dict):
    """One child process running the claim->dispatch->verify loop.

    Inline source (not imported) so process-boundary behaviors are real:
    the child can die mid-dispatch, exactly like a live worker.
    """
    code = f"""
import json, sys
sys.path.insert(0, {HERE!r})
sys.path.insert(0, '/home/you/the-queue-driver')
from backend import DockerBackend
from tools import worker_policy

ws_of = {json.dumps(ws_of)}
queue = {queue!r}
ledger = {os.path.join(ledger_dir, 'ledger-' + worker + '.jsonl')!r}
backend = DockerBackend()

while True:
    got = worker_policy.claim_task(queue, worker={worker!r})
    if got is None:
        print(json.dumps({{"event": "worker_done", "worker": {worker!r}}}), flush=True)
        break
    task, att = got
    row = backend.run_case(task, ws_of[task], ledger, brief="fix " + task,
                           attempt_id=att)
    print(json.dumps({{"event": "attempted", "worker": {worker!r},
                       "task": task, "att": att,
                       "decision": row["decision"],
                       "fixed": row["fixed"]}}), flush=True)
    if row["decision"] == "requeue_fresh":
        worker_policy.requeue_task(queue, task, att)
"""
    return subprocess.Popen([sys.executable, "-c", code], env=env,
                            text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="l2-rehearse-")
    bindir = os.path.join(tmp, "bin")
    stage_toy_docker(bindir)
    # The MAIN process also makes direct backend calls (H3/H5); it must
    # see the shim too. Discovered live: example-host-c has real docker, and
    # without this the direct calls hit it and died on rc 125
    # image-missing (recorded in results_l2.json as a candidate
    # error-table row: image-missing = environmental, not requeueable).
    os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
    killlog = os.path.join(tmp, "kills.txt")
    # main process also needs the shim's env (direct H3/H5 calls);
    # default mode is fix, H3's timer temporarily overrides to hang
    os.environ["TOY_WORKER_MODE"] = "fix"
    os.environ["TOY_FIX_JSON"] = json.dumps(FIX_JSON)
    os.environ["TOY_DOCKER_KILLLOG"] = killlog
    os.environ["TOY_FLAKY_DIR"] = tmp
    ledger_dir = os.path.join(tmp, "ledgers")
    os.makedirs(ledger_dir)
    queue = os.path.join(tmp, "queue")
    ws_of = {}
    for t in ("fizzbuzz", "sumto"):
        ws_of[t] = os.path.join(tmp, f"ws-{t}")
        toy_fixtures.make_toy_fixture(t, ws_of[t])
        r = sh([sys.executable, "-m", "pytest", "-q"], cwd=ws_of[t])
        assert r.returncode != 0, f"{t} baseline must FAIL"

    R = {"tmp": tmp, "checks": {}}

    # ---- H1 + H2: exactly-once race, oracle-matched deliveries --------
    write_queue(queue, ["fizzbuzz", "sumto"])
    env = fresh_worker_env(bindir, "fix", killlog, tmp)
    workers = [spawn_worker(f"w{i}", queue, ledger_dir, ws_of, env)
               for i in range(3)]
    for w in workers:
        out, err = w.communicate(timeout=300)
        assert w.returncode == 0, f"worker died: {err[-400:]}"

    rows = audit_ledgers(ledger_dir)
    claims = [r for r in rows if r["event"] == "claim"]
    verdicts = [r for r in rows if r["event"] == "verdict"]
    delivered = [v for v in verdicts if v["decision"] == "deliver"]
    h1 = (len(claims) == 2 and len(delivered) == 2
          and sorted(c["task"] for c in claims)
          == ["fizzbuzz", "sumto"]
          and all(v["fixed"] and v["trailer_ok"] for v in delivered))
    R["checks"]["h1_exactly_once"] = {
        "pass": h1, "claims": sorted(c["task"] for c in claims),
        "decisions": sorted(v["decision"] for v in verdicts)}
    o = {t: pytest_tail(ws_of[t]) for t in ws_of}
    h2 = all(ok for ok, _ in o.values())
    R["checks"]["h2_oracle_matches"] = {
        "pass": h2, "tails": {t: tl for t, (ok, tl) in o.items()}}

    # ---- H3: timeout -> requeue -> heir delivers ----------------------
    # The timer runs with the shim in `hang` mode (env must be set in the
    # PARENT — the backend's subprocess inherits it); its 2s backend cap
    # fires after the harness kill path (rc 124) -> requeue_fresh. The
    # heir runs in `fix` mode on a FRESH BROKEN ws and delivers once.
    queue3 = os.path.join(tmp, "queue3")
    ws3 = os.path.join(tmp, "ws-hang")
    toy_fixtures.make_toy_fixture("fizzbuzz", ws3)
    write_queue(queue3, ["fizzbuzz"])
    ledger3 = os.path.join(ledger_dir, "ledger-timer.jsonl")
    task, att = worker_policy.claim_task(queue3, worker="timer")
    old_mode = os.environ.get("TOY_WORKER_MODE")
    os.environ["TOY_WORKER_MODE"] = "hang"
    try:
        row = DockerBackend(timeout_s=2).run_case(
            task, ws3, ledger3, brief="fix", attempt_id=att)
    finally:
        if old_mode is None:
            del os.environ["TOY_WORKER_MODE"]
        else:
            os.environ["TOY_WORKER_MODE"] = old_mode
    moved = worker_policy.requeue_task(queue3, task, att)
    task2, att2 = worker_policy.claim_task(queue3, worker="heir")
    row2 = DockerBackend(timeout_s=240).run_case(
        task2, ws3, ledger3, brief="fix", attempt_id=att2)
    heir_att = row2["attemptId"]
    shutil.rmtree(os.path.join(queue3, "claimed",
                               f"{task2}.{att2}"))  # done: consume claim
    requeue_after_done = worker_policy.requeue_task(queue3, task2, att2)
    h3 = (row["decision"] == "requeue_fresh" and moved
          and row2["fixed"] and heir_att != att
          and requeue_after_done is False)
    R["checks"]["h3_requeue_heir"] = {
        "pass": h3, "timer_decision": row["decision"],
        "heir_fixed": row2["fixed"], "requeue_after_done":
        requeue_after_done}
    toy_fixtures.make_toy_fixture("fizzbuzz", ws3)  # restore broken

    # ---- H4: worker death mid-dispatch (c4 shape), heir delivers ------
    # The flaky shim kills its FIRST dispatch (exit 137, c4 shape) and
    # delivers on the retry. This needs a FRESH BROKEN ws — on an
    # already-fixed ws the fix-commit is a no-op crash and the worker
    # would claim<->requeue forever (rehearsal caught exactly this).
    toy_fixtures.make_toy_fixture("fizzbuzz", ws_of["fizzbuzz"])
    write_queue(queue, ["fizzbuzz"])
    env_flaky = fresh_worker_env(bindir, "flaky", killlog, tmp)
    w = spawn_worker("flaky", queue, ledger_dir, ws_of, env_flaky)
    out, err = w.communicate(timeout=300)
    assert w.returncode == 0, f"flaky worker died: {err[-400:]}"
    rows4 = [r for r in audit_ledgers(ledger_dir)
             if r["event"] == "verdict" and r["task"] == "fizzbuzz"
             and r["attemptId"].startswith("att-flaky-")]
    dead = [v for v in rows4 if v["decision"] == "requeue_fresh"]
    heir = [v for v in rows4 if v["decision"] == "deliver"]
    h4 = (len(dead) == 1 and len(heir) == 1 and heir[0]["fixed"]
          and dead[0]["attemptId"] != heir[0]["attemptId"])
    R["checks"]["h4_crash_revive"] = {
        "pass": h4,
        "attempts": [v["attemptId"] for v in rows4]}

    # ---- H5: schema parity + honest classification across backends ----
    be5 = HeadlessBackend(model="hpc-glm/nonexistent-model-xyz")
    ws5 = os.path.join(tmp, "ws-headless")
    toy_fixtures.make_toy_fixture("sumto", ws5)
    rows5 = be5.run_case("sumto", ws5,
                         os.path.join(ledger_dir, "ledger-hless.jsonl"),
                         brief="fix sumto")
    h5 = ({"event", "task", "attemptId", "model", "fixed",
           "error_class", "decision"} <= set(rows5)
          and rows5["decision"].startswith("requeue")
          and rows5["fixed"] is False)
    R["checks"]["h5_schema_parity"] = {
        "pass": h5, "headless_decision": rows5["decision"]}

    R["pass"] = all(c["pass"] for c in R["checks"].values())
    with open(os.path.join(HERE, "results_l2.json"), "w") as f:
        json.dump(R, f, indent=1)
    print(json.dumps(R, indent=1))
    return 0 if R["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
