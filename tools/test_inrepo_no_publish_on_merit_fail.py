"""Operator-authored contract closing the merit-failure publish gap
(wave-3, 2026-09-21). Main-push honesty was enforced in one direction
only: fixed:true requires the commit to land (round 6, f7). The other
direction was never closed — work_task published to main
UNCONDITIONALLY after the oracle ran, so a dispatch that completed but
FAILED its oracle still add'ed + committed + pushed its tree (possibly
broken partial work, or an empty --allow-empty commit) and then wrote
fixed:false. Main would carry changes no verdict claims — the exact
inverse of ba42841 (found live in wave 2 on the env-death path).
c6 law: a merit failure is FINAL; the lane's product is main; ergo a
change that fails its oracle is never a fix and never lands.
Contract (local bare origin + stub dispatch, no fleet; stub completes
and leaves work in the tree — the dangerous shape):
  - oracle fails, env healthy → NO commit on origin main (tip
    unchanged), no refs/swarm/tasks ref, final verdict fixed:false
    with the REAL pytest_rc, event reason merit_failure_no_publish.
  - success path unchanged (policy suite still pins it).
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, text=True, capture_output=True)


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
        cwd=REPO, capture_output=True, text=True, env=env, timeout=300,
    )
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


TASK = "T-merit-pub"


def test_merit_failure_never_publishes_to_main(lane):
    o, w, stub = lane
    spec = _root_commit(w, f"spec {TASK}\nverify: false")  # oracle always fails
    _push(o, w, spec, f"refs/swarm/specs/{TASK}")
    c = _root_commit(w, f"claim {TASK} att-w1-merit1")
    _push(o, w, c, f"refs/swarm/claims/{TASK}")
    # dispatch COMPLETES and leaves partial work — merit failure shape
    p = stub / "opencode"
    p.write_text("#!/bin/bash\necho partial > work.txt\nexit 0\n")
    os.chmod(p, 0o755)

    tip_before = _git("-C", o, "rev-parse", "main").stdout.strip()
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode"),
             SWARM_MODEL="stub/model"),
        TASK, "att-w1-merit1",
    )
    assert ev["fixed"] is False and ev["env_death"] is False
    assert ev["main_push"] is False
    assert ev.get("reason") == "merit_failure_no_publish"
    assert ev["pytest_rc"] != 0

    tip_after = _git("-C", o, "rev-parse", "main").stdout.strip()
    assert tip_after == tip_before, "merit-failure tree landed on main"
    refs = _git("ls-remote", o).stdout
    assert f"refs/swarm/tasks/{TASK}" not in refs
    assert f"refs/swarm/verdicts/{TASK}" in refs
