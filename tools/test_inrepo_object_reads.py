"""Operator-authored contract for object-level origin reads (wave 14,
INT-013, 2026-09-29). Wave 11 made the BOARD read loud (require_remote:
an unreachable origin raises — 'queue empty' and 'cannot see the queue'
must not look alike) and wave 13 applied the same law to audit's board
read. But the OBJECT reads — origin_raw/origin_body/origin_commit_ts —
still return ""/None on failure, indistinguishable from "the object
exists and its body is empty". Three decision-grade consumers lie:

1. correction_graph(): a failed verdict/claim body read silently drops
   vertices — a SUCCESS-SHAPED INCOMPLETE GRAPH. The INT-032 frozen
   rule grades the substrate "FALSIFIED iff all chains single-vertex",
   so a read blip during the two-model window would falsify the
   substrate on a dead read (the exact wave-11 hazard, one layer down:
   blind → incomplete → FALSIFIED).

2. reconcile(): a failed claim-timestamp read classifies a stale claim
   as "claim object undateable; not swept" — a PERMANENT anomaly label
   on what is a retry-able read failure. Reconcile is the lane's only
   TTL backstop; while the read fails, stale claims are never
   requeued, and the classification tells the operator the object is
   broken when it is the read that is.

3. audit(): a failed verdict-body read yields vd={} → verdict None →
   h1_pass FALSE — a HEALTHY claimed task reads VIOLATED (the wave-12
   inverse: a board where nothing is wrong reads violated because the
   instrument went blind). A failed claim-body read crashes
   `"".split()[-1]` with IndexError — loud but unclassified.

Contract (local fixtures; no fleet, no pushes to any live origin):
  - correction_graph() raises RuntimeError naming the failed read when
    a board-visible verdict/claim object cannot be read; the reachable
    graph is complete (regression guard pinned both ways)
  - reconcile() records the failed dating read in `errors` (retry-able)
    and never in `anomalies` (permanent); the stale-claim sweep is
    preserved when reads succeed
  - audit() raises when a board-visible claim/verdict object cannot be
    read — never h1_pass false on an unreadable verdict, never an
    IndexError on an unreadable claim

The frozen origin_body law is preserved: a ref NOT on the board reads
honestly empty (test_inrepo_origin_body); the strict reads are invoked
only for board-visible refs, where absence of the OBJECT is a broken
board, never an empty body.

Fault staging: ls-remote reads only the refs DB, so DELETING ONE LOOSE
OBJECT leaves the board readable while that object's read fails — the
fine-grained board-vs-object split the wave-11 law did not cover. (A
whole-store fault is unavailable for local origins: git needs objects/
to recognize the repo at all, so the board read itself fails — already
covered by the wave-13 contracts.)

All in-parent calls here target LOCAL fixture paths — no env-bound
ORIGIN is touched (the wave-6/8 hermeticity law).
"""
import importlib.util
import json
import os
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    p = pathlib.Path(REPO / "l2" / "inrepo.py")
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None, env_extra=None):
    env = dict(os.environ, **(env_extra or {}))
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                          text=True, env=env, check=False)


def _cid():
    return "-c", "user.email=a@b", "-c", "user.name=a"


