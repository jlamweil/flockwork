"""Contract: there is NO default origin (WQ-093, INT-085).

The law's default at l2/inrepo.py used to be the PRODUCTION board, so
any seat that forgot SWARM_ORIGIN pushed seed/verdict/spec refs straight
into production — several near-misses. The WQ-093 law:

  - the module constant ORIGIN carries NO default (None when
    SWARM_ORIGIN was unset at import) and the production URL appears
    NOWHERE in lane source;
  - every origin resolution is explicit-arg > SWARM_ORIGIN at CALL
    time > import-time ORIGIN > OriginUnsetError naming the fix;
  - a write leg with no origin anywhere REFUSES before any git/ssh
    contact; reads refuse the same way (the audit table, WQ-093
    receipt: no live caller class relied on the default);
  - the suite's own inrepo.ORIGIN-monkeypatch pattern (the wave-8
    discipline) keeps working — the module attribute stays a legal
    resolution source.

Every test here is hermetic: the production host is never contacted
(ssh is a PATH stub that records and refuses); boards are local bare
repos under tmp_path.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
PROD_URL = "ssh://board-host.example.invalid/boards/prod-board.git"

CHILD = r"""
import importlib.util, json, os, pathlib, subprocess, sys, traceback

mode = sys.argv[1]
tmp = pathlib.Path(sys.argv[2])

import l2.inrepo as inrepo

out = {"mode": mode}

if mode == "module-default":
    out["origin"] = inrepo.ORIGIN

elif mode == "write-refusal":
    spec = tmp / "spec.json"
    spec.write_text(json.dumps({"T-wq093": "brief"}))
    try:
        inrepo.seed(str(spec))
        out["raised"] = None
    except Exception as e:
        out["raised"] = type(e).__name__
        out["msg"] = str(e)

elif mode == "read-refusal":
    try:
        inrepo.open_tasks()
        out["raised"] = None
    except Exception as e:
        out["raised"] = type(e).__name__
        out["msg"] = str(e)

elif mode == "attr-pattern":
    sandbox = tmp / "sandbox.git"
    spec = tmp / "spec.json"
    spec.write_text(json.dumps({"T-wq093": "brief"}))
    inrepo.ORIGIN = str(sandbox)
    inrepo.seed(str(spec))
    r = subprocess.run(["git", "ls-remote", str(sandbox)],
                       capture_output=True, text=True)
    out["refs"] = r.stdout

elif mode == "call-time-env":
    a, b = tmp / "a.git", tmp / "b.git"
    spec = tmp / "spec.json"
    spec.write_text(json.dumps({"T-wq093": "brief"}))
    os.environ["SWARM_ORIGIN"] = str(b)  # re-bound AFTER import
    inrepo.seed(str(spec))
    ra = subprocess.run(["git", "ls-remote", str(a)],
                        capture_output=True, text=True)
    rb = subprocess.run(["git", "ls-remote", str(b)],
                        capture_output=True, text=True)
    out["a"] = ra.stdout
    out["b"] = rb.stdout

