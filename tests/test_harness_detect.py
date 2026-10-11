"""Contracts for the harness substrate (C8): marker-only workspace-detect,
bootstrap, and the out-of-tree ledger location.

The plugin is thin glue over this substrate; the substrate owns the policy.
The workspace-detect is MARKER-ONLY (a local, gitignored .flockwork/origin
file) — there is NO global env trigger (the SWARM_ORIGIN-env-activation was
the production-push hazard the oracle flagged; a stray env var must never
turn the plugin on). The ledgers live out-of-tree (XDG), keyed by origin.
"""
import importlib.util
import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "harness_w", REPO / "l2" / "harness.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_silent_when_no_marker(tmp_path):
    """No .flockwork/origin marker -> the plugin is silent (inactive), even
    if a stray SWARM_ORIGIN env var is set (the env must NOT trigger it)."""
    m = _load()
    env = dict(os.environ)
    env["SWARM_ORIGIN"] = "ssh://prod.example.invalid/board.git"
    os.environ["SWARM_ORIGIN"] = env["SWARM_ORIGIN"]
    try:
        assert m.active(str(tmp_path)) is False
        assert m.origin_of(str(tmp_path)) is None
    finally:
        os.environ.pop("SWARM_ORIGIN", None)


def test_marker_only_activates(tmp_path):
    """A local .flockwork/origin marker activates the workspace and is the
    sole origin source (read from the marker, not from env)."""
    m = _load()
    d = m.bootstrap(str(tmp_path), "git@board.example:repo.git")
    assert m.active(str(tmp_path)) is True
    assert m.origin_of(str(tmp_path)) == "git@board.example:repo.git"
    marker = tmp_path / ".flockwork" / "origin"
    assert marker.is_file()
    assert d["marker"] == str(marker)


def test_bootstrap_is_idempotent(tmp_path):
    """Re-bootstrapping the same workspace is a no-op (the marker is
    untouched; a re-bootstrap with a DIFFERENT origin is refused, not
    silently re-pointed — re-pointing would be a push-target change)."""
    m = _load()
    m.bootstrap(str(tmp_path), "git@board.example:repo.git")
    before = (tmp_path / ".flockwork" / "origin").read_text()
    again = m.bootstrap(str(tmp_path), "git@board.example:repo.git")
    assert again["created"] is False
    assert (tmp_path / ".flockwork" / "origin").read_text() == before
    refused = m.bootstrap(str(tmp_path), "git@other.example:other.git")
    assert refused["created"] is False
    assert refused["refused"] == "origin-changed"
    assert m.origin_of(str(tmp_path)) == "git@board.example:repo.git"


def test_ledger_dir_is_out_of_tree(tmp_path, monkeypatch):
    """The ledgers live OUT-OF-TREE (XDG), keyed by origin — never in the
    repo working dir, so a swarm ledger is not a repo artifact."""
    m = _load()
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    d = m.ledger_dir("git@board.example:repo.git")
    assert not str(d).startswith(str(tmp_path / "repo"))  # not the repo
    assert str(d).startswith(str(xdg))
    assert "flockwork" in str(d)
    # keyed by origin: a different origin -> a different dir
    d2 = m.ledger_dir("git@other.example:other.git")
    assert d != d2
