"""Contract: origin-host reads must accept every documented SWARM_ORIGIN
form. The 09-19 loop addendum's own claim command ships scp-form
(`SWARM_ORIGIN=you@example-host-a:solve-metrics-origin.git`); pre-fix,
origin_body/origin_commit_ts (and everything behind them: audit body
reads, sweep att-recovery, reconcile dating) crashed on it with
FileNotFoundError, because origin_raw's scheme dispatch falls through to
the local-path branch. Preregistered in LOOP-2026-09-20.md (H2/H3).

No fleet, no network: a PATH-stub `ssh` maps the "remote" onto a tmp
directory (scp-form path = relative to the fake remote root, exactly the
remote-HOME convention), so the whole lane — ls-remote, fetch, push,
cat-file — runs against a local bare origin addressed AS a remote.
"""
import importlib.util
import os
import pathlib
import subprocess

import pytest


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_scp", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()

SSH_STUB = """\
#!/bin/bash
# fake ssh: [-o k=v]* [-p port] host <remote command...>
while [ "$1" = "-o" ]; do shift 2; done
if [ "$1" = "-p" ]; then shift 2; fi
shift  # host
# git over ssh sends the remote command as ONE argument
# (`git-upload-pack 'origin.git'`) — de-quote, then word-split it apart
if [ $# -eq 1 ]; then set -- $(printf %s "$1" | tr -d "'"); fi
case "$1" in
  git-upload-pack) shift; exec git -C "$FAKE_REMOTE_ROOT" upload-pack "$@" ;;
  git-receive-pack) shift; exec git -C "$FAKE_REMOTE_ROOT" receive-pack "$@" ;;
esac
if [ "$1" = "git" ] && [ "$2" = "-C" ]; then
  shift 2
  path="$1"; shift
  # scp-form paths are relative to the remote HOME (the fake remote root);
  # ssh://-form paths arrive absolute, so strip nothing — the root IS the
  # remote /, and seeds live directly under it
  exec git -C "$FAKE_REMOTE_ROOT/${path#/}" "$@"
fi
exec git -C "$FAKE_REMOTE_ROOT" "$@"
"""


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, text=True, capture_output=True)


@pytest.fixture
def lane(tmp_path, monkeypatch):
    root = tmp_path / "remote"
    o = root / "origin.git"
    o.mkdir(parents=True)
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stub = bin_ / "ssh"
    stub.write_text(SSH_STUB)
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_REMOTE_ROOT", str(root))
    return str(o), root


def _seed_claim(origin, task, att, with_return=True, with_verdict=True):
    w = pathlib.Path(origin).parent / "seed-w"
    if not w.exists():
        _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    et = _git("-C", str(w), "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    c = _git("-C", str(w), *cid, "commit-tree", et,
             "-m", f"claim {task} {att}").stdout.strip()
    _git("-C", str(w), "push", "-q", str(origin), f"{c}:refs/swarm/claims/{task}")
    if with_return or with_verdict:
        msha = _git("-C", str(w), "rev-parse", "main").stdout.strip()
        _git("-C", str(w), "push", "-q", str(origin), f"{msha}:refs/swarm/tasks/{task}")
    if with_verdict:
        v = _git("-C", str(w), *cid, "commit-tree", et,
                 "-m", f"verdict {task}\nfixed: true\nattempt: {att}").stdout.strip()
        _git("-C", str(w), "push", "-q", str(origin),
             f"{v}:refs/swarm/verdicts/{task}")
    return c


def test_scp_form_body_matches_ssh_form(lane):
    """The H2 core: same object, both documented forms, byte-identical."""
    o, root = lane
    _seed_claim(o, "T-scp", "att-w1-scp01")
    scp = "fakeuser@fakehost:origin.git"
    ssh = "ssh://fakehost/origin.git"
    ref = "refs/swarm/claims/T-scp"
    assert inrepo.origin_body(scp, ref) == inrepo.origin_body(ssh, ref)
    assert "att-w1-scp01" in inrepo.origin_body(scp, ref)


def test_scp_form_body_without_user(lane):
    o, root = lane
    _seed_claim(o, "T-scp2", "att-w2-scp02")
    assert "att-w2-scp02" in inrepo.origin_body("fakehost:origin.git",
                                                "refs/swarm/claims/T-scp2")


def test_scp_form_missing_ref_is_empty_not_crash(lane):
    o, root = lane
    assert inrepo.origin_body("fakehost:origin.git",
                              "refs/swarm/claims/nope") == ""


def test_scp_form_commit_ts(lane):
    """reconcile's dating path — pre-fix the FileNotFoundError surfaced
    here uncaught, killing the whole reconcile run."""
    o, root = lane
    _seed_claim(o, "T-ts", "att-w3-scp03", with_return=False, with_verdict=False)
    ts = inrepo.origin_commit_ts("fakehost:origin.git", "refs/swarm/claims/T-ts")
    assert ts is not None
    assert abs(ts - __import__("time").time()) < 600


def test_scp_form_reconcile_completes(lane):
    o, root = lane
    _seed_claim(o, "T-rec", "att-w4-scp04", with_return=False, with_verdict=False)
    out = inrepo.reconcile("fakehost:origin.git")
    assert out["errors"] == []
    assert "T-rec" in out["fresh"]


def test_scp_form_sweep_recovers_att(lane):
    """The recovery path: sweep with att=None derives the att from the
    claim body via origin_body — pre-fix this crashed mid-sweep."""
    o, root = lane
    _seed_claim(o, "T-swp", "att-w5-scp05")
    res = inrepo.sweep("fakehost:origin.git", "T-swp")
    assert res["att"] == "att-w5-scp05"
    have = _git("ls-remote", o).stdout
    assert "refs/swarm/archive/claims/T-swp@att-w5-scp05" in have
    assert "refs/swarm/claims/T-swp" not in have
