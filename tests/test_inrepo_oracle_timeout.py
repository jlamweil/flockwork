"""Contracts for the oracle seam: the verify leg carries its own timeout.

The oracle leg runs the task's `verify:` command (or host pytest). It is
bounded by `SWARM_ORACLE_TIMEOUT` (default 600s) via coreutils `timeout`,
so a hung verify degrades to an honest measured result — rc 124 — instead
of sitting the worker down for the full floor or leaking a TimeoutExpired
out of `_work_task`. A completed dispatch with a hung oracle is a merit
final (fixed:false, pytest_rc 124, oracle_timeout true), not an env death:
the claim stays live for the TTL re-lease, the slow deliberate path.
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
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return "-c", "user.email=a@b", "-c", "user.name=a"


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    return _git("-C", w, *_cid(), "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    r = _git("-C", w, *_cid(), "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


SUB_DRIVER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('inrepo', 'l2/inrepo.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
try:
    print(json.dumps(m.work_task(sys.argv[1], sys.argv[2], sys.argv[3])))
except Exception as e:
    print(json.dumps({'event': 'driver_exception', 'type': type(e).__name__}))
"""


def _run_work_task(env_extra, task, att, worker="w2"):
    env = dict(os.environ, **env_extra)
    r = subprocess.run(
        [sys.executable, "-c", SUB_DRIVER, worker, task, att],
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
    _git("-C", str(w), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(w), *_cid(), "push", "-q", str(o), "main")
    # stub dispatch: writes work into the tree, exits 0 — the dispatch
    # leg genuinely completes (oc_rc 0) with a populated tree, so the
    # oracle result decides the attempt honestly.
    stub = tmp_path / "stub-opencode"
    stub.write_text("#!/bin/bash\ntouch dispatched.txt\nexit 0\n")
    stub.chmod(0o755)
    return str(o), str(w), str(stub)


TASK = "T-oraclehang"


def _seed_spec(o, w, verify_line):
    # the spec needs a plain-text task statement (the "what") AND a
    # `verify:` done-bar.
    _push(o, w, _root_commit(w,
          f"do the thing so it works\nverify: {verify_line}"),
          f"refs/swarm/specs/{TASK}")


def _claim(o, w, att):
    _push(o, w, _root_commit(w, f"claim {TASK} {att}"),
          f"refs/swarm/claims/{TASK}")


def _env(o, stub):
    return dict(
        SWARM_ORIGIN=o,
        OPENCODE_BIN=stub,
        SWARM_ORACLE_TIMEOUT="3",
        SWARM_HEIR_MAX="1",
        SWARM_MODEL="stub/model",
    )


def _refs(o):
    return _git("ls-remote", o).stdout


def test_hung_oracle_is_honest_merit_final(lane):
    """A verify that hangs past SWARM_ORACLE_TIMEOUT degrades to the
    structured merit-final verdict (rc 124, oracle_timeout), not a crash:
    the dispatch completed (oc_rc 0) but the oracle never finished, so
    fixed:false is FINAL and the claim stays live for the TTL re-lease."""
    o, w, stub = lane
    _seed_spec(o, w, "sleep 30")
    _claim(o, w, "att-w2-hang001")
    ev = _run_work_task(_env(o, stub), TASK, "att-w2-hang001")
    assert ev.get("event") != "driver_exception", ev
    assert ev["fixed"] is False
    assert ev["pytest_rc"] == 124
    assert ev["oracle_timeout"] is True
    assert ev["oc_rc"] == 0
    assert ev["env_death"] is False
    assert ev["reason"] == "merit_failure_no_publish"
    assert ev["verdict_pushed"] is True
    refs = _refs(o)
    # only the verdict lands; the claim stays LIVE; main + tasks untouched
    assert f"refs/swarm/verdicts/{TASK}" in refs
    assert f"refs/swarm/claims/{TASK}" in refs
    assert f"refs/swarm/tasks/{TASK}" not in refs
    assert "refs/swarm/archive/" not in refs


def test_healthy_oracle_publishes_without_the_flag(lane):
    """The timeout guard must not perturb the healthy path: a verify that
    exits 0 over real dispatch work reaches fixed:true, publishes to main,
    and carries NO oracle_timeout flag (the flag is timeout-shape only)."""
    o, w, stub = lane
    _seed_spec(o, w, "true")
    _claim(o, w, "att-w2-ok0001")
    ev = _run_work_task(_env(o, stub), TASK, "att-w2-ok0001")
    assert ev.get("event") != "driver_exception", ev
    assert ev["fixed"] is True
    assert ev["main_push"] is True
    assert ev["return_pushed"] is True
    assert ev["pytest_rc"] == 0
    assert "oracle_timeout" not in ev
    refs = _refs(o)
    assert f"refs/swarm/tasks/{TASK}" in refs
    assert f"refs/swarm/verdicts/{TASK}" in refs
