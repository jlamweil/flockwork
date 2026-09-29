"""Operator-authored contract for settled-task rediscovery (wave 6,
INT-013, 2026-09-29). The correction-graph law (test_correction_graph_
spec) pins reconcile's TTL re-lease of merit-failed tasks — a fixed:
false verdict does not keep its claim alive forever. The unpinned
sibling shape is the SETTLED task: verdict fixed:true whose return ref
was lost (r2 transient failure after main_push landed) or never
written. reconcile files it as claim+verdict-no-return, sweeps it, the
task re-enters open_tasks, and the rediscovering attempt finds the fix
ALREADY on main — the oracle passes on an unchanged tree.

On the unmodified tree the success path then commits --allow-empty and
publishes a new empty "fix <task>" commit to main: the ba42841
substrate-lie class (main history claiming a fix the tree never had)
reopened through the re-lease route. The commit must never be created;
the return ref is pointed at the main tip that already carries the
work, and the verdict records the attempt honestly.Contract (local bare origin + stub dispatch, no fleet; oracle `true`,
  dispatch writes nothing):
  - settled shape swept by reconcile → rediscovered work_task:
    origin main tip UNCHANGED (no empty fix commit), the return ref is
    created at the existing main tip, the rediscovery attempt's own
    fixed:true verdict replaces the swept original (create-once with a
    live claim — the same re-lease law the CG cycle depends on), event
    says fixed:true with already_on_main recorded
  - the healed shape is healthy on the next reconcile (no treadmill:
    the task never re-sweeps)
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
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(w), *_cid(), "push", "-q", str(o), "main")
    stub = tmp_path / "stub"
    stub.mkdir()
    return str(o), str(w), stub


TASK = "T-settled"


def _seed_settled(o, w, stub):
    """The settled shape: spec + claim + fixed:true verdict, NO return
    ref — main already carries the fix (the seed commit itself passes
    the `true` oracle)."""
    spec = _root_commit(w, f"spec {TASK}\nverify: true")
    _push(o, w, spec, f"refs/swarm/specs/{TASK}")
    att1 = "att-w1-settl1"
    _push(o, w, _root_commit(w, f"claim {TASK} {att1}", age_s=7200),
          f"refs/swarm/claims/{TASK}")
    _push(o, w, _root_commit(
        w, f"verdict\ntask: {TASK}\nattempt: {att1}\nfixed: true\n"
           f"host: h1\noc_rc: 0\npytest_rc: 0"),
        f"refs/swarm/verdicts/{TASK}")
    p = stub / "opencode"
    p.write_text("#!/bin/bash\nexit 0\n")  # dispatch 'works', writes nothing
    os.chmod(p, 0o755)
    return att1


def _refs(o):
    return _git("ls-remote", o).stdout


def _claim(o, w, task, att):
    """The worker-loop claim step: a root claim commit on the CAS ref."""
    _push(o, w, _root_commit(w, f"claim {task} {att}"),
          f"refs/swarm/claims/{task}")


def test_rediscovered_settled_task_never_publishes_empty_fix(lane):
    o, w, stub = lane
    att1 = _seed_settled(o, w, stub)
    tip_before = _git("-C", o, "rev-parse", "main").stdout.strip()
    # the re-lease law frees the claim (reconcile sweep == archive+free)
    inrepo.sweep(o, TASK)
    assert f"refs/swarm/claims/{TASK}" not in _refs(o)
    # a fresh worker claims and rediscovers the settled task
    att2 = "att-w2-redis1"
    _claim(o, w, TASK, att2)
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode"),
             SWARM_MODEL="stub/model"),
        TASK, att2,
    )
    assert ev["fixed"] is True and ev["already_on_main"] is True
    assert ev["verdict_pushed"] is True  # own claim: CG re-lease law
    tip_after = _git("-C", o, "rev-parse", "main").stdout.strip()
    assert tip_after == tip_before, "empty fix commit landed on main"
    refs = _refs(o)
    # the return ref heals at the EXISTING main tip — no new commit
    tasks_line = [ln for ln in refs.splitlines()
                  if ln.endswith(f"refs/swarm/tasks/{TASK}")]
    assert tasks_line, "return ref not created"
    assert tasks_line[0].split()[0] == tip_before
    # the live verdict is the rediscovery attempt's own, honest record
    body = inrepo.origin_body(o, f"refs/swarm/verdicts/{TASK}")
    assert f"attempt: {ev['att']}" in body and "fixed: true" in body


def test_reconcile_re_lease_of_settled_task_converges_healthy(lane):
    o, w, stub = lane
    _seed_settled(o, w, stub)
    rec = inrepo.reconcile(o, ttl_s=1.0)
    assert rec["swept"] == ["att-w1-settl1"]  # the re-lease fires (CG law)
    tip_before = _git("-C", o, "rev-parse", "main").stdout.strip()
    _claim(o, w, TASK, "att-w2-redis2")
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub / "opencode"),
             SWARM_MODEL="stub/model"),
        TASK, "att-w2-redis2",
    )
    assert ev["fixed"] is True and ev["already_on_main"] is True
    assert _git("-C", o, "rev-parse", "main").stdout.strip() == tip_before
    # healed: claim+return+verdict all live → healthy, never re-swept
    rec2 = inrepo.reconcile(o, ttl_s=1.0)
    assert rec2["healthy"] == [TASK]
    assert rec2["swept"] == [] and rec2["stale"] == []


# ------------------------------------------------------- wave 7 (race)

def test_main_advance_during_attempt_still_heals_return_ref(lane):
    """Wave 7 (INT-013, 2026-09-29): the no-diff path read origin main's
    tip with ls-remote and pushed THAT sha from the attempt's clone —
    which never fetched it. When main advances during the attempt (a
    concurrent worker lands a commit), the return push fails (`not our
    ref`), return_pushed comes back false for work that IS on main, and
    the settled task re-enters the re-lease treadmill. Falsified with a
    dispatch stub that advances origin main MID-ATTEMPT."""
    o, w, stub = lane
    _seed_settled(o, w, stub)
    seed_tip = _git("-C", o, "rev-parse", "main").stdout.strip()
    inrepo.sweep(o, TASK)
    att2 = "att-w3-race01"
    _claim(o, w, TASK, att2)
    marker = pathlib.Path(stub) / "concurrent-landed"
    p = stub / "opencode"
    p.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        "d=$(mktemp -d)\n"
        "git clone -q \"$SWARM_ORIGIN\" \"$d/c\"\n"
        "cd \"$d/c\"\n"
        "echo concurrent >> f.txt\n"
        "git -c user.email=c@a -c user.name=c commit -qam 'concurrent B'\n"
        "git push -q origin main\n"
        "touch " + str(marker) + "\n"
        "exit 0\n"
    )
    os.chmod(p, 0o755)
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(p), SWARM_MODEL="stub/model"),
        TASK, att2,
    )
    assert marker.exists(), "stub failed to advance origin main"
    assert ev["fixed"] is True and ev["already_on_main"] is True
    assert ev["return_pushed"] is True, "return push lost the tip race"
    refs = _refs(o)
    tasks_line = [ln for ln in refs.splitlines()
                  if ln.endswith(f"refs/swarm/tasks/{TASK}")]
    assert tasks_line, "return ref not created"
    # the return ref points at real main-lineage work: the attempt's
    # own verified view of main (the seed tip), an ANCESTOR of the
    # concurrent-advanced main — recorded honestly, not re-read
    tasks_sha = tasks_line[0].split()[0]
    assert tasks_sha == seed_tip
    assert _git("-C", o, "merge-base", "--is-ancestor", tasks_sha,
                "main").returncode == 0
    rec = inrepo.reconcile(o, ttl_s=1800)
    assert rec["healthy"] == [TASK]


# ------------------------------------------------- wave 8 (zombie seed)

def test_seed_refuses_at_sign_task_names_instead_of_zombieing(lane, monkeypatch):
    """Wave 8 (INT-013, 2026-09-29): the archive-marker law embeds @att
    in refnames, so every lane read (open_tasks, sweep, audit) excludes
    @-carrying refs — but seed() pushed specs for ANY json key. An
    @-named task seeded today becomes a PERMANENT zombie: the spec ref
    lands (seeded:true), open_tasks never shows it, and its claim is
    un-sweepable (sweep's live list filters @). Refuse loudly at the
    entry points instead (seed + claim_detail defense in depth).

    HERMETICITY (incident #2, disclosed in the wave-8 commit): seed()
    and claim_detail() push via the module-level ORIGIN (no explicit
    origin parameter), so this test MUST re-point inrepo.ORIGIN at the
    local bare origin — the first cut ran in-parent and seeded the fake
    tasks onto the real example-host-a origin (contained: both refs deleted
    within minutes, zero claims ever existed)."""
    o, w, stub = lane
    monkeypatch.setattr(inrepo, "ORIGIN", o)
    spec = {TASK: f"spec {TASK}\nverify: true", "a@b": "spec a@b"}
    sp = stub / "spec.json"
    sp.write_text(json.dumps(spec))
    res = inrepo.seed(str(sp))
    assert res[TASK] is True                   # safe names still seed
    assert str(res["a@b"]).startswith("refused")  # hostile name refused
    assert "refs/swarm/specs/a@b" not in _refs(o)
    assert f"refs/swarm/specs/{TASK}" in _refs(o)
    # defense in depth: a direct claim of an @-name cannot land either
    d = inrepo.claim_detail("w1", "a@b")
    assert d["att"] is None
    assert "refs/swarm/claims/a@b" not in _refs(o)
