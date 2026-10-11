"""Contract for correct model attribution (write-side).

The dispatched model is `SWARM_MODEL`, the exact `-m <model>` string the
worker hands opencode. It is recorded into the ref BODIES — the claim (at
claim time, the pre-dispatch checkpoint) and the verdict (redundantly, as
the authoritative record) — as a structured `model:` line, so the model is
durable on the checkpoint that survives a dispatch death and readable for
every attempt (archived, live, final).

The load-bearing invariant (F5): the att stays the LAST token of the claim
body. Every att reader — sweep, relabel, correction_graph, audit — extracts
it as `body.split()[-1]`; a `model:` line may precede the att, never follow
it. These tests pin that.

Hermetic: boards are local bare repos under tmp_path; the model is set via
env; the production host is never contacted.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_inrepo():
    p = REPO / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_ma", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return ["-c", "user.email=a@b", "-c", "user.name=a"]


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    return _git("-C", w, *_cid(), "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    r = _git("-C", w, *_cid(), "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


def _body(o, ref):
    return _git("-C", str(o), "log", "-1", "--format=%B", ref).stdout


def _lane(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "s")
    _git("-C", str(w), *_cid(), "push", "-q", str(o), "main")
    return str(o), str(w)


SUB_DRIVER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('inrepo', 'l2/inrepo.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
try:
    print(json.dumps(m.work_task(sys.argv[1], sys.argv[2], sys.argv[3])))
except Exception as e:
    print(json.dumps({'event': 'driver_exception', 'type': type(e).__name__}))
"""


def _run_work_task(env_extra, task, att, worker="w2"):
    env = dict(os.environ, **env_extra)
    r = subprocess.run(
        [sys.executable, "-c", SUB_DRIVER, worker, task, att],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=300,
    )
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------ write-side


def test_verdict_body_records_model(monkeypatch):
    """The verdict body carries the dispatched model as `model: <model>`."""
    inrepo = _load_inrepo()
    monkeypatch.setenv("SWARM_MODEL", "t3/coder-coder")
    body = inrepo.verdict_body("T", "att-w1-abcd12", True, "w1", 0, 0)
    assert "model: t3/coder-coder" in body
    assert "attempt: att-w1-abcd12" in body  # unchanged fields intact


def test_claim_body_records_model_before_att(monkeypatch, tmp_path):
    """claim_detail writes `model: <model>` into the claim body with the att
    still the last token (the F5 invariant)."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_MODEL", "t3/coder-coder")
    monkeypatch.chdir(w)
    res = inrepo.claim_detail("w1", "T-claim")
    assert res["att"] is not None
    body = _body(o, "refs/swarm/claims/T-claim")
    assert "model: t3/coder-coder" in body
    assert body.strip().split()[-1] == res["att"]  # att is LAST
    lines = body.splitlines()
    model_i = next(i for i, l in enumerate(lines) if l.startswith("model:"))
    att_i = next(i for i, l in enumerate(lines) if l.strip() == res["att"])
    assert model_i < att_i  # model line precedes the att line


def test_claim_body_empty_model_omits_line(monkeypatch, tmp_path):
    """An empty SWARM_MODEL yields no misleading `model:` line; the att
    stays the last token."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_MODEL", "")
    monkeypatch.chdir(w)
    res = inrepo.claim_detail("w1", "T-empty")
    body = _body(o, "refs/swarm/claims/T-empty")
    assert not any(l.strip().startswith("model:") for l in body.splitlines())
    assert body.strip().split()[-1] == res["att"]


def test_att_last_invariant_readers(monkeypatch, tmp_path):
    """Guard (F5): with a 3-line claim body (model before att), the readers
    still recover the att. A future reorder (model after att) breaks both."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    att = "att-mm-coder-x-deadbeef"
    _push(o, w, _root_commit(w, "the brief\nverify: true"),
          "refs/swarm/specs/T-inv")
    _push(o, w, _root_commit(w, f"claim T-inv\nmodel: t3/coder-coder\n{att}"),
          "refs/swarm/claims/T-inv")
    inrepo = _load_inrepo()
    # correction_graph recovers the att from the body
    g = inrepo.correction_graph(o)
    assert att in [a["att"] for a in g["tasks"]["T-inv"]["attempts"]]
    # sweep (att=None -> reads the claim body's last token) archives under it
    sw = inrepo.sweep(o, "T-inv")
    assert sw["att"] == att


# ------------------------------------------------------------ read-side


def test_graph_model_from_body(monkeypatch, tmp_path):
    """The graph reads the model from the ref body (the exact dispatched
    model), not from the att token."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-g")
    _push(o, w, _root_commit(
        w, "claim T-g\nmodel: t3/coder-coder\natt-w1-aaaa01"),
        "refs/swarm/claims/T-g")
    _push(o, w, _root_commit(
        w, "verdict\ntask: T-g\nattempt: att-w1-aaaa01\nfixed: true\n"
           "host: w1\noc_rc: 0\npytest_rc: 0\nmodel: t3/coder-coder"),
        "refs/swarm/verdicts/T-g")
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    atts = {a["att"]: a["model"] for a in g["tasks"]["T-g"]["attempts"]}
    assert atts["att-w1-aaaa01"] == "t3/coder-coder"


