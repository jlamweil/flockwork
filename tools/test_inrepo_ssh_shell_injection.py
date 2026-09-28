"""Contract: origin-host reads must be shell-inert in refnames that
arrive from REMOTE DATA.

The claim-CAS namespace accepts any legal refname (audit's own
claim_orphans note, 2026-09-21) — and `git check-ref-format` accepts
shell-active names like `refs/swarm/claims/T-$(touch$IFS/…)`. ssh
concatenates its command argv into ONE string for the remote login
shell, so l2/inrepo.py's ssh-transported reads (origin_raw →
origin_body/origin_commit_ts, behind sweep att-recovery, reconcile
dating, relabel, audit) executed that payload ON THE ORIGIN HOST the
moment the lane read the hostile claim. This is DESIGN-NEXT §5's
recorded class ("ssh joins argv and the remote shell eats the parens" —
c8 run-5 receipt; its shlex.quote-join repair was applied to the c8
driver, never carried into the l2 lane).

Preregistered 2026-09-28 (INT-013 freebuff sitting): the contract is RED
at measurement by construction — the first run reproduced the dangerous
shape (marker file created on the remote side). No thresholds touched;
the fix is quoting-only, so every legitimate read must stay
byte-identical (the scp-form suite pins that).

No fleet, no network: the fake ssh maps the "remote" onto a tmp
directory and runs multi-arg remote commands through a real shell,
exactly like sshd does (`shell -c <joined argv>`).
"""
import importlib.util
import os
import pathlib
import subprocess

import pytest


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_sshinj", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()

SSH_STUB = """\
#!/bin/bash
# fake ssh: [-o k=v]* [-p port] host <remote command as ONE string>
while [ "$1" = "-o" ]; do shift 2; done
if [ "$1" = "-p" ]; then shift 2; fi
shift  # host
export FAKE_REMOTE_ROOT
# git transport sends `git-upload-pack '<url>'` — a protocol leg, not a
# shell command; de-quote, strip the ssh://-absolute leading '/', and
# dispatch into the fake root
 case "${1%% *}" in
  git-upload-pack|git-receive-pack)
    set -- $(printf %s "$1" | tr -d "'")
    cmd=$1; p=$2; p=${p#/}
    case "$cmd" in
      git-upload-pack) exec git -C "$FAKE_REMOTE_ROOT" upload-pack "$p" ;;
      git-receive-pack) exec git -C "$FAKE_REMOTE_ROOT" receive-pack "$p" ;;
    esac ;;
esac
# harness fiction: rewrite the fake-remote path prefix INSIDE the
# command string, preserving the rest byte-for-byte (QUOTES INCLUDED) —
# the remote login shell then parses the full string exactly like sshd
# does (`$SHELL -c <string as received>`). Client-side quoting is the
# defense under test; the stub must not launder it away.
if [[ "$1" == "git -C "* ]]; then
  rest=${1#git -C }
  path=${rest%% *}
  set -- "git -C $FAKE_REMOTE_ROOT/${path#/}${rest#\"$path\"}"
fi
exec bash -c "$1"
"""


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, text=True, capture_output=True)


@pytest.fixture
def lane(tmp_path, monkeypatch):
    root = tmp_path / "remote"
    o = root / "origin.git"
    o.mkdir(parents=True)
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stub = bin_ / "ssh"
    stub.write_text(SSH_STUB)
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_REMOTE_ROOT", str(root))
    return str(o), root


# shell-ACTIVE payload, space-free ($IFS supplies the separator — a
# literal space would break ls-remote's whitespace parsing and could
# never arrive through real remote data)
HOSTILE_TASK = "T-$(touch$IFS$FAKE_REMOTE_ROOT/swarm-pwned)"
MARKER = "swarm-pwned"


