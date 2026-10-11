"""Contracts for the external-work CLI verbs (C5).

The plugin (C9) shells out to these thin verbs, each of which owns exactly one
substrate op + prints a stable JSON result + a meaningful exit code:
  brief <task>              the task's brief (work instruction + verify)
  claim <task>              create-once claim -> att
  judge <task> <tree> [v]   run the oracle in the tree -> {rc, timed_out, ...}
  land <task> <att> <tree>  land a scoped fix -> {main_pushed, ...}
  verdict <task> <att> ...  write the terminal verdict
  renew <task> <att>        re-lease a live claim
  work-started / work-done  the [work] leg checkpoints
  work-external <task> <att> <tree> [paths...]
                            the full external-work attempt

Each verb works standalone against a test origin; missing args are a clean
usage error (exit 2), never a traceback.
"""
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
LAW = REPO / "l2" / "inrepo.py"


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return ["-c", "user.email=a@b", "-c", "user.name=a"]


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    return _git("-C", w, *_cid(), "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    _git("-C", w, *_cid(), "push", "-q", o, f"{src}:{ref}")


def _run(*args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run(
        [sys.executable, str(LAW), *args],
        cwd=str(REPO), env=e, capture_output=True, text=True, timeout=120,
    )


def _lane(tmp_path, att="att-verb0001"):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(seed))
    (seed / "app.py").write_text("X = 0\n")
    _git("-C", str(seed), "add", "-A")
    _git("-C", str(seed), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(seed), *_cid(), "push", "-q", str(o), "main")
    _push(str(o), str(seed),
          _root_commit(str(seed), "do the thing\nverify: true"),
          "refs/swarm/specs/T")
    _push(str(o), str(seed),
          _root_commit(str(seed), f"claim T\nharness: opencode\n{att}"),
          "refs/swarm/claims/T")
    w = tmp_path / "work"
    _git("clone", "-q", "-b", "main", str(o), str(w))
    _git("-C", str(w), "config", "user.email", "w1@swarm")
    _git("-C", str(w), "config", "user.name", "w1")
    return str(o), w, att


def _refs(o):
    return _git("ls-remote", o).stdout


def test_brief_prints_the_work_instruction(tmp_path):
    o, w, att = _lane(tmp_path)
    r = _run("brief", "T", env={"SWARM_ORIGIN": o})
    assert r.returncode == 0, r.stderr
    assert "do the thing" in r.stdout
    assert "verify: true" in r.stdout


def test_brief_absent_spec_is_a_clean_refusal(tmp_path):
    o, w, att = _lane(tmp_path)
    r = _run("brief", "NOPE", env={"SWARM_ORIGIN": o})
    assert r.returncode == 1  # not a traceback, not exit 2
    assert json.loads(r.stdout)["reason"] == "spec absent"


def test_claim_and_renew_verbs(tmp_path):
    o, w, att = _lane(tmp_path)
    env = {"SWARM_ORIGIN": o, "SWARM_HOST": "w1"}
    r = _run("renew", "T", att, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["renew"] is True
    # a wrong att is a clean refusal (exit 1), not a crash
    r2 = _run("renew", "T", "att-wrong", env=env)
    assert r2.returncode == 1
    assert json.loads(r2.stdout)["renew"] is False


def test_judge_verdict_land_verbs(tmp_path):
    o, w, att = _lane(tmp_path)
    env = {"SWARM_ORIGIN": o, "SWARM_HOST": "w1"}
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    # judge: the oracle runs in the tree
    rj = _run("judge", "T", str(w), "true", env=env)
    assert rj.returncode == 0
    assert json.loads(rj.stdout)["rc"] == 0
    # land: the scoped fix commits + pushes main + the return ref
    rl = _run("land", "T", att, str(w), "fix.py", env=env)
    assert rl.returncode == 0
    out = json.loads(rl.stdout)
    assert out["main_pushed"] is True and out["return_pushed"] is True
    # verdict: the terminal fixed:true
    rv = _run("verdict", "T", att, "true", "0", "0", env=env)
    assert rv.returncode == 0
    assert json.loads(rv.stdout)["verdict_pushed"] is True
    refs = _refs(o)
    assert "refs/swarm/tasks/T" in refs
    assert "refs/swarm/verdicts/T" in refs


def test_work_external_verb(tmp_path):
    o, w, att = _lane(tmp_path)
    env = {"SWARM_ORIGIN": o, "SWARM_HOST": "w1"}
    (w / "fix.py").write_text("def fix():\n    return 1\n")
    r = _run("work-external", "T", att, str(w), "fix.py", env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["fixed"] is True and out["return_pushed"] is True
    assert "refs/swarm/verdicts/T" in _refs(o)


def test_missing_args_is_a_clean_usage_error(tmp_path):
    o, w, att = _lane(tmp_path)
    for verb, args in (("brief", ("brief",)), ("claim", ("claim",)),
                       ("land", ("land", "T")), ("verdict", ("verdict", "T")),
                       ("work-external", ("work-external", "T"))):
        r = _run(*args, env={"SWARM_ORIGIN": o})
        assert r.returncode == 2, (verb, r.stderr)
        assert "usage:" in r.stderr, (verb, r.stderr)
