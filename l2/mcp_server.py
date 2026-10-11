"""flockwork MCP server (C15, optional).

Exposes the substrate's external-work verbs (the C5 legs) as MCP tools over the
stdio transport, HARNESsS-AGNOSTIC: the client names the board (an explicit
`origin` argument) — the server never falls back to a default origin (the
production-push hazard the substrate exists to prevent). This lets any MCP
client (Claude, an IDE, a script) drive claim -> work -> judge -> land ->
verdict without the opencode plugin.

The transport is the MCP stdio convention: newline-delimited JSON-RPC 2.0 —
one JSON object per line on stdin in, one per line on stdout out. Notifications
get no response; requests get a result or a JSON-RPC error.

The policy is the substrate (l2/inrepo.py); this is a thin dispatch + framing
layer. `handle()` + `dispatch()` are pure + unit-testable; `main()` is the
stdio loop.
"""
from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "flockwork"


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("flockwork")
    except Exception:
        return "0.0.0"


def _text(s: str) -> dict:
    return {"type": "text", "text": s}


def _ok(text: str, extra: dict | None = None) -> dict:
    out = {"content": [_text(text)], "isError": False}
    if extra:
        out["structuredContent"] = extra
    return out


def _fail(text: str) -> dict:
    return {"content": [_text(text)], "isError": True}


def _require_origin(args: dict) -> str | None:
    o = args.get("origin")
    return o if isinstance(o, str) and o.strip() else None


def _num(args: dict, key: str, default=None):
    v = args.get(key, default)
    try:
        return type(default)(v) if default is not None and v is not None else (
            int(v) if v is not None else default)
    except (TypeError, ValueError):
        return default


def dispatch(name: str, args: dict) -> dict:
    """Route an MCP tool call to the substrate. Returns an MCP tool result
    ({content, isError[, structuredContent]}). A failing oracle is an HONEST
    result (isError False — the judge ran; the rc is in the text), not a
    tool error."""
    import l2.inrepo as inrepo
    import l2.harness as fw_harness

    if name == "fw_protocol":
        return _ok(fw_harness.protocol())

    origin = _require_origin(args)
    if name != "fw_judge" and origin is None:
        return _fail("origin is required (harness-agnostic: the client must "
                     "name the board; there is no default)")
    task = args.get("task", "")
    if not task:
        return _fail("task is required")

    if name == "fw_brief":
        body = inrepo.brief_body(origin, task)
        if body is None:
            return _fail(f"spec absent for {task}")
        return _ok(body)

    if name == "fw_claim":
        worker = args.get("worker") or inrepo._worker_name()
        res = inrepo.claim_detail(worker, task, origin)
        att = res.get("att")
        if not att:
            return _fail(f"claim failed for {task}: lost the race or rejected")
        return _ok(f"claimed {task}\natt={att}", {"att": att})

    if name == "fw_judge":
        tree = args.get("tree", "")
        if not tree:
            return _fail("tree is required for fw_judge")
        verify = args.get("verify") or inrepo._brief_verify(origin or "", task)
        jres = inrepo.judge_tree(tree, task, verify)
        # an honest oracle result, not a tool error
        return _ok(
            f"oracle rc={jres.get('rc')} timed_out={jres.get('timed_out')}\n"
            f"{jres.get('detail', '').strip()}",
            {"rc": jres.get("rc"), "timed_out": jres.get("timed_out")},
        )

    if name == "fw_land":
        att = args.get("att", "")
        tree = args.get("tree", "")
        if not (att and tree):
            return _fail("att and tree are required for fw_land")
        paths = args.get("paths") or None
        out = inrepo.land_fix(origin, tree, task, att, paths)
        okd = out.get("main_pushed") or out.get("already_on_main")
        text = (f"land {'succeeded' if okd else 'failed'} "
                f"(main_pushed={out.get('main_pushed')} "
                f"return_pushed={out.get('return_pushed')})")
        return _ok(text) if okd else _fail(text)

    if name == "fw_verdict":
        att = args.get("att", "")
        if not att:
            return _fail("att is required for fw_verdict")
        fixed = str(args.get("fixed", "false")).strip().lower() in ("true", "1", "yes")
        oc_rc = int(args.get("oc_rc", 0))
        pytest_rc = int(args.get("pytest_rc", 0))
        oc_elapsed = args.get("oc_elapsed")
        worker = args.get("worker") or inrepo._worker_name()
        r = inrepo.write_verdict(origin, task, att, fixed, worker, oc_rc,
                                 pytest_rc, oc_elapsed)
        okd = inrepo.ok(r)
        return _ok(f"verdict written: {task} fixed={fixed}") if okd \
            else _fail(f"verdict NOT written for {task} (create-once refusal)")

    if name in ("fw_renew", "fw_work_started", "fw_work_done"):
        att = args.get("att", "")
        if not att:
            return _fail("att is required")
        fn = {"fw_renew": inrepo.renew_claim,
              "fw_work_started": inrepo.mark_work_started,
              "fw_work_done": inrepo.mark_work_done}[name]
        res = fn(origin, task, att)
        return _ok(f"{name[3:]} ok") if res is not None else _fail(f"{name} failed")

    if name == "fw_work_external":
        att = args.get("att", "")
        tree = args.get("tree", "")
        if not (att and tree):
            return _fail("att and tree are required for fw_work_external")
        worker = args.get("worker") or inrepo._worker_name()
        out = inrepo.work_external(origin, task, att, tree, worker,
                                   args.get("paths") or None,
                                   args.get("verify"))
        return _ok(f"work_external fixed={out.get('fixed')}\n"
                   f"{json.dumps(out, indent=1)}") if out.get("fixed") \
            else _fail(f"work_external fixed={out.get('fixed')}\n"
                       f"{json.dumps(out, indent=1)}")

    return _fail(f"unknown tool: {name}")


