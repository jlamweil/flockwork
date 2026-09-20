"""Operator-authored contract for the worker law-freshness gate
(wave-3, 2026-09-20). Three recorded incidents share one class: a worker
claimed and judged with lane law (l2/inrepo.py) that differed from the
substrate's published law — round-6 finding 2 (stale scp-dropped copy),
round-6 finding 6 (node with no git remote at all), 09-19 (stale
substrate; the receipt was a false fixed:true on an oc_rc-124 death).
divergence() made that observable; observable-on-request is not
enforced. Contract (local bare origins + stub dispatch, no fleet):
  - gate at the claim moment, BEFORE the claim CAS: the RUNNING module's
    blob sha must equal origin main's l2/inrepo.py (read on the origin
    host), or the worker refuses with law_freshness_refusal + exit 1
    and ZERO live claims
  - origin publishes no law file (cross-repo solve-lane shape) →
    pass-through: claims + completes
  - a node running from a NON-GIT directory is still gated (hashing
    needs no repo) — the scp-drop shape
  - SWARM_ALLOW_DIVERGED=1 proceeds on stale/error but records
    law_freshness_bypass — never silent
  - hashing keys on the running FILE, so the worker's own return
    commits on origin main never trip the gate (main moves, law doesn't)
law_check statuses: match | stale | absent (unknown-revision class) |
error (anything else — fail closed at the caller).
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
LAW = REPO / "l2" / "inrepo.py"


def _load():
    spec = importlib.util.spec_from_file_location("inrepo", LAW)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return "-c", "user.email=a@b", "-c", "user.name=a"


def _seed_task(o, task, verify="true"):
    """Spec ref via root commit (contentless — the root tree lives in the
    origin, not in any clone)."""
    scratch = o.parent / "scratch"
    if not scratch.exists():
        _git("init", "-q", "-b", "main", str(scratch))
    spec = _git("-C", str(scratch), *_cid(), "commit-tree",
                _git("hash-object", "-t", "tree", "/dev/null").stdout.strip(),
                "-m", f"spec {task}\nverify: {verify}").stdout.strip()
    r = _git("-C", str(scratch), *_cid(), "push", "-q", str(o),
             f"{spec}:refs/swarm/specs/{task}")
    assert r.returncode == 0, r.stderr


def _publish_law(o, body: bytes, msg="law"):
    """Commit the law file onto origin main (through a scratch clone)."""
    scratch = o.parent / "lawpush"
    if not scratch.exists():
        _git("clone", "-q", str(o), str(scratch))
        _git("-C", str(scratch), "config", "user.email", "a@b")
        _git("-C", str(scratch), "config", "user.name", "a")
    d = scratch / "l2"
    d.mkdir(parents=True, exist_ok=True)
    (d / "inrepo.py").write_bytes(body)
    _git("-C", str(scratch), "add", "-A")
    r = _git("-C", str(scratch), *_cid(), "commit", "-qm", msg)
    if r.returncode != 0:
        assert "nothing to commit" in r.stdout + r.stderr, r
    rp = _git("-C", str(scratch), *_cid(), "push", "-q", "origin", "main")
    assert rp.returncode == 0, rp.stderr


def _node_clone(tmp_path, law_body: bytes, name="w"):
    """A node checkout: has the law file (possibly stale), initialized
    with main so the CLI run looks like a real node."""
    w = tmp_path / name
    (w / "l2").mkdir(parents=True)
    (w / "l2" / "inrepo.py").write_bytes(law_body)
    _git("init", "-q", "-b", "main", str(w))
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "node seed")
    return w


def _stub(stub_dir):
    stub_dir.mkdir(parents=True, exist_ok=True)
    p = stub_dir / "opencode"
    p.write_text("#!/bin/bash\necho work > marker.txt\nexit 0\n")
    os.chmod(str(p), 0o755)
    return str(p)


def _run_worker(law_file, cwd, origin, extra_env=(), label="wl"):
    env = dict(os.environ, SWARM_ORIGIN=str(origin),
               OPENCODE_BIN=_stub(pathlib.Path(cwd) / "stub"),
               SWARM_MODEL="stub/model")
    env.update({k: v for k, v in extra_env})
    try:
        r = subprocess.run([sys.executable, str(law_file), "worker", label],
                           cwd=str(cwd), capture_output=True, text=True,
                           env=env, timeout=120)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "worker spun instead of finishing — pre-gate this is the "
            "structural claim-loss loop (claim fails, not-a-race, "
            "re-scan forever); post-gate the refusal must fire first"
        )
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    return r, events


def _live_claims(o):
    return [ln for ln in _git("ls-remote", str(o)).stdout.splitlines()
            if "refs/swarm/claims/" in ln and "@" not in ln]


ORIGIN_LAW = LAW.read_bytes()
STALE_LAW = ORIGIN_LAW + b"\n# stale node copy\n"


# ------------------------------------------------------------- H2 matrix


def test_insync_worker_claims_and_completes(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    _publish_law(o, ORIGIN_LAW)
    _seed_task(o, "T-sync", "test -f marker.txt")
    w = _node_clone(tmp_path, ORIGIN_LAW)
    r, events = _run_worker(w / "l2" / "inrepo.py", w, o)
    assert r.returncode == 0, r.stdout + r.stderr
    assert events[-1] == {"event": "worker_done", "worker": "wl",
                          "completed": 1}
    assert not any(e["event"].startswith("law_freshness") for e in events)


def test_stale_law_refuses_before_claiming(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    _publish_law(o, ORIGIN_LAW)          # substrate has current law…
    _seed_task(o, "T-stale", "test -f marker.txt")
    w = _node_clone(tmp_path, STALE_LAW)  # …the node runs stale bytes
    r, events = _run_worker(w / "l2" / "inrepo.py", w, o)
    assert r.returncode == 1
    assert [e["event"] for e in events] == ["law_freshness_refusal"]
    assert events[0]["law"]["status"] == "stale"
    assert events[0]["task"] == "T-stale"
    assert _live_claims(o) == []          # nothing was claimed


def test_no_published_law_passes_through(tmp_path):
    # cross-repo solve-lane shape: task origin publishes no l2/inrepo.py
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    scratch = tmp_path / "seedmain"
    _git("init", "-q", "-b", "main", str(scratch))
    (scratch / "readme.md").write_text("satellite\n")
    _git("-C", str(scratch), "add", "-A")
    _git("-C", str(scratch), *_cid(), "commit", "-qm", "satellite main")
    r = _git("-C", str(scratch), *_cid(), "push", "-q", str(o), "main")
    assert r.returncode == 0, r.stderr
    _seed_task(o, "T-cross", "test -f marker.txt")
    w = _node_clone(tmp_path, ORIGIN_LAW)
    r, events = _run_worker(w / "l2" / "inrepo.py", w, o)
    assert r.returncode == 0, r.stdout + r.stderr
    assert events[-1]["event"] == "worker_done"
    assert events[-1]["completed"] == 1


def test_nongit_cwd_stale_copy_still_gated(tmp_path):
    # round-6 finding 6 shape: no git remote / not even a repo — the
    # scp-era node runs a stale dropped copy; hashing needs no repo
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    _publish_law(o, ORIGIN_LAW)
    _seed_task(o, "T-scpland", "test -f marker.txt")
    bare = tmp_path / "scpland"
    bare.mkdir()
    stale_copy = tmp_path / "stale.py"
    stale_copy.write_bytes(STALE_LAW)     # the scp-dropped stale module
    r, events = _run_worker(stale_copy, bare, o)
    assert r.returncode == 1
    assert [e["event"] for e in events] == ["law_freshness_refusal"]
    assert events[0]["law"]["status"] == "stale"
    assert _live_claims(o) == []


def test_bypass_proceeds_and_records(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    _publish_law(o, ORIGIN_LAW)
    _seed_task(o, "T-bypass", "test -f marker.txt")
    w = _node_clone(tmp_path, STALE_LAW)
    r, events = _run_worker(w / "l2" / "inrepo.py", w, o,
                            extra_env=(("SWARM_ALLOW_DIVERGED", "1"),))
    assert r.returncode == 0, r.stdout + r.stderr
    kinds = [e["event"] for e in events]
    assert "law_freshness_bypass" in kinds
    assert events[-1] == {"event": "worker_done", "worker": "wl",
                          "completed": 1}


# ------------------------------------------------- law_check unit probes


def test_law_check_statuses_match_absent_error(tmp_path):
    o = tmp_path / "match.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    _publish_law(o, ORIGIN_LAW)
    d = inrepo.law_check(str(o))
    assert d["status"] == "match" and d["origin_sha"] and d["local_sha"]
    assert d["origin_sha"] == d["local_sha"]

    o2 = tmp_path / "absent.git"
    _git("init", "-q", "--bare", str(o2))   # empty: nothing published
    d = inrepo.law_check(str(o2))
    assert d["status"] == "absent" and d["origin_sha"] is None

    notarepo = tmp_path / "notarepo"
    notarepo.mkdir()                       # exists, not a git repo
    d = inrepo.law_check(str(notarepo))
    assert d["status"] == "error" and d["reason"]
    d = inrepo.law_check(str(tmp_path / "vanished"))  # path absent entirely
    assert d["status"] == "error" and d["reason"]


def test_law_check_keys_on_running_file_not_cwd(tmp_path):
    # the sha comes from the running module file; a stale COPY reports
    # stale even when the CWD clone is in sync (and vice versa)
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    _publish_law(o, STALE_LAW)
    stale_file = tmp_path / "stale.py"
    stale_file.write_bytes(STALE_LAW)
    spec = importlib.util.spec_from_file_location("stalegate", stale_file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    d = m.law_check(str(o))
    assert d["status"] == "match"          # running bytes == published
