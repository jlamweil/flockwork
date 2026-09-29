"""Operator-authored contract for the unchecked spec fetch (wave 9,
INT-013, 2026-09-29). _work_task cloned, then fetched the brief with an
UNCHECKED `git fetch refs/swarm/specs/<task>`: a missing/unreadable
spec ref (a lost-ref transient — the r2 class the wave-6 row documents
for return refs; origin-host containment surgery like wave 6's own
`update-ref -d`; a spec body that is empty) left brief="" and the
attempt DISPATCHED ON AN EMPTY BRIEF — a real backend leg (a 5-credit
freebuff session, an opencode run) on nothing — and then the wave-6
no-diff oracle-pass path could bless the attempt fixed:true /
already_on_main whenever main already passed the default oracle: a
GREEN VERDICT for an attempt that never saw its task. The ba42841
substrate-lie family (main/verdicts claiming work that never happened)
reopened through a new route.

Contract (local bare origin + stub dispatch, no fleet):
  - claim-orphan shape (live claim, NO spec ref), dispatch stub records
    the brief it received and exits 0, main carries a passing oracle:
    the attempt is an ENVIRONMENTAL DEATH BEFORE DISPATCH — the stub
    never runs, no verdict ref is written, no return ref, the claim is
    archived under its true att (the sweep law) so the task re-enters
    the queue when its spec is healthy again; no heir cost (the
    clone-failure shape: the attempt died before anything was spent)
  - spec ref present but body empty: same refusal — the stub never runs

HERMETICITY (wave-6 incident law): work_task runs via the subprocess
DRIVER with SWARM_ORIGIN in the env — ORIGIN binds at import, so any
in-parent call would touch the real example-host-a origin. This file never
calls origin-touching inrepo functions in-parent (refs are pushed with
git directly); the only in-parent calls are read-only helpers on the
local fixture origin.
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


def _git(*a, cwd=None, env=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True,
                          env=env)


def _cid():
    return "-c", "user.email=a@b", "-c", "user.name=a"


def _root_commit(w, msg, age_s=None):
    """Root commit with a controlled committer date (None = now)."""
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    env = None
    if age_s is not None:
        import time

        past = f"@{int(time.time() - age_s)} +0000"
        env = dict(os.environ, GIT_COMMITTER_DATE=past, GIT_AUTHOR_DATE=past)
    return _git("-C", w, *_cid(), "commit-tree", et, "-m", msg,
                env=env).stdout.strip()


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
    (w / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(w), *_cid(), "push", "-q", str(o), "main")
    stub = tmp_path / "stub"
    stub.mkdir()
    return str(o), str(w), stub


TASK = "T-specfetch"


def _claim(o, w, task, att):
    """The worker-loop claim step: a root claim commit on the CAS ref."""
    _push(o, w, _root_commit(w, f"claim {task} {att}"),
          f"refs/swarm/claims/{task}")


def _brief_recorder_stub(stub, marker="brief-seen"):
    """Dispatch stub: records the brief argv it received, exits 0."""
    p = stub / "opencode"
    p.write_text(
        "#!/bin/bash\n"
        "printf '%s' \"$5\" > " + str(stub / marker) + "\n"
        "exit 0\n"
    )
    os.chmod(p, 0o755)
    return p


def _refs(o):
    return _git("ls-remote", o).stdout


def test_missing_spec_is_env_death_before_dispatch_never_a_verdict(lane):
    """The claim-orphan shape: a live claim with NO spec ref (the lost-ref
    transient). On the unmodified tree the attempt dispatched an empty
    brief, the default oracle passed on main's own test, and the event +
    verdict blessed fixed:true — proven first, then closed."""
    o, w, stub = lane
    att = "att-w2-specf01"
    _claim(o, w, TASK, att)
    stub_opencode = _brief_recorder_stub(stub)
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub_opencode),
             SWARM_MODEL="stub/model"),
        TASK, att,
    )
    # no dispatch: the stub must never run — the attempt died before it
    assert not (stub / "brief-seen").exists(), "dispatch ran on an empty brief"
    assert ev["env_death"] is True
    assert ev.get("reason", "").startswith("spec-fetch")
    # no verdict, no return: a briefless attempt judges nothing
    refs = _refs(o)
    assert f"refs/swarm/verdicts/{TASK}" not in refs
    assert f"refs/swarm/tasks/{TASK}" not in refs
    # the live claim is freed under its TRUE att (the sweep law) so the
    # task re-enters the queue when its spec is healthy again
    assert f"refs/swarm/claims/{TASK}" not in refs
    assert f"refs/swarm/archive/claims/{TASK}@{att}" in refs
    assert ev["requeued"] is True and ev["swept"] == att


def test_empty_spec_body_never_dispatches(lane):
    """Spec ref present but its body is empty (hand-seeded shape; seed()
    can never produce one). Same refusal: nothing to dispatch on."""
    o, w, stub = lane
    att = "att-w2-specf02"
    _push(o, w, _root_commit(w, ""), f"refs/swarm/specs/{TASK}")
    _claim(o, w, TASK, att)
    stub_opencode = _brief_recorder_stub(stub, "brief-seen-2")
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub_opencode),
             SWARM_MODEL="stub/model"),
        TASK, att,
    )
    assert not (stub / "brief-seen-2").exists(), "dispatch ran on an empty brief"
    assert ev["env_death"] is True
    refs = _refs(o)
    assert f"refs/swarm/verdicts/{TASK}" not in refs
    assert f"refs/swarm/claims/{TASK}" not in refs
    assert f"refs/swarm/archive/claims/{TASK}@{att}" in refs


def test_healthy_spec_still_dispatches_and_judges(lane):
    """Guard against over-blocking: a healthy brief flows exactly as the
    settled-rediscovery contracts pin it (dispatch sees the brief, the
    no-diff oracle pass records already_on_main honestly)."""
    o, w, stub = lane
    att = "att-w2-specf03"
    _push(o, w, _root_commit(w, f"spec {TASK}\nverify: true"),
          f"refs/swarm/specs/{TASK}")
    _claim(o, w, TASK, att)
    stub_opencode = _brief_recorder_stub(stub, "brief-seen-3")
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub_opencode),
             SWARM_MODEL="stub/model"),
        TASK, att,
    )
    assert (stub / "brief-seen-3").exists(), "healthy brief never dispatched"
    assert f"verify: true" in (stub / "brief-seen-3").read_text()
    assert ev["fixed"] is True and ev["already_on_main"] is True
    assert ev["verdict_pushed"] is True
    body = inrepo.origin_body(o, f"refs/swarm/verdicts/{TASK}")
    assert f"attempt: {att}" in body and "fixed: true" in body
