"""Contract for the flockwork verdict-count gate (WQ-025, INT-085b pilot 1).

The design is docs/GATES.md (landed through flockwork itself — the
pilot's wave-A task); this suite pins every falsifiable property its
section 7 lists. The gate is pure substrate: create-once CAS on the
same git refspace the lane already uses, no new wire semantics.

  - reviewer verdicts: refs/swarm/verdicts/<task>@<reviewer> — the
    @-law (heirs_count's archive shape), because the D/F conflict makes
    refs/swarm/verdicts/<task>/<reviewer> illegal beside the leaf.
  - schema is the instruction: a body that fails any required-field
    check is not a verdict and is never counted.
  - the flip: refs/swarm/integrated/<task>, create-once CAS — racing
    flips yield exactly one winner; re-running the gate after a flip
    is an honest idempotent no-op.
  - a single live veto blocks the flip regardless of the agree count.
"""
import importlib.util
import json
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _gates():
    p = REPO / "l2" / "gates.py"
    spec = importlib.util.spec_from_file_location("flockwork_gates_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _ok(r):
    return r.returncode == 0


def _origin(tmp_path):
    """Scratch bare origin with one commit on main (the T5 shape)."""
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


def _work_repo(tmp_path):
    """A repo cwd for the object store: commit-tree/push run in the
    process cwd (the test_claim_loss house lesson)."""
    w = tmp_path / "node"
    _git("init", "-q", "-b", "main", str(w))
    (w / ".seed").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "s")
    return w


def _commit_body(repo, body):
    """Empty-tree commit carrying `body`, its sha resolvable in repo."""
    r = _git("-C", str(repo), "hash-object", "-t", "tree", "/dev/null")
    tree = r.stdout.strip()
    r = _git("-C", str(repo), "commit-tree", tree, "-m", body)
    assert _ok(r), r.stderr
    return r.stdout.strip()


def _push_ref(repo, origin, sha, ref):
    return _git("-C", str(repo), "push", "-q", str(origin),
                f"{sha}:{ref}")


def _seed_spec(repo, origin, task, n, m):
    body = f"spec {task}\nsome brief\ntasks: x\nn: {n}\nm: {m}\n"
    sha = _commit_body(repo, body)
    r = _push_ref(repo, origin, sha, f"refs/swarm/specs/{task}")
    assert _ok(r), r.stderr


def _leaf_return(repo, origin, task):
    """The return ref + final verdict ref, so `evidence` resolves
    (property 9's world: the attempt already landed)."""
    sha = _commit_body(repo, "fix whatever")
    r = _push_ref(repo, origin, sha, f"refs/swarm/tasks/{task}")
    assert _ok(r), r.stderr
    r = _push_ref(repo, origin, sha, f"refs/swarm/verdicts/{task}")
    assert _ok(r), r.stderr


@pytest.fixture()
def board(tmp_path, monkeypatch):
    o = _origin(tmp_path)
    w = _work_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.chdir(w)
    return {"origin": str(o), "repo": w, "tmp": tmp_path}


# ------------------------------------------------- property 1 (p1 create-once)


def test_reviewer_second_verdict_rejected(board):
    g = _gates()
    _leaf_return(board["repo"], board["origin"], "T1")
    r1 = g.review_verdict("T1", "rev-a", "agree", "refs/swarm/tasks/T1")
    assert r1["pushed"] is True
    r2 = g.review_verdict("T1", "rev-a", "veto", "refs/swarm/tasks/T1")
    assert r2["pushed"] is False
    assert r2["rejected"] is True
    # and the ref still carries the FIRST verdict
    raw = _git("ls-remote", board["origin"], "refs/swarm/verdicts/T1@rev-a")
    sha = raw.stdout.split()[0]
    body = _git("cat-file", "-p", sha).stdout
    assert "outcome: agree" in body


# --------------------------------------- property 2 (fires exactly at n, 0 veto)


