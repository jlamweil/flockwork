"""Contract for FLOCKWORK_KEEP_TREE (WQ-031, INT-085): preserve the
attempt tree of a merit-failed dispatch so a failure is auditable.

The pilots measured the gap (WQ-025): a merit failure — dispatch ran,
oc_rc 0, verify oracle failed — used to destroy the attempt tree in
work_task's finally-block rmtree, leaving zero artifact of what the
model wrote. The switch is shaped exactly like the metrics layer
(WQ-024's contract):

  - env-switched: FLOCKWORK_KEEP_TREE=1 enables; OFF (the default) is
    byte-identical wire behavior — the tree is removed exactly as
    today, no kept-attempts dir is created, no extra event keys, no
    extra verdict line.
  - merit-fail path ONLY: the merit_failure_no_publish leg (dispatch
    completed, oracle failed) MOVES the tree (not deletes) to the keep
    dir — FLOCKWORK_KEEP_DIR or <cwd>/kept-attempts/<task>@<att> — and
    the attempted event and the verdict body carry the kept path.
    A successful attempt removes the tree as today; an environmental
    death (the dispatch never worked) keeps nothing.
  - observe-only: a broken keep target never breaks the lane — the
    attempt records kept_error and the honest verdict still lands.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_inrepo():
    p = REPO / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_keep_tree_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _scratch_node(tmp_path):
    """A repo cwd for the object store (the test_claim_loss house
    lesson: push from a non-repo cwd is rc-128 structural)."""
    w = tmp_path / "node"
    _git("init", "-q", "-b", "main", str(w))
    (w / ".seed").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "s")
    return w


def _verdict_body(origin, task):
    """The verdict root-commit's message, read from the bare origin
    (the object was created by the child worker in a temp tree that no
    longer exists — the origin is the only copy)."""
    raw = _git("ls-remote", str(origin), f"refs/swarm/verdicts/{task}")
    sha = raw.stdout.split()[0]
    return _git("cat-file", "-p", sha, cwd=str(origin)).stdout


def _run_worker(tmp_path, monkeypatch, shim_body, keep=None, keep_dir=None):
    """One worker() pass over a one-task scratch board with the
    deterministic dispatch shim. Returns (rc, events, node, origin)."""
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    w = _scratch_node(tmp_path)
    shim = tmp_path / "dispatch.sh"
    shim.write_text(f"#!/bin/sh\n{shim_body}\n")
    shim.chmod(0o755)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.setenv("OPENCODE_BIN", str(shim))
    if keep is None:
        monkeypatch.delenv("FLOCKWORK_KEEP_TREE", raising=False)
    else:
        monkeypatch.setenv("FLOCKWORK_KEEP_TREE", keep)
    if keep_dir is None:
        monkeypatch.delenv("FLOCKWORK_KEEP_DIR", raising=False)
    else:
        monkeypatch.setenv("FLOCKWORK_KEEP_DIR", str(keep_dir))
    monkeypatch.chdir(w)
    inrepo = _load_inrepo()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(
        {"hello-demo": "demo task\n\nadd hello.txt saying hi\n\n"
                       "verify: grep -qx hi hello.txt"}))
    assert inrepo.seed(str(spec))["hello-demo"] is True
    r = subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"), "worker", "wm"],
        cwd=str(w),
        env=dict(os.environ),  # monkeypatched env travels to the child
        capture_output=True, text=True, timeout=120)
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    return r.returncode, events, w, o


def _attempted(events):
    return [e for e in events if e.get("event") == "attempted"]


MERIT_FAIL_SHIM = "echo wrong > hello.txt; exit 0"  # dispatch ran, oracle fails


# ------------------------------------------------------------ the switch


def test_off_by_default_merit_fail_removes_tree_exactly_as_today(
        tmp_path, monkeypatch):
    rc, events, w, o = _run_worker(tmp_path, monkeypatch, MERIT_FAIL_SHIM)
    assert rc == 0
    att = _attempted(events)[0]
    assert att["reason"] == "merit_failure_no_publish"
    assert att["fixed"] is False
    assert "kept" not in att and "kept_error" not in att
    assert not (w / "kept-attempts").exists()
    # the verdict body keeps its exact today shape — no kept line
    body = _verdict_body(o, "hello-demo")
    assert "kept:" not in body
    assert "fixed: false" in body and "oc_rc: 0" in body


def test_on_success_removes_tree_as_today(tmp_path, monkeypatch):
    rc, events, w, o = _run_worker(
        tmp_path, monkeypatch, "echo hi > hello.txt; exit 0", keep="1")
    assert rc == 0
    att = _attempted(events)[0]
    assert att["fixed"] is True
    assert "kept" not in att and "kept_error" not in att
    assert not (w / "kept-attempts").exists()
    body = _verdict_body(o, "hello-demo")
    assert "kept:" not in body and "fixed: true" in body


def test_on_env_death_never_keeps(tmp_path, monkeypatch):
    """A dispatch that dies leaving no work is an environmental death:
    nothing was made, so there is no artifact to keep — the requeue and
    heir-exhausted legs both discard the tree even with the switch on."""
    rc, events, w, o = _run_worker(tmp_path, monkeypatch, "exit 1", keep="1")
    assert rc == 0
    atts = _attempted(events)
    assert len(atts) == 2  # first death requeues, second is final
    assert atts[0]["env_death"] is True and atts[0]["requeued"] is True
    assert atts[1]["heir_exhausted"] is True
    for a in atts:
        assert "kept" not in a and "kept_error" not in a
    assert not (w / "kept-attempts").exists()


# --------------------------------------------------- the merit-fail keep


def test_on_merit_fail_keeps_tree_and_records_path(tmp_path, monkeypatch):
    rc, events, w, o = _run_worker(tmp_path, monkeypatch, MERIT_FAIL_SHIM,
                                   keep="1")
    assert rc == 0
    att = _attempted(events)[0]
    assert att["reason"] == "merit_failure_no_publish"
    kept = pathlib.Path(att["kept"])
    # the default keep dir: <cwd>/kept-attempts/<task>@<att>
    assert kept == w / "kept-attempts" / f"hello-demo@{att['att']}"
    # the tree MOVED intact: what the model wrote is auditable
    assert (kept / "hello.txt").read_text().strip() == "wrong"
    assert (kept / ".git").exists()
    # the verdict carries the kept path — the refs alone point at it
    body = _verdict_body(o, "hello-demo")
    assert f"kept: {att['kept']}" in body
    assert "fixed: false" in body


def test_keep_dir_env_override(tmp_path, monkeypatch):
    kd = tmp_path / "kept-elsewhere"
    rc, events, w, o = _run_worker(tmp_path, monkeypatch, MERIT_FAIL_SHIM,
                                   keep="1", keep_dir=kd)
    assert rc == 0
    att = _attempted(events)[0]
    kept = pathlib.Path(att["kept"])
    assert kept == kd / f"hello-demo@{att['att']}"
    assert (kept / "hello.txt").read_text().strip() == "wrong"
    assert not (w / "kept-attempts").exists()


# ------------------------------------------------------- observe-only law


def test_broken_keep_target_never_breaks_the_lane(tmp_path, monkeypatch):
    """An unwritable keep dir must never break the attempt: the tree
    falls back to today's rmtree, the failure is recorded honestly, and
    the verdict lands WITHOUT a kept line (nothing was kept)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a dir\n")
    rc, events, w, o = _run_worker(tmp_path, monkeypatch, MERIT_FAIL_SHIM,
                                   keep="1", keep_dir=blocker / "kd")
    assert rc == 0
    att = _attempted(events)[0]
    assert att["reason"] == "merit_failure_no_publish"
    assert "kept" not in att and att.get("kept_error")
    body = _verdict_body(o, "hello-demo")
    assert "kept:" not in body and "fixed: false" in body
    assert not (blocker / "kd").exists()


