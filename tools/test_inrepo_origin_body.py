"""Operator-authored contract for T5-audit-host-reads (the lane's second
TDD task). The worker's job: make audit() read object bodies on the
ORIGIN HOST so it works even when the auditor's clone lacks freshly
pushed objects. Contract:
  - inrepo.origin_body(origin, ref_or_sha) -> str body text of the
    object (empty string if absent).
  - audit() must use it (verified by behavior: after seeding refs and
    pushing, WITHOUT fetching, audit still reports correct atts).
These tests run against a LOCAL bare origin via the file:// transport —
no fleet, no network.
"""
import importlib.util
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
    _git("-C", str(w), "push", "-q", str(o), "main")
    et = _git("-C", str(w), "hash-object", "-t", "tree",
              "/dev/null").stdout.strip()
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    c = _git("-C", str(w), *cid, "commit-tree", et,
             "-m", "claim T-a att-w1-aaaa").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o), f"{c}:refs/swarm/claims/T-a")
    return str(o), str(w), c


def test_origin_body_reads_claim_body(origin):
    o, w, csha = origin
    body = inrepo.origin_body(o, "refs/swarm/claims/T-a")
    assert body.strip().startswith("claim T-a")
    assert "att-w1-aaaa" in body


def test_origin_body_accepts_raw_sha(origin):
    o, w, csha = origin
    body = inrepo.origin_body(o, csha)
    assert "att-w1-aaaa" in body


def test_origin_body_empty_for_missing_ref(origin):
    o, w, csha = origin
    assert inrepo.origin_body(o, "refs/swarm/claims/nope") == ""


def test_audit_works_without_fetching_fresh_refs(origin, capsys):
    """The behavior test that motivated the subcommand: audit run with a
    stale clone (never fetched the swarm refs) must still report the
    right atts — because bodies come from the origin host."""
    import json as _json
    import os
    o, w, csha = origin
    old = os.environ.get("SWARM_ORIGIN")
    os.environ["SWARM_ORIGIN"] = o
    try:
        inrepo.audit(["T-a"])
    finally:
        if old is None:
            os.environ.pop("SWARM_ORIGIN", None)
        else:
            os.environ["SWARM_ORIGIN"] = old
    out = capsys.readouterr().out
    # audit may print the seed/worker events before the final JSON;
    # take the LAST JSON object on stdout
    payload = _json.loads(out[out.rindex("{"):])
    assert payload["h1_pass"] is True
    assert payload["tasks"]["T-a"]["claimed"] == "att-w1-aaaa"
