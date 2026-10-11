"""Contracts for the law-freshness boundary (C8').

C1-C7 extended the law file (l2/inrepo.py) with the separable legs + the
external-work mode. The boundary this pins:
  - the HEADLESS worker loop runs law_gate at claim time: it must run origin
    main's published copy of the law. A worker whose local law matches the
    published law proceeds; a diverged (stale) law is refused (fail closed) —
    so using the new law on a deployed board requires re-publishing the law
    to origin main + re-provisioning the workers;
  - the EXTERNAL-WORK verbs (work_external/judge/land/verdict/renew) are
    operator/plugin-initiated and do NOT run the gate — they work regardless
    of the worker's law freshness (a fresh interactive session re-claims
    under the current law; the gate is a headless-loop concern only).

The headless path itself is byte-for-byte preserved by the C1-C3 extractions;
the full E2E suite is the gate for that.
"""
import importlib.util
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
REAL_LAW = (REPO / "l2" / "inrepo.py").read_text()


def _load_inrepo():
    spec = importlib.util.spec_from_file_location(
        "inrepo", REPO / "l2" / "inrepo.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return ["-c", "user.email=a@b", "-c", "user.name=a"]


def _publish_law(tmp_path, law_content, name="origin.git"):
    o = tmp_path / name
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / f"law-{name}"
    _git("init", "-q", "-b", "main", str(w))
    (w / "l2").mkdir()
    (w / "l2" / "inrepo.py").write_text(law_content)
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "law")
    _git("-C", str(w), *_cid(), "push", "-q", str(o), "main")
    return str(o)


def test_law_gate_proceeds_when_the_law_is_published(tmp_path):
    """A headless worker whose local law matches origin main's published copy
    proceeds (law_gate -> None) — the new law, once published, is usable by
    re-provisioned workers."""
    m = _load_inrepo()
    o = _publish_law(tmp_path, REAL_LAW)
    assert m.law_check(o)["status"] == "match"
    assert m.law_gate(o, "w1", "T") is None  # proceed


def test_law_gate_refuses_a_stale_law(tmp_path):
    """A worker whose local law differs from origin main's published copy is
    refused (fail closed) — the deployed-board boundary: re-publish the law
    + re-provision before the headless loop can use it."""
    m = _load_inrepo()
    o = _publish_law(tmp_path, "# stale published law\nprint('old')\n")
    assert m.law_check(o)["status"] == "stale"
    ev = m.law_gate(o, "w1", "T")
    assert ev is not None
    assert ev["event"] == "law_freshness_refusal"


def test_external_work_verbs_do_not_run_the_law_gate(tmp_path):
    """The external-work verbs are operator-initiated: they do NOT run the
    law_gate, so they work even against a board whose published law is stale
    (a fresh interactive session re-claims under the current law)."""
    m = _load_inrepo()
    o = _publish_law(tmp_path, "# stale published law\nprint('old')\n")
    assert m.law_check(o)["status"] == "stale"  # the headless gate WOULD refuse
    # but the external-work leg (operator-initiated) still runs:
    tree = tmp_path / "tree"
    _git("init", "-q", "-b", "main", str(tree))
    (tree / "f.txt").write_text("x\n")
    _git("-C", str(tree), "add", "-A")
    _git("-C", str(tree), *_cid(), "commit", "-qm", "seed")
    jres = m.judge_tree(str(tree), "T", "true")  # no gate refusal, clean result
    assert jres["rc"] == 0
    assert jres["timed_out"] is False
