"""Operator-authored contract for scratch-tree hygiene (2026-09-21 tail
wave 2). tempfile_tree() mkdtemps and every caller abandons the dir —
measured live: 568 abandoned /tmp/inrepo-* dirs (130 MB) spanning three
days, one added by every work_task attempt, sweep, and relabel, and by
every contract-suite run. The no-orphans law ("no orphaned processes or
repos at sitting end") applies to the lane itself. Contract:
  - one work_task attempt (subprocess, stub dispatch) leaves ZERO new
    /tmp/inrepo-* dirs — success and env-death requeue paths both exit
    through the cleanup
  - one sweep leaves ZERO new dirs (the scratch lens is removed even
    when the push fails)
  - one relabel repair leaves ZERO new dirs
The functional results (events, archived refs, relabeled refs) must be
byte-identical to the pre-hygiene behavior — these tests assert both.
"""
import glob
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

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
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _scratch():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "inrepo-*")))


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
    "import importlib.util as iu, json as js, sys as s;"
    "spec = iu.spec_from_file_location('inrepo', 'l2/inrepo.py');"
    "m = iu.module_from_spec(spec); spec.loader.exec_module(m);"
    "print(js.dumps(m.work_task(s.argv[1], s.argv[2], s.argv[3])))"
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


def test_work_task_success_leaves_no_scratch(lane):
    o, w, stub = lane
    spec = _root_commit(w, "spec T-scrub\nverify: true")
    _push(o, w, spec, "refs/swarm/specs/T-scrub")
    c = _root_commit(w, "claim T-scrub att-w1-scr01")
    _push(o, w, c, "refs/swarm/claims/T-scrub")
    _stub_opencode(stub, "exit 0\n")
    before = _scratch()
    ev = _run_work_task(dict({"SWARM_MODEL": "stub/model"},
                             SWARM_ORIGIN=o,
                             OPENCODE_BIN=str(stub / "opencode")),
                        "T-scrub", "att-w1-scr01")
    after = _scratch()
    assert ev["fixed"] is True and ev["env_death"] is False
    assert after - before == set(), \
        f"work_task abandoned {[p for p in after - before]}"


def test_work_task_env_death_requeue_leaves_no_scratch(lane):
    o, w, stub = lane
    spec = _root_commit(w, "spec T-scrub2\nverify: true")
    _push(o, w, spec, "refs/swarm/specs/T-scrub2")
    c = _root_commit(w, "claim T-scrub2 att-w1-scr02")
    _push(o, w, c, "refs/swarm/claims/T-scrub2")
    _stub_opencode(stub, "exit 3\n")   # model/provider death shape
    before = _scratch()
    ev = _run_work_task(dict({"SWARM_MODEL": "stub/model"},
                             SWARM_ORIGIN=o,
                             OPENCODE_BIN=str(stub / "opencode")),
                        "T-scrub2", "att-w1-scr02")
    after = _scratch()
    assert ev["env_death"] is True and ev["requeued"] is True
    assert after - before == set(), \
        f"requeue path abandoned {[p for p in after - before]}"


def test_sweep_leaves_no_scratch(lane):
    o, w, _stub = lane
    c = _root_commit(w, "claim T-scrub3 att-w1-scr03")
    _push(o, w, c, "refs/swarm/claims/T-scrub3")
    before = _scratch()
    res = inrepo.sweep(o, "T-scrub3")
    after = _scratch()
    assert res["archived"] == \
        ["refs/swarm/archive/claims/T-scrub3@att-w1-scr03"]
    assert after - before == set(), \
        f"sweep abandoned {[p for p in after - before]}"


def test_relabel_repair_leaves_no_scratch(lane):
    o, w, _stub = lane
    c = _root_commit(w, "attempt T-scrub4 att-w1-scr04")
    _push(o, w, c, "refs/swarm/archive/tasks/T-scrub4@orphan")
    before = _scratch()
    res = inrepo.relabel_orphan_archives(o)
    after = _scratch()
    assert res["relabeled"] == \
        [{"from": "refs/swarm/archive/tasks/T-scrub4@orphan",
          "to": "refs/swarm/archive/tasks/T-scrub4@att-w1-scr04",
          "sha": _git("ls-remote", o,
                      "refs/swarm/archive/tasks/T-scrub4@att-w1-scr04"
                      ).stdout.split()[0]}]
    assert after - before == set(), \
        f"relabel abandoned {[p for p in after - before]}"