def test_gate_fires_exactly_at_n_of_m(board):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T2", n=2, m=3)
    _leaf_return(board["repo"], board["origin"], "T2")
    g.review_verdict("T2", "rev-a", "agree", "refs/swarm/tasks/T2")
    ev = g.gate("T2")
    assert ev["fired"] is False and ev["agree"] == 1
    g.review_verdict("T2", "rev-b", "agree", "refs/swarm/tasks/T2")
    ev = g.gate("T2")
    assert ev["fired"] is True and ev["flipped"] is True
    assert ev["agree"] == 2 and ev["veto"] == 0
    # the flip exists, create-once
    raw = _git("ls-remote", board["origin"], "refs/swarm/integrated/T2")
    assert "refs/swarm/integrated/T2" in raw.stdout


# ------------------------------------------- property 3 (one veto blocks)


def test_single_veto_blocks_even_at_n_agrees(board):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T3", n=2, m=3)
    _leaf_return(board["repo"], board["origin"], "T3")
    g.review_verdict("T3", "rev-a", "agree", "refs/swarm/tasks/T3")
    g.review_verdict("T3", "rev-b", "agree", "refs/swarm/tasks/T3")
    g.review_verdict("T3", "rev-c", "veto", "refs/swarm/tasks/T3")
    ev = g.gate("T3")
    assert ev["agree"] == 2 and ev["veto"] == 1
    assert ev["fired"] is False and ev["flipped"] is False
    raw = _git("ls-remote", board["origin"], "refs/swarm/integrated/T3")
    assert raw.stdout.strip() == ""


# --------------------------------- property 4 (racing flips: exactly one winner)


def test_racing_flip_exactly_one_winner(board):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T4", n=1, m=1)
    _leaf_return(board["repo"], board["origin"], "T4")
    g.review_verdict("T4", "rev-a", "agree", "refs/swarm/tasks/T4")
    # two flips race the same create-once CAS: first wins, second rejected
    # (1.1s apart so the two flip commits differ by committer timestamp —
    # identical same-second commits are the SAME object, and pushing an
    # already-pointed-at sha is a true no-op, not a race)
    r1 = g.flip("T4", ["rev-a"])
    import time
    time.sleep(1.1)
    r2 = g.flip("T4", ["rev-a"])
    assert r1["pushed"] is True
    assert r2["pushed"] is False and r2["rejected"] is True
    raw = _git("ls-remote", board["origin"], "refs/swarm/integrated/T4")
    assert len(raw.stdout.strip().splitlines()) == 1


# ------------------------------- property 5 (re-gate after flip: idempotent no-op)


def test_regate_after_flip_is_idempotent_noop(board):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T5", n=1, m=1)
    _leaf_return(board["repo"], board["origin"], "T5")
    g.review_verdict("T5", "rev-a", "agree", "refs/swarm/tasks/T5")
    ev1 = g.gate("T5")
    assert ev1["fired"] is True
    before = _git("ls-remote", board["origin"],
                  "refs/swarm/integrated/T5").stdout
    ev2 = g.gate("T5")
    assert ev2["already_integrated"] is True
    assert ev2["flipped"] is False
    assert ev2["fired"] is False  # honest: no second flip happened
    after = _git("ls-remote", board["origin"],
                 "refs/swarm/integrated/T5").stdout
    assert before == after


# ------------------------------------------- property 6 (@ ref coexists with leaf)


def test_reviewer_ref_coexists_with_leaf_verdict_ref(board):
    g = _gates()
    _leaf_return(board["repo"], board["origin"], "T6")
    r = g.review_verdict("T6", "rev-a", "agree", "refs/swarm/tasks/T6")
    assert r["pushed"] is True
    leaf = _git("ls-remote", board["origin"], "refs/swarm/verdicts/T6")
    atref = _git("ls-remote", board["origin"], "refs/swarm/verdicts/T6@rev-a")
    assert "refs/swarm/verdicts/T6" in leaf.stdout
    assert "refs/swarm/verdicts/T6@rev-a" in atref.stdout


# ------------------------- property 7 (reviewer-field mismatch is not a vote)


