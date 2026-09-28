"""INT-013 wave 4 (2026-09-28, freebuff sitting): the freebuff dispatch
leg must not do its own slot clearing.

Found live: the leg opened with `pkill -f 'the-freebuff-runtime --continue'`
plus an unconditional `rm -f ~/.config/the-freebuff-runtime/freebuff-instance-owner.json`
before run_prompt(takeover=True). Two contract violations in one block:

1. pattern-kill — `pkill -f` matches ANY freebuff holder on the host,
   including an ACTIVE mid-turn session it does not own (the 09-14 3.5h
   bounce storm started exactly this way; fbconn's own docs say "never
   kill by pattern — only the pid recorded in the owner file"). On this
   box the freebuff night conductor is that live session.
2. evidence destruction — the owner file is what fbconn's guarded
   take_over() classifies from (active vs bounced, pid-recycling
   identity). rm-ing it first blinds the guard the very next leg needs.

Contract: displacement belongs ENTIRELY to fbconn's guarded path
(run_prompt(takeover=True) -> take_over: exact-pid, identity-verified,
typed refusal on an active/interactive owner). The leg itself never
spawns a kill and never touches the owner file; a refused takeover is
an honest dispatch_error (oc_rc 1), not a crash.

Hermetic: `pkill` is shadowed by a PATH stub that only records its argv
(nothing real is ever signalled); `fbconn` is a stub package under
FBCONN_HOME; HOME points at a throwaway dir holding a planted owner
file (sentinel for the rm). work_task runs in a SUBPROCESS with
SWARM_ORIGIN in the env — ORIGIN is bound at import time (T5 lesson).
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    return _git("-C", w, *cid, "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    r = _git("-C", w, *cid, "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


DRIVER = (
    "import importlib.util, json, sys;"
    "spec = importlib.util.spec_from_file_location('inrepo', 'l2/inrepo.py');"
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
    "print(json.dumps(m.work_task(sys.argv[1], sys.argv[2], sys.argv[3])))"
)

PKILL_STUB = """\
#!/bin/bash
# recording stub: never signals anything — logs argv and exits 0
printf '%s\\n' "$*" >> "$PKILL_LOG"
exit 0
"""

FBCONN_INIT = ""

FBCONN_API = '''\
import json, os

RECORD = os.environ["FB_DISPATCH_RECORD"]

def run_prompt(folder, prompt, **kw):
    with open(RECORD, "a") as f:
        f.write(json.dumps({"folder": folder, "prompt_len": len(prompt),
                            "kw": kw}) + "\\n")
    return {"text": "ok", "status": "done"}
'''


@pytest.fixture
def lane(tmp_path):
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

    # PATH stub dir: pkill that records but never signals
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "pkill").write_text(PKILL_STUB)
    os.chmod(stub / "pkill", 0o755)
    pkill_log = tmp_path / "pkill.log"

    # throwaway HOME with a PLANTED owner file (someone else's live slot)
    home = tmp_path / "home"
    manidir = home / ".config" / "the-freebuff-runtime"
    manidir.mkdir(parents=True)
    owner_file = manidir / "freebuff-instance-owner.json"
    owner_file.write_text(json.dumps(
        {"instanceId": "someone-elses", "pid": 999999}))

    # stub fbconn package (dispatch target of the leg)
    fbhome = tmp_path / "fbconn-home"
    pkg = fbhome / "fbconn"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(FBCONN_INIT)
    (pkg / "api.py").write_text(FBCONN_API)
    record = tmp_path / "dispatch-record.jsonl"

    return {
        "origin": str(o), "work": str(w), "stub": str(stub),
        "pkill_log": str(pkill_log), "home": str(home),
        "owner_file": owner_file, "fbhome": str(fbhome),
        "record": str(record),
    }


def _seed_task(o, w, task, verify):
    spec = _root_commit(w, f"spec {task}\nverify: {verify}")
    _push(o, w, spec, f"refs/swarm/specs/{task}")
    return spec


def _seed_claim(o, w, task, att):
    c = _root_commit(w, f"claim {task} {att}")
    _push(o, w, c, f"refs/swarm/claims/{task}")
    return c


def _run_freebuff_work_task(lane, task, att, worker="w-fb"):
    env = dict(
        os.environ,
        SWARM_ORIGIN=lane["origin"],
        SWARM_DISPATCH="freebuff",
        FBCONN_HOME=lane["fbhome"],
        FB_DISPATCH_RECORD=lane["record"],
        PKILL_LOG=lane["pkill_log"],
        HOME=lane["home"],
        PATH=lane["stub"] + os.pathsep + os.environ["PATH"],
    )
    r = subprocess.run(
        [sys.executable, "-c", DRIVER, worker, task, att],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------- contracts

def test_freebuff_dispatch_never_pattern_kills_never_rms_owner_file(lane):
    """THE contract: the leg leaves the slot holder and the owner file
    alone. Displacement is run_prompt(takeover=True)'s guarded job."""
    _seed_task(lane["origin"], lane["work"], "T-fb", "true")
    _seed_claim(lane["origin"], lane["work"], "T-fb", "att-w1-fb0001")
    ev = _run_freebuff_work_task(lane, "T-fb", "att-w1-fb0001")
    assert ev["oc_rc"] == 0, ev
    # no kill signal was even ATTEMPTED (the stub would have recorded it)
    assert not os.path.exists(lane["pkill_log"]), open(
        lane["pkill_log"]).read()
    # the owner file (classification evidence for the guarded takeover)
    # is still on disk, byte-identical
    assert lane["owner_file"].exists(), "owner file was destroyed"
    assert json.loads(lane["owner_file"].read_text())["pid"] == 999999


def test_freebuff_dispatch_goes_through_fbconn_run_prompt(lane):
    """Shape pin: the leg dispatches via fbconn's run_prompt with the
    guarded takeover flag, for the claimed tree and the seeded brief."""
    _seed_task(lane["origin"], lane["work"], "T-fb2", "true")
    _seed_claim(lane["origin"], lane["work"], "T-fb2", "att-w1-fb0002")
    ev = _run_freebuff_work_task(lane, "T-fb2", "att-w1-fb0002")
    assert ev["oc_rc"] == 0, ev
    recs = [json.loads(ln) for ln in
            open(lane["record"]).read().splitlines() if ln.strip()]
    assert len(recs) == 1
    assert recs[0]["kw"].get("takeover") is True
    assert "T-fb2" in pathlib.Path(recs[0]["folder"]).name
    assert recs[0]["prompt_len"] > 0