def _tool(name: str, description: str, props: dict, required: list) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props,
                            "required": required}}


# (built at import; the origin prop is shared by the board ops)
_ORIGIN = {"type": "string", "description": "the board origin (required)"}
_TASK = {"type": "string", "description": "the task id"}
_ATT = {"type": "string", "description": "the attestation token from fw_claim"}
_TREE = {"type": "string", "description": "the working-tree path"}
_PATHS = {"type": "array", "items": {"type": "string"},
          "description": "the scoped files changed (None = whole tree)"}

TOOLS: list = [
    _tool("fw_protocol", "Print the canonical 2-step swarm protocol.",
          {}, []),
    _tool("fw_brief", "Read the brief (spec) for a task: what to do + the "
          "verify command.", {"origin": _ORIGIN, "task": _TASK},
          ["origin", "task"]),
    _tool("fw_claim", "Claim a task (take the lease); returns the att token.",
          {"origin": _ORIGIN, "task": _TASK,
           "worker": {"type": "string", "description": "the worker name"}},
          ["origin", "task"]),
    _tool("fw_judge", "Run the task's verify command (the oracle) against the "
          "working tree. Returns the honest rc (may block for minutes).",
          {"task": _TASK, "tree": _TREE,
           "origin": {"type": "string",
                      "description": "optional, for the spec's verify"},
           "verify": {"type": "string",
                      "description": "override the verify command"}},
          ["task", "tree"]),
    _tool("fw_land", "Land the scoped fix: commit the files changed, push to "
          "board main, push the return ref.",
          {"origin": _ORIGIN, "task": _TASK, "att": _ATT, "tree": _TREE,
           "paths": _PATHS},
          ["origin", "task", "att", "tree"]),
    _tool("fw_verdict", "Write the terminal create-once verdict (fixed true/"
          "false + rcs + elapsed). Exactly once per claim.",
          {"origin": _ORIGIN, "task": _TASK, "att": _ATT,
           "fixed": {"type": "boolean"}, "oc_rc": {"type": "integer"},
           "pytest_rc": {"type": "integer"},
           "oc_elapsed": {"type": "number"}},
          ["origin", "task", "att", "fixed", "oc_rc", "pytest_rc"]),
    _tool("fw_renew", "Renew the lease on a claimed task (long work).",
          {"origin": _ORIGIN, "task": _TASK, "att": _ATT},
          ["origin", "task", "att"]),
    _tool("fw_work_started", "Checkpoint that work on a claimed task began.",
          {"origin": _ORIGIN, "task": _TASK, "att": _ATT},
          ["origin", "task", "att"]),
    _tool("fw_work_done", "Checkpoint that work on a claimed task completed.",
          {"origin": _ORIGIN, "task": _TASK, "att": _ATT},
          ["origin", "task", "att"]),
    _tool("fw_work_external", "The full external-work leg in one call "
          "(work_started -> judge -> land -> verdict -> work_done).",
          {"origin": _ORIGIN, "task": _TASK, "att": _ATT, "tree": _TREE,
           "paths": _PATHS,
           "verify": {"type": "string", "description": "override the verify"}},
          ["origin", "task", "att", "tree"]),
]


def _resp(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": code, "message": message}}


def handle(req: dict) -> dict | None:
    """Route one JSON-RPC request. Returns a response dict, or None for
    notifications (no id -> no response)."""
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        return _resp(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
        })
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "tools/list":
        return _resp(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            return _resp(rid, dispatch(name, args))
        except Exception as e:  # noqa: BLE001 — surface, don't crash the loop
            return _resp(rid, _fail(f"{type(e).__name__}: {e}"))
    if method == "ping":
        return _resp(rid, {})
    if rid is not None:
        return _err(rid, -32601, f"method not found: {method}")
    return None


def main() -> None:
    """The stdio loop: newline-delimited JSON-RPC on stdin -> stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps(
                _err(None, -32700, f"parse error: {e}")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
