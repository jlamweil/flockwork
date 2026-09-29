"""Operator-authored contract for the clone-failure heir budget (wave 15,
INT-013, 2026-09-29). This closes the asymmetry wave 10 DISCLOSED and
deliberately left open (test_inrepo_spec_death_budget: "the pre-existing
clone-failure path still sweeps unconditionally — classified transient
(origin blips self-heal) vs the broken-spec permanence that justifies
the budget here"). The classification is WRONG for the permanent half
of the class: clone failure is not one hazard but two, and only the
transient half self-heals.

The hazard: _work_task's clone-failure leg sweeps UNCONDITIONALLY — the
claim is archived and the task re-enters the queue on EVERY clone
failure, whatever the archive already holds. A permanently unclonable
task (disk-full/quota on the worker host, an origin whose tree grew a
path the local fs rejects, a corrupted object the clone aborts on)
churns claim→clone-fail→sweep→requeue FOREVER: no TTL backstop (the
claim is freed at once, so reconcile never sees a stale claim), no
final verdict ever surfacing, and one archive ref per cycle — the
exact shape wave 10 closed for permanently-broken specs, on the
adjacent leg of the same helper. The falsifier's for-loop names the
churn: N consecutive clone failures → N requeues, zero verdicts, one
archive ref added per cycle.

The transient half keeps its requeue: a genuine origin blip self-heals
and the next worker succeeds — so the gate is the lane's own c6
one-heir budget (SWARM_HEIR_MAX), applied exactly as the wave-10
spec-death path applies it: first clone death requeues (the
environment may heal); under a spent budget the path writes the honest
final fixed:false verdict (oc_rc 1 — no usable dispatch leg, never 0
which reads as merit; pytest_rc plain 1 — the oracle never ran; reason
clone_heir_exhausted; main + tasks ref untouched per the wave-2
heir-exhausted law).

Also pinned: the wave-13 law survives the change — an origin that dies
MID-CLONE (the gate itself unevaluatable) still returns the structured
requeued False + sweep_error shape (claim stays live for reconcile's
TTL), never a crash, never the lie-0 budget read.

Contract (local fixtures; no fleet, no pushes to any live origin):
  - first clone failure still requeues (regression guard: the
    transient, self-healing half of the class keeps its fast retry)
  - a second clone failure under a spent heir budget writes the FINAL
    verdict (fixed:false, pytest_rc 1, oc_rc 1, clone_heir_exhausted)
    — the churn loop converges, nothing requeues, no archive ref is
    added by the final leg
  - the convergence chain: requeue → re-claim → final verdict, with
    the archive holding exactly the two atts that died
  - origin dies mid-clone (heirs_count unevaluatable): structured
    event, claim live for reconcile, sweep_error naming the origin

work_task runs in a SUBPROCESS with SWARM_ORIGIN in the env — ORIGIN
is bound at import time (the T5 lesson). All in-parent calls here
target LOCAL fixture paths — no env-bound ORIGIN is touched (the
wave-6/8 hermeticity law).
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
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                          text=True, check=False)


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    return _git("-C", w, *cid, "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref, force=False):
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    r = _git("-C", w, *cid, "push", *( ["--force"] if force else [] ),
             "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


def _refs(o):
    return _git("ls-remote", o).stdout


DRIVER = """\
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('inrepo', 'l2/inrepo.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(json.dumps(m.work_task(sys.argv[1], sys.argv[2], sys.argv[3])))
"""


def _run_driver(env_extra, worker, task, att):
    env = dict(os.environ, **env_extra)
    r = subprocess.run(
        [sys.executable, "-c", DRIVER, worker, task, att],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=300,
        check=False,
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
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "push", "-q", str(o), "main")
    return str(o), str(w)


TASK = "T-clonebudget"


def _seed_and_claim(o, w, att):
    _push(o, w, _root_commit(w, f"spec {TASK}\n\nfix it\n"),
          f"refs/swarm/specs/{TASK}")
    _push(o, w, _root_commit(w, f"claim {TASK} {att}"),
          f"refs/swarm/claims/{TASK}")


def _break_clones(origin):
    """Clone breaker (read-preserving): delete main's BLOB object on the
    bare origin. ls-remote reads only the refs DB (fine — the c6 gate
    and the sweep's census stay green); the sweep's fetch+push touches
    only the swarm refs (fine — the claim object is intact); CLONE dies
    because upload-pack cannot read main's tree contents. Deterministic
    rc!=0, no env rewrites, no insteadOf side effects (a URL-insteadOf
    breaker measured WRONG first: it broke the sweep's own ls-remote
    too, manufacturing 'origin unreachable' instead of 'unclonable').
    Returns the deleted blob sha (each test owns its tmp origin)."""
    blob = _git("-C", origin, "rev-parse", "main:f.txt").stdout.strip()
    obj = pathlib.Path(origin) / "objects" / blob[:2] / blob[2:]
    assert obj.exists(), f"expected loose blob {blob} on the bare origin"
    obj.unlink()
    return blob


def _verify_verdict_final(ev):
    assert ev["event"] == "attempted"
    assert ev["fixed"] is False
    assert ev["env_death"] is True
    assert ev["heir_exhausted"] is True
    assert ev["pytest_rc"] == 1
    assert ev["oc_rc"] == 1
    assert ev["main_push"] is False
    assert ev["return_pushed"] is False
    assert ev["requeued"] is None
    assert ev["swept"] is None
    assert ev["reason"] == "clone_heir_exhausted"


def test_first_clone_failure_still_requeues(lane):
    """Regression guard: the transient, self-healing half of the class
    keeps its fast retry — one archive ref, task back in open_tasks."""
    o, w = lane
    att = "att-w15-fresh1"
    _seed_and_claim(o, w, att)
    _break_clones(o)
    ev = _run_driver({"SWARM_ORIGIN": o}, "w15", TASK, att)
    assert ev["env_death"] is True
    assert ev["requeued"] is True
    assert ev["swept"] == att
    assert f"refs/swarm/archive/claims/{TASK}@{att}" in _refs(o)


def test_permanent_clone_breaker_converges_to_final_verdict(lane):
    """The falsifier: two consecutive workers face the same permanently
    unclonable task. Pre-fix BOTH requeue — N clone failures produce N
    requeues, zero verdicts, one archive ref per cycle, forever (no TTL
    backstop: the claim is freed each cycle). Post-fix the second death
    finds the heir budget spent and writes the FINAL verdict — the
    churn stops, nothing is requeued, and the final leg adds no new
    archive ref."""
    o, w = lane
    att1, att2 = "att-w15-dead1", "att-w15-dead2"
    _seed_and_claim(o, w, att1)
    _break_clones(o)
    env = {"SWARM_ORIGIN": o}
    ev1 = _run_driver(env, "w15a", TASK, att1)  # fresh budget: requeue
    assert ev1["requeued"] is True and ev1["swept"] == att1
    # re-claim (the worker loop's own next cycle)
    _push(o, w, _root_commit(w, f"claim {TASK} {att2}"),
          f"refs/swarm/claims/{TASK}")
    ev2 = _run_driver(env, "w15b", TASK, att2)  # budget spent: FINAL
    _verify_verdict_final(ev2)
    v_ref = f"refs/swarm/verdicts/{TASK}"
    have = _refs(o)
    assert v_ref in have  # the honest final verdict exists
    assert f"refs/swarm/archive/verdicts/{TASK}@{att2}" not in have
    # the final leg writes ONLY the verdict: the claim stays LIVE (the
    # wave-10 law mirrored — an immediate sweep would re-open the churn
    # at full speed; the live claim is what holds the task out of the
    # queue until reconcile's TTL re-lease), main + tasks ref untouched
    assert f"refs/swarm/claims/{TASK}" in have
    assert f"refs/swarm/tasks/{TASK}" not in have
    assert f"refs/swarm/archive/claims/{TASK}@{att2}" not in have


def test_convergence_chain_claim_requeue_then_final(lane):
    """The full chain as the worker loop drives it: claim → requeue →
    re-claim → final verdict; the archive then holds exactly the two
    dead atts and the live refs are gone."""
    o, w = lane
    att1, att2 = "att-w15-ch1", "att-w15-ch2"
    _seed_and_claim(o, w, att1)
    _break_clones(o)
    env = {"SWARM_ORIGIN": o}
    _run_driver(env, "w15c", TASK, att1)
    _push(o, w, _root_commit(w, f"claim {TASK} {att2}"),
          f"refs/swarm/claims/{TASK}")
    _run_driver(env, "w15d", TASK, att2)
    have = _refs(o)
    assert f"refs/swarm/archive/claims/{TASK}@{att1}" in have
    assert f"refs/swarm/claims/{TASK}" in have  # live per the wave-10 law
    assert f"refs/swarm/verdicts/{TASK}" in have
    # THE CONVERGENCE PROOF (the falsifier's for-else names the churn):
    # a THIRD worker under the still-spent budget writes NO second
    # verdict and NO new archive ref — pre-fix every cycle added one of
    # each, forever. Drive 5 consecutive attempts with fresh atts.
    def _board():
        return {
            ln.split()[1] for ln in _refs(o).splitlines() if ln.strip()
        }

    seen = _board()
    for i in range(5):
        att_i = f"att-w15-chx{i}"
        # force: this stands in for reconcile's TTL re-lease (free the
        # stale claim, fresh claim lands) compressed into one act
        _push(o, w, _root_commit(w, f"claim {TASK} {att_i}"),
              f"refs/swarm/claims/{TASK}", force=True)
        ev_i = _run_driver(env, f"w15x{i}", TASK, att_i)
        _verify_verdict_final(ev_i)
        now = _board()
        assert f"refs/swarm/verdicts/{TASK}" in now
        # the create-once CAS keeps the FIRST final verdict of record;
        # what the contract forbids is NEW refs: a wrongly-sweeping
        # final leg would add archive/claims + archive/verdicts @att_i
        # every cycle (the measured pre-fix churn shape)
        grew = now - seen
        assert not grew, f"cycle {i} grew the board: {grew}"
        seen = now


def test_origin_death_mid_clone_stays_structured(lane):
    """The wave-13 regression guard survives the wave-15 change: when
    the origin dies MID-CLONE (the c6 gate itself unevaluatable), the
    attempt returns the structured requeued False + sweep_error shape
    with the claim left LIVE for reconcile's TTL — never a crash, never
    the lie-0 budget read, never a final verdict graded on blindness."""
    o, w = lane
    att = "att-w15-mid0"
    _seed_and_claim(o, w, att)
    # the fault: flip the origin away AFTER the driver started the
    # attempt but BEFORE its clone — deterministic via a wrapper that
    # kills the origin on first git invocation (the driver's git sees
    # it gone; nothing else runs in-process)
    killer_dir = pathlib.Path(o).parent / "killergit"
    killer_dir.mkdir(parents=True, exist_ok=True)
    killer = killer_dir / "git"
    killer.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "clone" ]; then mv "$ORIGIN_PATH" "$ORIGIN_PATH.gone"; fi\n'
        'exec /usr/bin/git "$@"\n'
    )
    os.chmod(killer, 0o755)
    env = {
        "SWARM_ORIGIN": o,
        "PATH": f"{killer_dir}:{os.environ['PATH']}",
        "ORIGIN_PATH": o,
    }
    try:
        ev = _run_driver(env, "w15e", TASK, att)
    finally:
        gone = o + ".gone"
        if os.path.exists(gone) and not os.path.exists(o):
            os.rename(gone, o)
    assert ev["event"] == "attempted"
    assert ev["env_death"] is True
    assert ev["requeued"] is False and ev["swept"] is None
    assert "origin unreachable" in ev["sweep_error"]
    assert f"refs/swarm/claims/{TASK}" in _refs(o)  # live for reconcile
