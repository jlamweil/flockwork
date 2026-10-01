"""Contract for the gate verbs in the lane law (WQ-032, INT-085).

WQ-025's pilot landed the verdict-count gate as a STANDALONE module
(l2/gates.py, its own suite) — deliberately unwired, because the lane
law cannot move under live workers. This suite pins the integration:
the law stays the single CLI surface (python3 l2/inrepo.py review|gate)
by THIN delegation to the sibling module — the law adds no second
implementation to drift — and audit() gains integrated-ref visibility
without a new failure class. The lone-law story holds: an inrepo.py
staged without l2/ still boots; the gate verb refuses honestly there,
because unlike metrics (observe-only) a gate run mutates the board and
must never fake a success-shaped no-op (wave-11 law).
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_inrepo(name="inrepo_gate_under_test"):
    p = REPO / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_gates():
    p = REPO / "l2" / "gates.py"
    spec = importlib.util.spec_from_file_location("gates_under_law_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
    """A repo cwd for the object store (the test_claim_loss lesson)."""
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


def _seed_board(repo, origin, task, n=1, m=1):
    """Spec + return leaf + final verdict leaf, so evidence resolves."""
    spec = _commit_body(repo, f"spec {task}\nn: {n}\nm: {m}\n")
    _push_ref(repo, origin, spec, f"refs/swarm/specs/{task}")
    fix = _commit_body(repo, "fix whatever")
    _push_ref(repo, origin, fix, f"refs/swarm/tasks/{task}")
    _push_ref(repo, origin, fix, f"refs/swarm/verdicts/{task}")


def _law_cli(args, cwd, origin):
    env = dict(os.environ, SWARM_ORIGIN=str(origin))
    return subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"), *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


def _last_json(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


@pytest.fixture()
def board(tmp_path, monkeypatch):
    o = _origin(tmp_path)
    w = _work_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.chdir(w)
    return {"origin": str(o), "repo": w, "tmp": tmp_path}


# ------------------------------------------------- the lone-law boot story


def test_lone_law_copy_boots_without_gate_sibling(tmp_path, monkeypatch,
                                                  capsys):
    """inrepo.py staged WITHOUT l2/ still imports (the lawgate lone-law
    pattern): the gate sibling resolves lazily, its absence leaves the
    law bootable. The verbs refuse honestly there — a gate run MUTATES
    the board, so the fallback is a refusal, never a silent no-op."""
    lone = tmp_path / "staged" / "inrepo.py"
    lone.parent.mkdir(parents=True)
    lone.write_text((REPO / "l2" / "inrepo.py").read_text())
    # scrub any l2 dir an earlier sibling-resolution inserted into this
    # session's sys.path, and evict the cached sibling itself — earlier
    # inrepo loads in the same session leave sys.modules["gates"] set,
    # and the import machinery consults that cache BEFORE any path
    l2dir = str(REPO / "l2")
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path
                      if os.path.abspath(p or os.getcwd()) != l2dir])
    monkeypatch.setitem(sys.modules, "gates", None)
    spec = importlib.util.spec_from_file_location("inrepo_lone_law", lone)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # boot: must not raise
    assert m.gates is None
    rc = m.gate_cmd("T")
    assert rc == 2
    ev = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert ev["event"] == "gate" and ev["refused"] is True
    rc = m.review_cmd("T", "rev-a", "agree")
    assert rc == 2
    ev = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert ev["event"] == "review_verdict" and ev["refused"] is True


# ---------------------------------------------- review verb (thin, same events)


def test_review_verb_same_json_event_shape_as_gate_module(board):
    """The law CLI's review event is the gate module's event — same key
    set, same pushed/refused semantics — only the surface differs."""
    events = {}
    for build in ("law", "module"):
        o = _origin(board["tmp"] / f"cmp-{build}")  # fresh identical board
        w = _work_repo(board["tmp"] / f"cmp-{build}")
        _seed_board(w, o, "T")
        if build == "law":
            argv = [sys.executable, str(REPO / "l2" / "inrepo.py")]
        else:
            argv = [sys.executable, str(REPO / "l2" / "gates.py")]
        proc = subprocess.run(
            [*argv, "review", "T", "rev-a", "agree"], cwd=w,
            env=dict(os.environ, SWARM_ORIGIN=str(o)),
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        ev = _last_json(proc)
        assert ev["event"] == "review_verdict"
        assert ev["pushed"] is True and ev["rejected"] is False
        events[build] = ev
    # same JSON event, different surface: identical key sets (the shas
    # differ — separate boards, separate commits — so keys are the pin)
    assert sorted(events["law"].keys()) == sorted(events["module"].keys())
    assert sorted(events["law"].keys()) == sorted(
        ["event", "task", "reviewer", "outcome", "ref", "sha",
         "pushed", "rejected", "reason"])


def test_review_verb_records_veto_and_rejects_duplicates(board):
    proc = _law_cli(["review", "T", "rev-a", "veto"],
                    board["repo"], board["origin"])
    assert proc.returncode == 0, proc.stderr
    ev = _last_json(proc)
    assert ev["outcome"] == "veto" and ev["pushed"] is True
    dup = _law_cli(["review", "T", "rev-a", "agree"],
                   board["repo"], board["origin"])
    assert dup.returncode == 1  # create-once: a reviewer votes once
    ev = _last_json(dup)
    assert ev["rejected"] is True and ev["pushed"] is False


# ------------------------------------------------ gate verb (fire, flip, hold)


def test_gate_verb_fires_flips_and_is_idempotent(board):
    _seed_board(board["repo"], board["origin"], "T", n=1, m=1)
    _law_cli(["review", "T", "rev-a", "agree"], board["repo"],
             board["origin"])
    proc = _law_cli(["gate", "T"], board["repo"], board["origin"])
    assert proc.returncode == 0, proc.stderr
    ev = _last_json(proc)
    assert ev["fired"] is True and ev["flipped"] is True
    before = _git("ls-remote", board["origin"],
                  "refs/swarm/integrated/T").stdout
    again = _law_cli(["gate", "T"], board["repo"], board["origin"])
    assert again.returncode == 0  # idempotent no-op stays success
    ev = _last_json(again)
    assert ev["already_integrated"] is True and ev["flipped"] is False
    after = _git("ls-remote", board["origin"],
                 "refs/swarm/integrated/T").stdout
    assert before == after


def test_gate_verb_holds_at_a_veto(board):
    _seed_board(board["repo"], board["origin"], "T", n=2, m=3)
    for rev, out in (("rev-a", "agree"), ("rev-b", "agree"),
                     ("rev-c", "veto")):
        _law_cli(["review", "T", rev, out], board["repo"], board["origin"])
    proc = _law_cli(["gate", "T"], board["repo"], board["origin"])
    assert proc.returncode == 1  # a live veto holds the gate closed
    ev = _last_json(proc)
    assert ev["fired"] is False and ev["veto"] == 1
    raw = _git("ls-remote", board["origin"], "refs/swarm/integrated/T")
    assert raw.stdout.strip() == ""


def test_law_gate_cmd_delegates_to_the_gate_module(board, capsys):
    """Thin delegation, pinned by equality: after a flip, the law's
    gate verb and the module return the SAME already-integrated event."""
    g = _load_gates()
    inrepo = _load_inrepo()
    _seed_board(board["repo"], board["origin"], "T", n=1, m=1)
    g.review_verdict("T", "rev-a", "agree", "refs/swarm/tasks/T")
    g.gate("T")
    capsys.readouterr()
    rc = inrepo.gate_cmd("T")
    law_ev = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    module_ev = g.gate("T")
    assert rc == 0
    assert law_ev == module_ev
    assert law_ev["already_integrated"] is True


# --------------------------------------------- audit integrated-ref visibility


def test_audit_shows_integrated_true_and_flip_sha(board, capsys):
    inrepo = _load_inrepo()
    _seed_board(board["repo"], board["origin"], "T")
    sha = _commit_body(board["repo"], "integrated\ntask: T\n")
    _push_ref(board["repo"], board["origin"], sha,
              "refs/swarm/integrated/T")
    inrepo.audit(["T"])
    out = json.loads(capsys.readouterr().out)
    t = out["tasks"]["T"]
    assert t["integrated"] is True
    assert t["integrated_sha"] == sha
    # visibility only: h1 and the orphan census are untouched by it
    assert out["h1_pass"] is True
    assert out["claim_orphans"] == []


def test_audit_absent_integration_shows_nothing(board, capsys):
    """Absence shows NOTHING — no integrated keys, no new failure class
    (a healthy board reads exactly as it did before the field existed)."""
    inrepo = _load_inrepo()
    _seed_board(board["repo"], board["origin"], "T")
    inrepo.audit(["T"])
    out = json.loads(capsys.readouterr().out)
    t = out["tasks"]["T"]
    assert "integrated" not in t and "integrated_sha" not in t
    assert out["h1_pass"] is True
    assert out["claim_orphans"] == []