def _root_commit(w, msg, env_extra=None):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    r = _git("-C", w, *_cid(), "commit-tree", et, "-m", msg,
             env_extra=env_extra)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _push(o, w, src, ref):
    r = _git("-C", w, *_cid(), "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


def _refs(o):
    return _git("ls-remote", o).stdout


def _delete_loose_object(o, ref):
    """Delete ONE loose object (a small push unpacks loose) so only that
    object's read fails while the rest of the board — including
    ls-remote — reads fine."""
    sha = _git("-C", o, "rev-parse", ref).stdout.strip()
    obj = pathlib.Path(o) / "objects" / sha[:2] / sha[2:]
    assert obj.exists(), f"expected loose object for {ref}: {obj}"
    obj.unlink()
    return sha


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


TASK = "T-objread"


def _seed_board(o, w, att="att-w14-cla001", att_v=None,
                with_verdict=True):
    """spec + live claim (+ verdict). att_v=None seeds the settled shape
    (the verdict carries the SAME att — h1's matching law); distinct
    atts seed a two-attempt correction-graph shape."""
    _push(o, w, _root_commit(w, f"spec {TASK}\nverify: true"),
          f"refs/swarm/specs/{TASK}")
    _push(o, w, _root_commit(w, f"claim {TASK} {att}"),
          f"refs/swarm/claims/{TASK}")
    # the return ref — H1's full protocol shape is claim+return+verdict
    msha = _git("-C", w, "rev-parse", "main").stdout.strip()
    _push(o, w, msha, f"refs/swarm/tasks/{TASK}")
    if with_verdict:
        _push(o, w, _root_commit(
            w, f"verdict\ntask: {TASK}\nattempt: {att_v or att}\n"
               f"fixed: true\nhost: h\noc_rc: 0\npytest_rc: 0"),
            f"refs/swarm/verdicts/{TASK}")
    return att, att_v or att


# ------------------------------------------- part 1: correction_graph


def test_correction_graph_reachable_board_is_complete(lane):
    """Regression guard: with every object readable the graph carries
    the live claim AND the verdict as vertices, with the corrected
    edge between them."""
    o, w = lane
    att_c, _att_v = _seed_board(o, w, att="att-w14-cla001",
                                att_v="att-w14-ver001")
    atts = [a["att"] for a in inrepo.correction_graph(o)["tasks"][TASK]["attempts"]]
    assert atts == [att_c, "att-w14-ver001"]


def test_correction_graph_never_returns_success_shaped_incomplete(lane):
    """The falsifier: the verdict object is unreadable while the board
    (and the claim object) read fine. Pre-fix the graph returns SUCCESS
    with the verdict vertex silently dropped — one single-vertex chain
    for a task with a live claim and a recorded verdict. Under the
    INT-032 frozen rule (FALSIFIED iff all chains single-vertex) that
    is a substrate falsification by read blip. The contract: raise,
    naming the failed read."""
    o, w = lane
    _att_c, _att_v = _seed_board(o, w, att="att-w14-cla001",
                                 att_v="att-w14-ver001")
    g = inrepo.correction_graph(o)  # reachable: complete (guard above)
    assert len(g["tasks"][TASK]["attempts"]) == 2
    _delete_loose_object(o, f"refs/swarm/verdicts/{TASK}")
    with pytest.raises(RuntimeError) as ei:
        inrepo.correction_graph(o)
    assert "origin object read failed" in str(ei.value)


def test_correction_graph_unreadable_claim_object_raises(lane):
    """The claim-object twin: the live-claim vertex drops the same way
    (its att comes from the claim BODY). Same contract."""
    o, w = lane
    _seed_board(o, w)
    _delete_loose_object(o, f"refs/swarm/claims/{TASK}")
    with pytest.raises(RuntimeError) as ei:
        inrepo.correction_graph(o)
    assert "origin object read failed" in str(ei.value)


# ------------------------------------------------- part 2: reconcile


def _seed_stale_claim(o, w, task, att):
    """A claim whose committer date is far past the default 1800s TTL."""
    _push(o, w, _root_commit(w, f"spec {task}\nverify: true"),
          f"refs/swarm/specs/{task}")
    _push(o, w, _root_commit(
        w, f"claim {task} {att}",
        env_extra={"GIT_COMMITTER_DATE": "2026-09-01T00:00:00Z"}),
        f"refs/swarm/claims/{task}")


def test_reconcile_sweeps_stale_claim_when_reads_succeed(lane):
    """Regression guard: the TTL backstop works on a healthy read."""
    o, w = lane
    att = "att-w14-stale1"
    _seed_stale_claim(o, w, TASK, att)
    out = inrepo.reconcile(o)
    assert out["stale"] and out["swept"] == [att]
    assert f"refs/swarm/archive/claims/{TASK}@{att}" in _refs(o)


def test_reconcile_records_read_failure_not_permanent_anomaly(lane):
    """The falsifier: a stale claim whose claim object cannot be read.
    Pre-fix it lands in `anomalies` as 'claim object undateable; not
    swept' — a PERMANENT classification on a retry-able read failure,
    and reconcile (the only TTL backstop) never recovers the claim
    while the read is down. The contract: `errors`, naming the read —
    the operator sees the truth (the read failed) and a later healthy
    reconcile sweep will succeed."""
    o, w = lane
    _seed_stale_claim(o, w, TASK, "att-w14-stale2")
    _delete_loose_object(o, f"refs/swarm/claims/{TASK}")
    out = inrepo.reconcile(o)
    assert out["stale"] == []
    assert [e["task"] for e in out["errors"]] == [TASK]
    assert "origin object read failed" in out["errors"][0]["err"]
    assert out["anomalies"] == []  # never the permanent label


# ----------------------------------------------------- part 3: audit


def test_audit_healthy_board_when_objects_readable(lane, monkeypatch,
                                                   capsys):
    """Regression guard: claim + verdict live, all reads fine → h1 true
    with the verdict's att matched (the wave-12 law's honest case)."""
    o, w = lane
    att_c, att_v = _seed_board(o, w)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    inrepo.audit()
    out = json.loads(capsys.readouterr().out)
    assert out["h1_pass"] is True
    assert out["tasks"][TASK]["claimed"] == att_c
    assert out["tasks"][TASK]["verdict_att"] == att_v


def test_audit_never_grades_unreadable_verdict_as_violated(lane,
                                                           monkeypatch):
    """The falsifier, fine-grained: only the VERDICT object is unreadable
    (claim and board read fine). Pre-fix vbody='' → vd={} → verdict None
    → h1_pass FALSE: a healthy claimed task with a recorded verdict
    reads VIOLATED because one object read failed — the wave-12
    inverse. The contract: audit raises (the wave-13 law: the operator
    instrument never certifies OR condemns a board it cannot see)."""
    o, w = lane
    _seed_board(o, w)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _delete_loose_object(o, f"refs/swarm/verdicts/{TASK}")
    with pytest.raises(RuntimeError) as ei:
        inrepo.audit()
    assert "origin object read failed" in str(ei.value)


def test_audit_never_crashes_on_unreadable_claim(lane, monkeypatch):
    """The falsifier: only the CLAIM object is unreadable. Pre-fix
    `body(claims/T).strip().split()[-1]` raises IndexError — loud but
    unclassified (no origin, no object, no hint). The contract: a
    RuntimeError naming the failed read."""
    o, w = lane
    _seed_board(o, w, with_verdict=False)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _delete_loose_object(o, f"refs/swarm/claims/{TASK}")
    with pytest.raises(RuntimeError) as ei:
        inrepo.audit()
    assert "origin object read failed" in str(ei.value)
