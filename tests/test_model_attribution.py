"""Contract for correct model attribution (write-side).

The dispatched model is `SWARM_MODEL`, the exact `-m <model>` string the
worker hands opencode. It is recorded into the ref BODIES — the claim (at
claim time, the pre-dispatch checkpoint) and the verdict (redundantly, as
the authoritative record) — as a structured `model:` line, so the model is
durable on the checkpoint that survives a dispatch death and readable for
every attempt (archived, live, final).

The load-bearing invariant (F5): the att stays the LAST token of the claim
body. Every att reader — sweep, relabel, correction_graph, audit — extracts
it as `body.split()[-1]`; a `model:` line may precede the att, never follow
it. These tests pin that.

Hermetic: boards are local bare repos under tmp_path; the model is set via
env; the production host is never contacted.
"""
import importlib.util
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_inrepo():
    p = REPO / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_ma", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return ["-c", "user.email=a@b", "-c", "user.name=a"]


def _root_commit(w, msg):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    return _git("-C", w, *_cid(), "commit-tree", et, "-m", msg).stdout.strip()


def _push(o, w, src, ref):
    r = _git("-C", w, *_cid(), "push", "-q", o, f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


def _body(o, ref):
    return _git("-C", str(o), "log", "-1", "--format=%B", ref).stdout


def _lane(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "s")
    return str(o), str(w)


# ------------------------------------------------------------ write-side


def test_verdict_body_records_model(monkeypatch):
    """The verdict body carries the dispatched model as `model: <model>`."""
    inrepo = _load_inrepo()
    monkeypatch.setenv("SWARM_MODEL", "t3/unistra-coder")
    body = inrepo.verdict_body("T", "att-w1-abcd12", True, "w1", 0, 0)
    assert "model: t3/unistra-coder" in body
    assert "attempt: att-w1-abcd12" in body  # unchanged fields intact


def test_claim_body_records_model_before_att(monkeypatch, tmp_path):
    """claim_detail writes `model: <model>` into the claim body with the att
    still the last token (the F5 invariant)."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_MODEL", "t3/unistra-coder")
    monkeypatch.chdir(w)
    res = inrepo.claim_detail("w1", "T-claim")
    assert res["att"] is not None
    body = _body(o, "refs/swarm/claims/T-claim")
    assert "model: t3/unistra-coder" in body
    assert body.strip().split()[-1] == res["att"]  # att is LAST
    lines = body.splitlines()
    model_i = next(i for i, l in enumerate(lines) if l.startswith("model:"))
    att_i = next(i for i, l in enumerate(lines) if l.strip() == res["att"])
    assert model_i < att_i  # model line precedes the att line


def test_claim_body_empty_model_omits_line(monkeypatch, tmp_path):
    """An empty SWARM_MODEL yields no misleading `model:` line; the att
    stays the last token."""
    inrepo = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_MODEL", "")
    monkeypatch.chdir(w)
    res = inrepo.claim_detail("w1", "T-empty")
    body = _body(o, "refs/swarm/claims/T-empty")
    assert not any(l.strip().startswith("model:") for l in body.splitlines())
    assert body.strip().split()[-1] == res["att"]


def test_att_last_invariant_readers(monkeypatch, tmp_path):
    """Guard (F5): with a 3-line claim body (model before att), the readers
    still recover the att. A future reorder (model after att) breaks both."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    att = "att-mm-unistra-x-deadbeef"
    _push(o, w, _root_commit(w, "the brief\nverify: true"),
          "refs/swarm/specs/T-inv")
    _push(o, w, _root_commit(w, f"claim T-inv\nmodel: t3/unistra-coder\n{att}"),
          "refs/swarm/claims/T-inv")
    inrepo = _load_inrepo()
    # correction_graph recovers the att from the body
    g = inrepo.correction_graph(o)
    assert att in [a["att"] for a in g["tasks"]["T-inv"]["attempts"]]
    # sweep (att=None -> reads the claim body's last token) archives under it
    sw = inrepo.sweep(o, "T-inv")
    assert sw["att"] == att


# ------------------------------------------------------------ read-side


def test_graph_model_from_body(monkeypatch, tmp_path):
    """The graph reads the model from the ref body (the exact dispatched
    model), not from the att token."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-g")
    _push(o, w, _root_commit(
        w, "claim T-g\nmodel: t3/unistra-coder\natt-w1-aaaa01"),
        "refs/swarm/claims/T-g")
    _push(o, w, _root_commit(
        w, "verdict\ntask: T-g\nattempt: att-w1-aaaa01\nfixed: true\n"
           "host: w1\noc_rc: 0\npytest_rc: 0\nmodel: t3/unistra-coder"),
        "refs/swarm/verdicts/T-g")
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    atts = {a["att"]: a["model"] for a in g["tasks"]["T-g"]["attempts"]}
    assert atts["att-w1-aaaa01"] == "t3/unistra-coder"


def test_graph_mm_att_uses_body_not_split(monkeypatch, tmp_path):
    """An mm-* att is attributed from the body's model, never from the
    att-token split (the old mm_model_of misparse). The retired function
    is gone."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-mm")
    _push(o, w, _root_commit(
        w, "claim T-mm\nmodel: t3/unistra-coder\natt-mm-unistra-a-deadbeef"),
        "refs/swarm/claims/T-mm")
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    atts = {a["att"]: a["model"] for a in g["tasks"]["T-mm"]["attempts"]}
    # body model wins; the split would have yielded "unistra"
    assert atts["att-mm-unistra-a-deadbeef"] == "t3/unistra-coder"
    assert not hasattr(inrepo, "mm_model_of")


def test_graph_unknown_fallback(monkeypatch, tmp_path):
    """A pre-protocol claim body (no `model:` line) attributes honestly as
    'unknown', never a guess."""
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    _push(o, w, _root_commit(w, "the brief\nverify: true"), "refs/swarm/specs/T-unk")
    _push(o, w, _root_commit(w, "claim T-unk att-w1-aaaa01"),
          "refs/swarm/claims/T-unk")  # legacy one-line body, no model
    inrepo = _load_inrepo()
    g = inrepo.correction_graph(o)
    atts = {a["att"]: a["model"] for a in g["tasks"]["T-unk"]["attempts"]}
    assert atts["att-w1-aaaa01"] == "unknown"
