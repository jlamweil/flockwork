"""Contracts for work_external: the external-work mode (C4a happy path).

work_external is the interactive twin of the headless _work_task: the caller
(an agent) already did the work in ITS OWN tree (not a headless dispatch
clone). It reuses the C1-C3 legs — mark_work_started -> judge_tree ->
land_fix (scoped changeset + return ref) -> write_verdict(fixed:true) ->
mark_work_done — and returns the structured outcome. C4a pins the happy
path (oracle pass + fix lands on main + H1 return ref + fixed:true verdict);
C4b pins the failure matrix (the four honesty laws on the new path).
"""
import importlib.util
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_inrepo():
    spec = importlib.util.spec_from_file_location(
        "inrepo", REPO / "l2" / "inrepo.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_landgate():
    spec = importlib.util.spec_from_file_location(
        "landgate", REPO / "l2" / "landgate.py"
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules["landgate"] = m
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return ["-c", "user.email=a@b", "-c", "user.name=a"]


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    return _git("-C", w, *_cid(), "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    _git("-C", w, *_cid(), "push", "-q", o, f"{src}:{ref}")


def _work_lane(tmp_path, att="att-w1-ext0001", verify="true"):
    """origin (main + a spec with verify: + a live claim) + a working clone."""
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(seed))
    (seed / "app.py").write_text("X = 0\n")
    _git("-C", str(seed), "add", "-A")
    _git("-C", str(seed), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(seed), *_cid(), "push", "-q", str(o), "main")
    _push(str(o), str(seed),
          _root_commit(str(seed), f"do the thing so it works\nverify: {verify}"),
          f"refs/swarm/specs/T")
    _push(str(o), str(seed), _root_commit(str(seed), f"claim T\nharness: opencode\n{att}"),
          f"refs/swarm/claims/T")
    w = tmp_path / "work"
    _git("clone", "-q", "-b", "main", str(o), str(w))
    _git("-C", str(w), "config", "user.email", "w1@swarm")
    _git("-C", str(w), "config", "user.name", "w1")
    return str(o), w, att


def _refs(o):
    return _git("ls-remote", o).stdout


def test_work_external_happy_path_lands_the_fix(tmp_path):
    """Oracle pass + a scoped fix in the caller's tree → the fix lands on
    main, the H1 return ref is pushed, and the fixed:true verdict is written.
    The unrelated dirty file is NOT published (scoped changeset)."""
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path)
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    (w / "unrelated.py").write_text("x = 0\n")  # the human's in-progress work
    out = m.work_external(o, "T", att, str(w), "w1", paths=["fix.py"])
    assert out["fixed"] is True
    assert out["main_push"] is True
    assert out["return_pushed"] is True
    assert out["verdict_pushed"] is True
    refs = _refs(o)
    assert f"refs/swarm/tasks/T" in refs      # H1 return ref
    assert f"refs/swarm/verdicts/T" in refs
    chk = tmp_path / "chk"
    _git("clone", "-q", "-b", "main", o, str(chk))
    assert (chk / "fix.py").exists()
    assert not (chk / "unrelated.py").exists()  # not published
    # H1 essence: the return ref + verdict ref both carry the claim's att
    att_v = _git("-C", str(o), "show", "-s", "--format=%B",
                 f"refs/swarm/verdicts/T").stdout
    assert f"attempt: {att}" in att_v


def test_work_external_reads_verify_from_the_spec(tmp_path):
    """No explicit verify: the oracle command is read from the spec ref on the
    origin (the task's acceptance criterion), not invented."""
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path, verify="test -f fix.py")
    (w / "fix.py").write_text("1\n")
    out = m.work_external(o, "T", att, str(w), "w1", paths=["fix.py"])
    assert out["fixed"] is True
    assert out["pytest_rc"] == 0


# -------------------------------------------------- C4b: the failure matrix
# The four honesty laws re-proven on the external-work path.


def test_work_external_merit_failure_no_publish(tmp_path):
    """A change that FAILS its oracle is never a fix and never lands: main +
    the tasks ref stay untouched, only the honest fixed:false verdict is
    written, and the claim stays live for the TTL re-lease."""
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path, verify="false")
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    out = m.work_external(o, "T", att, str(w), "w1", paths=["fix.py"])
    assert out["fixed"] is False
    assert out["reason"] == "merit_failure_no_publish"
    assert out["main_push"] is False
    assert out["return_pushed"] is False
    assert out["verdict_pushed"] is True
    refs = _refs(o)
    assert f"refs/swarm/verdicts/T" in refs
    assert f"refs/swarm/tasks/T" not in refs      # no return ref
    assert f"refs/swarm/claims/T" in refs        # claim stays live
    chk = tmp_path / "chk"
    _git("clone", "-q", "-b", "main", o, str(chk))
    assert not (chk / "fix.py").exists()          # the fix did not publish


