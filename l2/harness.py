"""flockwork harness — the substrate the opencode plugin drives.

The plugin is thin glue; THIS module owns the harness policy. It exists so
the interactive-work substrate is a first-class, testable part of the law
rather than logic smeared across a TypeScript file:

  - workspace-detect is MARKER-ONLY (a local, gitignored .flockwork/origin
    file). A stray SWARM_ORIGIN env var must NEVER activate the plugin — the
    env-trigger was the production-push hazard class this project exists to
    prevent;
  - bootstrap declares the origin (one-time) and provisions the OUT-OF-TREE
    (XDG) ledger dir;
  - the ledgers + durable session map (C12) and the idle backstop (C11) build
    on these primitives and reuse the substrate's claim/lease/verdict legs.

Nothing here imports opencode; it is pure substrate, drivable from the CLI
(``flockwork harness ...``) and the plugin alike.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

_MARKER = Path(".flockwork") / "origin"
_TAG = "flockwork"


def _marker_path(directory: str) -> Path:
    return Path(directory) / _MARKER


def origin_of(directory: str) -> str | None:
    """The origin declared by the local marker, or None. Reads ONLY the
    marker (never the env) — the env is not a legal activation source."""
    p = _marker_path(directory)
    if not p.is_file():
        return None
    v = p.read_text().strip()
    return v or None


def active(directory: str) -> bool:
    """Marker-only workspace-detect: True iff the local marker declares an
    origin. Deliberately ignores SWARM_ORIGIN (see the module docstring)."""
    return origin_of(directory) is not None


def _key(origin: str) -> str:
    return hashlib.sha256(origin.encode()).hexdigest()[:16]


def ledger_dir(origin: str) -> Path:
    """The OUT-OF-TREE ledger root for an origin (XDG data dir, keyed by an
    origin hash). Never under the repo working dir — a swarm ledger is not a
    repo artifact, and re-pointing the origin yields a distinct dir."""
    root = os.environ.get("XDG_DATA_HOME")
    if not root:
        root = str(Path.home() / ".local" / "share")
    return Path(root) / _TAG / "harness" / _key(origin)


def bootstrap(directory: str, origin: str) -> dict:
    """Declare the origin for this workspace + provision the out-of-tree
    ledger dir. Idempotent: re-bootstrapping the SAME origin is a no-op;
    bootstrapping a DIFFERENT origin is REFUSED (re-pointing would change
    where future writes land — a push-target change is not a silent no-op).
    Returns {marker, created, refused?}."""
    existing = origin_of(directory)
    if existing is not None:
        if existing == origin:
            ledger_dir(origin).mkdir(parents=True, exist_ok=True)
            return {"marker": str(_marker_path(directory)),
                    "created": False}
        return {"marker": str(_marker_path(directory)),
                "created": False, "refused": "origin-changed"}
    p = _marker_path(directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(origin + "\n")
    ledger_dir(origin).mkdir(parents=True, exist_ok=True)
    return {"marker": str(p), "created": True}


def ledger_entry(origin: str, name: str) -> Path:
    """A named JSONL ledger under the out-of-tree dir (run/gate ledgers)."""
    return ledger_dir(origin) / name


def _read_jsonl(p: Path) -> list:
    if not p.is_file():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _append_jsonl(p: Path, entry: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------- run/gate ledgers
#
# Durable, out-of-tree JSONL logs of what a session did on the substrate. The
# run ledger records the work legs (claim / work_started / work_done /
# verdict); the gate ledger records the backstop's per-idle decisions. The
# plugin appends here after each leg; a human reads them (or `flockwork debug`
# merges them into the timeline) after the fact. They are keyed by origin, so
# two boards on one machine never interleave.

def append_run_ledger(origin: str, session: str, entry: dict) -> None:
    _append_jsonl(ledger_entry(origin, "run-ledger.jsonl"),
                 {"session": session, **entry})


def read_run_ledger(origin: str) -> list:
    return _read_jsonl(ledger_entry(origin, "run-ledger.jsonl"))


def append_gate_ledger(origin: str, session: str, entry: dict) -> None:
    _append_jsonl(ledger_entry(origin, "gate-ledger.jsonl"),
                 {"session": session, **entry})


def read_gate_ledger(origin: str) -> list:
    return _read_jsonl(ledger_entry(origin, "gate-ledger.jsonl"))


# ------------------------------------------------- durable session map
#
# The session->[(task,att)] map. It is the bridge that lets the idle backstop
# (C11) know which claims a live opencode session is holding. It lives on disk
# (the out-of-tree ledger dir), NOT in a process global, so it survives an
# opencode restart: a session that is resumed in a fresh process still maps to
# the claims it claimed before the restart.

def _session_map_path(origin: str) -> Path:
    return ledger_dir(origin) / "session-map.json"


def session_map(origin: str) -> dict:
    """The full durable map: {sessionID: {task: att}}. {} when absent/corrupt
    (a corrupt map degrades to 'no live claims' — the backstop then no-ops
    rather than guessing)."""
    p = _session_map_path(origin)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def session_claims(origin: str, session: str) -> dict:
    """The live {task: att} a session holds, or {} (unknown session)."""
    return dict(session_map(origin).get(session, {}))


def _write_session_map(origin: str, m: dict) -> None:
    p = _session_map_path(origin)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=1) + "\n")


def session_put(origin: str, session: str, task: str, att: str) -> None:
    """Record a live claim (called on a successful claim / work_started)."""
    m = session_map(origin)
    m.setdefault(session, {})[task] = att
    _write_session_map(origin, m)


def session_drop(origin: str, session: str, task: str) -> None:
    """Drop a settled claim (called on work_done / terminal verdict). A session
    with no remaining claims is removed so the map does not accumulate."""
    m = session_map(origin)
    sess = m.get(session)
    if not sess:
        return
    sess.pop(task, None)
    if not sess:
        m.pop(session, None)
    else:
        m[session] = sess
    _write_session_map(origin, m)
