"""Contracts for the external-work checkpoints + lease renewal (C6).

The interactive (external-work) leg needs legible checkpoints the headless
dispatch_started alone does not cover:
  - mark_work_started / mark_work_done push a legible marker onto the claim
    ref (att stays the LAST body token — every att reader extracts it via
    split()[-1]); best-effort CAS, conflict-tolerant (a concurrent change
    skips, never errors).
  - renew_claim re-leases a live claim (re-commit the same body for a fresh
    committer ts, att preserved) so a human-paced multi-hour task is not
    swept by reconcile's TTL mid-work; a wrong att is refused (the claim was
    swept/taken over).
  - attribution's interrupted leg recognizes work_started: (not just
    dispatch_started:) — CORR-6. A live claim with work_started + no verdict
    + no archive, aged past the bound, is legible as interrupted.
  - live_claim_att returns the live claim's att (last body token) or None —
    the plugin compares its recorded att against this; a mismatch is
    claim_lost.
"""
import importlib.util
import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]


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


def _claim(o, w, task, att, old_date=False, extra=""):
    body = f"claim {task}\nharness: opencode{extra}\n{att}"
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    env = dict(os.environ)
    if old_date:
        env.update(GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
                   GIT_AUTHOR_DATE="2000-01-01T00:00:00Z")
    sha = subprocess.run(
        ["git", "-C", w, "commit-tree", et, "-m", body],
        env=env, capture_output=True, text=True).stdout.strip()
    subprocess.run(
        ["git", "-C", w, "push", "-q", "-f", o,
         f"{sha}:refs/swarm/claims/{task}"],
        capture_output=True, text=True)
    return sha


def _push_spec(o, w, task):
    et = _git("-C", w, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    sha = _git("-C", w, *_cid(), "commit-tree", et,
               "-m", f"task {task}\nverify: true").stdout.strip()
    _git("-C", w, *_cid(), "push", "-q", o, f"{sha}:refs/swarm/specs/{task}")


def _claim_body(o, task):
    r = _git("ls-remote", o, f"refs/swarm/claims/{task}")
    if not r.stdout.strip():
        return None
    sha = r.stdout.split()[0]
    obj = _git("-C", o, "cat-file", "-p", sha).stdout
    return obj.split("\n\n", 1)[1].rstrip("\n") if "\n\n" in obj else ""


def _claim_sha(o, task):
    r = _git("ls-remote", o, f"refs/swarm/claims/{task}")
    return r.stdout.split()[0] if r.stdout.strip() else None


def test_mark_work_started_is_legible_and_att_stays_last(tmp_path):
    m = _load_inrepo()
    o, w = _lane(tmp_path)
    att = "att-w1-ws0001"
    _claim(o, w, "T", att)
    assert m.mark_work_started(o, "T", att) is not None
    body = _claim_body(o, "T")
    assert "work_started:" in body
    assert body.split()[-1] == att  # att stays the last token


def test_renew_claim_releases_a_live_claim(tmp_path):
    m = _load_inrepo()
    o, w = _lane(tmp_path)
    att = "att-w1-renew01"
    old_sha = _claim(o, w, "T", att, old_date=True)
    age_before = m._claim_age_s(o, f"refs/swarm/claims/T")
    assert age_before is not None and age_before > 1000
    assert m.renew_claim(o, "T", att) is not None
    assert _claim_sha(o, "T") != old_sha  # the ref advanced (re-committed)
    age_after = m._claim_age_s(o, f"refs/swarm/claims/T")
    assert age_after is not None and age_after < 10  # fresh ts -> age reset
    assert _claim_body(o, "T").split()[-1] == att  # att preserved


def test_renew_claim_wrong_att_is_refused(tmp_path):
    m = _load_inrepo()
    o, w = _lane(tmp_path)
    att = "att-w1-renew02"
    _claim(o, w, "T", att)
    before = _claim_sha(o, "T")
    assert m.renew_claim(o, "T", "att-wrong") is None
    assert _claim_sha(o, "T") == before  # the live claim is untouched


def test_attribution_recognizes_work_started(tmp_path, monkeypatch):
    """CORR-6: the interrupted leg keys on work_started: too (not only
    dispatch_started:) — a work_started claim aged past the bound is
    legible as interrupted, from the origin alone."""
    m = _load_inrepo()
    o, w = _lane(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", o)
    monkeypatch.setenv("SWARM_LEASE_TTL", "60")
    _push_spec(o, w, "T")
    _claim(o, w, "T", "att-w1-intws01", old_date=True,
           extra="\nwork_started: 20260101T000000Z")
    out = m.attribution(o)
    assert "T" in out.get("interrupted", {}), out.get("interrupted")
    assert out["interrupted"]["T"]["claim_age_s"] > 60


def test_live_claim_att(tmp_path):
    m = _load_inrepo()
    o, w = _lane(tmp_path)
    att = "att-w1-live001"
    assert m.live_claim_att(o, "T-none") is None  # no claim
    _claim(o, w, "T", att)
    assert m.live_claim_att(o, "T") == att
