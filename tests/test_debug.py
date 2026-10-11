"""Contracts for run_timeline / `flockwork debug <task>` (C7).

Reconstruct the full run timeline for a task from the refs alone (the durable
board truth — no metrics, no local state): spec, claim (+ its checkpoints),
return ref, verdict, archive. A completed run shows the full chain in order;
a crashed/stalled run is legible (work_started present, no verdict) instead
of silently vanishing. This is the after-the-fact legibility the plan
maximizes: a human can read `flockwork debug <task>` to see what happened.
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


def _work_lane(tmp_path, att="att-db0001"):
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
          _root_commit(str(seed), "do the thing\nverify: true"),
          "refs/swarm/specs/T")
    _push(str(o), str(seed),
          _root_commit(str(seed), f"claim T\nharness: opencode\n{att}"),
          "refs/swarm/claims/T")
    w = tmp_path / "work"
    _git("clone", "-q", "-b", "main", str(o), str(w))
    _git("-C", str(w), "config", "user.email", "w1@swarm")
    _git("-C", str(w), "config", "user.name", "w1")
    return str(o), w, att


def test_run_timeline_completed_run(tmp_path):
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path)
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    m.work_external(o, "T", att, str(w), "w1", paths=["fix.py"])
    tl = m.run_timeline("T", o)
    assert tl["spec"] is not None and "verify: true" in tl["spec"]
    assert tl["claim"] is not None
    assert tl["claim"]["body"].split()[-1] == att
    assert "work_started" in tl["checkpoints"]
    assert "work_done" in tl["checkpoints"]
    assert tl["return_ref"] is not None
    assert tl["verdict"] is not None
    assert tl["verdict"]["fields"]["fixed"] == "true"
    assert tl["archived"] is False


def test_run_timeline_crashed_run_is_legible(tmp_path):
    """The work started but the session died before the verdict: the timeline
    shows work_started present + no verdict + nothing landed — legible as a
    stalled run, not a silent vanishing."""
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path)
    m.mark_work_started(o, "T", att)
    tl = m.run_timeline("T", o)
    assert tl["claim"] is not None
    assert "work_started" in tl["checkpoints"]
    assert "work_done" not in tl["checkpoints"]
    assert tl["verdict"] is None
    assert tl["return_ref"] is None
    assert tl["archived"] is False


def test_run_timeline_unknown_task(tmp_path):
    m = _load_inrepo()
    o, w, att = _work_lane(tmp_path)
    tl = m.run_timeline("NOPE", o)
    assert tl["spec"] is None
    assert tl["claim"] is None
    assert tl["verdict"] is None
    assert tl["archived"] is False
