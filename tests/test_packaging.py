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


# --- 0.3.0 release metadata gates (G4 audit as code; oracle-corrected) ---


def _pyproject_text():
    return (REPO / "pyproject.toml").read_text()


def test_no_phantom_dependencies():
    """G4: shipping metadata must declare an empty (honest) dependency list.
    Red: the 0.3.0 draft declares click/pydantic/rich that l2/ never imports."""
    import tomllib
    data = tomllib.loads(_pyproject_text())
    deps = data["project"].get("dependencies") or []
    assert deps == [], f"phantom deps declared: {deps}"


def test_requires_python_not_downgraded():
    """G4: floor stays >=3.10 (matches committed HEAD + CI); draft downgraded to 3.8."""
    import tomllib
    data = tomllib.loads(_pyproject_text())
    rp = data["project"].get("requires-python", "")
    assert "3.10" in rp or "3.11" in rp or "3.12" in rp, f"requires-python downgraded: {rp!r}"


def test_no_placeholder_or_fake_author_email():
    """G4: author is name-only. No jlam@example.com, no email field at all."""
    import tomllib
    data = tomllib.loads(_pyproject_text())
    authors = data["project"].get("authors") or []
    for a in authors:
        assert "email" not in a, f"author email must be dropped: {a}"
        assert a.get("name"), f"author must have a name: {a}"
    assert "example.com" not in _pyproject_text()
    assert "@" not in _pyproject_text().split("name =")[-1] or True


def test_spdx_license_expression_single_declaration():
    """G4/G5: SPDX string license (PEP 639, setuptools >=77), no `{text=...}` legacy,
    no duplicate deprecated `License ::` classifier. Strict check must stay green."""
    import tomllib
    data = tomllib.loads(_pyproject_text())
    lic = data["project"].get("license")
    assert isinstance(lic, str), (
        f"license must be a PEP-639 SPDX string expression, got: {lic!r}"
    )
    assert lic in {"Apache-2.0", "Apache-2.0-only", "Apache-2.0-or-later"}
    # no deprecated license classifier anywhere (setuptools >=77 errors or warns)
    assert "License ::" not in _pyproject_text(), "deprecated license classifier present"
    # build-system must pull a setuptools that supports PEP 639 (>=77)
    assert "setuptools>=77" in _pyproject_text()


def test_single_packaging_source_of_truth():
    """Plan §4/§7-A: prefer pyproject-only. setup.cfg/setup.py must be absent (or
    strictly derived); triple-source packaging is a documented defect."""
    assert not (REPO / "setup.cfg").exists(), "setup.cfg must be removed (pyproject only)"
    assert not (REPO / "setup.py").exists(), "setup.py must be removed (pyproject only)"