def test_reviewer_field_mismatch_not_counted(board):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T7", n=1, m=1)
    _leaf_return(board["repo"], board["origin"], "T7")
    # a body whose reviewer field names someone else, pushed raw under
    # rev-a's ref: the schema check must refuse to count it
    sha = _commit_body(board["repo"],
                       "review-verdict\ntask: T7\nreviewer: rev-ghost\n"
                       "outcome: agree\nevidence: refs/swarm/tasks/T7\n")
    r = _push_ref(board["repo"], board["origin"], sha,
                  "refs/swarm/verdicts/T7@rev-a")
    assert _ok(r)
    ev = g.gate("T7")
    assert ev["agree"] == 0 and ev["invalid"] >= 1
    assert ev["fired"] is False


# --------------------------- property 8 (unknown outcome is not a vote)


def test_unknown_outcome_not_counted(board):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T8", n=1, m=1)
    _leaf_return(board["repo"], board["origin"], "T8")
    sha = _commit_body(board["repo"],
                       "review-verdict\ntask: T8\nreviewer: rev-a\n"
                       "outcome: maybe\nevidence: refs/swarm/tasks/T8\n")
    r = _push_ref(board["repo"], board["origin"], sha,
                  "refs/swarm/verdicts/T8@rev-a")
    assert _ok(r)
    ev = g.gate("T8")
    assert ev["agree"] == 0 and ev["veto"] == 0 and ev["invalid"] >= 1
    assert ev["fired"] is False


# ----------------------- property 9 (evidence ref must resolve, or no fire)


def test_missing_evidence_blocks_the_fire(board):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T9", n=1, m=1)
    _leaf_return(board["repo"], board["origin"], "T9")
    g.review_verdict("T9", "rev-a", "agree", "refs/swarm/verdicts/nope")
    ev = g.gate("T9")
    assert ev["fired"] is False and ev["evidence_missing"] >= 1
    raw = _git("ls-remote", board["origin"], "refs/swarm/integrated/T9")
    assert raw.stdout.strip() == ""


# -------------------- property 10 (illegal subpath ref is refused at birth)


def test_illegal_reviewer_names_refused(board):
    g = _gates()
    for bad in ("rev/a", "a@b", ""):
        r = g.review_verdict("T10", bad, "agree", "refs/swarm/tasks/T10")
        assert r["pushed"] is False and r["refused"] is True, bad
    raw = _git("ls-remote", board["origin"], "refs/swarm/verdicts/T10@*")
    assert raw.stdout.strip() == ""


# ----------------------------------------------- the integrated body schema


def test_integrated_body_carries_the_design_fields(board):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T11", n=2, m=2)
    _leaf_return(board["repo"], board["origin"], "T11")
    g.review_verdict("T11", "rev-a", "agree", "refs/swarm/tasks/T11")
    g.review_verdict("T11", "rev-b", "agree", "refs/swarm/tasks/T11")
    ev = g.gate("T11")
    assert ev["fired"] is True
    raw = _git("ls-remote", board["origin"], "refs/swarm/integrated/T11")
    sha = raw.stdout.split()[0]
    body = _git("log", "-1", "--format=%B", sha).stdout
    assert body.startswith("integrated\n")
    assert "task: T11" in body
    assert "n: 2" in body and "m: 2" in body
    assert "rev-a" in body and "rev-b" in body
    assert "evidence: refs/swarm/tasks/T11" in body


# ------------------------------------------------------ CLI honesty (house law)


def test_cli_gate_reports_one_json_event(board, capsys):
    g = _gates()
    _seed_spec(board["repo"], board["origin"], "T12", n=1, m=1)
    _leaf_return(board["repo"], board["origin"], "T12")
    g.review_verdict("T12", "rev-a", "agree", "refs/swarm/tasks/T12")
    rc = g.main(["gate", "T12"])
    out = capsys.readouterr().out
    ev = json.loads(out.strip().splitlines()[-1])
    assert rc == 0
    assert ev["task"] == "T12" and ev["fired"] is True
