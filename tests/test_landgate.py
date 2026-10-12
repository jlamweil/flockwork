"""Contract for the flockwork settle/land gate (l2/landgate.py).

The authorization leg the merit leg cannot give: before a task is settled
(write_verdict) or a fix published (land_fix), the principal must present a
token validly signed under the gate's secret whose role may settle. The
substrate's own law is that the client is never trusted to grant itself the
tier, so this suite pins every falsifiable property of the gate:

- no unsigned fallback: a missing secret, a missing token, an unsigned /
  expired / forged token all default-deny (the lakekit.enforce contract);
- the tier boundary: a read-only (public) worker cannot settle; team+ can;
- merit binds a fixed claim: fixed:true with a failing (or unknown) oracle
  is refused, while an honest fixed:false needs authorization only;
- the write_verdict hook is env-gated and OFF by default (the pre-gate law
  is byte-identical, so the existing write_verdict suite is untouched) and
  fail-closed when enabled.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import threading
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]

SECRET = "s3cr3t"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "l2" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m  # register so @dataclass field resolution finds the module
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _make_origin(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(seed))
    (seed / "f.txt").write_text("v1\n")
    _git("-C", str(seed), "add", "-A")
    _git("-C", str(seed), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    _git("-C", str(seed), "-c", "user.email=a@b", "-c", "user.name=a",
         "push", "-q", str(o), "main")
    return str(o)


def _verdict_ref(o, task):
    r = _git("ls-remote", o, f"refs/swarm/verdicts/{task}")
    return r.stdout.split()[0] if r.stdout.strip() else None


# --------------------------------------------------------- settle_check contract


def test_allow_owner_fixed_with_passing_oracle():
    m = _load("landgate")
    tok = m.sign_token("owner", "agent-1", SECRET)
    v = m.settle_check(tok, SECRET, fixed=True, oracle_rc=0)
    assert v.allowed and v.role == "owner"


def test_allow_team_fixed_with_passing_oracle():
    m = _load("landgate")
    tok = m.sign_token("team", "agent-2", SECRET)
    assert m.settle_check(tok, SECRET, fixed=True, oracle_rc=0).allowed


def test_allow_honest_fixed_false_needs_no_oracle():
    m = _load("landgate")
    tok = m.sign_token("owner", "agent-1", SECRET)
    assert m.settle_check(tok, SECRET, fixed=False).allowed


def test_deny_no_token_client_bypass():
    m = _load("landgate")
    v = m.settle_check(None, SECRET)
    assert not v.allowed and "client bypass" in v.reason


def test_deny_no_secret_server_default_deny():
    m = _load("landgate")
    tok = m.sign_token("owner", "a", SECRET)
    v = m.settle_check(tok, "")
    assert not v.allowed and "no verification secret" in v.reason


def test_deny_forged_signature():
    m = _load("landgate")
    v = m.settle_check("owner:a:999999999999:deadbeef", SECRET)
    assert not v.allowed and "signature mismatch" in v.reason


def test_deny_unsigned_token():
    m = _load("landgate")
    v = m.settle_check("owner:a", SECRET)
    assert not v.allowed


def test_deny_expired_token():
    m = _load("landgate")
    tok = m.sign_token("owner", "a", SECRET, ttl_seconds=-10)
    v = m.settle_check(tok, SECRET)
    assert not v.allowed and "expired" in v.reason


def test_deny_readonly_public_tier_cannot_settle():
    m = _load("landgate")
    tok = m.sign_token("public", "a", SECRET)
    v = m.settle_check(tok, SECRET, fixed=False)
    assert not v.allowed and "not in tier" in v.reason


def test_deny_fixed_with_failing_oracle():
    m = _load("landgate")
    tok = m.sign_token("owner", "a", SECRET)
    v = m.settle_check(tok, SECRET, fixed=True, oracle_rc=1)
    assert not v.allowed and "oracle failed" in v.reason


def test_deny_fixed_with_unknown_oracle():
    m = _load("landgate")
    tok = m.sign_token("owner", "a", SECRET)
    v = m.settle_check(tok, SECRET, fixed=True, oracle_rc=None)
    assert not v.allowed and "fail-closed" in v.reason


# --------------------------------------------------------------------- CLI contract


def test_cli_settle_allow_exit0(capsys):
    m = _load("landgate")
    tok = m.sign_token("owner", "agent-1", SECRET)
    rc = m.main(["settle", "--token", tok, "--fixed", "--oracle-rc", "0",
                 "--secret", SECRET])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["allowed"] is True


def test_cli_settle_deny_exit2(capsys):
    m = _load("landgate")
    rc = m.main(["settle", "--token", "owner:a:999999999999:deadbeef",
                 "--fixed", "--oracle-rc", "0", "--secret", SECRET])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["allowed"] is False


# ------------------------------------------------- write_verdict hook (env-gated)


def test_hook_off_by_default_writes_verdict(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOCKWORK_LAND_GATE", raising=False)
    m = _load("inrepo")
    o = _make_origin(tmp_path)
    r = m.write_verdict(o, "T", "att1", True, "w", 0, 0, None)
    assert m.ok(r) and _verdict_ref(o, "T")


def test_hook_on_valid_token_passing_oracle_writes(tmp_path, monkeypatch):
    g = _load("landgate")
    m = _load("inrepo")
    o = _make_origin(tmp_path)
    tok = g.sign_token("owner", "agent-1", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_GATE", "1")
    monkeypatch.setenv("FLOCKWORK_LAND_SECRET", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_TOKEN", tok)
    r = m.write_verdict(o, "T", "att1", True, "w", 0, 0, None)
    assert m.ok(r) and _verdict_ref(o, "T")


def test_hook_on_forged_token_refuses(tmp_path, monkeypatch):
    m = _load("inrepo")
    o = _make_origin(tmp_path)
    monkeypatch.setenv("FLOCKWORK_LAND_GATE", "1")
    monkeypatch.setenv("FLOCKWORK_LAND_SECRET", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_TOKEN",
                       "owner:a:999999999999:deadbeef")
    r = m.write_verdict(o, "T", "att1", True, "w", 0, 0, None)
    assert not m.ok(r) and _verdict_ref(o, "T") is None


def test_hook_on_valid_token_but_oracle_failed_refuses(tmp_path, monkeypatch):
    g = _load("landgate")
    m = _load("inrepo")
    o = _make_origin(tmp_path)
    tok = g.sign_token("owner", "agent-1", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_GATE", "1")
    monkeypatch.setenv("FLOCKWORK_LAND_SECRET", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_TOKEN", tok)
    r = m.write_verdict(o, "T", "att1", True, "w", 0, 1, None)
    assert not m.ok(r) and _verdict_ref(o, "T") is None


def test_hook_on_honest_false_with_valid_token_writes(tmp_path, monkeypatch):
    g = _load("landgate")
    m = _load("inrepo")
    o = _make_origin(tmp_path)
    tok = g.sign_token("owner", "agent-1", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_GATE", "1")
    monkeypatch.setenv("FLOCKWORK_LAND_SECRET", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_TOKEN", tok)
    r = m.write_verdict(o, "T", "att1", False, "w", 1, 0, None)
    assert m.ok(r) and _verdict_ref(o, "T")


def test_hook_explicit_gate_token_beats_absent_env(tmp_path, monkeypatch):
    """An explicitly presented gate_token is checked even when the env token
    is absent (the seat presents its own token via the MCP arg)."""
    g = _load("landgate")
    m = _load("inrepo")
    o = _make_origin(tmp_path)
    tok = g.sign_token("owner", "agent-1", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_GATE", "1")
    monkeypatch.setenv("FLOCKWORK_LAND_SECRET", "hooksecret")
    monkeypatch.delenv("FLOCKWORK_LAND_TOKEN", raising=False)
    r = m.write_verdict(o, "T", "att1", True, "w", 0, 0, None, gate_token=tok)
    assert m.ok(r) and _verdict_ref(o, "T")


def test_hook_explicit_forged_token_refuses_even_with_valid_env(tmp_path, monkeypatch):
    """The PRESENTED gate_token is what's checked: a forged explicit token
    refuses even when a valid env token is also present."""
    g = _load("landgate")
    m = _load("inrepo")
    o = _make_origin(tmp_path)
    good = g.sign_token("owner", "agent-1", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_GATE", "1")
    monkeypatch.setenv("FLOCKWORK_LAND_SECRET", "hooksecret")
    monkeypatch.setenv("FLOCKWORK_LAND_TOKEN", good)
    r = m.write_verdict(o, "T", "att1", True, "w", 0, 0, None,
                        gate_token="owner:a:999999999999:deadbeef")
    assert not m.ok(r) and _verdict_ref(o, "T") is None


# --------------------------------------------------------------- gate host (serve)
# The un-bypassable control plane: holds the secret + does the privileged
# settle/land. Exercised here on an ephemeral port against a scratch board.


def _post(url, obj):
    req = urllib.request.Request(
        url, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def _start_host(m, secret):
    httpd, port = m._make_gate_host(0, secret)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", httpd


def test_gate_host_settle_valid_writes_verdict(tmp_path):
    m = _load("landgate")
    url, httpd = _start_host(m, "hostsecret")
    try:
        o = _make_origin(tmp_path)
        tok = m.sign_token("owner", "agent-1", "hostsecret")
        out = _post(url + "/settle", {"origin": o, "task": "T", "att": "att1",
                                      "fixed": True, "oracle_rc": 0,
                                      "token": tok})
        assert out["settled"] is True
        assert _verdict_ref(o, "T")
    finally:
        httpd.shutdown()


def test_gate_host_settle_forged_refuses_no_verdict(tmp_path):
    m = _load("landgate")
    url, httpd = _start_host(m, "hostsecret")
    try:
        o = _make_origin(tmp_path)
        out = _post(url + "/settle", {"origin": o, "task": "T", "att": "att1",
                                      "fixed": True, "oracle_rc": 0,
                                      "token": "owner:a:999999999999:deadbeef"})
        assert out["settled"] is False
        assert _verdict_ref(o, "T") is None
    finally:
        httpd.shutdown()


def test_gate_host_settle_valid_token_oracle_fail_refuses(tmp_path):
    m = _load("landgate")
    url, httpd = _start_host(m, "hostsecret")
    try:
        o = _make_origin(tmp_path)
        tok = m.sign_token("owner", "agent-1", "hostsecret")
        out = _post(url + "/settle", {"origin": o, "task": "T", "att": "att1",
                                      "fixed": True, "oracle_rc": 1,
                                      "token": tok})
        assert out["settled"] is False and "oracle" in out["reason"]
        assert _verdict_ref(o, "T") is None
    finally:
        httpd.shutdown()


def test_gate_host_land_valid_publishes(tmp_path):
    m = _load("landgate")
    url, httpd = _start_host(m, "hostsecret")
    try:
        o = _make_origin(tmp_path)
        w = tmp_path / "work"
        _git("clone", "-q", "-b", "main", o, str(w))
        (w / "fix.py").write_text("x = 1\n")
        tok = m.sign_token("team", "agent-1", "hostsecret")
        out = _post(url + "/land", {"origin": o, "tree": str(w),
                                    "task": "T", "att": "att1", "token": tok})
        assert out["landed"] is True
        chk = tmp_path / "chk"
        _git("clone", "-q", "-b", "main", o, str(chk))
        assert (chk / "fix.py").exists()
    finally:
        httpd.shutdown()


def test_gate_host_land_forged_refuses_no_publish(tmp_path):
    m = _load("landgate")
    url, httpd = _start_host(m, "hostsecret")
    try:
        o = _make_origin(tmp_path)
        w = tmp_path / "work"
        _git("clone", "-q", "-b", "main", o, str(w))
        (w / "fix.py").write_text("x = 1\n")
        out = _post(url + "/land", {"origin": o, "tree": str(w),
                                    "task": "T", "att": "att1",
                                    "token": "owner:a:999999999999:deadbeef"})
        assert out["landed"] is False
        chk = tmp_path / "chk"
        _git("clone", "-q", "-b", "main", o, str(chk))
        assert not (chk / "fix.py").exists()
    finally:
        httpd.shutdown()


# --------------------------------------- inrepo client mode (delegate to host)
# FLOCKWORK_LAND_GATE_URL set -> write_verdict / land_fix route the settle
# through the gate host (which does the privileged push) instead of locally.


def test_client_mode_settle_delegates_to_host(tmp_path, monkeypatch):
    m = _load("landgate")
    inr = _load("inrepo")
    url, httpd = _start_host(m, "hostsecret")
    try:
        o = _make_origin(tmp_path)
        tok = m.sign_token("owner", "agent-1", "hostsecret")
        monkeypatch.setenv("FLOCKWORK_LAND_GATE_URL", url)
        r = inr.write_verdict(o, "T", "att1", True, "w", 0, 0, None,
                              gate_token=tok)
        assert inr.ok(r) and _verdict_ref(o, "T")
    finally:
        httpd.shutdown()


def test_client_mode_settle_forged_refused(tmp_path, monkeypatch):
    m = _load("landgate")
    inr = _load("inrepo")
    url, httpd = _start_host(m, "hostsecret")
    try:
        o = _make_origin(tmp_path)
        monkeypatch.setenv("FLOCKWORK_LAND_GATE_URL", url)
        r = inr.write_verdict(o, "T", "att1", True, "w", 0, 0, None,
                              gate_token="owner:a:999999999999:deadbeef")
        assert not inr.ok(r) and _verdict_ref(o, "T") is None
    finally:
        httpd.shutdown()


def test_client_mode_settle_unreachable_host_fails_closed(tmp_path, monkeypatch):
    inr = _load("inrepo")
    o = _make_origin(tmp_path)
    # a closed local port -> the delegation fails CLOSED (refuse), it never
    # writes a verdict locally (the whole point of routing to the host).
    monkeypatch.setenv("FLOCKWORK_LAND_GATE_URL", "http://127.0.0.1:1/")
    r = inr.write_verdict(o, "T", "att1", True, "w", 0, 0, None,
                          gate_token="whatever")
    assert not inr.ok(r) and _verdict_ref(o, "T") is None
