"""Operator-authored contract for main-push honesty (wave-5,
2026-09-20). The lane's product is main: work_task computes `fixed`
from the oracle alone, then pushes main with one rebase retry — on a
permanent rejection it STILL pushed `fixed: true` with the fix missing
from main (round-6 finding 7 saw the collision live; the rebase retry
covers one collision, not two). Contract, staged with a sandbox
pre-receive hook that rejects refs/heads/main:
  - permanent rejection → the attempt does not claim success: requeue
    (archive + free, reason main_push_rejected), the heir re-attempts,
    and with heirs exhausted the final verdict on the origin is
    fixed:false — the c6 bound (one heir) holds
  - a single collision → the existing rebase retry recovers
    (fixed:true, main_push:true) — no over-refusal
  - env_death stays false in this path: the dispatch SUCCEEDED, the
    completion failed (the error table keys on the difference)
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


def _seed_spec(o, task, verify):
    scratch = o.parent / "scratch"
    if not scratch.exists():
        _git("init", "-q", "-b", "main", str(scratch))
    et = _git("hash-object", "-t", "tree", "/dev/null").stdout.strip()
    spec = _git("-C", str(scratch), *_cid(), "commit-tree", et,
                "-m", f"spec {task}\nverify: {verify}").stdout.strip()
    r = _git("-C", str(scratch), *_cid(), "push", "-q", str(o),
             f"{spec}:refs/swarm/specs/{task}")
    assert r.returncode == 0, r.stderr


def _install_hook(o, mode):
    """mode: 'always' — reject every main push; 'once' — reject the
    first main push only (counts main rejections, other refs pass)."""
    hook = o / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    if mode == "always":
        body = ("#!/bin/sh\nwhile read old new ref; do\n"
                '  case "$ref" in refs/heads/main) exit 1;; esac\n'
                "done\nexit 0\n")
    else:
        counter = o.parent / "main-rejects"
        body = ("#!/bin/sh\nreject=0\nwhile read old new ref; do\n"
                '  case "$ref" in refs/heads/main)\n'
                f"    c=$(cat {counter} 2>/dev/null || echo 0); "
                f"c=$((c+1)); echo $c > {counter}; "
                '    [ "$c" -le 1 ] && reject=1;;\n  esac\n'
                "done\n[ \"$reject\" -eq 1 ] && exit 1\nexit 0\n")
    hook.write_text(body)
    os.chmod(str(hook), 0o755)


def _sandbox(tmp_path, hook_mode):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    scratch = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(scratch))
    (scratch / "readme.md").write_text("seed\n")
    _git("-C", str(scratch), "add", "-A")
    _git("-C", str(scratch), *_cid(), "commit", "-qm", "seed main")
    r = _git("-C", str(scratch), *_cid(), "push", "-q", str(o), "main")
    assert r.returncode == 0, r.stderr
    _install_hook(o, hook_mode)
    _seed_spec(o, "T-mp", "test -f marker.txt")
    # node clone (a real repo cwd; origin publishes no law → pass-through)
    w = tmp_path / "w"
    (w).mkdir()
    _git("init", "-q", "-b", "main", str(w))
    (w / ".gitkeep").write_text("")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "node")
    stub = w / "stub"
    stub.mkdir()
    oc = stub / "opencode"
    oc.write_text("#!/bin/bash\necho work > marker.txt\nexit 0\n")
    os.chmod(str(oc), 0o755)
    return o, w


def _run_worker(o, w, label="wm"):
    env = dict(os.environ, SWARM_ORIGIN=str(o), OPENCODE_BIN=str(w / "stub" / "opencode"),
               SWARM_MODEL="stub/model")
    r = subprocess.run([sys.executable, str(REPO / "l2" / "inrepo.py"), "worker", label],
                       cwd=str(w), capture_output=True, text=True, env=env, timeout=300)
    return r, [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]


def test_permanent_main_rejection_never_verdicts_true(tmp_path):
    o, w = _sandbox(tmp_path, "always")
    r, events = _run_worker(o, w)
    assert r.returncode == 0, r.stdout + r.stderr
    atts = [e for e in events if e["event"] == "attempted"]
    # the c6 bound: exactly the requeued attempt + one heir
    assert len(atts) == 2, events
    first, second = atts
    assert first["fixed"] is False and first["main_push"] is False
    assert first["env_death"] is False
    assert first["requeued"] is True and first["reason"] == "main_push_rejected"
    assert second["fixed"] is False and second["main_push"] is False
    assert second["requeued"] is None  # heir exhausted: final verdict
    assert events[-1] == {"event": "worker_done", "worker": "wm",
                          "completed": 2}
    # the origin's verdict says fixed:false — never the lie
    body = _git("-C", str(o), "log", "-1", "--format=%B",
                "refs/swarm/verdicts/T-mp").stdout
    assert "fixed: false" in body
    # dead attempt archived, claim live (heir-exhausted shape)
    refs = _git("ls-remote", str(o)).stdout
    assert "refs/swarm/archive/claims/T-mp@" in refs
    assert "refs/swarm/claims/T-mp" in refs
    # the task is closed: not claimable again
    os.environ["SWARM_ORIGIN"] = str(o)
    m = importlib.util.spec_from_file_location("inrepo", REPO / "l2" / "inrepo.py")
    mm = importlib.util.module_from_spec(m); m.loader.exec_module(mm)
    try:
        assert mm.open_tasks() == []
    finally:
        del os.environ["SWARM_ORIGIN"]


def test_single_collision_recovers_via_rebase(tmp_path):
    o, w = _sandbox(tmp_path, "once")
    r, events = _run_worker(o, w)
    assert r.returncode == 0, r.stdout + r.stderr
    atts = [e for e in events if e["event"] == "attempted"]
    assert len(atts) == 1
    assert atts[0]["fixed"] is True and atts[0]["main_push"] is True
    assert events[-1]["completed"] == 1
