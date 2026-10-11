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
