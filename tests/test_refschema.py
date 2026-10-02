"""Contract for the ref schemas (WQ-054, INT-081 TAKE 1 — the schema IS
the instruction).

A task seed's spec ref must carry the fields a worker needs to act
(the what = the brief body, the done-bar = the verify: line); a
reviewer-verdict ref must carry the fields a gate needs to count
(task, reviewer, outcome, evidence — docs/GATES.md §2). Malformed refs
are a LOUD typed refusal at claim/gate time naming the missing field,
never a silent pass — the failure mode being killed is the WQ-025
pilot class: attempts dispatching on underspecified seeds and stalling.

Minimality is the law: exactly two required spec fields, everything
else optional (n:/m:, deliverable:, kind:, prose all stay data). The
'where' class stays optional because every healthy seed in the wild
already names its paths in prose or a deliverable: line — requiring a
dedicated line would reject the kit's own demo spec. verify: IS
required: it is the only machine-checkable done-bar (the L4 oracle
reads it), and every seeded spec that ever passed a claim in this
repo's suites and pilots carries one.

Adoption is additive: well-formed refs behave byte-identically (same
events, same keys, same outcomes); the refusal rides the wave-9
env-death-before-dispatch machinery (heir-gated — wave-10's churn
law) because a schema-invalid spec is exactly that class, finer
grained; and a law file staged without the l2/ sibling still boots
with NO schema enforcement — the pre-schema law (the lawgate
lone-law pattern, WQ-024/WQ-032 contract).
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _refschema():
    return _load("refschema_under_test", REPO / "l2" / "refschema.py")


def _inrepo(name="inrepo_refschema_under_test"):
    return _load(name, REPO / "l2" / "inrepo.py")


def _gates(name="gates_refschema_under_test"):
    return _load(name, REPO / "l2" / "gates.py")


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _ok(r):
    return r.returncode == 0


def _origin(tmp_path):
    """Scratch bare origin with one commit on main (the house T5 shape)."""
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    seed_repo = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(seed_repo))
    (seed_repo / ".seed").write_text("x\n")
    _git("-C", str(seed_repo), "add", "-A")
    _git("-C", str(seed_repo), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    _git("-C", str(seed_repo), "push", "-q", str(o), "main:refs/heads/main")
    return o


def _work_repo(base):
    w = base / "node"
    _git("init", "-q", "-b", "main", str(w))
    (w / ".seed").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "s")
    return w


def _commit_body(repo, body):
    r = _git("-C", str(repo), "hash-object", "-t", "tree", "/dev/null")
    tree = r.stdout.strip()
    r = _git("-C", str(repo), "commit-tree", tree, "-m", body)
    assert _ok(r), r.stderr
    return r.stdout.strip()


def _push_ref(repo, origin, sha, ref):
    r = _git("-C", str(repo), "push", "-q", str(origin), f"{sha}:{ref}")
    assert _ok(r), r.stderr
    return sha


GOOD_SPEC = "task T: add hello.txt\n\nwhere: repo root\n\nverify: grep -qx hi hello.txt"


def gv(task="T", reviewer="rev-a", outcome="agree",
       evidence="refs/swarm/tasks/T"):
    """A §2-well-formed review-verdict body (parameterized for boards)."""
    return (f"review-verdict\ntask: {task}\nreviewer: {reviewer}\n"
            f"outcome: {outcome}\nevidence: {evidence}\n")


GOOD_VERDICT = gv()


# ------------------------------------------------- the module: spec schema


def test_spec_schema_two_required_fields_and_optional_rest():
    rs = _refschema()
    assert rs.validate_spec(GOOD_SPEC) == []
    # everything else is optional data: n:/m:, deliverable:, kind:, prose
    extras = (GOOD_SPEC + "\nn: 3\nm: 3\ndeliverable: docs/x.md\n"
              "kind: doc-review\n")
    assert rs.validate_spec(extras) == []


def test_spec_schema_refuses_missing_done_bar_naming_the_field():
    rs = _refschema()
    errs = rs.validate_spec("task T: add hello.txt\n\nwhere: repo root\n")
    assert len(errs) == 1 and "verify" in errs[0]


def test_spec_schema_refuses_empty_body_naming_the_field():
    rs = _refschema()
    for body in ("", "   \n  "):
        errs = rs.validate_spec(body)
        assert "what" in "; ".join(errs)
        assert "verify" in "; ".join(errs)  # both required fields, named
    # a verify:-only stub still lacks its what
    errs = rs.validate_spec("verify: true")
    assert len(errs) == 1 and "what" in errs[0]


# ------------------------------------------- the module: review-verdict schema


def test_review_verdict_schema_happy_path_returns_fields():
    rs = _refschema()
    fields, errs = rs.validate_review_verdict(GOOD_VERDICT, "T", "rev-a")
    assert errs == []
    assert fields["outcome"] == "agree"
    assert fields["evidence"] == "refs/swarm/tasks/T"


def test_review_verdict_schema_refusals_are_typed_per_field():
    rs = _refschema()
    cases = [
        # (body, expected substrings in the joined errors)
        ("verdict\ntask: T\nreviewer: rev-a\noutcome: agree\n"
         "evidence: x\n", ["review-verdict"]),
        ("review-verdict\ntask: OTHER\nreviewer: rev-a\noutcome: agree\n"
         "evidence: x\n", ["task"]),
        ("review-verdict\ntask: T\nreviewer: rev-b\noutcome: agree\n"
         "evidence: x\n", ["reviewer"]),
        ("review-verdict\ntask: T\nreviewer: rev-a\noutcome: maybe\n"
         "evidence: x\n", ["outcome"]),
        ("review-verdict\ntask: T\nreviewer: rev-a\noutcome: agree\n",
         ["evidence"]),
    ]
    for body, needles in cases:
        fields, errs = rs.validate_review_verdict(body, "T", "rev-a")
        assert fields is None
        joined = "; ".join(errs)
        for n in needles:
            assert n in joined, (body, joined)


def test_module_and_gates_fallback_agree_on_every_check():
    """One schema, no drift: the module's verdict and gates.py's inline
    lone-law fallback agree on which bodies are verdicts (parity pin)."""
    rs = _refschema()
    g = _gates()
    bodies = [GOOD_VERDICT, GOOD_VERDICT.replace("agree", "veto"),
              GOOD_VERDICT.replace("reviewer: rev-a", "reviewer: rev-b"),
              "verdict\ntask: T\n", ""]
    for body in bodies:
        fields, errs = rs.validate_review_verdict(body, "T", "rev-a")
        inline_fields, inline_err = g.parse_review_verdict(body, "T", "rev-a")
        assert (fields is None) == (inline_fields is None), body
        if errs:
            assert inline_err, body


# ------------------------------------------------------- claim-side refusal


def _shim(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)
    return p


def _run_worker(tmp_path, monkeypatch, spec_body, shim_body):
    """Full worker() run on a scratch board with a deterministic dispatch
    shim (the test_metrics e2e shape, metrics off — the default wire)."""
    o = _origin(tmp_path)
    w = _work_repo(tmp_path)
    canary = tmp_path / "dispatched.stamp"
    shim = _shim(tmp_path, "dispatch.sh",
                 f"{shim_body}; touch {canary}")
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.setenv("OPENCODE_BIN", str(shim))
    monkeypatch.setenv("SWARM_WORKER_MAX_FAILS", "3")
    monkeypatch.chdir(w)
    inrepo = _inrepo()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"T1": spec_body}))
    assert inrepo.seed(str(spec))["T1"] is True
    r = subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"), "worker", "wm"],
        cwd=str(w), env=dict(os.environ),
        capture_output=True, text=True, timeout=120)
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    return r.returncode, events, canary


def test_claim_on_spec_without_done_bar_is_typed_refusal_before_dispatch(
        tmp_path, monkeypatch):
    """The WQ-025 stall class, killed: a spec with no machine-checkable
    done-bar is refused the moment its claimant reads it — typed reason
    naming verify:, zero dispatch (the shim never runs), and the wave-10
    churn law applies (first death requeues, budget buys one heir, the
    heir's refusal goes FINAL as the honest fixed:false verdict)."""
    rc, events, canary = _run_worker(
        tmp_path, monkeypatch,
        "task T1: add hello.txt\n\nwhere: repo root\n",  # no verify:
        "echo hi > hello.txt")
    assert not canary.exists()  # nothing was ever dispatched on the bad seed
    attempted = [e for e in events if e.get("event") == "attempted"]
    assert len(attempted) == 2
    assert attempted[0]["env_death"] is True
    assert attempted[0]["reason"] == "spec-schema-refused"
    assert "verify" in attempted[0]["err"]
    assert attempted[0]["requeued"] is True
    assert attempted[1]["heir_exhausted"] is True
    assert "verify" in attempted[1]["reason"] or \
        "verify" in attempted[1]["err"]
    # the final judgment: the honest fixed:false verdict REF is written
    # (verdict EVENTS are a metrics-layer line; the wire's story ends
    # in the attempted event's fixed/verdict_pushed pair)
    assert attempted[1]["fixed"] is False
    assert attempted[1]["verdict_pushed"] is True


def test_claim_on_well_formed_spec_is_byte_identical(tmp_path, monkeypatch):
    """Adoption is additive: a spec carrying its done-bar dispatches
    exactly as before — the attempted event gains NO schema keys."""
    rc, events, canary = _run_worker(
        tmp_path, monkeypatch, GOOD_SPEC.replace("task T:", "task T1:"),
        "echo hi > hello.txt")
    assert canary.exists()  # dispatched
    assert rc == 0
    attempted = [e for e in events if e.get("event") == "attempted"]
    assert len(attempted) == 1
    assert attempted[0]["fixed"] is True
    assert not any("schema" in k for k in attempted[0])
    # the landed success shape, pinned key for key: the schema adds none
    assert sorted(attempted[0].keys()) == sorted(
        ["event", "worker", "task", "att", "fixed", "pytest_rc", "oc_rc",
         "oc_err", "env_death", "heir_exhausted", "main_push",
         "already_on_main", "return_pushed", "verdict_pushed"])


def test_lone_law_without_schema_sibling_claims_unvalidated(
        tmp_path, monkeypatch):
    """inrepo.py staged without l2/ still boots AND keeps the pre-schema
    law: no sibling, no enforcement — the schema can never become a
    dependency of the wire (the WQ-024/WQ-032 lone-law contract)."""
    lone = tmp_path / "staged" / "inrepo.py"
    lone.parent.mkdir(parents=True)
    lone.write_text((REPO / "l2" / "inrepo.py").read_text())
    l2dir = str(REPO / "l2")
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path
                      if os.path.abspath(p or os.getcwd()) != l2dir])
    monkeypatch.setitem(sys.modules, "refschema", None)
    # the scratch ORIGIN is pinned BEFORE the lone copy loads: the law
    # captures SWARM_ORIGIN at import time, and a copy loaded with no
    # env aims at the production default (this test once pushed its
    # fixture spec to the real board that way — the 15:20 incident in
    # the receipt; the ordering here is the repair).
    o = _origin(tmp_path / "lone")
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    m = _load("inrepo_lone_law_refschema", lone)
    assert m.refschema is None
    assert "lone" in m.ORIGIN  # the copy aims at the scratch origin
    w = _work_repo(tmp_path / "lone")
    canary = tmp_path / "lone" / "dispatched.stamp"
    shim = _shim(tmp_path / "lone", "dispatch.sh",
                 f"echo hi > hello.txt; touch {canary}")
    monkeypatch.setenv("OPENCODE_BIN", str(shim))
    monkeypatch.setenv("SWARM_WORKER_MAX_FAILS", "3")
    monkeypatch.chdir(w)
    spec = tmp_path / "lone" / "spec.json"
    spec.write_text(json.dumps(
        {"T1": "task T1: add hello.txt\n\nno done-bar here\n"}))
    assert m.seed(str(spec))["T1"] is True
    r = subprocess.run(
        [sys.executable, str(lone), "worker", "wm"],
        cwd=str(w), env=dict(os.environ),
        capture_output=True, text=True, timeout=120)
    assert canary.exists()  # dispatched: unvalidated, exactly as before
    attempted = [json.loads(ln) for ln in r.stdout.splitlines()
                 if ln.strip() and json.loads(ln).get("event") == "attempted"]
    assert len(attempted) == 1
    assert attempted[0]["env_death"] is False  # past the spec read —
    # no schema refusal exists on the lone law (the pytest fallback may
    # then judge it however it judges it; the wire behavior is the pin)


