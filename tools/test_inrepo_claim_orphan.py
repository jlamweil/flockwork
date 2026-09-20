"""Operator-authored contract for claim-orphan observability (2026-09-21
tail wave). The claim-CAS namespace accepts ANY ref name: a claim for a
task with no spec is creatable by the env-bind slip class (T5 lesson —
ORIGIN binds at import; the wave-4 test fault pushed `claim T
att-w1-3b4d61` to the production substrate at 23:51:21 and audit's h1
passed over it). Contract:
  - audit() reports `claim_orphans`: every live refs/swarm/claims/<t>
    ref (no "@") whose task has no spec on the origin — namespace-global,
    independent of the `only` task filter.
  - REPORT-ONLY: h1 stays the per-spec claimed/returned/verdicted-att
    invariant; orphans never flip it.
  - the repair path is sweep() with the att recovered from the claim
    body (the archive marker must be TRUE, never degraded).
These tests run against a LOCAL bare origin — no fleet, no network.
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
    return subprocess.run(["git", *a], cwd=cwd, text=True,
                          capture_output=True)


def _commit(w, msg):
    et = _git("-C", str(w), "hash-object", "-t", "tree",
              "/dev/null").stdout.strip()
    return _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
                "commit-tree", et, "-m", msg).stdout.strip()


@pytest.fixture
def origin(tmp_path):
    """One healthy quad (T-a) + two orphan claims + one "@"-bearing
    claim ref. SWARM_ORIGIN is set BEFORE the caller imports the module
    (the T5 lesson is load-bearing)."""
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
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    et = _git("-C", str(w), "hash-object", "-t", "tree",
              "/dev/null").stdout.strip()
    # healthy quad: spec + claim + tasks + verdicts, matching atts
    s = _git("-C", str(w), *cid, "commit-tree", et,
             "-m", "spec T-a").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o), f"{s}:refs/swarm/specs/T-a")
    c = _git("-C", str(w), *cid, "commit-tree", et,
             "-m", "claim T-a att-w1-aaaa").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o), f"{c}:refs/swarm/claims/T-a")
    msha = _git("-C", str(w), "rev-parse", "main").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o), f"{msha}:refs/swarm/tasks/T-a")
    v = _git("-C", str(w), *cid, "commit-tree", et,
             "-m", "verdict T-a\nfixed: true\nattempt: att-w1-aaaa").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o), f"{v}:refs/swarm/verdicts/T-a")
    # the litter shape: claims for spec-less tasks
    for name, att in (("T-orphan", "att-w1-bbbb"), ("T-orphan-b", "att-w1-cccc")):
        c2 = _git("-C", str(w), *cid, "commit-tree", et,
                  f"-m", f"claim {name} {att}").stdout.strip()
        _git("-C", str(w), "push", "-q", str(o),
             f"{c2}:refs/swarm/claims/{name}")
    # archive-marker lookalike in the LIVE namespace — never an orphan
    c3 = _git("-C", str(w), *cid, "commit-tree", et,
              "-m", "claim T-att@att-w1-zzz att-w1-zzz").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o),
         f"{c3}:refs/swarm/claims/T-att@att-w1-zzz")
    return str(o)


def _audit_payload(origin, *args):
    """audit in a SUBPROCESS with SWARM_ORIGIN in the env BEFORE import;
    decode the last column-0 JSON object (the pretty-printed report)."""
    env = dict(os.environ, SWARM_ORIGIN=origin)
    r = subprocess.run([sys.executable, "l2/inrepo.py", "audit", *args],
                       cwd=REPO, capture_output=True, text=True, env=env,
                       timeout=120)
    dec = json.JSONDecoder()
    payload = None
    out = r.stdout
    for i, ch in enumerate(out):
        if ch == "{" and (i == 0 or out[i - 1] == "\n"):
            try:
                payload, _ = dec.raw_decode(out, i)
            except json.JSONDecodeError:
                pass
    assert payload is not None, \
        f"no JSON object in stdout: {out[:200]!r} {r.stderr[-200:]!r}"
    return payload


def test_audit_reports_claim_orphans(origin):
    payload = _audit_payload(origin)
    assert payload["h1_pass"] is True
    assert payload["claim_orphans"] == ["T-orphan", "T-orphan-b"]


def test_claim_orphans_report_only_and_global(origin):
    """Report-only: orphans never flip h1. Namespace-global: the orphan
    scan runs even when `only` narrows the per-task table."""
    payload = _audit_payload(origin, "T-a")
    assert payload["h1_pass"] is True
    assert set(payload["tasks"]) == {"T-a"}
    assert payload["claim_orphans"] == ["T-orphan", "T-orphan-b"]


def test_sweep_orphan_recovers_true_att(origin, tmp_path):
    """The repair path: sweep of a spec-less claim archives at the att
    recovered from the claim BODY (marker true, never degraded) and
    frees the live ref; audit then reports no orphans."""
    res = inrepo.sweep(origin, "T-orphan")
    assert res["att"] == "att-w1-bbbb"
    assert res["archived"] == ["refs/swarm/archive/claims/T-orphan@att-w1-bbbb"]
    assert res["deleted"] == ["refs/swarm/claims/T-orphan"]
    refs = _git("ls-remote", origin).stdout
    assert "refs/swarm/archive/claims/T-orphan@att-w1-bbbb" in refs
    live = {ln.split("\t")[1] for ln in refs.splitlines() if "\t" in ln}
    assert "refs/swarm/claims/T-orphan" not in live  # exact ref, not a
    # substring of the still-live T-orphan-b
    payload = _audit_payload(origin)
    assert payload["claim_orphans"] == ["T-orphan-b"]
    assert payload["h1_pass"] is True
