"""Contracts for the MCP server (C15) — the C5 verbs as a harness-agnostic
MCP server over stdio (newline-delimited JSON-RPC 2.0).

Pins: the MCP handshake (initialize / tools/list), the routing (tools/call ->
substrate), that origin is REQUIRED (no default — the production-push hazard
is absent), that a full claim->judge->land->verdict flow drives the board
truthfully, that a failing oracle is an honest result (isError False), and
that the stdio framing works end-to-end under a real subprocess.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "mcp_server", REPO / "l2" / "mcp_server.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return ["-c", "user.email=a@b", "-c", "user.name=a"]


def _board(tmp_path):
    """A local board with a seeded task (verify: true) + a working tree."""
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(w), *_cid(), "push", "-q", str(o), "main")
    # a spec (verify: true) so the oracle is a real (passing) command
    et = _git("-C", str(w), "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    sha = _git("-C", str(w), *_cid(), "commit-tree", et,
               "-m", "task T-mcp\nverify: true").stdout.strip()
    _git("-C", str(w), *_cid(), "push", "-q", str(o), f"{sha}:refs/swarm/specs/T-mcp")
    return str(o), str(w)


def _ref(o, ref):
    r = _git("ls-remote", o, ref)
    return r.stdout.split()[0] if r.stdout.strip() else None


def test_initialize_and_tools_list():
    m = _load()
    init = m.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05"}})
    assert init["result"]["serverInfo"]["name"] == "flockwork"
    assert "protocolVersion" in init["result"]
    tl = m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in tl["result"]["tools"]}
    assert {"fw_brief", "fw_claim", "fw_judge", "fw_land", "fw_verdict"} <= names
    # every tool has an inputSchema
    assert all(t["inputSchema"]["type"] == "object" for t in tl["result"]["tools"])


def test_origin_is_required_no_default(tmp_path, monkeypatch):
    """A board op with no origin is refused typed — the harness-agnostic
    server has no default (the production-push hazard is absent)."""
    m = _load()
    monkeypatch.delenv("SWARM_ORIGIN", raising=False)
    res = m.dispatch("fw_brief", {"task": "T-mcp"})
    assert res["isError"] is True
    assert "origin is required" in res["content"][0]["text"]


def test_claim_judge_land_verdict_drives_the_board(tmp_path):
    m = _load()
    o, w = _board(tmp_path)
    # claim
    c = m.dispatch("fw_claim", {"origin": o, "task": "T-mcp"})
    assert c["isError"] is False
    att = c["structuredContent"]["att"]
    # make a real change (so land has a diff)
    (pathlib.Path(w) / "f.txt").write_text("fixed\n")
    # judge (oracle: verify: true -> passes)
    j = m.dispatch("fw_judge", {"origin": o, "task": "T-mcp", "tree": w})
    assert j["isError"] is False
    assert j["structuredContent"]["rc"] == 0
    # land (scoped to f.txt)
    l = m.dispatch("fw_land", {"origin": o, "task": "T-mcp", "att": att,
                               "tree": w, "paths": ["f.txt"]})
    assert l["isError"] is False
    # verdict
    v = m.dispatch("fw_verdict", {"origin": o, "task": "T-mcp", "att": att,
                                  "fixed": True, "oc_rc": 0, "pytest_rc": 0,
                                  "oc_elapsed": 1.0})
    assert v["isError"] is False
    # the board is truthful: the verdict ref + the main push exist
    assert _ref(o, "refs/swarm/verdicts/T-mcp") is not None
    # and the fix landed on main
    main_sha = _ref(o, "main")
    assert main_sha is not None
    blob = _git("-C", str(o), "cat-file", "-p", main_sha + ":f.txt").stdout
    assert blob.strip() == "fixed"


def test_failing_oracle_is_honest_not_an_error(tmp_path):
    """A failing oracle is an honest result (isError False — the judge ran;
    the rc is the value), not a tool error."""
    m = _load()
    o, w = _board(tmp_path)
    # override the verify to something that fails
    j = m.dispatch("fw_judge", {"origin": o, "task": "T-mcp", "tree": w,
                                "verify": "false"})
    assert j["isError"] is False  # the judge ran; it is an honest rc!=0
    assert j["structuredContent"]["rc"] != 0


def test_stdio_framing_end_to_end(tmp_path):
    """A real subprocess speaking the newline-delimited JSON-RPC stdio
    transport: initialize -> tools/list -> tools/call(fw_protocol)."""
    o, _w = _board(tmp_path)
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "fw_protocol", "arguments": {}}},
    ]
    inp = "".join(json.dumps(r) + "\n" for r in reqs)
    r = subprocess.run(
        [sys.executable, "-m", "l2.mcp_server"],
        input=inp, capture_output=True, text=True, cwd=REPO, timeout=60,
    )
    assert r.returncode == 0, r.stderr[-500:]
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    # the notification gets NO response: 3 responses for 3 id-bearing requests
    assert len(lines) == 3
    by_id = {json.loads(ln)["id"]: json.loads(ln)["result"] for ln in lines}
    assert by_id[1]["serverInfo"]["name"] == "flockwork"
    assert {t["name"] for t in by_id[2]["tools"]} >= {"fw_claim", "fw_verdict"}
    assert "fw_verdict" in by_id[3]["content"][0]["text"] or \
        "Step 1" in by_id[3]["content"][0]["text"]
