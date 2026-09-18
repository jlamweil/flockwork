"""Operator-authored contract for reconcile() — the lane's stale-claim
recovery (hardening round, 2026-09-18). The dogfood batch recovered six
environmental deaths by MANUAL sweep; reconcile automates detection:
a claim older than the lease TTL whose task lacks live return+verdict
is archived+freed (the T4 sweep, one atomic push), so the task re-enters
open_tasks. Contract (local bare origin, no fleet, no network):
  - stale claim-only and claim+return-no-verdict → swept, task re-open
  - fresh claim (age <= TTL) → untouched
  - healthy task (claim+return+verdict) → untouched
  - orphan return/verdict refs (no live claim) → flagged, refs preserved
  - undateable claim object → flagged, NOT swept (never destroy what
    you cannot date)
  - a second pass over a reconciled lane is a no-op
"""
import importlib.util
import os
import pathlib
import subprocess

import pytest


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None, env=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                          text=True, env=env)


def _root_commit(w, msg, age_s=None):
    """Root commit with a controlled committer date (None = now)."""
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    env = None
    if age_s is not None:
        import time
        past = f"@{int(time.time() - age_s)} +0000"
        env = dict(os.environ, GIT_COMMITTER_DATE=past,
                   GIT_AUTHOR_DATE=past)
    return _git("-C", w, "commit-tree", et, "-m", msg, env=env).stdout.strip()


def _push(o, src, ref):
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    r = _git("-C", _w[0], *cid, "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


@pytest.fixture
def origin(tmp_path):
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
    _w[0] = str(w)
    return str(o), str(w)


_w = [None]  # worktree path for _push (pytest fixtures can't pass it down)


def _refs(o):
    return _git("ls-remote", o).stdout


# ------------------------------------------------------------- stale


def test_stale_claim_only_is_swept_and_task_reopens(origin):
    o, w = origin
    spec = _root_commit(w, "spec T-stale\nverify: true")
    _push(o, spec, "refs/swarm/specs/T-stale")
    claim = _root_commit(w, "claim T-stale att-w1-dead01", age_s=7200)
    _push(o, claim, "refs/swarm/claims/T-stale")
    res = inrepo.reconcile(o, ttl_s=1800)
    assert res["stale"] == [{"task": "T-stale", "age_s": pytest.approx(7200, abs=5),
                             "shape": "claim"}]
    assert res["swept"] == ["att-w1-dead01"]
    refs = _refs(o)
    assert "refs/swarm/claims/T-stale" not in refs          # freed
    assert "refs/swarm/archive/claims/T-stale@att-w1-dead01" in refs
    assert claim in refs                                    # object preserved
    # re-entry is definitional: spec present, claim and return absent
    os.environ["SWARM_ORIGIN"] = o
    try:
        assert inrepo.open_tasks() == ["T-stale"]
    finally:
        del os.environ["SWARM_ORIGIN"]


def test_stale_claim_with_return_but_no_verdict_is_swept(origin):
    o, w = origin
    claim = _root_commit(w, "claim T-half att-w2-dead02", age_s=7200)
    _push(o, claim, "refs/swarm/claims/T-half")
    msha = _git("-C", w, "rev-parse", "main").stdout.strip()
    _push(o, msha, "refs/swarm/tasks/T-half")
    res = inrepo.reconcile(o, ttl_s=1800)
    assert res["swept"] == ["att-w2-dead02"]
    assert res["stale"][0]["shape"] == "claim+return"
    refs = _refs(o)
    assert "refs/swarm/claims/T-half" not in refs
    assert "refs/swarm/tasks/T-half" not in refs
    assert "refs/swarm/archive/claims/T-half@att-w2-dead02" in refs
    assert "refs/swarm/archive/tasks/T-half@att-w2-dead02" in refs


# ------------------------------------------------------------- fresh


def test_fresh_claim_is_untouched(origin):
    o, w = origin
    claim = _root_commit(w, "claim T-live att-w3-alive1")  # now
    _push(o, claim, "refs/swarm/claims/T-live")
    res = inrepo.reconcile(o, ttl_s=1800)
    assert res["fresh"] == ["T-live"]
    assert res["stale"] == [] and res["swept"] == []
    assert f"refs/swarm/claims/T-live" in _refs(o)


# ----------------------------------------------------------- healthy


def test_healthy_task_is_untouched_even_when_old(origin):
    o, w = origin
    claim = _root_commit(w, "claim T-done att-w4-old001", age_s=86400)
    _push(o, claim, "refs/swarm/claims/T-done")
    msha = _git("-C", w, "rev-parse", "main").stdout.strip()
    _push(o, msha, "refs/swarm/tasks/T-done")
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    v = _git("-C", w, *cid, "commit-tree", et,
             "-m", "verdict\ntask: T-done\nfixed: true\nattempt: att-w4-old001"
             ).stdout.strip()
    _push(o, v, "refs/swarm/verdicts/T-done")
    res = inrepo.reconcile(o, ttl_s=1800)
    assert res["healthy"] == ["T-done"]
    assert res["stale"] == [] and res["swept"] == []
    refs = _refs(o)
    assert "refs/swarm/claims/T-done" in refs
    assert "refs/swarm/verdicts/T-done" in refs


# --------------------------------------------------------- anomalies


def test_orphan_return_ref_is_flagged_not_swept(origin):
    o, w = origin
    msha = _git("-C", w, "rev-parse", "main").stdout.strip()
    _push(o, msha, "refs/swarm/tasks/T-orphan")  # no claim, no verdict
    res = inrepo.reconcile(o, ttl_s=1800)
    assert {"task": "T-orphan",
            "reason": "live tasks ref with no live claim"} in res["anomalies"]
    assert f"refs/swarm/tasks/T-orphan" in _refs(o)  # preserved


def test_undateable_claim_is_flagged_not_swept(origin):
    o, w = origin
    tree = _git("-C", w, "rev-parse", "main^{tree}").stdout.strip()
    _push(o, tree, "refs/swarm/claims/T-weird")  # a tree, not a commit
    res = inrepo.reconcile(o, ttl_s=1800)
    assert {"task": "T-weird",
            "reason": "claim object undateable; not swept"} in res["anomalies"]
    assert "refs/swarm/claims/T-weird" in _refs(o)  # preserved


# --------------------------------------------------------- idempotent


def test_second_pass_over_reconciled_lane_is_noop(origin):
    o, w = origin
    claim = _root_commit(w, "claim T-again att-w5-dead03", age_s=7200)
    _push(o, claim, "refs/swarm/claims/T-again")
    first = inrepo.reconcile(o, ttl_s=1800)
    assert first["swept"] == ["att-w5-dead03"]
    second = inrepo.reconcile(o, ttl_s=1800)
    assert second["stale"] == [] and second["swept"] == []
    assert second["anomalies"] == []
    refs = _refs(o)
    assert sum(1 for ln in refs.splitlines() if "T-again" in ln) == 1


# ----------------------------------------------------------- CLI


def test_cli_reconcile_defaults_ttl_and_reports_json(origin):
    o, w = origin
    claim = _root_commit(w, "claim T-cli att-w6-dead04", age_s=7200)
    _push(o, claim, "refs/swarm/claims/T-cli")
    import json
    import sys
    repo = pathlib.Path(__file__).resolve().parents[1]
    r = subprocess.run(
        [sys.executable, "l2/inrepo.py", "reconcile", o, "1800"],
        cwd=repo, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    payload = json.loads(r.stdout)
    assert payload["swept"] == ["att-w6-dead04"]
    assert payload["ttl_s"] == 1800.0
