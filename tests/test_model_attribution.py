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
    monkeypatch.setenv("SWARM_MODEL", "t3/unistra-coder")
    body = inrepo.verdict_body("T", "att-w1-abcd12", True, "w1", 0, 0)
    assert "model: t3/unistra-coder" in body
    assert "attempt: att-w1-abcd12" in body  # unchanged fields intact


def test_claim_body_records_model_before_att(monkeypatch, tmp_path):
    """claim_detail writes `model: <model>` into the claim body with the att
    still the last token (the F5 invariant)."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_MODEL", "t3/unistra-coder")
    monkeypatch.chdir(w)
    res = inrepo.claim_detail("w1", "T-claim")
    assert res["att"] is not None
    body = _body(o, "refs/swarm/claims/T-claim")
    assert "model: t3/unistra-coder" in body
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
    att = "att-mm-unistra-x-deadbeef"
    _push(o, w, _root_commit(w, "the brief\nverify: true"),
          "refs/swarm/specs/T-inv")
    _push(o, w, _root_commit(w, f"claim T-inv\nmodel: t3/unistra-coder\n{att}"),
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
        w, "claim T-g\nmodel: t3/unistra-coder\natt-w1-aaaa01"),
        "refs/swarm/claims/T-g")
    _push(o, w, _root_commit(
        w, "verdict\ntask: T-g\nattempt: att-w1-aaaa01\nfixed: true\n"
           "host: w1\noc_rc: 0\npytest_rc: 0\nmodel: t3/unistra-coder"),
        "refs/swarm/verdicts/T-g")
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    atts = {a["att"]: a["model"] for a in g["tasks"]["T-g"]["attempts"]}
    assert atts["att-w1-aaaa01"] == "t3/unistra-coder"


def test_graph_mm_att_uses_body_not_split(monkeypatch, tmp_path):
    """An mm-* att is attributed from the body's model, never from the
    att-token split (the old mm_model_of misparse). The retired function
    is gone."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-mm")
    _push(o, w, _root_commit(
        w, "claim T-mm\nmodel: t3/unistra-coder\natt-mm-unistra-a-deadbeef"),
        "refs/swarm/claims/T-mm")
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    atts = {a["att"]: a["model"] for a in g["tasks"]["T-mm"]["attempts"]}
    # body model wins; the split would have yielded "unistra"
    assert atts["att-mm-unistra-a-deadbeef"] == "t3/unistra-coder"
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
        {"model_name": "t3/tier-unistra", "litellm_params": {"model": "openai/a"}},
        {"model_name": "t3/tier-unistra", "litellm_params": {"model": "openai/b"}},
    ]}
    counter = {}
    srv, url = _router_server(cfg, counter)
    try:
        monkeypatch.setenv("SWARM_ROUTER_URL", url)
        assert inrepo._model_kind("t3/tier-unistra") == "chain"
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
        {"model_name": "t3/tier-unistra", "litellm_params": {"model": "openai/a"}},
        {"model_name": "t3/tier-unistra", "litellm_params": {"model": "openai/b"}},
    ]}
    counter = {}
    srv, url = _router_server(cfg, counter)
    o, w = _lane(tmp_path)
    try:
        monkeypatch.setenv("SWARM_ORIGIN", o)
        monkeypatch.setenv("SWARM_MODEL", "t3/tier-unistra")
        monkeypatch.setenv("SWARM_ROUTER_URL", url)
        monkeypatch.chdir(w)
        res = inrepo.claim_detail("w1", "T-k")
        body = _body(o, "refs/swarm/claims/T-k")
        assert "model: t3/tier-unistra" in body
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
        dict(SWARM_ORIGIN=o, SWARM_MODEL="t3/unistra-coder",
             SWARM_MODEL_KIND="direct", SWARM_HEIR_MAX="1"),
        "T-nd", att)
    assert ev.get("event") != "driver_exception", ev
    assert ev.get("env_death") is True
    assert ev["model"] == "t3/unistra-coder"
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
        dict(SWARM_ORIGIN=o, OPENCODE_BIN=str(stub), SWARM_MODEL="t3/unistra-coder",
             SWARM_MODEL_KIND="direct", SWARM_HEIR_MAX="1",
             SWARM_DISPATCH_TIMEOUT="30"),
        TASK, att)
    assert ev.get("event") != "driver_exception", ev
    assert ev["model"] == "t3/unistra-coder"
    assert ev["kind"] == "direct"
    assert isinstance(ev.get("oc_elapsed"), (int, float)) and ev["oc_elapsed"] >= 0


def test_attribution_output_shape(monkeypatch, tmp_path):
    """The read-only attribution diagnostic: per task, a flat table of
    att | model | kind | verdict(fixed/oc_rc/pytest_rc) | archived | host."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-a")
    _push(o, w, _root_commit(
        w, "claim T-a\nmodel: t3/unistra-coder\nkind: direct\natt-w1-aaaa01"),
        "refs/swarm/claims/T-a")
    _push(o, w, _root_commit(
        w, "verdict\ntask: T-a\nattempt: att-w1-aaaa01\nfixed: true\n"
           "host: w1\noc_rc: 0\npytest_rc: 0\nmodel: t3/unistra-coder\n"
           "kind: direct"),
        "refs/swarm/verdicts/T-a")
    inrepo = _load_inrepo()
    out = inrepo.attribution(o)
    row = out["tasks"]["T-a"][0]
    for k in ("att", "model", "kind", "fixed", "oc_rc", "pytest_rc",
              "host", "archived"):
        assert k in row, (k, row)
    assert row["model"] == "t3/unistra-coder" and row["kind"] == "direct"
    assert row["fixed"] == "true" and row["host"] == "w1"
