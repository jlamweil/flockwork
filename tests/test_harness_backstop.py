"""Contracts for the idle backstop, C11-lite (CORR-1).

The backstop fires on session.idle (and per-turn for the lease renewal). The
two invariants that make it safe to fire on EVERY idle:
  - it NEVER writes a terminal verdict on a plain idle (that would kill
    in-progress work — the two-turn case: idle after turn 1 must leave NO
    verdict ref);
  - on an abandonment signal it stamps a NON-TERMINAL `stalled:` checkpoint
    (rescue + legibility) — the honest terminal verdict is still the
    TTL+heir's job (a fresh worker re-claims after the lease expires).

The substrate (renew_claim / mark_stalled / session map) is what the plugin
shells to; this pins its contract on a real local board.
"""
import importlib.util
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return ["-c", "user.email=a@b", "-c", "user.name=a"]


def _lane(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "seed")
    _git("-C", str(w), *_cid(), "push", "-q", str(o), "main")
    return str(o), str(w)


def _claim(o, w, task, att):
    body = f"claim {task}\n{att}"
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    sha = _git("-C", w, *_cid(), "commit-tree", et, "-m", body).stdout.strip()
    _git("-C", w, *_cid(), "push", "-q", "-f", o, f"{sha}:refs/swarm/claims/{task}")
    return sha


def _ref(o, ref):
    r = _git("ls-remote", o, ref)
    return r.stdout.split()[0] if r.stdout.strip() else None


def _claim_body(o, task):
    sha = _ref(o, f"refs/swarm/claims/{task}")
    if not sha:
        return None
    obj = _git("-C", o, "cat-file", "-p", sha).stdout
    return obj.split("\n\n", 1)[1].rstrip("\n") if "\n\n" in obj else ""


def test_idle_backstop_renews_but_writes_no_verdict(tmp_path, monkeypatch):
    """The two-turn case: idle after turn 1 renews the lease + records the
    decision, but leaves NO verdict ref (the work is still in progress)."""
    h = _load("harness_c11a", "l2/harness.py")
    o, w = _lane(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    att = "att-w1-idle001"
    _claim(o, w, "T", att)
    h.session_put(o, "sess-1", "T", att)
    out = h.backstop(o, "sess-1", abandoned=False)
    assert out["claims"] == 1
    assert out["renewed"][0]["renewed"] is True
    assert out["stalled"] == []  # no abandonment -> no stalled marker
    # the C11-lite invariant: NO terminal verdict was written on a plain idle
    assert _ref(o, "refs/swarm/verdicts/T") is None
    # the lease was re-committed (ref advanced) but the att is intact
    body = _claim_body(o, "T")
    assert body.split()[-1] == att
    assert "stalled:" not in body
    # the decision is in the gate ledger
    assert any(e["event"] == "idle" for e in h.read_gate_ledger(o))


def test_abandonment_stamps_a_nonterminal_stalled_marker(tmp_path, monkeypatch):
    """On an abandonment signal the claim is marked `stalled:` (legible,
    rescue) — but STILL no verdict: the honest terminal verdict is the
    TTL+heir's to produce."""
    h = _load("harness_c11b", "l2/harness.py")
    o, w = _lane(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    att = "att-w1-aband001"
    _claim(o, w, "T", att)
    h.session_put(o, "sess-1", "T", att)
    out = h.backstop(o, "sess-1", abandoned=True)
    assert out["stalled"] == ["T"]
    body = _claim_body(o, "T")
    assert "stalled:" in body  # legible from the refs alone
    assert body.split()[-1] == att  # att stays last (still re-claimable)
    assert _ref(o, "refs/swarm/verdicts/T") is None  # still NO verdict


def test_stalled_claim_is_still_reclaimable_by_an_heir(tmp_path, monkeypatch):
    """The stalled marker is NON-TERMINAL: a fresh worker re-claiming after the
    lease expires re-writes the claim (a new att), so the honest work +
    verdict can still happen — the backstop did not wedge the task."""
    h = _load("harness_c11c", "l2/harness.py")
    m = _load("inrepo_c11c", "l2/inrepo.py")
    o, w = _lane(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    att = "att-w1-heir001"
    _claim(o, w, "T", att)
    h.session_put(o, "sess-1", "T", att)
    h.backstop(o, "sess-1", abandoned=True)
    assert "stalled:" in _claim_body(o, "T")
    # a fresh worker's re-claim (a new att) succeeds over the stalled claim
    heir_att = "att-w2-heir002"
    assert m.renew_claim(o, "T", heir_att) is None  # old att no longer valid
    # but a brand-new claim (heir) re-writes the ref cleanly
    _claim(o, w, "T", heir_att)  # force-push a new claim as the heir would
    assert _claim_body(o, "T").split()[-1] == heir_att
    assert "stalled:" not in _claim_body(o, "T")  # the new claim replaced it
