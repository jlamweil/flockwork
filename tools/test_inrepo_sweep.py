"""Operator-authored contract for T4-sweep-command (the lane's TDD task).

The worker's job: implement inrepo.sweep(origin, task, att=None) so all
three tests pass. The tests run against a LOCAL bare origin — no fleet,
no network. Contract:
  - sweep archives every live refs/swarm/{claims,tasks,verdicts}/<task>
    to refs/swarm/archive/<kind>/<task>@<att> and deletes the live refs,
    in ONE git transaction (push --atomic).
  - att comes from the claim commit's last body token unless given.
  - sweeping an already-swept task returns empty lists and must not
    create junk refs (idempotent).
"""
import importlib.util
import pathlib
import subprocess

import pytest


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None, inp=None):
    return subprocess.run(["git", *a], cwd=cwd, input=inp, text=True,
                          capture_output=True)


@pytest.fixture
def origin(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    _git("-C", str(w), "push", "-q", str(o), "main")
    et = _git("-C", str(w), "hash-object", "-t", "tree",
              "/dev/null").stdout.strip()
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    c = _git("-C", str(w), *cid, "commit-tree", et,
             "-m", "claim T-sweep att-w1-zz").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o), f"{c}:refs/swarm/claims/T-sweep")
    # a verdict too (root commit) so sweep must archive BOTH kinds
    v = _git("-C", str(w), *cid, "commit-tree", et,
             "-m", "verdict\ntask: T-sweep\nfixed: false").stdout.strip()
    _git("-C", str(w), "push", "-q", str(o), f"{v}:refs/swarm/verdicts/T-sweep")
    return str(o), str(w), c, v


def test_sweep_archives_and_frees_claim_and_verdict(origin):
    o, w, csha, vsha = origin
    res = inrepo.sweep(o, "T-sweep")
    assert res["att"] == "att-w1-zz"
    refs = _git("ls-remote", o).stdout
    assert "refs/swarm/claims/T-sweep" not in refs
    assert "refs/swarm/verdicts/T-sweep" not in refs
    assert "refs/swarm/archive/claims/T-sweep@att-w1-zz" in refs
    assert "refs/swarm/archive/verdicts/T-sweep@att-w1-zz" in refs
    assert csha in refs and vsha in refs          # objects preserved


def test_sweep_explicit_att_overrides_claim_body(origin):
    o, w, csha, vsha = origin
    res = inrepo.sweep(o, "T-sweep", att="att-given-01")
    assert res["att"] == "att-given-01"
    refs = _git("ls-remote", o).stdout
    assert "refs/swarm/archive/claims/T-sweep@att-given-01" in refs


def test_sweep_idempotent_when_nothing_live(origin):
    o, w, csha, vsha = origin
    inrepo.sweep(o, "T-sweep")
    res2 = inrepo.sweep(o, "T-sweep")
    assert res2["archived"] == [] and res2["deleted"] == []
    n = sum(1 for ln in _git("ls-remote", o).stdout.splitlines()
            if "@att-given-01" in ln or "att-w1-zz" in ln)
    assert n == 2   # exactly the first sweep's archive refs, no junk
