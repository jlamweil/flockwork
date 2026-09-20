"""Operator-authored contract for the sweep CLI receipt (2026-09-21
tail wave 3). Every other lane command prints a JSON receipt — audit,
open_tasks, reconcile, relabel, divergence — but the CLI `sweep` (the
one command that MUTATES the substrate) discards sweep()'s result dict
and prints nothing: a CLI-driven repair is evidenced only by a ref
census (measured live 2026-09-21 while repairing the claims/T litter).
Contract:
  - `python3 l2/inrepo.py sweep ORIGIN TASK [ATT]` prints exactly
    sweep()'s return — task, att (recovered from the claim body when
    not given), archived marker refs, deleted live refs.
The atomic push itself is unchanged; this is observability only.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    p = pathlib.Path(REPO / "l2" / "inrepo.py")
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, text=True,
                          capture_output=True)


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    return _git("-C", w, *cid, "commit-tree", et, "-m", msg).stdout.strip()


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
    c = _root_commit(w, "claim T-rcpt att-w1-rcpt")
    _git("-C", str(w), "push", "-q", str(o), f"{c}:refs/swarm/claims/T-rcpt")
    return str(o)


def _last_json(out):
    dec = json.JSONDecoder()
    payload = None
    for i, ch in enumerate(out):
        if ch == "{" and (i == 0 or out[i - 1] == "\n"):
            try:
                payload, _ = dec.raw_decode(out, i)
            except json.JSONDecodeError:
                pass
    return payload


def test_sweep_cli_prints_receipt(origin):
    """SWARM_ORIGIN is set BEFORE the subprocess imports the module
    (the T5 lesson). The receipt must equal sweep()'s return."""
    env = dict(os.environ, SWARM_ORIGIN=origin)
    r = subprocess.run(
        [sys.executable, "l2/inrepo.py", "sweep", origin, "T-rcpt"],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    payload = _last_json(r.stdout)
    assert payload is not None, \
        f"no JSON receipt in stdout: {r.stdout[:200]!r}"
    assert payload == {
        "task": "T-rcpt",
        "att": "att-w1-rcpt",
        "archived": ["refs/swarm/archive/claims/T-rcpt@att-w1-rcpt"],
        "deleted": ["refs/swarm/claims/T-rcpt"],
    }
    # the repair itself is unchanged: live ref gone, marker present
    refs = _git("ls-remote", origin).stdout
    assert "refs/swarm/archive/claims/T-rcpt@att-w1-rcpt" in refs
    live = {ln.split("\t")[1] for ln in refs.splitlines() if "\t" in ln}
    assert "refs/swarm/claims/T-rcpt" not in live


def test_sweep_cli_att_override_in_receipt(origin, tmp_path):
    """An explicit att is honored and reported (the caller-supplied
    marker path — same receipt, caller's att)."""
    w = tmp_path / "w2"
    _git("init", "-q", "-b", "main", str(w))
    c = _root_commit(w, "claim T-rcpt2 att-w1-auto")
    _git("-C", str(w), "push", "-q", origin,
         f"{c}:refs/swarm/claims/T-rcpt2")
    env = dict(os.environ, SWARM_ORIGIN=origin)
    r = subprocess.run(
        [sys.executable, "l2/inrepo.py", "sweep", origin, "T-rcpt2",
         "att-w1-given"],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    payload = _last_json(r.stdout)
    assert payload is not None, f"no JSON receipt: {r.stdout[:200]!r}"
    assert payload["att"] == "att-w1-given"
    assert payload["archived"] == \
        ["refs/swarm/archive/claims/T-rcpt2@att-w1-given"]