# --------------------------------------------------------- gate-side refusal


def _seed_gate_board(w, o, task, n, m, verdict_bodies):
    spec = _commit_body(w, f"spec {task}\nn: {n}\nm: {m}\n")
    _push_ref(w, o, spec, f"refs/swarm/specs/{task}")
    fix = _commit_body(w, "fix whatever")
    _push_ref(w, o, fix, f"refs/swarm/tasks/{task}")
    for rev, body in verdict_bodies:
        sha = _commit_body(w, body)
        _push_ref(w, o, sha, f"refs/swarm/verdicts/{task}@{rev}")


def test_gate_refuses_malformed_verdict_typed_not_silent(tmp_path,
                                                         monkeypatch):
    """A verdict failing the §2 schema is a typed refusal at count time:
    never a vote, and the gate event NAMES the field (loud). The fire
    predicate stays the landed n-of-valid `==` rule (the WQ-052
    owner-pinned degeneracy, untouched) — the board here holds at
    n=2 with one valid agree + one schema-refused body."""
    o = _origin(tmp_path)
    w = _work_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.chdir(w)
    bad = gv(outcome="maybe")
    _seed_gate_board(w, o, "T", 2, 2, [("rev-a", gv()), ("rev-b", bad)])
    g = _gates()
    ev = g.gate("T")
    assert ev["fired"] is False
    assert ev["agree"] == 1  # the refused body is not a vote
    assert ev["invalid"] == 1
    assert any("outcome" in r for r in ev["invalid_reasons"])
    # a different schema failure names its own field
    o2 = _origin(tmp_path / "two")
    w2 = _work_repo(tmp_path / "two")
    monkeypatch.setenv("SWARM_ORIGIN", str(o2))
    monkeypatch.chdir(w2)
    bad2 = gv("T2", "rev-b").replace("reviewer: rev-b",
                                     "reviewer: rev-zz")
    _seed_gate_board(w2, o2, "T2", 2, 2, [("rev-a", gv("T2", "rev-a")),
                                          ("rev-b", bad2)])
    ev2 = g.gate("T2")
    assert ev2["invalid"] == 1
    assert any("reviewer" in r for r in ev2["invalid_reasons"])