def test_graph_mm_att_uses_body_not_split(monkeypatch, tmp_path):
    """An mm-* att is attributed from the body's model, never from the
    att-token split (the old mm_model_of misparse). The retired function
    is gone."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-mm")
    _push(o, w, _root_commit(
        w, "claim T-mm\nmodel: t3/coder-coder\natt-mm-coder-a-deadbeef"),
        "refs/swarm/claims/T-mm")
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    atts = {a["att"]: a["model"] for a in g["tasks"]["T-mm"]["attempts"]}
    # body model wins; the split would have yielded "coder"
    assert atts["att-mm-coder-a-deadbeef"] == "t3/coder-coder"
    assert not hasattr(inrepo, "mm_model_of")


def test_graph_unknown_fallback(monkeypatch, tmp_path):
    """A pre-protocol claim body (no `model:` line) attributes honestly as
    'unknown', never a guess."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-unk")
    _push(o, w, _root_commit(w, "claim T-unk att-w1-aaaa01"),
          "refs/swarm/claims/T-unk")  # legacy one-line body, no model
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    atts = {a["att"]: a["model"] for a in g["tasks"]["T-unk"]["attempts"]}
    assert atts["att-w1-aaaa01"] == "unknown"


# ------------------------------------------------------------ chain/direct flag


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # now a closed port: connecting to it is refused (fast)
    return port


def _router_server(config, counter):
    import http.server
    import threading

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            counter["n"] = counter.get("n", 0) + 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(config).encode())

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_kind_chain_from_router(monkeypatch):
    inrepo = _load_inrepo()
    cfg = {"model_list": [
        {"model_name": "t3/tier-coder", "litellm_params": {"model": "openai/a"}},
        {"model_name": "t3/tier-coder", "litellm_params": {"model": "openai/b"}},
    ]}
    counter = {}
    srv, url = _router_server(cfg, counter)
    try:
        monkeypatch.setenv("SWARM_ROUTER_URL", url)
        assert inrepo._model_kind("t3/tier-coder") == "chain"
        assert counter["n"] >= 1
    finally:
        srv.shutdown()


def test_kind_direct_from_router(monkeypatch):
    inrepo = _load_inrepo()
    cfg = {"model_list": [
        {"model_name": "gemini/gemini-3.5-flash",
         "litellm_params": {"model": "gemini/gemini-3.5-flash"}},
    ]}
    counter = {}
    srv, url = _router_server(cfg, counter)
    try:
        monkeypatch.setenv("SWARM_ROUTER_URL", url)
        assert inrepo._model_kind("gemini/gemini-3.5-flash") == "direct"
    finally:
        srv.shutdown()


def test_kind_unknown_when_router_down(monkeypatch):
    """A dead router degrades to 'unknown' fast — never a hang, never a
    fabricated kind."""
    inrepo = _load_inrepo()
    monkeypatch.setenv("SWARM_ROUTER_URL", f"http://127.0.0.1:{_free_port()}")
    assert inrepo._model_kind("any/model") == "unknown"


def test_kind_env_override_skips_query(monkeypatch):
    """SWARM_MODEL_KIND is an operator assertion — it classifies without
    touching the router at all."""
    inrepo = _load_inrepo()
    counter = {}
    srv, url = _router_server({"model_list": []}, counter)
    try:
        monkeypatch.setenv("SWARM_ROUTER_URL", url)
        monkeypatch.setenv("SWARM_MODEL_KIND", "chain")
        assert inrepo._model_kind("any/model") == "chain"
        assert counter.get("n", 0) == 0  # no router query
    finally:
        srv.shutdown()


def test_kind_cached_per_model(monkeypatch):
    """A worker handling many claims under one router pays one query, not
    one per claim: the second call for the same model is a cache hit."""
    inrepo = _load_inrepo()
    cfg = {"model_list": [
        {"model_name": "m1", "litellm_params": {"model": "openai/x"}},
    ]}
    counter = {}
    srv, url = _router_server(cfg, counter)
    try:
        monkeypatch.setenv("SWARM_ROUTER_URL", url)
        assert inrepo._model_kind("m1") == "direct"
        assert inrepo._model_kind("m1") == "direct"
        assert counter["n"] == 1  # one query for two calls
    finally:
        srv.shutdown()


