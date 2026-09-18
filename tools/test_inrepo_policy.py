"""Operator-authored contract for the lane's c6 error policy (hardening
round 2, 2026-09-18). The frozen c6 rule (experiments/c6/FREEZE.md) —
"environmental death = no commit ∧ no patch → one heir attempt max;
merit failure final" — lived in prose only: the worker loop recorded
environmental deaths as final fixed:false verdicts, permanently closing
the task. Contract (local bare origin + stub dispatch, no fleet):
  - env death (timeout, or dispatch exit≠0 with an empty tree) with no
    dead attempt yet → attempt archived, claim freed, task re-enters
    open_tasks in the SAME run (no reconcile TTL wait, no heir yet)
  - env death when a dead attempt is already archived → honest
    fixed:false verdict is FINAL (one heir attempt max), heir_exhausted
  - merit failure (dispatch completed, oracle failed) → final, never
    requeued
  - success path unchanged: fix on main with Attempt trailer + verdict
  - open_tasks raises on an unreachable origin; worker gives up after
    SWARM_WORKER_MAX_FAILS consecutive unreachable scans with exit 1
work_task runs in a SUBPROCESS with SWARM_ORIGIN in the env — ORIGIN is
bound at import time (the T5 lesson).
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    return _git("-C", w, *cid, "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    r = _git("-C", w, *cid, "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


DRIVER = (
    "import importlib.util, json, sys;"
    "spec = importlib.util.spec_from_file_location('inrepo', 'l2/inrepo.py');"
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
    "print(json.dumps(m.work_task(sys.argv[1], sys.argv[2], sys.argv[3])))"
)


def _run_work_task(env_extra, task, att, worker="w1"):
    env = dict(os.environ, **env_extra)
    r = subprocess.run(
        [sys.executable, "-c", DRIVER, worker, task, att],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture
def lane(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    _git("-C", str(w), "push", "-q", str(o), "main")
    stub = tmp_path / "stub"
    stub.mkdir()
    return str(o), str(w), stub


def _stub_opencode(stub_dir, body):
    p = stub_dir / "opencode"
    p.write_text("#!/bin/bash\n" + body)
    os.chmod(p, 0o755)
    return str(p)


def _seed_task(o, w, task, verify):
    spec = _root_commit(w, f"spec {task}\nverify: {verify}")
    _push(o, w, spec, f"refs/swarm/specs/{task}")
    return spec


def _seed_claim(o, w, task, att):
    c = _root_commit(w, f"claim {task} {att}")
    _push(o, w, c, f"refs/swarm/claims/{task}")
    return c


def _refs(o):
    return _git("ls-remote", o).stdout


BASE_ENV = {"SWARM_MODEL": "stub/model"}


# ------------------------------------------------- env death → requeue


def test_timeout_requeues_task_same_run(lane, tmp_path):
    o, w, stub = lane
    _seed_task(o, w, "T-to", "true")
    _seed_claim(o, w, "T-to", "att-w1-to0001")
    _stub_opencode(stub, "sleep 3\n")
    ev = _run_work_task(dict(BASE_ENV, SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode"),
                             SWARM_DISPATCH_TIMEOUT="1"), "T-to", "att-w1-to0001")
    assert ev["env_death"] is True and ev["oc_rc"] == 124
    assert ev["requeued"] is True and ev["swept"] == "att-w1-to0001"
    refs = _refs(o)
    assert "refs/swarm/claims/T-to" not in refs          # freed in-run
    assert "refs/swarm/archive/claims/T-to@att-w1-to0001" in refs
    assert "refs/swarm/verdicts/T-to" not in refs        # no verdict for a death
    # the point of in-run requeue: the task is claimable again NOW
    os.environ["SWARM_ORIGIN"] = o
    try:
        assert inrepo.open_tasks() == ["T-to"]
    finally:
        del os.environ["SWARM_ORIGIN"]


def test_dispatch_fail_with_empty_tree_requeues(lane, tmp_path):
    o, w, stub = lane
    _seed_task(o, w, "T-die", "true")
    _seed_claim(o, w, "T-die", "att-w1-die002")
    _stub_opencode(stub, "exit 3\n")   # model/provider death shape
    ev = _run_work_task(dict(BASE_ENV, SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode")),
                        "T-die", "att-w1-die002")
    assert ev["env_death"] is True and ev["oc_rc"] == 3
    assert ev["requeued"] is True
    assert "refs/swarm/claims/T-die" not in _refs(o)
    assert "refs/swarm/archive/claims/T-die@att-w1-die002" in _refs(o)


# ------------------------------------------- merit final / heir maxed


def test_merit_failure_is_final_never_requeued(lane, tmp_path):
    o, w, stub = lane
    _seed_task(o, w, "T-merit", "false")     # oracle always fails
    _seed_claim(o, w, "T-merit", "att-w1-me003")
    _stub_opencode(stub, "exit 0\n")         # dispatch completed, no work
    ev = _run_work_task(dict(BASE_ENV, SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode")),
                        "T-merit", "att-w1-me003")
    assert ev["fixed"] is False and ev["env_death"] is False
    assert ev.get("requeued") is None
    refs = _refs(o)
    assert "refs/swarm/claims/T-merit" in refs           # untouched
    assert "refs/swarm/verdicts/T-merit" in refs         # final verdict stands
    assert not any("archive" in ln for ln in refs.splitlines())
    body = inrepo.origin_body(o, "refs/swarm/verdicts/T-merit")
    assert "fixed: false" in body and "oc_rc: 0" in body


def test_heir_exhausted_env_death_writes_final_verdict(lane, tmp_path):
    o, w, stub = lane
    _seed_task(o, w, "T-heir", "true")
    dead = _root_commit(w, "claim T-heir att-w0-dead00")
    _push(o, w, dead, "refs/swarm/archive/claims/T-heir@att-w0-dead00")
    _seed_claim(o, w, "T-heir", "att-w1-heir04")
    _stub_opencode(stub, "exit 3\n")
    ev = _run_work_task(dict(BASE_ENV, SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode")),
                        "T-heir", "att-w1-heir04")
    assert ev["env_death"] is True and ev["heir_exhausted"] is True
    assert ev.get("requeued") is None and ev["fixed"] is False
    refs = _refs(o)
    assert "refs/swarm/verdicts/T-heir" in refs          # final, honest
    assert "refs/swarm/claims/T-heir" in refs            # not freed
    body = inrepo.origin_body(o, "refs/swarm/verdicts/T-heir")
    assert "fixed: false" in body and "oc_rc: 3" in body


# ------------------------------------------------------- success path


def test_success_path_unchanged_trailer_and_verdict(lane, tmp_path):
    o, w, stub = lane
    _seed_task(o, w, "T-win", "test -f marker.txt")
    _seed_claim(o, w, "T-win", "att-w1-win005")
    _stub_opencode(stub, "echo work > marker.txt\nexit 0\n")
    ev = _run_work_task(dict(BASE_ENV, SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode")),
                        "T-win", "att-w1-win005")
    assert ev["fixed"] is True and ev["env_death"] is False
    assert ev["main_push"] and ev["return_pushed"] and ev["verdict_pushed"]
    body = inrepo.origin_body(o, "refs/swarm/verdicts/T-win")
    assert "fixed: true" in body and "oc_rc: 0" in body
    # Attempt trailer lands with the return commit on main
    log = _git("-C", o, "log", "-1", "--format=%B", "main")
    assert "Attempt: att-w1-win005" in log.stdout


# ------------------------------------------ origin unreachable honesty


def test_open_tasks_raises_on_unreachable_origin(tmp_path):
    os.environ["SWARM_ORIGIN"] = str(tmp_path / "nope.git")
    try:
        with pytest.raises(RuntimeError, match="origin unreachable"):
            inrepo.open_tasks()
    finally:
        del os.environ["SWARM_ORIGIN"]


def test_worker_gives_up_honestly_on_dead_origin(tmp_path):
    env = dict(os.environ, SWARM_ORIGIN=str(tmp_path / "nope.git"),
               SWARM_WORKER_MAX_FAILS="2")
    r = subprocess.run([sys.executable, "l2/inrepo.py", "worker", "w9"],
                       cwd=REPO, capture_output=True, text=True, env=env,
                       timeout=120)
    assert r.returncode == 1
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    assert [e["event"] for e in events] == [
        "origin_unreachable", "origin_unreachable", "worker_gave_up"]
    assert events[-1]["completed"] == 0


def test_worker_exits_clean_on_empty_queue(lane):
    o, w, stub = lane
    env = dict(os.environ, SWARM_ORIGIN=o)
    r = subprocess.run([sys.executable, "l2/inrepo.py", "worker", "w8"],
                       cwd=REPO, capture_output=True, text=True, env=env,
                       timeout=120)
    assert r.returncode == 0
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    assert events[-1] == {"event": "worker_done", "worker": "w8", "completed": 0}
