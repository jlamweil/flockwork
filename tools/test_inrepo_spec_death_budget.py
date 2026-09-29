"""Operator-authored contract for the pre-dispatch death budget (wave 10,
INT-013, 2026-09-29). Wave 9 made a missing/empty task brief an
environmental death before dispatch — but its sweep is UNCONDITIONAL:
the c6 one-heir law (experiments/c6/FREEZE.md — an environmental death
gets ONE heir attempt max) is bypassed entirely on the pre-dispatch
paths. A PERMANENTLY broken spec (an empty brief that nothing repairs —
seed() can never produce one, but a hand-seeded or damaged ref
persists) therefore churns claim→death→sweep FOREVER: a fast loop with
no TTL, no reconcile backstop (the claim is freed immediately), no
final verdict ever surfacing, and one archive ref added per cycle.
The budget must gate pre-dispatch deaths exactly as it gates
post-dispatch ones: first death requeues (a spec may be mid-repair by
its owner), the next one under budget-constrained heirs writes the
honest final fixed:false verdict and stops the cycle.

Contract (local bare origin + stub dispatch, no fleet):
  - empty-brief spec, consecutive workers claim the task while it is
    claimable: every death is env_death, requeues stop within the heir
    budget, the cycle ENDS with a final fixed:false verdict (claim
    stays live — the task stops cycling), pytest_rc recorded as a
    plain 1 (never a measured result — the oracle never ran), oc_rc 1
    (no usable dispatch leg — never 0, which reads as merit)
  - disclosed asymmetry (NOT touched here): the pre-existing
    clone-failure path also sweeps unconditionally — classified
    transient (origin blips self-heal), while a broken spec is
    permanent; changing clone semantics needs its own falsifier.

work_task runs in a SUBPROCESS with SWARM_ORIGIN in the env — ORIGIN
is bound at import time (the T5 lesson).
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
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return "-c", "user.email=a@b", "-c", "user.name=a"


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    return _git("-C", w, *_cid(), "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    r = _git("-C", w, *_cid(), "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


DRIVER = (
    "import importlib.util, json, sys;"
    "spec = importlib.util.spec_from_file_location('inrepo', 'l2/inrepo.py');"
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
    "print(json.dumps(m.work_task(sys.argv[1], sys.argv[2], sys.argv[3])))"
)


def _run_work_task(env_extra, task, att, worker="w2"):
    env = dict(os.environ, **env_extra)
    r = subprocess.run(
        [sys.executable, "-c", DRIVER, worker, task, att],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=300,
    )
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture
def lane(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(w), *_cid(), "push", "-q", str(o), "main")
    stub = tmp_path / "stub"
    stub.mkdir()
    return str(o), str(w), stub


TASK = "T-specdeath"


def _refs(o):
    return _git("ls-remote", o).stdout


def test_permanently_broken_spec_converges_to_final_verdict(lane):
    """The falsifier: consecutive workers on a permanently empty-brief
    spec. On the wave-9 tree every death requeues — the cycle never ends
    (no verdict, unbounded archive growth); under the c6 law it must
    converge to ONE final honest verdict."""
    o, w, stub = lane
    _push(o, w, _root_commit(w, ""), f"refs/swarm/specs/{TASK}")
    stub_opencode = stub / "opencode"
    stub_opencode.write_text("#!/bin/bash\nexit 0\n")  # must NEVER run
    os.chmod(stub_opencode, 0o755)
    env = dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub_opencode),
               SWARM_MODEL="stub/model")
    requeues = 0
    for i in range(5):
        att = f"att-w2-broken{i}"
        _push(o, w, _root_commit(w, f"claim {TASK} {att}"),
              f"refs/swarm/claims/{TASK}")
        ev = _run_work_task(env, TASK, att)
        assert ev["env_death"] is True
        assert not (stub / "brief-seen").exists()  # nothing was ever spent
        if ev.get("requeued"):
            requeues += 1
            assert f"refs/swarm/claims/{TASK}" not in _refs(o)
            continue
        # the cycle ended: honest final verdict, claim stays live
        assert ev["heir_exhausted"] is True
        assert ev["fixed"] is False and ev["pytest_rc"] == 1 and ev["oc_rc"] == 1
        assert ev["verdict_pushed"] is True
        assert ev["requeued"] is None and ev["swept"] is None
        refs = _refs(o)
        assert f"refs/swarm/verdicts/{TASK}" in refs
        assert f"refs/swarm/claims/{TASK}" in refs
        body = inrepo.origin_body(o, f"refs/swarm/verdicts/{TASK}")
        assert "fixed: false" in body and "attempt: " + att in body
        break
    else:
        pytest.fail(
            "permanently-broken spec never surfaced a final verdict — "
            "unbounded requeue churn (c6 one-heir law bypassed)"
        )
    assert requeues <= 1, f"budget not honored: {requeues} requeues"


def test_first_spec_death_still_requeues_under_default_budget(lane):
    """The wave-9 semantics are preserved at MAX=1: the FIRST death
    requeues (the spec may be mid-repair), only the next one under a
    spent budget goes final."""
    o, w, stub = lane
    _push(o, w, _root_commit(w, ""), f"refs/swarm/specs/{TASK}")
    att = "att-w2-first01"
    _push(o, w, _root_commit(w, f"claim {TASK} {att}"),
          f"refs/swarm/claims/{TASK}")
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o,
             OPENCODE_BIN=str(stub / "opencode"),  # absent binary: never runs
             SWARM_MODEL="stub/model"),
        TASK, att,
    )
    assert ev["env_death"] is True and ev["requeued"] is True
    assert ev["swept"] == att and ev.get("heir_exhausted") is None
    assert f"refs/swarm/archive/claims/{TASK}@{att}" in _refs(o)