def test_kind_recorded_in_claim_body(monkeypatch, tmp_path):
    """The claim body carries `kind:` after `model:`, att still last."""
    inrepo = _load_inrepo()
    cfg = {"model_list": [
        {"model_name": "t3/tier-coder", "litellm_params": {"model": "openai/a"}},
        {"model_name": "t3/tier-coder", "litellm_params": {"model": "openai/b"}},
    ]}
    counter = {}
    srv, url = _router_server(cfg, counter)
    o, w = _lane(tmp_path)
    try:
        monkeypatch.setenv("SWARM_ORIGIN", o)
        monkeypatch.setenv("SWARM_MODEL", "t3/tier-coder")
        monkeypatch.setenv("SWARM_ROUTER_URL", url)
        monkeypatch.chdir(w)
        res = inrepo.claim_detail("w1", "T-k")
        body = _body(o, "refs/swarm/claims/T-k")
        assert "model: t3/tier-coder" in body
        assert "kind: chain" in body
        assert body.strip().split()[-1] == res["att"]
    finally:
        srv.shutdown()


def test_graph_renders_kind(monkeypatch, tmp_path):
    """The graph surfaces the kind alongside the model."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-gk")
    _push(o, w, _root_commit(
        w, "claim T-gk\nmodel: m1\nkind: chain\natt-w1-aaaa01"),
        "refs/swarm/claims/T-gk")
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    a = g["tasks"]["T-gk"]["attempts"][0]
    assert a["model"] == "m1" and a["kind"] == "chain"


# ------------------------------------------------------------ debuggability


def test_pre_dispatch_death_carries_model_kind(tmp_path):
    """A pre-dispatch death (spec-fetch-failed) still reports the seat's
    model + kind — you can tell which model a seat was running when it died
    before doing work. No oc_elapsed (the dispatch never ran)."""
    o, w = _lane(tmp_path)
    att = "att-w2-nd0001"
    _push(o, w, _root_commit(w, f"claim T-nd {att}"), "refs/swarm/claims/T-nd")
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, SWARM_MODEL="t3/coder-coder",
             SWARM_MODEL_KIND="direct", SWARM_HEIR_MAX="1"),
        "T-nd", att)
    assert ev.get("event") != "driver_exception", ev
    assert ev.get("env_death") is True
    assert ev["model"] == "t3/coder-coder"
    assert ev["kind"] == "direct"
    assert "oc_elapsed" not in ev


def test_dispatch_event_carries_model_kind_oc_elapsed(tmp_path):
    """A dispatch-reaching event carries model + kind + the introduced
    oc_elapsed (wall-clock around the dispatch)."""
    o, w = _lane(tmp_path)
    stub = tmp_path / "stub-opencode"
    stub.write_text("#!/bin/bash\nsleep 0.2\ntouch work.txt\nexit 0\n")
    stub.chmod(0o755)
    TASK = "T-disp"
    _push(o, w, _root_commit(w, "do it\nverify: true"), f"refs/swarm/specs/{TASK}")
    att = "att-w2-ds0001"
    _push(o, w, _root_commit(w, f"claim {TASK} {att}"), f"refs/swarm/claims/{TASK}")
    ev = _run_work_task(
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub), SWARM_MODEL="t3/coder-coder",
             SWARM_MODEL_KIND="direct", SWARM_HEIR_MAX="1",
             SWARM_DISPATCH_TIMEOUT="30"),
        TASK, att)
    assert ev.get("event") != "driver_exception", ev
    assert ev["model"] == "t3/coder-coder"
    assert ev["kind"] == "direct"
    assert isinstance(ev.get("oc_elapsed"), (int, float)) and ev["oc_elapsed"] >= 0


def test_attribution_output_shape(monkeypatch, tmp_path):
    """The read-only attribution diagnostic: per task, a flat table of
    att | model | kind | verdict(fixed/oc_rc/pytest_rc) | archived | host."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-a")
    _push(o, w, _root_commit(
        w, "claim T-a\nmodel: t3/coder-coder\nkind: direct\natt-w1-aaaa01"),
        "refs/swarm/claims/T-a")
    _push(o, w, _root_commit(
        w, "verdict\ntask: T-a\nattempt: att-w1-aaaa01\nfixed: true\n"
           "host: w1\noc_rc: 0\npytest_rc: 0\nmodel: t3/coder-coder\n"
           "kind: direct"),
        "refs/swarm/verdicts/T-a")
    inrepo = _load_inrepo()
    out = inrepo.attribution(o)
    row = out["tasks"]["T-a"][0]
    for k in ("att", "model", "kind", "fixed", "oc_rc", "pytest_rc",
              "host", "archived"):
        assert k in row, (k, row)
    assert row["model"] == "t3/coder-coder" and row["kind"] == "direct"
    assert row["fixed"] == "true" and row["host"] == "w1"


# -------------------------------------------------------- L1 fail-closed