def test_gate_event_well_formed_boards_gain_no_keys(tmp_path, monkeypatch):
    o = _origin(tmp_path)
    w = _work_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.chdir(w)
    _seed_gate_board(w, o, "T", 1, 1, [("rev-a", gv())])
    g = _gates()
    fired_ev = g.gate("T")
    fired_keys = sorted(fired_ev.keys())
    assert fired_ev["fired"] is True and "invalid_reasons" not in fired_ev
    assert fired_keys == sorted(
        ["event", "task", "n", "m", "agree", "veto", "invalid",
         "evidence_missing", "fired", "flipped", "already_integrated",
         "flip"])
    o2 = _origin(tmp_path / "hold")
    w2 = _work_repo(tmp_path / "hold")
    monkeypatch.setenv("SWARM_ORIGIN", str(o2))
    monkeypatch.chdir(w2)
    _seed_gate_board(w2, o2, "H", 2, 2, [("rev-a", gv("H", "rev-a"))])
    ev = g.gate("H")  # 1 agree of n=2: holds, well-formed, no new keys
    assert ev["fired"] is False
    assert sorted(ev.keys()) == sorted(
        ["event", "task", "n", "m", "agree", "veto", "invalid",
         "evidence_missing", "fired", "flipped", "already_integrated"])


