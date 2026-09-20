"""Operator-authored contract for the claim-loss honesty bound
(wave-4, 2026-09-20). Measured tonight with claim()'s exact push argv:
  - race loss      rc 1   `! [rejected] … (stale info)` — create-once held
  - unreachable    rc 128 `fatal: '<path>' does not appear to be a git
                          repository …`
  - non-repo cwd   rc 128 `fatal: not a git repository …`
worker() treats every claim failure as a lost race (sleep, re-scan,
forever) — a structurally broken environment spins silently, the same
"harm when most needed" shape open_tasks' honest give-up already fixed
on the scan side. Contract:
  - classify_claim_failure(rc, stderr) -> "race" | "structural";
    rejected/stale-info markers = race; EVERYTHING else = structural
    (the safe side — an unclassified failure must not spin)
  - a structural claim failure emits claim_failed (rc, err, counter)
  - consecutive structural failures reuse SWARM_WORKER_MAX_FAILS; at
    the limit: worker_gave_up reason=claim + exit 1, BOUNDED — the
    counter resets on any successful claim
  - race losses never count (contention storms stay healthy)
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
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


RACE_STDERR = (
    "To /tmp/x/o.git\n ! [rejected]   b19f -> refs/swarm/claims/T (stale info)\n"
    "error: failed to push some refs to '/tmp/x/o.git'"
)
UNREACHABLE_STDERR = (
    "fatal: '/tmp/x/vanished.git' does not appear to be a git repository\n"
    "fatal: Could not read from remote repository."
)
NONREPO_STDERR = (
    "fatal: not a git repository (or any of the parent directories): .git"
)


# ------------------------------------------------- classifier (unit)


def test_classifier_race_vs_structural():
    assert inrepo.classify_claim_failure(1, RACE_STDERR) == "race"
    assert inrepo.classify_claim_failure(128, UNREACHABLE_STDERR) == "structural"
    assert inrepo.classify_claim_failure(128, NONREPO_STDERR) == "structural"
    # hostile: an rc-1 rejection with NO stale-info markers — a push
    # denial we have never seen must NOT be read as healthy contention
    assert inrepo.classify_claim_failure(1, "error: failed to push some refs") == "structural"
    assert inrepo.classify_claim_failure(1, "") == "structural"
    assert inrepo.classify_claim_failure(1, "remote: permission denied") == "structural"


def test_claim_detail_reports_rc_and_stderr(tmp_path):
    # structural failure surfaces through claim_detail, not a bare None
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "s")
    os.environ["SWARM_ORIGIN"] = str(o)
    try:
        # claim from a NON-REPO cwd: commit-tree still runs in w, push
        # runs in the process cwd — structural
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            d = inrepo.claim_detail("w1", "T")
        finally:
            os.chdir(cwd)
        assert d["att"] is None
        assert d["rc"] == 128 and "not a git repository" in d["stderr"]
        assert inrepo.classify_claim_failure(d["rc"], d["stderr"]) == "structural"
        # success path unchanged: from the repo cwd the claim lands
        os.chdir(str(w))
        try:
            d2 = inrepo.claim_detail("w1", "T")
        finally:
            os.chdir(cwd)
        assert d2["att"] and d2["att"].startswith("att-w1-")
        assert "refs/swarm/claims/T" in _git("ls-remote", str(o)).stdout
    finally:
        del os.environ["SWARM_ORIGIN"]


# ------------------------------------- worker-level bounded give-up


def _seed_spec(o, task, verify="true"):
    scratch = o.parent / "scratch"
    if not scratch.exists():
        _git("init", "-q", "-b", "main", str(scratch))
    et = _git("hash-object", "-t", "tree", "/dev/null").stdout.strip()
    spec = _git("-C", str(scratch), "-c", "user.email=a@b", "-c", "user.name=a",
                "commit-tree", et, "-m", f"spec {task}\nverify: {verify}").stdout.strip()
    r = _git("-C", str(scratch), "-c", "user.email=a@b", "-c", "user.name=a",
             "push", "-q", str(o), f"{spec}:refs/swarm/specs/{task}")
    assert r.returncode == 0, r.stderr


def test_structural_claim_failures_bounded_gave_up(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    _seed_spec(o, "T-broken")
    nonrepo = tmp_path / "broken-node"
    nonrepo.mkdir()                       # a node without a checkout: the
    # scan sees the queue (ls-remote works), every claim fails 128 —
    # today this spins FOREVER; post-gate: bounded honest give-up
    env = dict(os.environ, SWARM_ORIGIN=str(o), SWARM_WORKER_MAX_FAILS="2")
    try:
        r = subprocess.run(
            [sys.executable, str(REPO / "l2" / "inrepo.py"), "worker", "wb"],
            cwd=str(nonrepo), capture_output=True, text=True,
            env=env, timeout=120)
    except subprocess.TimeoutExpired:
        pytest.fail("worker spun on structural claim failures (no bound)")
    assert r.returncode == 1
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    kinds = [e["event"] for e in events]
    assert kinds == ["claim_failed", "claim_failed", "worker_gave_up"]
    assert all(e["rc"] == 128 and "not a git repository" in e["err"]
               for e in events if e["event"] == "claim_failed")
    assert events[-1]["reason"] == "claim"
    assert events[-1]["completed"] == 0


def test_race_losses_do_not_count_against_the_bound():
    # contention must stay healthy: only structural failures count
    # (verified at the classifier seam — the live 8-thread probe is
    # measured in the results JSON, racy by nature for pytest)
    assert inrepo.classify_claim_failure(1, RACE_STDERR) == "race"