def test_worker_no_model_refuses_at_preflight(monkeypatch, tmp_path):
    """L1 (fail-closed model): a worker started with no SWARM_MODEL refuses
    at the seat preflight — a typed model_refusal event, exit 1, and no claim
    consumed — instead of dispatching to a stale default. The refusal is
    distinct from a claim/env death (it happens before any claim)."""
    o, w = _lane(tmp_path)
    # a spec the worker would claim (so there is real work to refuse over)
    _push(o, w, _root_commit(w, "demo\nverify: true"), "refs/swarm/specs/T-x")
    shim = tmp_path / "d.sh"
    shim.write_text("#!/bin/sh\nexit 0\n")
    shim.chmod(0o755)
    env = dict(os.environ, SWARM_ORIGIN=o, OPENCODE_BIN=str(shim))
    env.pop("SWARM_MODEL", None)
    r = subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"), "worker", "wm"],
        cwd=w, env=env, capture_output=True, text=True, timeout=120)
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    ref = [e for e in events if e.get("event") == "model_refusal"]
    assert ref and ref[0]["worker"] == "wm", events
    assert r.returncode == 1  # honest distinct exit
    assert not [e for e in events if e.get("event") == "attempted"]
    # no claim was consumed (the refusal is pre-claim)
    assert _git("-C", str(o), "for-each-ref",
                "refs/swarm/claims/T-x").stdout.strip() == ""


def test_swarm_model_default_is_empty(monkeypatch):
    """The stale GLM default is gone: with SWARM_MODEL unset, _swarm_model()
    returns the empty string (the preflight refuses it) — never a stale
    model id the harness cannot resolve."""
    inrepo = _load_inrepo()
    monkeypatch.delenv("SWARM_MODEL", raising=False)
    assert inrepo._swarm_model() == ""
    monkeypatch.setenv("SWARM_MODEL", "router/tier-coder")
    assert inrepo._swarm_model() == "router/tier-coder"


# ------------------------------------------------------- L2 dispatch seam


def test_dispatch_seam_opencode_contract(monkeypatch, tmp_path):
    """L2: _dispatch('opencode', ...) returns the same (rc, elapsed, err)
    contract the inlined leg had — success → rc 0; a failing stub → rc!=0
    with the stderr captured (the caller derives oc_err from it)."""
    inrepo = _load_inrepo()
    _git("init", "-q", "-b", "main", str(tmp_path / "w"))
    ok = tmp_path / "ok.sh"
    ok.write_text("#!/bin/sh\nexit 0\n")
    ok.chmod(0o755)
    monkeypatch.setenv("OPENCODE_BIN", str(ok))
    rc, elapsed, err = inrepo._dispatch("opencode", "m", "b", str(tmp_path / "w"))
    assert rc == 0 and elapsed >= 0 and err == ""
    bad = tmp_path / "bad.sh"
    bad.write_text("#!/bin/sh\necho boom >&2; exit 3\n")
    bad.chmod(0o755)
    monkeypatch.setenv("OPENCODE_BIN", str(bad))
    rc, elapsed, err = inrepo._dispatch("opencode", "m", "b", str(tmp_path / "w"))
    assert rc == 3 and "boom" in err


def test_dispatch_refusal_unknown_harness(monkeypatch, tmp_path):
    """L2: an unknown SWARM_DISPATCH is refused at the seat preflight — a
    typed dispatch_refusal, exit 1, no claim consumed (a config error must
    not burn a claim + heir budget mid-attempt)."""
    o, w = _lane(tmp_path)
    _push(o, w, _root_commit(w, "demo\nverify: true"), "refs/swarm/specs/T-x")
    shim = tmp_path / "d.sh"
    shim.write_text("#!/bin/sh\nexit 0\n")
    shim.chmod(0o755)
    env = dict(os.environ, SWARM_ORIGIN=o, SWARM_MODEL="stub/model",
               OPENCODE_BIN=str(shim), SWARM_DISPATCH="bogus")
    r = subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"), "worker", "wm"],
        cwd=w, env=env, capture_output=True, text=True, timeout=120)
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    ref = [e for e in events if e.get("event") == "dispatch_refusal"]
    assert ref and ref[0]["harness"] == "bogus", events
    assert r.returncode == 1
    assert not [e for e in events if e.get("event") == "attempted"]


def test_harness_stamped_on_bodies_and_event(monkeypatch, tmp_path):
    """L2: the dispatch harness is stamped on the verdict body, the claim
    body, and the attempt event, so attribution is harness-aware (per-
    harness durations become computable from the refs)."""
    inrepo = _load_inrepo()
    monkeypatch.setenv("SWARM_DISPATCH", "opencode")
    vb = inrepo.verdict_body("T", "att-w1-abcd12", True, "w1", 0, 0)
    assert "harness: opencode" in vb
    # claim body: harness present, att still the last token
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_MODEL", "t3/coder-coder")
    monkeypatch.chdir(w)
    res = inrepo.claim_detail("w1", "T-h")
    body = _body(o, "refs/swarm/claims/T-h")
    assert "harness: opencode" in body
    assert body.strip().split()[-1] == res["att"]  # att-last holds


