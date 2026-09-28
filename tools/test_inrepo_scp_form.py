"""Contract: origin-host reads must accept every documented SWARM_ORIGIN
form. The 09-19 loop addendum's own claim command ships scp-form
(`SWARM_ORIGIN=you@example-host-a:solve-metrics-origin.git`); pre-fix,
origin_body/origin_commit_ts (and everything behind them: audit body
reads, sweep att-recovery, reconcile dating) crashed on it with
FileNotFoundError, because origin_raw's scheme dispatch falls through to
the local-path branch. Preregistered in LOOP-2026-09-20.md (H2/H3).

No fleet, no network: a PATH-stub `ssh` maps the "remote" onto a tmp
directory (scp-form path = relative to the fake remote root, exactly the
remote-HOME convention), so the whole lane — ls-remote, fetch, push,
cat-file — runs against a local bare origin addressed AS a remote.

SSHD-FAITHFUL STUB (2026-09-28, INT-013 wave 3 — supersedes the modeling
limit disclosed in wave 2): the old stub stripped ALL single quotes from
the remote command and word-split it, silently defeating client-side
shell quoting — the exact defense test_inrepo_ssh_shell_injection.py
pins. This stub keeps git's protocol legs (`git-upload-pack '<url>'`) on
the de-quote path (they are one argument to upload-pack, not a shell
line) and hands EVERY other remote command to the login shell exactly as
sshd does: the harness rewrites only the fake-root path prefix inside
the string, preserving the rest byte-for-byte, then `bash -c <string as
received>`. Shell-active refnames stay shell-active here — which is what
makes the injection contracts below meaningful on the SCP-FORM branch of
ssh_target (the injection suite exercises only ssh:// forms).

FALSIFICATION RECEIPT (wave 3, measured before the contracts landed):
with this shell-faithful stub but _ssh_cmd reverted to the pre-wave-1
plain join, origin_body over a hostile-named claim created the marker
file ON THE FAKE ORIGIN HOST (the dangerous shape, reproduced); with the
committed shlex.quote-join the same reads are inert. The contracts below
pin the inert shape so a future de-quote regression measures RED here
too — not only on ssh:// forms.
"""
import importlib.util
import os
import pathlib
import subprocess

import pytest


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_scp", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()

