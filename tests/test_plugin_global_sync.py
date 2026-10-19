"""Deployment drift-guard: the global opencode plugin deployed on this box
must stay a real-file byte-identical copy of the repo source.

opencode-plugin/lib/flockwork.ts  (repo source of truth)
  ->
~/.config/opencode/lib/flockwork.ts  (deployed; a symlink breaks the loader)

Skips when the global plugin is not present (e.g. on CI / other machines that
don't deploy the global plugin here) — it only guards THIS box against drift.
Use `opencode-plugin/sync-global.sh --check` (or `--sync`) to manage it.
"""
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
SYNC = REPO / "opencode-plugin" / "sync-global.sh"
SRC = REPO / "opencode-plugin" / "lib" / "flockwork.ts"
GLOBAL = pathlib.Path.home() / ".config" / "opencode" / "lib" / "flockwork.ts"


def test_sync_script_is_runnable():
    assert SYNC.exists()


def test_global_plugin_is_a_real_file_in_sync():
    if not GLOBAL.exists():
        import pytest

        pytest.skip("global opencode plugin not deployed on this box")
    proc = subprocess.run([str(SYNC), "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert not GLOBAL.is_symlink()


def test_global_plugin_matches_repo_source():
    if not (SRC.exists() and GLOBAL.exists()):
        import pytest

        pytest.skip("global plugin or repo source not available")
    assert SRC.read_bytes() == GLOBAL.read_bytes()
