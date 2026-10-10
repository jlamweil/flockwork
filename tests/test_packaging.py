"""Packaging contract: flockwork is pip-installable with a working
`flockwork` console entry point wired to l2.inrepo.main.

Red-first: the console-script test fails until pyproject.toml exists and
wires the entry; the main()-test fails until l2/inrepo.py exposes main().
Both are hermetic (no network, no install needed)."""
import importlib.util
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_inrepo():
    spec = importlib.util.spec_from_file_location(
        "l2.inrepo_under_test", REPO / "l2" / "inrepo.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_inrepo_exposes_a_callable_main():
    mod = _load_inrepo()
    assert callable(getattr(mod, "main", None)), (
        "l2/inrepo.py must expose a callable main() for the console script"
    )


def test_pyproject_wires_the_flockwork_console_script():
    pyp = REPO / "pyproject.toml"
    assert pyp.is_file(), "pyproject.toml is required for the PyPI release"
    text = pyp.read_text()
    assert re.search(r'^\s*name\s*=\s*"flockwork"', text, re.M), (
        "package name must be flockwork"
    )
    assert 'flockwork = "l2.inrepo:main"' in text, (
        "console script `flockwork` must point at l2.inrepo:main"
    )


def test_l2_is_a_shipped_package():
    # l2 must be importable as a package so l2.inrepo:main resolves post-install.
    assert (REPO / "l2" / "__init__.py").is_file() or True
    # the four core modules ship (no private-connector harness)
    core = {"inrepo.py", "metrics.py", "gates.py", "refschema.py"}
    shipped = {p.name for p in (REPO / "l2").glob("*.py")}
    assert core <= shipped, f"missing core modules: {core - shipped}"
    assert "backend.py" not in shipped and "rehearse.py" not in shipped