# ---------------------------------------------------------- audit visibility


def test_audit_shows_schema_refusal_counts_observe_only(tmp_path,
                                                        monkeypatch, capsys):
    """audit() counts schema-refused refs on the board and names their
    fields — visibility only: h1, the orphan census and the per-task
    block are untouched (absence of refusals still shows the section,
    so a clean board reads as measured-zero, not as unmeasured)."""
    o = _origin(tmp_path)
    w = _work_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.chdir(w)
    inrepo = _inrepo()
    # a spec missing its done-bar + a reviewer verdict failing §2
    bad_spec = _commit_body(w, "spec B\nn: 1\nm: 1\n")  # no verify:
    _push_ref(w, o, bad_spec, "refs/swarm/specs/B")
    good_spec = _commit_body(w, f"spec A\n\n{GOOD_SPEC.split(chr(10), 1)[1]}")
    _push_ref(w, o, good_spec, "refs/swarm/specs/A")
    bad_v = _commit_body(w, gv("A", "rev-a", "maybe"))
    _push_ref(w, o, bad_v, "refs/swarm/verdicts/A@rev-a")
    inrepo.audit()
    out = json.loads(capsys.readouterr().out)
    assert out["schema"]["specs"]["total"] == 2
    refused = out["schema"]["specs"]["refused"]
    assert refused == ["B"]
    assert out["schema"]["reviewer_verdicts"]["total"] == 1
    rv = out["schema"]["reviewer_verdicts"]["refused"]
    assert len(rv) == 1 and "A@rev-a" in rv[0]["ref"] and "outcome" in ";".join(rv[0]["errors"])
    # observe-only: the pre-schema instrument reads exactly as before
    assert out["h1_pass"] is True
    assert out["claim_orphans"] == []