print(json.dumps(out))
"""


def _bare(path: pathlib.Path) -> pathlib.Path:
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
    )
    return path


def _child(mode: str, tmp_path: pathlib.Path, env_extra: dict) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("SWARM_ORIGIN", "FLOCKWORK_METRICS")
    }
    env["PYTHONPATH"] = str(REPO)
    env.update(env_extra)
    r = subprocess.run(
        [sys.executable, "-c", CHILD, mode, str(tmp_path)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, f"child failed: {r.stderr[-2000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _ssh_stub(tmp_path: pathlib.Path) -> dict:
    """A PATH-leading `ssh` that records every contact and refuses —
    the board host is never reached (rc 255, git push/ls-remote fails
    rc 128 without any network)."""
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    log = tmp_path / "ssh-contacts.log"
    stub = stub_dir / "ssh"
    stub.write_text(
        "#!/bin/bash\n"
        f'printf \'%s\\n\' "$*" >> "{log}"\n'
        "exit 255\n"
    )
    stub.chmod(0o755)
    return {"log": log, "env": {"PATH": f"{stub_dir}:{os.environ['PATH']}"}}


def test_no_production_default_in_module(tmp_path):
    """The import-time constant carries NO default: unset env -> None.
    (Pre-fix this was the production board URL — the defect of
    record.)"""
    out = _child("module-default", tmp_path, {})
    assert out["origin"] is None, (
        f"lane still ships a default origin: {out['origin']!r}"
    )


def test_production_url_absent_from_lane_source():
    """The production URL appears nowhere in l2/ source — the default
    must be gone, not merely shadowed. (The scp-form docstring's
    solve-metrics example is a different board and stays legal.)"""
    for p in (REPO / "l2").glob("*.py"):
        assert PROD_URL not in p.read_text(), f"{p.name} still names it"


def test_write_leg_refuses_when_origin_unset(tmp_path):
    """seed() with no origin anywhere REFUSES with a typed error naming
    the fix, BEFORE any git/ssh contact. (Pre-fix: the push silently
    targeted the production default — the stub records the contact,
    which is the RED.)"""
    stub = _ssh_stub(tmp_path)
    out = _child("write-refusal", tmp_path, stub["env"])
    assert out["raised"] == "OriginUnsetError", (
        f"expected typed refusal, got raised={out['raised']!r}"
    )
    assert "SWARM_ORIGIN" in out.get("msg", "")
    assert not stub["log"].exists(), (
        "unset-origin seed REACHED FOR THE DEFAULT ORIGIN over ssh "
        f"(recorded: {stub['log'].read_text()[:300]})"
    )


def test_read_leg_refuses_when_origin_unset(tmp_path):
    """open_tasks() with no origin anywhere refuses the same typed way,
    with zero contact — reads keep no default either (audit: no live
    caller class relied on the read default)."""
    stub = _ssh_stub(tmp_path)
    out = _child("read-refusal", tmp_path, stub["env"])
    assert out["raised"] == "OriginUnsetError", (
        f"expected typed refusal, got raised={out['raised']!r}"
    )
    assert "SWARM_ORIGIN" in out.get("msg", "")
    assert not stub["log"].exists(), (
        "unset-origin read REACHED FOR THE DEFAULT ORIGIN over ssh "
        f"(recorded: {stub['log'].read_text()[:300]})"
    )


def test_gate_write_refuses_when_origin_unset(tmp_path):
    """gates.review_verdict with no orig and no env refuses typed — the
    literal-'origin' remote-name default (the gate family's own footgun)
    is gone with the rest."""
    child = r"""
import importlib.util, json, pathlib, sys
p = pathlib.Path(sys.argv[1]) / "l2" / "gates.py"
spec = importlib.util.spec_from_file_location("gates_wq093", p)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
try:
    g.review_verdict("T-wq093", "rev-a", "agree", "refs/swarm/specs/T-wq093")
    print(json.dumps({"raised": None}))
except Exception as e:
    print(json.dumps({"raised": type(e).__name__, "msg": str(e)}))
"""
    env = {k: v for k, v in os.environ.items() if k != "SWARM_ORIGIN"}
    env["PATH"] = _ssh_stub(tmp_path)["env"]["PATH"]
    r = subprocess.run(
        [sys.executable, "-c", child, str(REPO)],
        cwd=tmp_path,  # a cwd with no git remotes at all
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["raised"] == "OriginUnsetError", (
        f"expected typed refusal, got raised={out['raised']!r} "
        f"msg={out.get('msg', '')!r}"
    )
    assert "SWARM_ORIGIN" in out.get("msg", "")


def test_module_attr_pattern_still_writes(tmp_path):
    """Overcorrection guard, green by design on BOTH sides of the fix:
    the suite's wave-8 discipline (monkeypatch inrepo.ORIGIN) remains a
    legal resolution source when the env is unset."""
    sandbox = _bare(tmp_path / "sandbox.git")
    out = _child("attr-pattern", tmp_path, {})
    assert "refs/swarm/specs/T-wq093" in out["refs"], (
        "the ORIGIN-attr pattern broke — the suite's own per-file-module "
        "pattern must keep working"
    )
    assert sandbox.exists()


def test_call_time_env_wins_over_import(tmp_path):
    """The T5 split-brain is dead: SWARM_ORIGIN re-bound AFTER import is
    honored by the write legs (pre-fix, seed/claim/verdict pushes went
    to the IMPORT-time constant — the wave-6 incident class)."""
    a = _bare(tmp_path / "a.git")
    b = _bare(tmp_path / "b.git")
    env = {"SWARM_ORIGIN": str(a)}  # bound at import...
    out = _child("call-time-env", tmp_path, env)
    assert "refs/swarm/specs/T-wq093" not in out["a"], (
        "seed followed the IMPORT-time binding, not the call-time env"
    )
    assert "refs/swarm/specs/T-wq093" in out["b"], (
        "the call-time re-bind was not honored by the write leg"
    )
