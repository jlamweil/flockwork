"""Operator-authored contract closing the empty-fix-commit gap (wave-2,
2026-09-21). Found live as commit ba42841 ("fix T6-readme-honesty", an
empty --allow-empty commit on main): when a dispatch dies
environmentally and the heir budget is spent, work_task fell through
to the fix-commit path and published a zero-change commit to main —
main history then claims a fix the tree never had, and
refs/swarm/tasks/<task> points at the empty commit. The c6 law — an
environmental death is never a fix — was already half-enforced (the
verdict says fixed:false); this closes the other half: main and tasks
stay untouched too. Contract (local bare origin + stub dispatch, no
fleet; the oracle is `true` so the fall-through trap is exact — the
oracle would pass on the unchanged tree):
  - env death with the heir budget spent → NO new commit on origin
    main (tip unchanged), no refs/swarm/tasks ref, final verdict
    fixed:false pushed, event carries heir_exhausted + reason.
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
    p = pathlib.Path(REPO / "l2" / "inrepo.py")
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


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


TASK = "T-empty"


def _seed(o, w, stub):
    spec = _root_commit(w, f"spec {TASK}\nverify: true")
    _push(o, w, spec, f"refs/swarm/specs/{TASK}")
    c = _root_commit(w, f"claim {TASK} att-w1-empty01")
    _push(o, w, c, f"refs/swarm/claims/{TASK}")
    dead = _root_commit(w, f"archive {TASK} att-w1-dead01")
    _push(o, w, dead, f"refs/swarm/archive/claims/{TASK}@att-w1-dead01")
    p = stub / "opencode"
    p.write_text("#!/bin/bash\nexit 7\n")  # dies, tree untouched
    os.chmod(p, 0o755)


def test_heir_exhausted_env_death_never_publishes_to_main(lane):
    o, w, stub = lane
    _seed(o, w, stub)
    tip_before = _git("-C", o, "rev-parse", "main").stdout.strip()
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode"),
             SWARM_MODEL="stub/model"),
        TASK, "att-w1-empty01",
    )
    assert ev["env_death"] is True and ev["heir_exhausted"] is True
    assert ev["fixed"] is False
    assert ev["main_push"] is False
    assert ev.get("reason") == "heir_exhausted_env_death"
    tip_after = _git("-C", o, "rev-parse", "main").stdout.strip()
    assert tip_after == tip_before, "empty fix commit landed on main"
    refs = _git("ls-remote", o).stdout
    assert f"refs/swarm/tasks/{TASK}" not in refs
    assert f"refs/swarm/verdicts/{TASK}" in refs
