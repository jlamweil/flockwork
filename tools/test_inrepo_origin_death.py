"""Operator-authored contract for origin-death mid-attempt (wave 13,
INT-013, 2026-09-29). Wave 11 made the automation READS fail loudly on
an unreachable origin — but two decision-grade consumers of origin
state were left rc-blind, and the attempt handler's own requeue legs
crash on a mid-attempt origin death:

1. audit() read the board with the UNCHECKED remote(): a dead origin
   yielded an empty have → empty A → h1_pass true. Wave 12 had just
   made the empty board read HEALTHY (correctly, for a reachable idle
   board) — composed, the two waves made audit the worst instrument on
   the wall: blind → empty → "healthy". The operator's primary health
   instrument must not be able to certify a board it cannot see.

2. heirs_count() read refs/swarm/archive/claims/<task>@* with the
   unchecked git() and returned 0 on any failure — and that 0 FEEDS THE
   c6 ONE-HEIR GATE. A dead origin therefore silently OPENS the heir
   budget (every fresh requeue looks budget-fresh), and the requeue
   legs' bare sweep() then raises into the worker's work_task_error
   catch-all: an attempt that died honestly gets recorded as an
   unstructured crash, zero c6 accounting, while the claim stays live
   to be TTL-swept later. The pre-dispatch paths (clone leg, wave-9
   helper) already guarded their sweep with try/except RuntimeError —
   the wave-10 helper even gates on heirs_count — so the post-dispatch
   legs were the last unguarded shape, one origin blip away from a
   crash storm exactly when the substrate is flapping.

Contract (local fixtures; no fleet, no pushes to any live origin):
  - audit() raises RuntimeError naming the unreachable origin; a
    REACHABLE idle board still reads h1_pass true (wave-12 law kept)
  - heirs_count() raises on an unreachable origin (never a lying 0);
    a reachable empty archive still counts 0
  - a mid-attempt origin death (staged: the origin directory is
    renamed away after dispatch) returns a STRUCTURED event —
    requeued False + sweep_error — never a crash; the claim stays
    live for reconcile's TTL; the backend leg's record proves the
    work was genuinely attempted before the death
  - the fresh-budget requeue paths are preserved when the origin
    stays up (regression guards, both dispatch shapes)

work_task runs in a SUBPROCESS with SWARM_ORIGIN in the env — ORIGIN
is bound at import time (the T5 lesson). All in-parent calls here
target LOCAL fixture paths — no env-bound ORIGIN is touched (the
wave-6/8 hermeticity law).
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


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    return _git("-C", w, *cid, "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    r = _git("-C", w, *cid, "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


def _refs(o):
    return _git("ls-remote", o).stdout


DRIVER = """\
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('inrepo', 'l2/inrepo.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
mode = sys.argv[1]
if mode == "open_tasks":
    try:
        print(json.dumps(m.open_tasks()))
    except RuntimeError as e:
        print(json.dumps({"RuntimeError": str(e)[:120]}))
else:
    print(json.dumps(m.work_task(sys.argv[2], sys.argv[3], sys.argv[4])))
"""


def _run_driver(env_extra, *args):
    env = dict(os.environ, **env_extra)
    r = subprocess.run(
        [sys.executable, "-c", DRIVER, *args],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=300,
    )
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _kill_origin(o):
    """Race-stable origin death: flip the directory away, never delete.
    A concurrent reader either sees the path gone (honest failure) or
    the flipped name (honest ls-remote of an empty spot) — either way
    every read fails loudly instead of half-working on a deleted tree."""
    gone = o + ".killed"
    os.rename(o, gone)
    return gone


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
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "push", "-q", str(o), "main")
    stub = tmp_path / "stub"
    stub.mkdir()
    return str(o), str(w), str(stub)


# ------------------------------------------------- part A: the reads


def test_audit_raises_on_unreachable_origin(lane, monkeypatch):
    o, w, stub = lane
    monkeypatch.setenv("SWARM_ORIGIN", o + ".never-created")
    with pytest.raises(RuntimeError) as ei:
        inrepo.audit()
    assert "origin unreachable" in str(ei.value)


def test_audit_reachable_idle_board_still_reads_healthy(lane, monkeypatch, capsys):
    """The wave-12 law is kept where it was right: a reachable board with
    nothing claimed is HEALTHY, not violating."""
    o, w, stub = lane
    monkeypatch.setenv("SWARM_ORIGIN", o)
    inrepo.audit()
    out = json.loads(capsys.readouterr().out)
    assert out["h1_pass"] is True and out["tasks"] == {}


def test_audit_never_grades_a_blind_board_healthy(lane, monkeypatch, capsys):
    """The composed waves-11+12 falsifier: the same instrument, the same
    board, the only difference is reachability — pre-fix the dead origin
    printed h1_pass true (blind → empty → healthy)."""
    o, w, stub = lane
    monkeypatch.setenv("SWARM_ORIGIN", o)
    inrepo.audit()
    assert json.loads(capsys.readouterr().out)["h1_pass"] is True
    gone = _kill_origin(o)
    try:
        with pytest.raises(RuntimeError) as ei:
            inrepo.audit()
        assert "origin unreachable" in str(ei.value)
    finally:
        os.rename(gone, o)


def test_heirs_count_refuses_unreachable_origin(lane):
    """Decision-grade falsifier: the 0 returned on a dead origin fed the
    c6 one-heir gate — silently OPENING the heir budget exactly when the
    lane cannot see its own archive."""
    o, w, stub = lane
    with pytest.raises(RuntimeError) as ei:
        inrepo.heirs_count(o + ".never-created", "T-origdeath")
    assert "origin unreachable" in str(ei.value)


def test_heirs_count_zero_on_reachable_empty_archive(lane):
    o, w, stub = lane
    assert inrepo.heirs_count(o, "T-origdeath") == 0


# ------------------------------- part B: the attempt survives (driver)


TASK = "T-origdeath"

EXIT1_STUB = """\
#!/bin/bash
printf '%s\\n' "$@" >> "$RECORD"
exit 1
"""

# the backend leg ran, then the origin died BEFORE the dispatch result
# was classified: dispatch exits 1 with no work in the tree -> the clean
# post-dispatch env-death requeue leg
DIE_MID_EXIT1_STUB = """\
#!/bin/bash
printf '%s\\n' "$@" >> "$RECORD"
mv "$ORIGIN_PATH" "$ORIGIN_PATH.killed"
exit 1
"""

# the backend leg really ran and left work, the oracle will pass, and
# the origin died before the return push: the main-push-rejected leg
DIE_MID_ATTEMPT_STUB = """\
#!/bin/bash
# the backend leg really ran, left work in the tree, and then the
# origin died before the return push
printf 'ran\\n' >> "$RECORD"
echo changed > "$PWD/work.txt"
mv "$ORIGIN_PATH" "$ORIGIN_PATH.killed"
exit 0
"""


def _stub(stub_dir, body):
    p = pathlib.Path(stub_dir) / "opencode"
    p.write_text(body)
    os.chmod(p, 0o755)
    return str(p)


def _seed_and_claim(o, w, att, brief="verify: true\n\nfix it\n"):
    _push(o, w, _root_commit(w, f"spec {TASK}\n\n{brief}"),
          f"refs/swarm/specs/{TASK}")
    _push(o, w, _root_commit(w, f"claim {TASK} {att}"),
          f"refs/swarm/claims/{TASK}")


def test_env_death_requeue_survives_mid_attempt_origin_death(lane):
    """Origin dies right after dispatch (backend leg ran, exited 1, no
    work in the tree): pre-fix the rc-blind heirs_count returned 0 (the
    c6 budget looks fresh), the bare sweep() raised, and the attempt
    crashed into work_task_error with zero c6 accounting. The contract:
    a STRUCTURED event (env_death, requeued False, sweep_error naming
    the origin), the claim left live for reconcile's TTL, and the
    record proving the backend leg genuinely ran before the death."""
    o, w, stub = lane
    rec = stub + ".record"
    att = "att-w2-dead01"
    _seed_and_claim(o, w, att)
    env = dict(
        SWARM_ORIGIN=o,
        OPENCODE_BIN=_stub(stub, DIE_MID_EXIT1_STUB),
        SWARM_MODEL="stub/model",
        RECORD=rec,
        ORIGIN_PATH=o,
    )
    try:
        ev = _run_driver(env, "work", "w2", TASK, att)
    finally:
        killed = o + ".killed"
        if os.path.exists(killed) and not os.path.exists(o):
            os.rename(killed, o)
    assert os.path.exists(rec)  # the backend leg genuinely ran
    assert ev["event"] == "attempted"
    assert ev["env_death"] is True
    assert ev["requeued"] is False and ev["swept"] is None
    assert "origin unreachable" in ev["sweep_error"]
    assert f"refs/swarm/claims/{TASK}" in _refs(o)  # claim stays live for reconcile


def test_env_death_fresh_budget_requeue_preserved_when_origin_stays_up(lane):
    """Regression guard: the c6 first-death requeue on the post-dispatch
    path is untouched when the origin is reachable."""
    o, w, stub = lane
    att = "att-w2-alive1"
    _seed_and_claim(o, w, att)
    ev = _run_driver(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=_stub(stub, EXIT1_STUB),
             SWARM_MODEL="stub/model", RECORD=stub + ".record2"),
        "work", "w2", TASK, att,
    )
    assert ev["env_death"] is True and ev["requeued"] is True
    assert ev["swept"] == att and "sweep_error" not in ev
    assert f"refs/swarm/archive/claims/{TASK}@{att}" in _refs(o)


def test_main_push_rejected_survives_mid_attempt_origin_death(lane):
    """The second unguarded leg: dispatch really ran and left work, the
    oracle passed, and the origin died before the return push — the
    main-push-rejected requeue then hit the same rc-blind gate + bare
    sweep. The contract: the honest main_push_rejected shape with
    requeued False + sweep_error (claim stays live; reconcile's TTL
    requeues when the origin heals), never a crash carrying no
    accounting."""
    o, w, stub = lane
    rec = stub + ".record"
    att = "att-w2-dead02"
    _seed_and_claim(o, w, att)
    env = dict(
        SWARM_ORIGIN=o,
        OPENCODE_BIN=_stub(stub, DIE_MID_ATTEMPT_STUB),
        SWARM_MODEL="stub/model",
        RECORD=rec,
        ORIGIN_PATH=o,
    )
    try:
        ev = _run_driver(env, "work", "w2", TASK, att)
    finally:
        killed = o + ".killed"
        if os.path.exists(killed) and not os.path.exists(o):
            os.rename(killed, o)
    assert os.path.exists(rec)  # the backend leg genuinely ran
    assert ev["event"] == "attempted"
    assert ev["reason"] == "main_push_rejected"
    assert ev["fixed"] is False and ev["verdict_pushed"] is False
    assert ev["requeued"] is False and ev["swept"] is None
    assert "origin unreachable" in ev["sweep_error"]
    assert f"refs/swarm/claims/{TASK}" in _refs(o)  # claim stays live for reconcile