# ------------------------------------------------------- the path helper


def test_keep_path_sanitizes_and_never_overwrites(tmp_path, monkeypatch):
    """Task names are remote data (any legal refname: '/' legal, '@'
    barred by the archive-marker law) and att carries the worker name —
    the keep leaf is sanitized to the refname-safe alphabet, and a
    colliding keep NEVER overwrites the first artifact."""
    monkeypatch.setenv("FLOCKWORK_KEEP_DIR", str(tmp_path / "kd"))
    m = _load_inrepo()
    tree = tmp_path / "t1"
    tree.mkdir()
    (tree / "x.txt").write_text("1\n")
    kept, err = m.keep_attempt_tree("a/b", "att-wm@1", str(tree))
    assert err is None and kept
    p = pathlib.Path(kept)
    assert p.parent == tmp_path / "kd"
    assert p.name == "a_b@att-wm_1"
    assert (p / "x.txt").exists() and not tree.exists()
    # collision: same task@att again — suffixed, never overwritten
    tree2 = tmp_path / "t2"
    tree2.mkdir()
    (tree2 / "x.txt").write_text("2\n")
    kept2, err2 = m.keep_attempt_tree("a/b", "att-wm@1", str(tree2))
    assert err2 is None and kept2 != kept
    assert pathlib.Path(kept2, "x.txt").read_text() == "2\n"
    assert pathlib.Path(kept, "x.txt").read_text() == "1\n"
