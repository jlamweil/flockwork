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


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _refs(o):
    return _git("ls-remote", o).stdout


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
MODE = os.environ.get("FB_STUB_MODE", "ok")


class InteractiveOwnerError(Exception):
    """mirrors fbconn's typed refusal (driver re-raises it typed)"""


def run_prompt(folder, prompt, **kw):
    with open(RECORD, "a") as f:
        f.write(json.dumps({"folder": folder, "prompt_len": len(prompt),
                            "kw": kw, "mode": MODE}) + "\\n")
    if MODE == "refuse":
        raise InteractiveOwnerError(
            "singleton owner pid 999999 is an ACTIVE freebuff session")
    if MODE == "no_text":
        return {"status": "timeout", "text": None}
    if MODE == "no_text_partial":
        # quiet death WITH partial work: the dispatch failed to answer
        # but left work in the tree (the c4 partial-orphan shape)
        with open(os.path.join(folder, "partial-work.txt"), "w") as f:
            f.write("half a fix\\n")
        return {"status": "timeout", "text": None}
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


def _run_freebuff_work_task_mode(lane, task, att, mode, worker="w-fb"):
    """Run work_task in a subprocess; return ALL JSON events it printed
    (the leg's dispatch_error lines first, work_task's final dict last)."""
    env = dict(
        os.environ,
        SWARM_ORIGIN=lane["origin"],
        SWARM_DISPATCH="freebuff",
        FBCONN_HOME=lane["fbhome"],
        FB_DISPATCH_RECORD=lane["record"],
        PKILL_LOG=lane["pkill_log"],
        HOME=lane["home"],
        FB_STUB_MODE=mode,
        PATH=lane["stub"] + os.pathsep + os.environ["PATH"],
    )
    r = subprocess.run(
        [sys.executable, "-c", DRIVER, worker, task, att],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    events = []
    for ln in r.stdout.splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            events.append(json.loads(ln))
    assert events, r.stdout
    return events


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


# ------------------------------------- wave 5: refusal/death flow law


def test_typed_refusal_records_dispatch_error_and_requeues(lane):
    """Wave-5 contract 1: a GUARDED takeover refusal (fbconn's typed
    InteractiveOwnerError — someone's live session owns the slot) is an
    honest environmental death, never a crash and never a kill: the
    dispatch_error event is printed with the typed err, oc_rc is 1, the
    dead attempt is archived and the claim freed in-run (the c6 requeue
    rule), and the owner file — the evidence the refusal classified
    from — SURVIVES (wave-4 law re-proven on the refusal path)."""
    _seed_task(lane["origin"], lane["work"], "T-ref", "true")
    _seed_claim(lane["origin"], lane["work"], "T-ref", "att-w1-ref006")
    ev = _run_freebuff_work_task_mode(lane, "T-ref", "att-w1-ref006",
                                      "refuse")
    disp = [e for e in ev if e.get("event") == "dispatch_error"]
    final = ev[-1]
    assert disp and "ACTIVE freebuff session" in disp[0]["err"]
    assert final["oc_rc"] == 1 and final["env_death"] is True
    assert final["requeued"] is True and final["swept"] == "att-w1-ref006"
    refs = _refs(lane["origin"])
    assert "refs/swarm/claims/T-ref" not in refs          # freed in-run
    assert "refs/swarm/archive/claims/T-ref@att-w1-ref006" in refs
    assert "refs/swarm/verdicts/T-ref" not in refs        # no verdict for a death
    assert lane["owner_file"].exists(), "owner file destroyed on refusal"
    # no kill signal even on the refusal path
    assert not os.path.exists(lane["pkill_log"])


def test_refusal_with_heir_spent_writes_final_honest_verdict(lane):
    """Wave-5 contract 2: the same refusal with the ONE heir attempt
    already archived is FINAL — an honest fixed:false verdict naming
    oc_rc 1, written via commit-tree on the empty tree (main and the
    tasks ref untouched; the c6 second-half law)."""
    _seed_task(lane["origin"], lane["work"], "T-refh", "true")
    dead = _root_commit(lane["work"], "claim T-refh att-w0-dead09")
    _push(lane["origin"], lane["work"], dead,
          "refs/swarm/archive/claims/T-refh@att-w0-dead09")
    _seed_claim(lane["origin"], lane["work"], "T-refh", "att-w1-ref007")
    ev = _run_freebuff_work_task_mode(lane, "T-refh", "att-w1-ref007",
                                      "refuse")
    final = ev[-1]
    assert final["env_death"] is True and final["oc_rc"] == 1, final
    assert final["heir_exhausted"] is True and final["fixed"] is False
    refs = _refs(lane["origin"])
    assert "refs/swarm/verdicts/T-refh" in refs
    assert "refs/swarm/claims/T-refh" in refs             # not freed
    body = inrepo.origin_body(lane["origin"], "refs/swarm/verdicts/T-refh")
    assert "fixed: false" in body and "oc_rc: 1" in body
    assert lane["owner_file"].exists()


def test_no_text_empty_tree_requeues_as_env_death(lane):
    """Wave-5 contract 3a: the quiet death (dispatch returns, status
    timeout, text None — the transcript-never-opened death) maps to
    oc_rc 1 (run_prompt: no text from a falsy-result dispatch), and
    with an EMPTY tree that is an environmental death: dispatch_error
    event, attempt archived, claim freed in-run — honestly requeued,
    no verdict."""
    _seed_task(lane["origin"], lane["work"], "T-quiet", "false")
    _seed_claim(lane["origin"], lane["work"], "T-quiet", "att-w1-qt0008")
    ev = _run_freebuff_work_task_mode(lane, "T-quiet", "att-w1-qt0008",
                                      "no_text")
    disp = [e for e in ev if e.get("event") == "dispatch_error"]
    final = ev[-1]
    # a quiet death raises nothing -> no dispatch_error line, just rc 1
    assert not disp, disp
    assert final["oc_rc"] == 1 and final["env_death"] is True, final
    assert final["requeued"] is True
    refs = _refs(lane["origin"])
    assert "refs/swarm/claims/T-quiet" not in refs
    assert "refs/swarm/archive/claims/T-quiet@att-w1-qt0008" in refs
    assert "refs/swarm/verdicts/T-quiet" not in refs


def test_no_text_with_partial_work_is_merit_final(lane):
    """Wave-5 contract 3b: the same quiet death WITH work already in
    the tree (the c4 partial-orphan shape) is a MERIT failure, not an
    environmental death: final fixed:false verdict (oc_rc 1 recorded
    honestly), claim KEPT, no requeue — the conservative side of the
    c6 error table (partial work must never be requeue-stormed)."""
    _seed_task(lane["origin"], lane["work"], "T-part", "false")
    _seed_claim(lane["origin"], lane["work"], "T-part", "att-w1-pt0009")
    ev = _run_freebuff_work_task_mode(lane, "T-part", "att-w1-pt0009",
                                      "no_text_partial")
    final = ev[-1]
    assert final["env_death"] is False, final
    assert final["fixed"] is False and final["oc_rc"] == 1
    assert final.get("requeued") is None
    refs = _refs(lane["origin"])
    assert "refs/swarm/verdicts/T-part" in refs
    assert "refs/swarm/claims/T-part" in refs
    body = inrepo.origin_body(lane["origin"], "refs/swarm/verdicts/T-part")
    assert "fixed: false" in body and "oc_rc: 1" in body
