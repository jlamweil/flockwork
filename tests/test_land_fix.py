"""Contracts for land_fix: the land leg, extracted from _work_task.

land_fix(origin, tree, task, att, paths) lands a VERIFIED fix in a tree:
commit the changeset (the scoped `paths`, or the whole tree when paths is
None — the headless fresh-clone shape), push main (with one bounded rebase
retry on a collision), and push the return ref (refs/swarm/tasks/<task> —
H1 requires every claimed task to have a return beside its verdict). It
returns the structured land outcome and does NOT decide requeue/verdict;
that is the caller's classification (the push-failure requeue lives in the
worker loop).

The interactive-tree contracts (CORR-2) this pins:
  - an unrelated dirty file in the human's worktree is NEVER published;
  - oracle side-effect artifacts (.pytest_cache) are never published when
    the changeset is scoped;
  - a tree behind origin main that cannot rebase lands NOTHING (honest
    refusal, no partial publish);
  - the no-diff (already-settled) shortcut points the return ref at main's
    tip without creating an empty commit.
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


def _make_origin(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(seed))
    (seed / "f.txt").write_text("v1\n")
    _git("-C", str(seed), "add", "-A")
    _git("-C", str(seed), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(seed), *_cid(), "push", "-q", str(o), "main")
    return str(o)


def _clone(o, tmp_path, name="work"):
    w = tmp_path / name
    _git("clone", "-q", "-b", "main", o, str(w))
    _git("-C", str(w), "config", "user.email", "w@swarm")
    _git("-C", str(w), "config", "user.name", "w")
    return w


def _fresh_main(o, tmp_path, name):
    c = tmp_path / name
    _git("clone", "-q", "-b", "main", o, str(c))
    return c


def _refs(o):
    return _git("ls-remote", o).stdout


def test_land_fix_publishes_a_fix(tmp_path):
    """paths=None → whole-tree (headless) shape: the fix commits to main and
    the return ref is pushed beside it."""
    m = _load_inrepo()
    o = _make_origin(tmp_path)
    w = _clone(o, tmp_path)
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    r = m.land_fix(o, str(w), "T", "att1")
    assert r["main_pushed"] is True
    assert r["already_on_main"] is False
    assert r["return_pushed"] is True
    chk = _fresh_main(o, tmp_path, "chk")
    assert (chk / "fix.py").exists()
    assert f"refs/swarm/tasks/T" in _refs(o)


def test_land_fix_no_diff_already_on_main(tmp_path):
    """No uncommitted change + oracle pass (the settled task): no empty commit
    is created on main; the return ref is pointed at main's tip."""
    m = _load_inrepo()
    o = _make_origin(tmp_path)
    b0 = _fresh_main(o, tmp_path, "b0")
    main_before = _git("-C", str(b0), "rev-parse", "main").stdout.strip()
    w = _clone(o, tmp_path)
    r = m.land_fix(o, str(w), "T", "att2")
    assert r["already_on_main"] is True
    assert r["main_pushed"] is False
    assert r["return_pushed"] is True
    assert f"refs/swarm/tasks/T" in _refs(o)
    b1 = _fresh_main(o, tmp_path, "b1")
    main_after = _git("-C", str(b1), "rev-parse", "main").stdout.strip()
    assert main_before == main_after  # main did not advance


def test_land_fix_scopes_the_changeset(tmp_path):
    """CORR-2: with paths=[...], an unrelated dirty file in the human's
    worktree is NEVER published under the task's fix commit — it stays
    uncommitted in the tree."""
    m = _load_inrepo()
    o = _make_origin(tmp_path)
    w = _clone(o, tmp_path)
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    (w / "unrelated.py").write_text("x = 0\n")
    r = m.land_fix(o, str(w), "T", "att3", paths=["fix.py"])
    assert r["main_pushed"] is True
    assert r["return_pushed"] is True
    chk = _fresh_main(o, tmp_path, "chk")
    assert (chk / "fix.py").exists()
    assert not (chk / "unrelated.py").exists()
    st = _git("-C", str(w), "status", "--porcelain").stdout
    assert "unrelated.py" in st  # still uncommitted locally


def test_land_fix_excludes_oracle_artifacts(tmp_path):
    """CORR-2: oracle side-effect artifacts (.pytest_cache) are not
    published when the changeset is scoped to the task's path."""
    m = _load_inrepo()
    o = _make_origin(tmp_path)
    w = _clone(o, tmp_path)
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    cache = w / ".pytest_cache"
    cache.mkdir()
    (cache / "CACHEDIR.TAG").write_text("create")
    r = m.land_fix(o, str(w), "T", "att4", paths=["fix.py"])
    chk = _fresh_main(o, tmp_path, "chk")
    assert (chk / "fix.py").exists()
    assert not (chk / ".pytest_cache").exists()


def test_land_fix_behind_main_honest_refusal(tmp_path):
    """CORR-2: a tree behind origin main that cannot rebase (a conflict)
    publishes NOTHING — main keeps its tip, no return ref, no partial
    publish."""
    m = _load_inrepo()
    o = _make_origin(tmp_path)
    w = _clone(o, tmp_path)
    (w / "f.txt").write_text("v2\n")
    adv = tmp_path / "adv"
    _git("clone", "-q", "-b", "main", o, str(adv))
    (adv / "f.txt").write_text("v3\n")
    _git("-C", str(adv), "add", "-A")
    _git("-C", str(adv), *_cid(), "commit", "-qm", "advance")
    _git("-C", str(adv), *_cid(), "push", "-q", o, "main")
    r = m.land_fix(o, str(w), "T", "att5")
    assert r["main_pushed"] is False
    assert r["return_pushed"] is False
    assert f"refs/swarm/tasks/T" not in _refs(o)
    chk = _fresh_main(o, tmp_path, "chk")
    assert (chk / "f.txt").read_text() == "v3\n"  # fix not published
