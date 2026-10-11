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

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_inrepo():
    spec = importlib.util.spec_from_file_location(
        "inrepo", REPO / "l2" / "inrepo.py"
    )
    m = importlib.util.module_from_spec(spec)
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