def test_alias_refusal_on_opencode_listing(monkeypatch, tmp_path):
    """L2 alias resolvability: a successful, non-empty opencode model listing
    that lacks the requested model is refused at the preflight (a stale id
    must not dispatch); a model in the listing proceeds; an empty/failed
    listing is 'cannot see' and proceeds (the house law)."""
    o, w = _lane(tmp_path)
    _push(o, w, _root_commit(w, "demo\nverify: true"), "refs/swarm/specs/T-x")
    shim = tmp_path / "d.sh"
    shim.write_text(
        "#!/bin/sh\n"
        'case "$1" in models) echo stub/known-model; exit 0;; esac\n'
        "exit 0\n")
    shim.chmod(0o755)

    def _run(model):
        env = dict(os.environ, SWARM_ORIGIN=o, SWARM_MODEL=model,
                   OPENCODE_BIN=str(shim), SWARM_MODEL_STRICT="1")
        return subprocess.run(
            [sys.executable, str(REPO / "l2" / "inrepo.py"), "worker", "wm"],
            cwd=w, env=env, capture_output=True, text=True, timeout=120)

    r = _run("stub/unknown")
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    assert [e for e in events if e.get("event") == "model_refusal"], events
    assert r.returncode == 1  # refused before dispatch

    r = _run("stub/known-model")
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    assert not [e for e in events if e.get("event") == "model_refusal"], events
    assert r.returncode == 0  # proceeded (the stub dispatched + no work done)


# -------------------------------------------- L4 attribution coherence


def test_kind_degrades_to_unknown_when_router_unreachable(monkeypatch):
    """L4: a model the router cannot classify — the deployed litellm 404s
    /model_info, so the auto-class is inert today — yields 'unknown'. The
    model string is still recorded verbatim on the verdict body (honest:
    we know the model, just not its chain/direct source)."""
    inrepo = _load_inrepo()
    monkeypatch.setenv("SWARM_ROUTER_URL", "http://127.0.0.1:1")  # refused
    monkeypatch.delenv("SWARM_MODEL_KIND", raising=False)
    assert inrepo._model_kind("router/some-model") == "unknown"
    monkeypatch.setenv("SWARM_MODEL", "router/some-model")
    assert "model: router/some-model" in inrepo.verdict_body(
        "T", "att-w1-abcd12", True, "w1", 0, 0)


def test_swarm_model_kind_asserts_without_router(monkeypatch):
    """L4: SWARM_MODEL_KIND is the production source — an explicit assertion
    is used without touching the router; a non-chain/direct value falls
    through to the (unreachable) router and degrades to 'unknown'."""
    inrepo = _load_inrepo()
    monkeypatch.setenv("SWARM_ROUTER_URL", "http://127.0.0.1:1")
    for v in ("chain", "direct"):
        monkeypatch.setenv("SWARM_MODEL_KIND", v)
        assert inrepo._model_kind(f"m-{v}") == v
    monkeypatch.setenv("SWARM_MODEL_KIND", "bogus")
    assert inrepo._model_kind("m-bogus") == "unknown"


# ---------------------------------------------------- L5.1 checkpoint