SSH_STUB = """\
#!/bin/bash
# fake ssh: [-o k=v]* [-p port] host <remote command as ONE argument>
while [ "$1" = "-o" ]; do shift 2; done
if [ "$1" = "-p" ]; then shift 2; fi
shift  # host
export FAKE_REMOTE_ROOT
# git transport sends the remote command as ONE argument
# (`git-upload-pack '<url>'`) — a protocol leg, not a shell line:
# de-quote, strip the leading '/', dispatch into the fake root
 case "${1%% *}" in
  git-upload-pack|git-receive-pack)
    set -- $(printf %s "$1" | tr -d "'")
    cmd=$1; p=$2; p=${p#/}
    case "$cmd" in
      git-upload-pack) exec git -C "$FAKE_REMOTE_ROOT" upload-pack "$p" ;;
      git-receive-pack) exec git -C "$FAKE_REMOTE_ROOT" receive-pack "$p" ;;
    esac ;;
esac
# harness fiction (sshd-faithful): rewrite the fake-remote path prefix
# INSIDE the command string, preserving the rest byte-for-byte (QUOTES
# INCLUDED) — the remote login shell then parses the full string exactly
# like sshd does (`$SHELL -c <string as received>`). Client-side quoting
# is NOT laundered away: shell-active refnames stay shell-active here,
# exactly as on the real origin host.
if [[ "$1" == "git -C "* ]]; then
  rest=${1#git -C }
  path=${rest%% *}
  set -- "git -C $FAKE_REMOTE_ROOT/${path#/}${rest#"$path"}"
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


def _seed_claim(origin, task, att, with_return=True, with_verdict=True):
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
    _git("-C", str(w), "push", "-q", str(origin), f"{c}:refs/swarm/claims/{task}")
    if with_return or with_verdict:
        msha = _git("-C", str(w), "rev-parse", "main").stdout.strip()
        _git("-C", str(w), "push", "-q", str(origin), f"{msha}:refs/swarm/tasks/{task}")
    if with_verdict:
        v = _git("-C", str(w), *cid, "commit-tree", et,
                 "-m", f"verdict {task}\nfixed: true\nattempt: {att}").stdout.strip()
        _git("-C", str(w), "push", "-q", str(origin),
             f"{v}:refs/swarm/verdicts/{task}")
    return c


# shell-ACTIVE payload, space-free ($IFS supplies the separator — a
# literal space would break ls-remote's whitespace parsing and could
# never arrive through real remote data). Same payload family as
# test_inrepo_ssh_shell_injection.py, now pinned on the SCP-FORM branch
# of ssh_target (the injection suite covers only ssh:// forms).
HOSTILE_TASK = "T-$(touch$IFS$FAKE_REMOTE_ROOT/swarm-pwned-scp)"
MARKER = "swarm-pwned-scp"


def test_premise_refname_is_legal():
    """Pin the premise: the payload name survives git's own refname
    rules — it CAN arrive through the claim-CAS namespace as scp-form
    remote data."""
    r = subprocess.run(
        ["git", "check-ref-format", f"refs/swarm/claims/{HOSTILE_TASK}"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0


def _pwned(root):
    return pathlib.Path(str(root)) / MARKER


# ------------------------------------------------- form parity (H2/H3)

def test_scp_form_body_matches_ssh_form(lane):
    """The H2 core: same object, both documented forms, byte-identical."""
    o, root = lane
    _seed_claim(o, "T-scp", "att-w1-scp01")
    scp = "fakeuser@fakehost:origin.git"
    ssh = "ssh://fakehost/origin.git"
    ref = "refs/swarm/claims/T-scp"
    assert inrepo.origin_body(scp, ref) == inrepo.origin_body(ssh, ref)
    assert "att-w1-scp01" in inrepo.origin_body(scp, ref)


def test_scp_form_body_without_user(lane):
    o, root = lane
    _seed_claim(o, "T-scp2", "att-w2-scp02")
    assert "att-w2-scp02" in inrepo.origin_body("fakehost:origin.git",
                                                "refs/swarm/claims/T-scp2")


def test_scp_form_missing_ref_is_empty_not_crash(lane):
    o, root = lane
    assert inrepo.origin_body("fakehost:origin.git",
                              "refs/swarm/claims/nope") == ""


def test_scp_form_commit_ts(lane):
    """reconcile's dating path — pre-fix the FileNotFoundError surfaced
    here uncaught, killing the whole reconcile run."""
    o, root = lane
    _seed_claim(o, "T-ts", "att-w3-scp03", with_return=False, with_verdict=False)
    ts = inrepo.origin_commit_ts("fakehost:origin.git", "refs/swarm/claims/T-ts")
    assert ts is not None
    assert abs(ts - __import__("time").time()) < 600


def test_scp_form_reconcile_completes(lane):
    o, root = lane
    _seed_claim(o, "T-rec", "att-w4-scp04", with_return=False, with_verdict=False)
    out = inrepo.reconcile("fakehost:origin.git")
    assert out["errors"] == []
    assert "T-rec" in out["fresh"]


def test_scp_form_sweep_recovers_att(lane):
    """The recovery path: sweep with att=None derives the att from the
    claim body via origin_body — pre-fix this crashed mid-sweep."""
    o, root = lane
    _seed_claim(o, "T-swp", "att-w5-scp05")
    res = inrepo.sweep("fakehost:origin.git", "T-swp")
    assert res["att"] == "att-w5-scp05"
    have = _git("ls-remote", o).stdout
    assert "refs/swarm/archive/claims/T-swp@att-w5-scp05" in have
    assert "refs/swarm/claims/T-swp" not in have


# ------------------------------ shell-inertness on the scp-form branch

def test_scp_form_hostile_ref_is_shell_inert(lane):
    """origin_body over a hostile refname via the DOCUMENTED scp-form
    origin must not execute anything on the origin host."""
    o, root = lane
    body = inrepo.origin_body("fakeuser@fakehost:origin.git",
                              f"refs/swarm/claims/{HOSTILE_TASK}")
    assert body == ""  # ref absent: read fails honestly, NOTHING runs
    assert not _pwned(root).exists()


def test_scp_form_hostile_claim_sweep_is_inert_and_complete(lane):
    """End-to-end on scp-form: a hostile-named claim (seeded by 'another
    worker', return+verdict present) is read by sweep's att-recovery —
    the exact production path. The lane must heal the task WITHOUT
    executing the payload, and still recover the true att (quoting is
    behavior-preserving for legitimate reads)."""
    o, root = lane
    _seed_claim(o, HOSTILE_TASK, "att-w6-scp06",
                with_return=True, with_verdict=True)
    res = inrepo.sweep("fakeuser@fakehost:origin.git", HOSTILE_TASK)
    assert res["att"] == "att-w6-scp06"
    have = _git("ls-remote", o).stdout
    assert f"refs/swarm/claims/{HOSTILE_TASK}" not in have
    assert f"refs/swarm/archive/claims/{HOSTILE_TASK}@att-w6-scp06" in have
    assert not _pwned(root).exists()


def test_scp_form_reconcile_over_hostile_claim_is_inert(lane):
    """reconcile dates every live claim via origin_commit_ts — remote
    task names flow straight into the scp-form ssh remote command."""
    o, root = lane
    _seed_claim(o, HOSTILE_TASK, "att-w7-scp07",
                with_return=False, with_verdict=False)
    out = inrepo.reconcile("fakeuser@fakehost:origin.git")
    assert out["errors"] == []
    assert HOSTILE_TASK in out["fresh"]
    assert not _pwned(root).exists()


def test_scp_form_legit_reads_unchanged_after_stub_rewrite(lane):
    """Behavior-preservation sentinel for the wave-3 stub rewrite: a
    normal ref reads exactly as before through the now shell-faithful
    transport."""
    o, root = lane
    _seed_claim(o, "T-nice", "att-w8-scp08")
    body = inrepo.origin_body("fakehost:origin.git",
                              "refs/swarm/claims/T-nice")
    assert "att-w8-scp08" in body
    ts = inrepo.origin_commit_ts("fakehost:origin.git",
                                 "refs/swarm/claims/T-nice")
    assert ts is not None
    assert not _pwned(root).exists()


# ----------------------------- law_check through the scp-form branch

def _publish_law(origin, law_bytes: bytes) -> None:
    """Put the RUNNING lane law at main:l2/inrepo.py on a bare origin."""
    w = pathlib.Path(origin).parent / "law-w"
    if not w.exists():
        _git("init", "-q", "-b", "main", str(w))
    d = w / "l2"
    d.mkdir(exist_ok=True)
    (d / "inrepo.py").write_bytes(law_bytes)
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "law")
    _git("-C", str(w), "push", "-q", "--force", str(origin), "HEAD:main")


def test_law_check_scp_form_reports_match(lane):
    """Coverage completion: law_check (the SECOND _ssh_cmd call site)
    through the scp-form branch of ssh_target — the documented
    SWARM_ORIGIN form had no law-check contract on any form before
    wave 3 (the injection suite pins law_check on ssh:// only)."""
    o, root = lane
    _publish_law(o, pathlib.Path(inrepo.__file__).read_bytes())
    out = inrepo.law_check("fakeuser@fakehost:origin.git")
    assert out["status"] == "match", out
    assert out["origin_sha"] == out["local_sha"]
    assert not _pwned(root).exists()


def test_law_check_scp_form_reports_stale(lane):
    o, root = lane
    _publish_law(o, b"# not the law\n")
    out = inrepo.law_check("fakeuser@fakehost:origin.git")
    assert out["status"] == "stale", out
    assert not _pwned(root).exists()