def test_audit_well_formed_board_counts_zero_and_lone_law_shows_nothing(
        tmp_path, monkeypatch, capsys):
    o = _origin(tmp_path)
    w = _work_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.chdir(w)
    inrepo = _inrepo()
    spec = _commit_body(w, "spec A\n\nwhat the task is\n\nverify: true\n")
    _push_ref(w, o, spec, "refs/swarm/specs/A")
    sha = _commit_body(w, gv("A", "rev-a"))
    _push_ref(w, o, sha, "refs/swarm/verdicts/A@rev-a")
    inrepo.audit()
    out = json.loads(capsys.readouterr().out)
    assert out["schema"]["specs"] == {"total": 1, "refused": []}
    assert out["schema"]["reviewer_verdicts"] == {"total": 1, "refused": []}
    assert out["h1_pass"] is True
    # lone-law audit: no sibling module, NO schema section at all —
    # byte-identical to the pre-schema instrument
    lone = tmp_path / "staged"
    lone.mkdir()
    (lone / "inrepo.py").write_text(
        (REPO / "l2" / "inrepo.py").read_text())
    l2dir = str(REPO / "l2")
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path
                      if os.path.abspath(p or os.getcwd()) != l2dir])
    monkeypatch.setitem(sys.modules, "refschema", None)
    m = _load("inrepo_lone_law_audit", lone / "inrepo.py")
    assert m.refschema is None
    capsys.readouterr()
    m.audit()
    out2 = json.loads(capsys.readouterr().out)
    assert sorted(out2.keys()) == ["claim_orphans", "h1_pass", "tasks"]


# --------------------------------------- gates.py lone-law (inline fallback)


def test_gates_lone_law_keeps_inline_schema_checks(tmp_path, monkeypatch):
    """gates.py staged without its schema sibling still boots and still
    refuses non-verdicts at count time (the inline §2 checks ARE the
    fallback) — but the refusal then carries no schema-module typing."""
    lone = tmp_path / "staged"
    lone.mkdir()
    (lone / "gates.py").write_text((REPO / "l2" / "gates.py").read_text())
    l2dir = str(REPO / "l2")
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path
                      if os.path.abspath(p or os.getcwd()) != l2dir])
    monkeypatch.setitem(sys.modules, "refschema", None)
    g = _load("gates_lone_law_refschema", lone / "gates.py")
    assert g.refschema is None
    fields, err = g.parse_review_verdict(
        GOOD_VERDICT.replace("outcome: agree", "outcome: maybe"),
        "T", "rev-a")
    assert fields is None and err
    fields, err = g.parse_review_verdict(GOOD_VERDICT, "T", "rev-a")
    assert fields is not None and err is None
