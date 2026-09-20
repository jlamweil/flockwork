"""Contract: divergence — a node's local main vs the substrate's main
(09-19 loop addendum finding 2; preregistered in LOOP-2026-09-20.md
addendum 2). Read-only, honest counts: ahead/behind exact when the
objects are local; `None` + reason when they are not. Never a fetch.
"""
import importlib.util
import pathlib
import subprocess

import pytest


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_div", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None, inp=None):
    return subprocess.run(["git", *a], cwd=cwd, input=inp, text=True,
                          capture_output=True)


@pytest.fixture
def pair(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "clone"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    _git("-C", str(w), "push", "-q", str(o), "main")
    return str(o), str(w)


def test_divergence_zero_when_in_sync(pair):
    o, w = pair
    d = inrepo.divergence(o, cwd=w)
    assert d["ahead"] == 0 and d["behind"] == 0
    assert d["local_main"] == d["origin_main"]


def test_divergence_counts_local_commits_ahead(pair):
    o, w = pair
    for i in range(2):
        (pathlib.Path(w) / f"g{i}.txt").write_text(f"{i}\n")
        _git("-C", str(w), "add", "-A")
        _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
             "commit", "-qm", f"local {i}")
    d = inrepo.divergence(o, cwd=w)
    assert d["ahead"] == 2 and d["behind"] == 0
    truth = _git("-C", str(w), "rev-list", "--count", "origin/main..main")
    # guard the ground truth ourselves — the command must not need a fetch
    assert truth.returncode != 0 or truth.stdout.strip() == "0"  # no tracking ref yet


def test_divergence_honest_unknown_when_objects_missing(tmp_path, pair):
    o, w = pair
    # commit on the bare origin from a THIRD repo, so the clone's object
    # store genuinely lacks it (creating it in the clone would pollute)
    s = tmp_path / "secret"
    _git("init", "-q", "-b", "main", str(s))
    et = _git("-C", str(s), "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    c = _git("-C", str(s), "-c", "user.email=a@b", "-c", "user.name=a",
             "commit-tree", et, "-m", "remote-only").stdout.strip()
    _git("-C", str(s), "push", "-q", "--force", str(o), f"{c}:main")
    d = inrepo.divergence(o, cwd=w)
    assert d["origin_main"] == c
    assert d["ahead"] is None and d["behind"] is None
    assert d["reason"] and "fetch" in d["reason"]


def test_divergence_no_local_main(tmp_path, pair):
    o, w = pair
    empty = tmp_path / "empty"
    empty.mkdir()
    d = inrepo.divergence(o, cwd=str(empty))
    assert d["ahead"] is None and d["reason"]
