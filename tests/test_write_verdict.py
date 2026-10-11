"""Contracts for write_verdict: the terminal verdict write, extracted.

write_verdict writes the create-once terminal verdict for an attempt: a
ROOT commit (L1 — never on main lineage) carrying verdict_body(...),
pushed to refs/swarm/verdicts/<task> with the failed-commit proc preserved
(a failed commit-tree never becomes a push — the phantom-verdict class).
It returns the push proc. Pure extraction: the headless path is unchanged.
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


def _make_origin(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(seed))
    (seed / "f.txt").write_text("v1\n")
    _git("-C", str(seed), "add", "-A")
    _git("-C", str(seed), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    _git("-C", str(seed), "-c", "user.email=a@b", "-c", "user.name=a",
         "push", "-q", str(o), "main")
    return str(o)


def _verdict_ref(o, task):
    r = _git("ls-remote", o, f"refs/swarm/verdicts/{task}")
    return r.stdout.split()[0] if r.stdout.strip() else None


def test_write_verdict_is_a_create_once_root_commit(tmp_path):
    m = _load_inrepo()
    o = _make_origin(tmp_path)
    r = m.write_verdict(o, "T", "att1", True, "w", 0, 0, 5.0)
    assert m.ok(r)
    sha = _verdict_ref(o, "T")
    assert sha is not None
    # L1: a ROOT commit — no parent
    chk = tmp_path / "chk"
    _git("clone", "-q", "-b", "main", o, str(chk))
    parents = _git("-C", str(chk), "log", "-1", "--format=%P", sha).stdout.strip()
    assert parents == ""  # no parent
    # create-once: a second verdict for the same task is rejected
    r2 = m.write_verdict(o, "T", "att2", True, "w", 0, 0, None)
    assert not m.ok(r2)


def test_write_verdict_body_carries_the_fields(tmp_path):
    m = _load_inrepo()
    o = _make_origin(tmp_path)
    m.write_verdict(o, "T", "att7", False, "w", 124, 1, 3.5)
    sha = _verdict_ref(o, "T")
    chk = tmp_path / "chk"
    _git("clone", "-q", "-b", "main", o, str(chk))
    body = _git("-C", str(chk), "log", "-1", "--format=%B", sha).stdout
    assert "task: T" in body
    assert "attempt: att7" in body
    assert "fixed: false" in body
    assert "host: w" in body
    assert "oc_rc: 124" in body
    assert "pytest_rc: 1" in body
    assert "oc_elapsed: 3.5" in body


def test_write_verdict_extra_appends(tmp_path):
    m = _load_inrepo()
    o = _make_origin(tmp_path)
    m.write_verdict(o, "T", "att9", False, "w", 0, 1, None,
                    extra="\nkept: /tmp/keep/path")
    sha = _verdict_ref(o, "T")
    chk = tmp_path / "chk"
    _git("clone", "-q", "-b", "main", o, str(chk))
    body = _git("-C", str(chk), "log", "-1", "--format=%B", sha).stdout
    assert "kept: /tmp/keep/path" in body
