"""Contracts for judge_tree: the oracle leg, extracted from _work_task.

judge_tree runs the task's `verify:` command (or host pytest at the task
dir) in a tree, bounded by a timeout via coreutils `timeout`, and returns
the RAW measured result — rc, timed_out (rc==124: the leg's own bound, not
a measured exit), and a detail tail. It does NOT apply the env-death gate;
that classification is the caller's job. This is a pure extraction: the
headless _work_task path must behave byte-for-byte as before.
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


def _tree(tmp_path):
    t = tmp_path / "tree"
    _git("init", "-q", "-b", "main", str(t))
    (t / "f.txt").write_text("x\n")
    _git("-C", str(t), "add", "-A")
    _git("-C", str(t), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    return str(t)


def test_judge_tree_verify_passes(tmp_path):
    m = _load_inrepo()
    res = m.judge_tree(_tree(tmp_path), "T", "true")
    assert res["rc"] == 0
    assert res["timed_out"] is False


def test_judge_tree_verify_fails(tmp_path):
    m = _load_inrepo()
    res = m.judge_tree(_tree(tmp_path), "T", "false")
    assert res["rc"] == 1
    assert res["timed_out"] is False


def test_judge_tree_hung_verify_is_honest_124(tmp_path):
    """A verify that hangs past the bound degrades to an honest measured
    rc 124 (the bound, not a crash), with timed_out True."""
    m = _load_inrepo()
    res = m.judge_tree(_tree(tmp_path), "T", "sleep 30", timeout=2)
    assert res["rc"] == 124
    assert res["timed_out"] is True


def test_judge_tree_pytest_fallback_when_no_verify(tmp_path):
    """No verify line → host pytest at the task dir (falls back to the
    tree root). The leg runs (not a timeout) and reports a measured rc."""
    m = _load_inrepo()
    t = _tree(tmp_path)
    res = m.judge_tree(t, "T-none", None)
    assert res["timed_out"] is False
    assert res["rc"] != 124
