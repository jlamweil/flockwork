"""Operator-authored contract for the audit H1 base rate (wave 12,
INT-013, 2026-09-29). audit()'s H1 was quantified over ALL spec tasks —
`all(...) if A else False` — so any task with NO live claim yet (an
idle board, or one where every cycle has settled but a spec was
re-seeded for a fresh round) made the operator's primary health
instrument read h1_pass FALSE. The lane's own receipts document the
confusion it manufactures: A Board Where Nothing Is Wrong Reads
VIOLATED — the inverse of the 09-20 law that 'queue empty' and
'cannot see the queue' must not look alike. A health gate must judge
CLAIMS (every live claim has its return+verdict with the matching
att), not PENding work.

Contract (local bare origin + the lane's own audit CLI in a subprocess
with SWARM_ORIGIN env-bound BEFORE import — the T5 law):
  - idle spec (claim+return+verdict never written): h1_pass TRUE,
    the task reported as pending — never a violation
  - all cycles settled (swept: live claim/return/verdict refs gone,
    work at refs/swarm/archive/...): h1_pass TRUE over the re-seeded
    spec — the board stays green between rounds
  - the invariant survives: a live claim WITHOUT its return+verdict
    (and verdict-att mismatches) still reads h1_pass FALSE
  - orphans stay report-only and never flip h1 (frozen contract)

All in-parent calls here target LOCAL fixture paths — no env-bound
ORIGIN is touched (the wave-6/8 hermeticity law).
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


def _cid():
    return "-c", "user.email=a@b", "-c", "user.name=a"


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    return _git("-C", w, *_cid(), "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    r = _git("-C", w, *_cid(), "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


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
    return str(o), str(w)


def _seed_spec(o, w, task):
    _push(o, w, _root_commit(w, f"spec {task}\nverify: true"),
          f"refs/swarm/specs/{task}")


def _seed_complete(o, w, task, att):
    _push(o, w, _root_commit(w, f"claim {task} {att}"),
          f"refs/swarm/claims/{task}")
    _push(o, w, _root_commit(w, f"fix {task}\n\nAttempt: {att}"),
          f"refs/swarm/tasks/{task}")
    _push(o, w, _root_commit(
        w, f"verdict\ntask: {task}\nattempt: {att}\nfixed: true\n"
           f"host: h\noc_rc: 0\npytest_rc: 0"),
        f"refs/swarm/verdicts/{task}")


def test_idle_spec_is_pending_not_violated(lane):
    """The falsifier: one spec, never claimed. On the unmodified tree
    h1_pass is False — a healthy idle board reading VIOLATED."""
    o, w = lane
    _seed_spec(o, w, "T-idle")
    p = _audit_payload(o)
    assert p["h1_pass"] is True
    assert p["tasks"]["T-idle"] == {
        "claimed": None, "returned": False, "verdict": None,
        "verdict_att": None,
    }
    assert p["claim_orphans"] == []


def test_settled_board_reads_healthy(lane):
    """Every cycle settled (live refs swept to archive), a fresh spec
    seeded for the next round: the board is green."""
    o, w = lane
    _seed_spec(o, w, "T-round")
    _seed_complete(o, w, "T-round", "att-w1-done01")
    inrepo.sweep(o, "T-round")  # archive + free: the CG cycle law
    _seed_spec(o, w, "T-next")
    p = _audit_payload(o)
    assert p["h1_pass"] is True
    assert p["tasks"]["T-next"]["claimed"] is None


def test_live_claim_without_return_still_violates(lane):
    """The invariant survives the base-rate repair: a live claim whose
    return+verdict are missing reads h1_pass False (an attempt IS
    outstanding)."""
    o, w = lane
    _seed_spec(o, w, "T-open")
    _push(o, w, _root_commit(w, "claim T-open att-w2-open1"),
          f"refs/swarm/claims/T-open")
    p = _audit_payload(o)
    assert p["h1_pass"] is False
    assert p["tasks"]["T-open"]["claimed"] == "att-w2-open1"


def test_verdict_att_mismatch_still_violates(lane):
    """Verdict att ≠ claim att on a live claim: still a violation. The
    old verdict+return (att-aaa) stay live while a NEW claim (att-bbb)
    takes the CAS ref — the create-once law forbids re-pushing over the
    aaa claim, so only return+verdict are pre-seeded."""
    o, w = lane
    _seed_spec(o, w, "T-mix")
    att_a = "att-w2-aaa001"
    _push(o, w, _root_commit(w, f"fix T-mix\n\nAttempt: {att_a}"),
          f"refs/swarm/tasks/T-mix")
    _push(o, w, _root_commit(
        w, f"verdict\ntask: T-mix\nattempt: {att_a}\nfixed: true\n"
           f"host: h\noc_rc: 0\npytest_rc: 0"),
        f"refs/swarm/verdicts/T-mix")
    _push(o, w, _root_commit(w, "claim T-mix att-w2-bbb002"),
          f"refs/swarm/claims/T-mix")
    p = _audit_payload(o)
    assert p["h1_pass"] is False