def test_dispatch_started_push_preserves_att_last(monkeypatch, tmp_path):
    """L5.1: the dispatch-start push inserts a whitespace-free
    dispatch_started token BEFORE the att (the att stays the last body
    token, so the four att-readers — sweep/relabel/graph/audit — still
    recover the att from the new body via split()[-1])."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_MODEL", "t3/coder-coder")
    monkeypatch.chdir(w)
    res = inrepo.claim_detail("w1", "T-d")
    att = res["att"]
    iso = inrepo._push_dispatch_started(o, "T-d", att, "20261011T000000Z")
    assert iso == "20261011T000000Z"
    body = _body(o, "refs/swarm/claims/T-d")
    assert "dispatch_started: 20261011T000000Z" in body
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert lines[-1].strip() == att  # att is still the last line/token
    assert body.strip().split()[-1] == att  # and the last whitespace token


def test_dispatch_started_resets_lease_age(monkeypatch, tmp_path):
    """L5.1: the dispatch-start push rewrites the claim with a fresh committer
    ts, resetting its lease age to now — a bounded run is sweep-proof. The
    control (an aged claim with no dispatch-start push) is swept; the reset
    claim is fresh."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_MODEL", "t3/coder-coder")
    monkeypatch.chdir(w)

    def _aged_claim(task, att):
        body = (f"claim {task}\nharness: opencode\n"
                f"model: t3/coder-coder\nkind: unknown\n{att}")
        et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
        sha = subprocess.run(
            ["git", "-C", w, "commit-tree", et, "-m", body],
            env=dict(os.environ,
                     GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
                     GIT_AUTHOR_DATE="2000-01-01T00:00:00Z"),
            capture_output=True, text=True).stdout.strip()
        r = subprocess.run(
            ["git", "-C", w, "push", "-q", "-f", str(o),
             f"{sha}:refs/swarm/claims/{task}"],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    _aged_claim("T-reset", "att-w1-reset01")
    _aged_claim("T-aged", "att-w1-aged001")
    # the dispatch-start push resets T-reset's committer ts to now
    inrepo._push_dispatch_started(o, "T-reset", "att-w1-reset01",
                                  "20260101T000000Z")
    out = inrepo.reconcile(o, ttl_s=60)
    swept = {x.get("task") for x in out["stale"]}
    assert "T-aged" in swept, out       # the aged claim is swept
    assert "T-reset" not in swept, out  # the reset claim is fresh
    assert "T-reset" in out["fresh"], out


# ------------------------------------------------------- L5.2 + L5.3


def test_lease_invariant_warns_on_misconfig(monkeypatch):
    """L5.2 (M1): a worker whose dispatch+oracle+slack could exceed the lease
    TTL is warned at start (not refused) — a run swept mid-flight is caught.
    The defaults (600 + 600 + slack < 1800) do not warn."""
    inrepo = _load_inrepo()
    monkeypatch.setenv("SWARM_MODEL", "t3/coder-coder")
    monkeypatch.setenv("SWARM_DISPATCH_TIMEOUT", "1000")
    monkeypatch.setenv("SWARM_ORACLE_TIMEOUT", "1000")
    monkeypatch.setenv("SWARM_LEASE_TTL", "1800")
    w = inrepo._lease_invariant_warn("wm")
    assert w and w["event"] == "lease_invariant_warning", w
    assert w["total_s"] >= 1800, w
    monkeypatch.setenv("SWARM_DISPATCH_TIMEOUT", "600")
    monkeypatch.setenv("SWARM_ORACLE_TIMEOUT", "600")
    assert inrepo._lease_invariant_warn("wm") is None  # defaults are safe


def test_attribution_reports_interrupted(monkeypatch, tmp_path):
    """L5.3: a live claim with dispatch_started + no verdict + no archive,
    aged past the lease bound, is reported interrupted in attribution — from
    the origin alone (no metrics needed). A claim still within the bound is
    not interrupted (it may still be running)."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_LEASE_TTL", "60")

    def _claim(task, att, started, old_date):
        extra = "\ndispatch_started: 20260101T000000Z" if started else ""
        body = f"claim {task}\nharness: opencode{extra}\n{att}"
        et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
        env = dict(os.environ)
        if old_date:
            env.update(GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
                       GIT_AUTHOR_DATE="2000-01-01T00:00:00Z")
        sha = subprocess.run(
            ["git", "-C", w, "commit-tree", et, "-m", body],
            env=env, capture_output=True, text=True).stdout.strip()
        subprocess.run(
            ["git", "-C", w, "push", "-q", "-f", str(o),
             f"{sha}:refs/swarm/claims/{task}"],
            capture_output=True, text=True)

    for t, att, old in (("T-int", "att-w1-int0001", True),
                        ("T-run", "att-w1-run001", False)):
        _push(o, w, _root_commit(w, f"task {t}\nverify: true"),
              f"refs/swarm/specs/{t}")
        _claim(t, att, started=True, old_date=old)
    out = inrepo.attribution(o)
    assert "T-int" in out.get("interrupted", {}), out.get("interrupted")
    assert out["interrupted"]["T-int"]["claim_age_s"] > 60
    assert "T-run" not in out.get("interrupted", {}), out.get("interrupted")


# ------------------------------------------------------ L6.1 streaming


def _json_stub(tmp_path, body, name="stream.sh"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def test_dispatch_streaming_success_contract(monkeypatch, tmp_path):
    """L6.1 (B3): streaming (opt-in) returns the same (rc, elapsed, err)
    contract — a success emits JSON events, rc 0, and an empty err (the
    error event is absent; stderr is drained concurrently)."""
    inrepo = _load_inrepo()
    _git("init", "-q", "-b", "main", str(tmp_path / "w"))
    shim = _json_stub(tmp_path,
                      'echo \'{"type":"step_start"}\'\n'
                      'echo \'{"type":"text","text":"hi"}\'\n'
                      'echo \'{"type":"step_finish"}\'')
    monkeypatch.setenv("OPENCODE_BIN", shim)
    rc, elapsed, err = inrepo._dispatch_streaming(
        "m", "b", str(tmp_path / "w"), 60)
    assert rc == 0 and elapsed >= 0
    assert err == ""  # no error event, and JSON mode leaves stderr empty


def test_dispatch_streaming_error_event_is_the_err(monkeypatch, tmp_path):
    """L6.1 (B3): in JSON mode stderr is empty, so oc_err comes from the
    `error` event — a failing dispatch surfaces its error message, not an
    empty tail (the default blob-capture would have it on stderr, but
    streaming must still recover it)."""
    inrepo = _load_inrepo()
    _git("init", "-q", "-b", "main", str(tmp_path / "w"))
    shim = _json_stub(tmp_path,
                      'echo \'{"type":"error","message":"boom-failed"}\'\n'
                      "exit 3")
    monkeypatch.setenv("OPENCODE_BIN", shim)
    rc, elapsed, err = inrepo._dispatch_streaming(
        "m", "b", str(tmp_path / "w"), 60)
    assert rc == 3
    assert "boom-failed" in err


def test_dispatch_streaming_hung_is_killed_at_bound(monkeypatch, tmp_path):
    """L6.1 (B2): a hung backend (no output) is SIGKILL'd at the dispatch
    bound — oc_rc=124 (the load-bearing 124⇒env_death) — not left to block
    the worker forever (the streaming path must re-implement the SIGKILL
    deadline the blob-capture path gets from subprocess.run's timeout)."""
    inrepo = _load_inrepo()
    _git("init", "-q", "-b", "main", str(tmp_path / "w"))
    shim = _json_stub(tmp_path, 'echo \'{"type":"step_start"}\'\nsleep 30')
    monkeypatch.setenv("OPENCODE_BIN", shim)
    import time as _t
    t0 = _t.monotonic()
    rc, elapsed, _ = inrepo._dispatch_streaming(
        "m", "b", str(tmp_path / "w"), 2)
    assert rc == 124  # killed at the bound
    assert _t.monotonic() - t0 < 10  # not left running for the 30s sleep


def test_dispatch_streaming_parity_with_blob(monkeypatch, tmp_path):
    """L6.1 (B3): the same stub yields the same rc + err under streaming and
    blob-capture — the streaming path must not change a dispatch's meaning,
    only how its output is captured."""
    inrepo = _load_inrepo()
    _git("init", "-q", "-b", "main", str(tmp_path / "w"))
    shim = _json_stub(tmp_path, 'echo \'{"type":"error","message":"x"}\'\n'
                                "echo boom >&2\nexit 3")
    monkeypatch.setenv("OPENCODE_BIN", shim)
    blob = inrepo._dispatch("opencode", "m", "b", str(tmp_path / "w"))
    stream = inrepo._dispatch_streaming("m", "b", str(tmp_path / "w"), 60)
    assert blob[0] == stream[0] == 3  # same rc: the dispatch meaning is unchanged
    assert blob[2]  # blob-capture recovers the stderr tail
    assert stream[2]  # streaming recovers the error (from the error event or stderr)


# ------------------------------------------------------- L6 debug surface


def test_durations_reports_per_model_from_verdict_refs(monkeypatch, tmp_path):
    """L6.2 (B5): durations reports the per-model oc_elapsed distribution from
    the VERDICT REFS (works with metrics off — the JSONL is off-by-default) +
    flags a near-bound model (oc_elapsed approaching the dispatch bound)."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    # distinct tasks per verdict (one verdict ref per task); the model groups
    # them (ma has two, mb has one).
    for task, model, el in (("T-ma-1", "ma", "5.0"),
                            ("T-ma-2", "ma", "8.0"),
                            ("T-mb-1", "mb", "590.0")):
        body = (f"verdict\ntask: {task}\nattempt: att-w1-x\nfixed: true\n"
                f"host: w1\noc_rc: 0\npytest_rc: 0\noc_elapsed: {el}\n"
                f"harness: opencode\nmodel: {model}\nkind: direct")
        _push(o, w, _root_commit(w, body), f"refs/swarm/verdicts/{task}")
        _push(o, w, _root_commit(w, f"task {task}\nverify: true"),
              f"refs/swarm/specs/{task}")
    out = inrepo.durations(o, bound_s=600)
    a = out["models"]["ma"]
    assert a["n"] == 2 and a["min_s"] == 5.0 and a["max_s"] == 8.0, out
    b = out["models"]["mb"]
    assert b["n"] == 1 and b["max_s"] == 590.0, out
    assert b["near_bound"] is True  # 590 approaching the 600 bound
    assert a["near_bound"] is False


def test_attribution_in_progress_no_verdict_yet(monkeypatch, tmp_path):
    """L6.3: a live claim with no verdict, within the lease bound, is reported
    in_progress (no_verdict_yet) with its age — a running board is legible
    (distinct from the aged interrupted case)."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "task T-run\nverify: true"),
          "refs/swarm/specs/T-run")
    body = ("claim T-run\nharness: opencode\n"
            "dispatch_started: 20261011T000000Z\natt-w1-run001")
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    sha = _git("-C", w, "commit-tree", et, "-m", body).stdout.strip()
    subprocess.run(["git", "-C", w, "push", "-q", "-f", str(o),
                    f"{sha}:refs/swarm/claims/T-run"],
                   capture_output=True, text=True)
    out = inrepo.attribution(o)
    assert "T-run" in out.get("in_progress", {}), out.get("in_progress")
    ip = out["in_progress"]["T-run"]
    assert ip["status"] == "no_verdict_yet" and ip["claim_age_s"] >= 0


def test_reconcile_flags_aging_claim(monkeypatch, tmp_path):
    """L6.5: a claim aged past 0.5*TTL (but within TTL) is flagged aging — an
    early warning that it is approaching the lease, before it is swept."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    import datetime
    d = (datetime.datetime.now(datetime.timezone.utc)
         - datetime.timedelta(seconds=75)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    body = "claim T-age\nharness: opencode\natt-w1-age001"
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    sha = subprocess.run(
        ["git", "-C", w, "commit-tree", et, "-m", body],
        env=dict(os.environ, GIT_COMMITTER_DATE=d, GIT_AUTHOR_DATE=d),
        capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", w, "push", "-q", "-f", str(o),
                    f"{sha}:refs/swarm/claims/T-age"],
                   capture_output=True, text=True)
    out = inrepo.reconcile(o, ttl_s=100)
    aging = {x.get("task") for x in out.get("aging", [])}
    assert "T-age" in aging, out  # 75s past, 0.5*100=50 threshold, age<=100


# ------------------------------------------------------ L3 freebuff adapter


def test_dispatch_freebuff_routes_and_timeout(monkeypatch, tmp_path):
    """L3 (C8): SWARM_DISPATCH=freebuff routes to fbconn.run_prompt (the
    freebuff remote PTY adapter) with takeover + the timeout fed from
    SWARM_DISPATCH_TIMEOUT. A stub connector (injected via sys.modules, not
    the ambient host — the connector is a remote dependency, never required
    for the default opencode path) classifies success (text), env-death (no
    text), and timeout (oc_rc=124)."""
    inrepo = _load_inrepo()
    _git("init", "-q", "-b", "main", str(tmp_path / "w"))
    calls = {}

    def run_prompt(folder, prompt, *, takeover=False, timeout=600, **kw):
        calls["timeout"] = timeout
        calls["takeover"] = takeover
        if "RAISE-TIMEOUT" in prompt:
            raise TimeoutError("timed out")
        # the real connector signals a prompt timeout by RETURNING
        # status="timeout" (driver.wait_idle) — with a partial-text harvest,
        # which must NOT read as a success (rc 0).
        if "STATUS-TIMEOUT" in prompt:
            return {"status": "timeout", "text": "partial work"}
        return {"text": "done"} if "OK" in prompt else {}

    api = types.ModuleType("fbconn.api")
    api.run_prompt = run_prompt
    mod = types.ModuleType("fbconn")
    mod.api = api
    monkeypatch.setitem(sys.modules, "fbconn", mod)
    monkeypatch.setitem(sys.modules, "fbconn.api", api)
    monkeypatch.setenv("SWARM_DISPATCH_TIMEOUT", "77")
    rc, elapsed, err = inrepo._dispatch(
        "freebuff", "m", "OK brief", str(tmp_path / "w"))
    assert rc == 0 and elapsed >= 0
    assert calls["timeout"] == 77 and calls["takeover"] is True
    assert inrepo._dispatch("freebuff", "m", "FAIL brief",
                            str(tmp_path / "w"))[0] == 1  # no text -> env death
    # the real connector signals a timeout by RETURNING status="timeout" with
    # a partial-text harvest -> 124 (not a success, not an env-death)
    assert inrepo._dispatch("freebuff", "m", "STATUS-TIMEOUT brief",
                            str(tmp_path / "w"))[0] == 124
    # and a raised timeout (the ready-leg) maps to 124 too
    assert inrepo._dispatch("freebuff", "m", "RAISE-TIMEOUT brief",
                            str(tmp_path / "w"))[0] == 124


def test_freebuff_is_a_known_harness(monkeypatch):
    """L3 (C8): freebuff is a recognized harness (the preflight does not
    refuse it) and its model-resolvability degrades (no freebuff listing —
    proceed, the alias check is opencode-only)."""
    inrepo = _load_inrepo()
    assert "freebuff" in inrepo._KNOWN_HARNESSES
    monkeypatch.setenv("SWARM_MODEL", "fb/model")
    monkeypatch.setenv("SWARM_DISPATCH", "freebuff")
    assert inrepo._dispatch_harness() == "freebuff"
    # the preflight proceeds for freebuff (no alias check, model is set)
    assert inrepo._model_preflight("wm") is False
