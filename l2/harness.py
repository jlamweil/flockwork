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


# ------------------------------------------------------- canonical protocol
#
# The single source of truth for the 2-step swarm protocol. The plugin injects
# it into the agent's system prompt (C10); the /flockwork skill (C13) and the
# docs reference it, so every surface teaches the SAME loop. It is
# deterministic (byte-stable across turns — a churn-free system prompt).

PROTOCOL = """\
You are working a task on a flockwork swarm board. Follow this exact ordered
protocol. The `fw_*` tools are the ONLY way to touch the board; the board (git
refs) is the durable truth — never assume state from the conversation alone.

Step 1 — understand, then claim
  1. fw_brief <task>      read the brief: what to do + the `verify` command.
  2. fw_claim <task>      take the lease; you get back an `att` (this attempt).
     If the claim fails (already claimed / rejected), STOP and say so — do not
     retry blindly.

Step 2 — work, prove, land, settle
  3. fw_work_started <task> <att>   checkpoint that work has begun.
  4. Make the change in the working tree (edit the files you need to).
  5. fw_judge <task> <tree>         run the verify command (the oracle). THIS
     MAY BLOCK FOR MINUTES — it is the merit leg, not an error to avoid.
  6. fw_land <task> <att> <tree> [paths...]
     commit ONLY the files you changed and push the scoped fix + return ref.
  7. fw_verdict <task> <att> <fixed> <oc_rc> <pytest_rc> [oc_elapsed]
     the terminal, create-once verdict (exactly once).
  8. fw_work_done <task> <att>      checkpoint that work is complete.

Honesty rules (non-negotiable)
  - A FAILING oracle is an honest result: land what you have, write
    fixed=false, and say why. Do not weaken the verify to make it pass.
  - The `verify` command is human-authored. When seeding a task, EXTRACT the
    verify from the human's message — never invent or loosen it. If it is
    missing or suspicious, ask before proceeding. Self-graded homework (you
    write your own acceptance test) is how fake fixed:true is born.
  - Always write fw_verdict before you stop. A task you claimed must end in a
    verdict (fixed true or false); stopping without one leaves an orphan claim
    that only the slow TTL+heir path resolves.
  - Never force-push. If fw_land reports a rejected push (behind main), it is a
    typed refusal — re-read main, retry ONCE, and if it still fails, stop and
    report. Do not fight the board.
  - During long work, periodically call fw_renew <task> <att> so your lease is
    not reaped as stale.
"""


def protocol() -> str:
    """The canonical 2-step protocol (see PROTOCOL). Deterministic."""
    return PROTOCOL


# ---------------------------------------------------------- idle backstop
#
# C11-lite. The plugin fires this on session.idle (and per-turn on
# chat.message for the lease renewal). It keeps a live session's claims
# alive + legible WITHOUT ever writing a terminal verdict:

#   idle (not abandoned)   -> renew each live claim + record the decision;
#   abandonment signal     -> also stamp the non-terminal `stalled:` checkpoint
#                             (rescue + legibility).
#
# A terminal verdict remains the TTL+heir's job: when a stalled/abandoned
# claim's lease expires, a fresh worker re-claims it and does the honest work
# (or the honest failed verdict). The backstop must never shortcut that —
# writing a verdict on a plain idle would kill in-progress work (the oracle's
# CORR-1).

def backstop(origin: str, session: str, abandoned: bool = False) -> dict:
    """Per live claim the session holds: renew the lease + record the gate
    decision; on abandonment, also stamp the non-terminal `stalled:` marker.
    NEVER writes a terminal verdict. Returns {session, claims, renewed,
    stalled}."""
    from l2 import inrepo  # the lease/claim legs live in the substrate

    claims = session_claims(origin, session)
    out: dict = {"session": session, "claims": len(claims),
                 "renewed": [], "stalled": []}
    for task, att in claims.items():
        renewed = inrepo.renew_claim(origin, task, att) is not None
        out["renewed"].append({"task": task, "att": att, "renewed": renewed})
        append_gate_ledger(origin, session, {
            "task": task, "att": att,
            "event": "abandoned" if abandoned else "idle",
            "renewed": renewed,
        })
        if abandoned:
            inrepo.mark_stalled(origin, task, att)
            out["stalled"].append(task)
    return out