def test_premise_refname_is_legal():
    """Pin the premise: the payload name survives git's own refname
    rules — it CAN arrive through the claim-CAS namespace."""
    r = subprocess.run(
        ["git", "check-ref-format", f"refs/swarm/claims/{HOSTILE_TASK}"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0


def _seed_claim(origin, task, att, with_return=False, with_verdict=False):
    w = pathlib.Path(origin).parent / "seed-w"
    if not w.exists():
        _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    cid = "-c", "user.email=a@b", "-c", "user.name=a"
    et = _git("-C", str(w), "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    c = _git("-C", str(w), *cid, "commit-tree", et,
             "-m", f"claim {task} {att}").stdout.strip()
    _git("-C", str(w), "push", "-q", str(origin),
         f"{c}:refs/swarm/claims/{task}")
    if with_return or with_verdict:
        msha = _git("-C", str(w), "rev-parse", "main").stdout.strip()
        _git("-C", str(w), "push", "-q", str(origin),
             f"{msha}:refs/swarm/tasks/{task}")
    if with_verdict:
        v = _git("-C", str(w), *cid, "commit-tree", et,
                 "-m", f"verdict {task}\nfixed: true\nattempt: {att}").stdout.strip()
        _git("-C", str(w), "push", "-q", str(origin),
             f"{v}:refs/swarm/verdicts/{task}")
    return c


def _pwned(root):
    return pathlib.Path(str(root)) / MARKER


def test_origin_body_hostile_ref_is_shell_inert(lane):
    """The choke point itself: origin_body over a hostile refname must
    not execute anything on the origin host. (Pre-fix this measured the
    dangerous shape: marker file created by the remote shell.)"""
    o, root = lane
    body = inrepo.origin_body("ssh://fakehost/origin.git",
                              f"refs/swarm/claims/{HOSTILE_TASK}")
    assert body == ""  # ref absent: read fails honestly, NOTHING runs
    assert not _pwned(root).exists()


def test_sweep_over_hostile_claim_is_inert_and_complete(lane):
    """End-to-end: a hostile-named claim (seeded by 'another worker',
    return+verdict present) is read by sweep's att-recovery — the exact
    production path (sweep consumes ls-remote output). The lane must
    heal the task WITHOUT executing the payload, and still recover the
    true att (quoting is behavior-preserving for legitimate reads)."""
    o, root = lane
    _seed_claim(o, HOSTILE_TASK, "att-w1-inj01",
                with_return=True, with_verdict=True)
    res = inrepo.sweep("ssh://fakehost/origin.git", HOSTILE_TASK)
    assert res["att"] == "att-w1-inj01"
    have = _git("ls-remote", o).stdout
    assert f"refs/swarm/claims/{HOSTILE_TASK}" not in have
    assert f"refs/swarm/archive/claims/{HOSTILE_TASK}@att-w1-inj01" in have
    assert not _pwned(root).exists()


def test_reconcile_over_hostile_claim_is_inert(lane):
    """reconcile dates every live claim via origin_commit_ts — remote
    task names flow straight into the ssh remote command."""
    o, root = lane
    _seed_claim(o, HOSTILE_TASK, "att-w2-inj02")
    out = inrepo.reconcile("ssh://fakehost/origin.git")
    assert out["errors"] == []
    assert HOSTILE_TASK in out["fresh"]
    assert not _pwned(root).exists()


def test_legit_reads_unchanged_after_quoting(lane):
    """Behavior-preservation sentinel: a normal ref reads exactly as
    before (the scp-form suite pins the other origin forms)."""
    o, root = lane
    _seed_claim(o, "T-nice", "att-w3-inj03")
    body = inrepo.origin_body("ssh://fakehost/origin.git",
                              "refs/swarm/claims/T-nice")
    assert "att-w3-inj03" in body
    ts = inrepo.origin_commit_ts("ssh://fakehost/origin.git",
                                 "refs/swarm/claims/T-nice")
    assert ts is not None
    assert not _pwned(root).exists()