def test_work_external_oracle_timeout_is_merit_final(tmp_path, monkeypatch):
    """A verify that hangs past the bound degrades to an honest measured rc
    124 (oracle_timeout) — a merit final, not a crash; fixed:false, no
    publish."""
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path, verify="sleep 30")
    monkeypatch.setenv("SWARM_ORACLE_TIMEOUT", "2")
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    out = m.work_external(o, "T", att, str(w), "w1", paths=["fix.py"])
    assert out["fixed"] is False
    assert out["pytest_rc"] == 124
    assert out["oracle_timeout"] is True
    assert out["reason"] == "merit_failure_no_publish"
    assert out["verdict_pushed"] is True
    assert f"refs/swarm/tasks/T" not in _refs(o)


def test_work_external_rejected_push_writes_no_verdict(tmp_path):
    """A fix that cannot land (a collision the one rebase retry did not
    resolve) writes NO verdict — the claim stays live for a re-land / a fresh
    session, bounded by the substrate TTL+heir; never a fixed:true the
    substrate contradicts."""
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path, verify="true")
    (w / "app.py").write_text("Y = 1\n")  # a change to a tracked file
    adv = tmp_path / "adv"
    _git("clone", "-q", "-b", "main", o, str(adv))
    (adv / "app.py").write_text("Z = 2\n")
    _git("-C", str(adv), "add", "-A")
    _git("-C", str(adv), *_cid(), "commit", "-qm", "advance")
    _git("-C", str(adv), *_cid(), "push", "-q", o, "main")
    out = m.work_external(o, "T", att, str(w), "w1", paths=["app.py"])
    assert out["fixed"] is False
    assert out["reason"] == "main_push_rejected"
    assert out["main_push"] is False
    assert out["verdict_pushed"] is False
    refs = _refs(o)
    assert f"refs/swarm/verdicts/T" not in refs  # NO verdict
    assert f"refs/swarm/claims/T" in refs        # claim stays live


def test_work_external_no_diff_is_settled(tmp_path):
    """The oracle may PASS on an unchanged tree (the settled task): no empty
    commit is created on main; the return ref is pointed at main's tip; an
    honest fixed:true is recorded."""
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path, verify="true")
    b0 = tmp_path / "b0"
    _git("clone", "-q", "-b", "main", o, str(b0))
    main_before = _git("-C", str(b0), "rev-parse", "main").stdout.strip()
    out = m.work_external(o, "T", att, str(w), "w1", paths=["app.py"])
    assert out["fixed"] is True
    assert out["already_on_main"] is True
    assert out["main_push"] is False
    assert out["return_pushed"] is True
    assert f"refs/swarm/tasks/T" in _refs(o)
    b1 = tmp_path / "b1"
    _git("clone", "-q", "-b", "main", o, str(b1))
    main_after = _git("-C", str(b1), "rev-parse", "main").stdout.strip()
    assert main_before == main_after  # main did not advance


def test_work_external_gate_on_valid_token_lands(tmp_path, monkeypatch):
    """work_external threads the presented token through BOTH legs: with a
    valid token + a passing oracle, the fix lands on main and the create-once
    verdict is written."""
    g = _load_landgate()
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path)
    (w / "fix.py").write_text("x = 1\n")
    tok = g.sign_token("owner", "agent-1", "extsecret")
    monkeypatch.setenv("FLOCKWORK_LAND_GATE", "1")
    monkeypatch.setenv("FLOCKWORK_LAND_SECRET", "extsecret")
    out = m.work_external(o, "T", att, str(w), "w1", paths=["fix.py"],
                          gate_token=tok)
    assert out["fixed"] is True
    assert out["verdict_pushed"] is True
    assert f"refs/swarm/verdicts/T" in _refs(o)


def test_work_external_gate_on_forged_token_writes_no_verdict(tmp_path, monkeypatch):
    """With a forged presented token the land leg is refused, so the full
    external-work leg lands NOTHING and writes NO verdict (publish + settle
    are both gated; the claim stays live for a re-attempt)."""
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path)
    (w / "fix.py").write_text("x = 1\n")
    monkeypatch.setenv("FLOCKWORK_LAND_GATE", "1")
    monkeypatch.setenv("FLOCKWORK_LAND_SECRET", "extsecret")
    out = m.work_external(o, "T", att, str(w), "w1", paths=["fix.py"],
                          gate_token="owner:a:999999999999:deadbeef")
    assert out["fixed"] is False
    assert out["reason"] == "land_gate_denied"
    assert out["verdict_pushed"] is False
    assert f"refs/swarm/verdicts/T" not in _refs(o)
    assert f"refs/swarm/claims/T" in _refs(o)
