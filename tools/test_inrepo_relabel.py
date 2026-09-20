"""Contract: archive markers must embed the true att (L3/c8 @-law;
AUTOWORK audit finding 4, revision preregistered in
LOOP-2026-09-20.md addendum).

  - relabel_orphan_archives(origin): every archive ref whose @-marker is
    not att-* is renamed to the true att recovered from its own body —
    same sha, one atomic push, never overwriting an existing target,
    never guessing when no att-* token is recoverable.
  - sweep(origin, task) with a LIVE claim whose body read fails must
    REFUSE (RuntimeError, live refs untouched) — the att was
    recoverable in principle; archiving under @unknown would delete
    the refs that carry it. The claim-less orphan shape still sweeps
    to @unknown (round-6 finding 5: zombie refs must not persist).

Local bare origins only — no fleet, no network.
"""
import importlib.util
import pathlib
import subprocess

import pytest


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_rel", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None, inp=None):
    return subprocess.run(["git", *a], cwd=cwd, input=inp, text=True,
                          capture_output=True)


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
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    et = _git("-C", str(w), "hash-object", "-t", "tree",
              "/dev/null").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o), "main")
    return str(o), str(w), et, cid


def _root_commit(w, et, cid, msg):
    c = _git("-C", str(w), *cid, "commit-tree", et, "-m", msg).stdout.strip()
    return c


def test_relabel_renames_orphan_to_true_att(origin):
    o, w, et, cid = origin
    c = _root_commit(w, et, cid, "claim T-rel att-w7-rel01")
    _git("-C", str(w), "push", "-q", str(o),
         f"{c}:refs/swarm/archive/claims/T-rel@orphan")
    res = inrepo.relabel_orphan_archives(o)
    assert res["relabeled"] == [{"from": "refs/swarm/archive/claims/T-rel@orphan",
                                 "to": "refs/swarm/archive/claims/T-rel@att-w7-rel01",
                                 "sha": c}]
    refs = _git("ls-remote", o).stdout
    assert "refs/swarm/archive/claims/T-rel@orphan" not in refs
    assert "refs/swarm/archive/claims/T-rel@att-w7-rel01" in refs
    assert c in refs  # same objects, renamed — nothing rewritten
    res2 = inrepo.relabel_orphan_archives(o)
    assert res2["relabeled"] == []  # idempotent


def test_relabel_skips_when_no_att_recoverable(origin):
    o, w, et, cid = origin
    c = _root_commit(w, et, cid, "claim T-bad attless-body")
    _git("-C", str(w), "push", "-q", str(o),
         f"{c}:refs/swarm/archive/claims/T-bad@orphan")
    res = inrepo.relabel_orphan_archives(o)
    assert res["relabeled"] == []
    assert res["skipped"] and res["skipped"][0]["ref"].endswith("T-bad@orphan")
    assert "refs/swarm/archive/claims/T-bad@orphan" in _git("ls-remote", o).stdout


def test_relabel_refuses_existing_target(origin):
    o, w, et, cid = origin
    c1 = _root_commit(w, et, cid, "claim T-dupe att-w8-dupe01")
    c2 = _root_commit(w, et, cid, "claim T-dupe att-w8-dupe01")
    _git("-C", str(w), "push", "-q", str(o),
         f"{c1}:refs/swarm/archive/claims/T-dupe@att-w8-dupe01")
    _git("-C", str(w), "push", "-q", str(o),
         f"{c2}:refs/swarm/archive/claims/T-dupe@orphan")
    res = inrepo.relabel_orphan_archives(o)
    assert res["relabeled"] == []
    assert res["skipped"], "target exists — must refuse, never overwrite"
    refs = _git("ls-remote", o).stdout
    assert "refs/swarm/archive/claims/T-dupe@orphan" in refs


def test_sweep_refuses_unreadable_live_claim_body(origin):
    o, w, et, cid = origin
    c = _root_commit(w, et, cid, "claim T-gate att-w9-gate01")
    v = _root_commit(w, et, cid, "verdict T-gate\nfixed: false")
    _git("-C", str(w), "push", "-q", str(o), f"{c}:refs/swarm/claims/T-gate")
    _git("-C", str(w), "push", "-q", str(o), f"{v}:refs/swarm/verdicts/T-gate")
    real = inrepo.origin_body
    inrepo.origin_body = lambda *a, **k: ""  # simulate a dead origin-host read
    try:
        with pytest.raises(RuntimeError):
            inrepo.sweep(o, "T-gate")
    finally:
        inrepo.origin_body = real
    refs = _git("ls-remote", o).stdout
    # live refs untouched — the att was recoverable in principle
    assert "refs/swarm/claims/T-gate" in refs
    assert "refs/swarm/verdicts/T-gate" in refs
    assert "@unknown" not in refs


def test_sweep_retries_transient_body_read_failure(origin):
    o, w, et, cid = origin
    c = _root_commit(w, et, cid, "claim T-flaky att-wa-flaky01")
    v = _root_commit(w, et, cid, "verdict T-flaky\nfixed: false")
    _git("-C", str(w), "push", "-q", str(o), f"{c}:refs/swarm/claims/T-flaky")
    _git("-C", str(w), "push", "-q", str(o), f"{v}:refs/swarm/verdicts/T-flaky")
    real = inrepo.origin_body
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return "" if calls["n"] == 1 else real(*a, **k)

    inrepo.origin_body = flaky
    try:
        res = inrepo.sweep(o, "T-flaky")
    finally:
        inrepo.origin_body = real
    assert res["att"] == "att-wa-flaky01"
    assert "refs/swarm/archive/claims/T-flaky@att-wa-flaky01" in \
        _git("ls-remote", o).stdout


def test_sweep_orphan_shape_still_archives_at_unknown(origin):
    """The gate must not over-refuse: no live claim -> nothing to read ->
    @unknown stands (the zombie-refs law outranks the marker cosmetics),
    and relabel can repair it later if the att ever surfaces."""
    o, w, et, cid = origin
    v = _root_commit(w, et, cid, "verdict T-orp\nfixed: false")
    _git("-C", str(w), "push", "-q", str(o), f"{v}:refs/swarm/verdicts/T-orp")
    res = inrepo.sweep(o, "T-orp")
    assert res["att"] == "unknown"
    assert "refs/swarm/archive/verdicts/T-orp@unknown" in \
        _git("ls-remote", o).stdout
    res2 = inrepo.relabel_orphan_archives(o)
    assert res2["relabeled"] == []  # no body token to recover — stays, flagged
    assert res2["skipped"]
