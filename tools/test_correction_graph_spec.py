"""INT-016 contract: the multi-model correction-graph SEED (DESIGN-NEXT
§4 trigger "≥2 models compete ... scoped reviews can no longer separate
them"). No live multi-model run ships with it — the tests drive only
local bare origins with the lane's own claim/sweep/reconcile machinery:

  - the spec JSON (loop/specs-correction-graph-2026-09-28.json) is
    seed-compatible end to end: seeded into a bare origin, every task
    lands in open_tasks; task names are refname-safe and @-free (the
    archive delimiter); every brief carries exactly one verify: oracle
  - every verify oracle is solvable (a reference solution passes) and
    falsifiable (an empty tree fails it) — an oracle that cannot fail
    cannot decide merit
  - the mm- label grammar attributes attempts from att tokens ALONE and
    stays honest on ambiguity: pre-protocol or ambiguous labels are
    `unknown`, never silently misfiled
  - correction_graph() reconstructs vertices, 'corrects' edges and the
    per-model census from refs + commit objects alone on a two-model
    substrate (heir correction on one task, reconcile re-lease on
    another — a correction cycle glm→mimo→glm), and writes nothing
"""
import contextlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SPEC = REPO / "loop" / "specs-correction-graph-2026-09-28.json"


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _cid():
    return "-c", "user.email=a@b", "-c", "user.name=a"


def _root_commit(w, msg):
    et = _git("-C", str(w), "hash-object", "-t", "tree", "/dev/null").stdout.strip()
    return _git("-C", str(w), *_cid(), "commit-tree", et, "-m", msg).stdout.strip()


def _push(w, src, ref, o):
    r = _git("-C", str(w), *_cid(), "push", "-q", str(o), f"{src}:{ref}")
    assert r.returncode == 0, r.stderr


def _bare_origin(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / ".gitkeep").write_text("")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), *_cid(), "commit", "-qm", "node")
    _git("-C", str(w), "push", "-q", str(o), "main")
    return o, w


def _load_inrepo(origin):
    """ORIGIN binds at import (the T5 lesson) — env first, then load."""
    os.environ["SWARM_ORIGIN"] = str(origin)
    m = importlib.util.spec_from_file_location("inrepo", REPO / "l2" / "inrepo.py")
    mm = importlib.util.module_from_spec(m)
    m.loader.exec_module(mm)
    return mm


@contextlib.contextmanager
def _committer_date(ts):
    """Claim ordering is commit-time; git's 1s resolution would make
    same-second claims tie-break on random att hex — pin the clock."""
    old = os.environ.get("GIT_COMMITTER_DATE")
    os.environ["GIT_COMMITTER_DATE"] = ts
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("GIT_COMMITTER_DATE", None)
        else:
            os.environ["GIT_COMMITTER_DATE"] = old


# --------------------------------------------------------------- spec


def _verify_line(brief):
    lines = [ln for ln in brief.splitlines() if ln.startswith("verify:")]
    assert len(lines) == 1, "brief must carry exactly one verify oracle"
    return lines[0][len("verify:") :].strip()


def test_spec_tasks_are_refname_safe_and_oracle_bearing():
    spec = json.loads(SPEC.read_text())
    assert isinstance(spec, dict) and len(spec) >= 4
    for task, brief in spec.items():
        # remote-data law: task names become ref names; '@' is the
        # archive delimiter and never legal in a live task name
        assert re.fullmatch(r"[A-Za-z0-9._-]+", task), task
        assert "@" not in task and task == task.strip()
        assert len(brief.strip()) > 80
        assert len(_verify_line(brief)) > 20


def test_spec_seeds_end_to_end_and_opens(tmp_path):
    spec = json.loads(SPEC.read_text())
    o, _w = _bare_origin(tmp_path)
    r = subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"), "seed", str(SPEC)],
        cwd=str(REPO), capture_output=True, text=True,
        env=dict(os.environ, SWARM_ORIGIN=str(o)), timeout=120,
    )
    assert r.returncode == 0, r.stderr
    seeded = json.loads(r.stdout)
    assert all(seeded["tasks"].values()), seeded
    r = subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"), "open_tasks"],
        cwd=str(REPO), capture_output=True, text=True,
        env=dict(os.environ, SWARM_ORIGIN=str(o)), timeout=120,
    )
    assert json.loads(r.stdout) == sorted(spec)


# reference solutions: the oracles must be solvable ...
REFERENCE = {
    "cg-rle": ("cg_rle.py", (
        "def encode(s):\n"
        "    out, i = [], 0\n"
        "    while i < len(s):\n"
        "        j = i\n"
        "        while j < len(s) and s[j] == s[i]:\n"
        "            j += 1\n"
        "        out.append(f'{s[i]}{j - i}')\n"
        "        i = j\n"
        "    return ''.join(out)\n")),
    "cg-secondmax": ("cg_secondmax.py", (
        "def second_max(nums):\n"
        "    d = sorted(set(nums), reverse=True)\n"
        "    return d[1] if len(d) >= 2 else None\n")),
    "cg-balanced": ("cg_balanced.py", (
        "def balanced(s):\n"
        "    pairs = {')': '(', ']': '[', '}': '{'}\n"
        "    st = []\n"
        "    for c in s:\n"
        "        if c in '([{':\n"
        "            st.append(c)\n"
        "        elif c in pairs:\n"
        "            if not st or st.pop() != pairs[c]:\n"
        "                return False\n"
        "    return not st\n")),
    "cg-rotate": ("cg_rotate.py", (
        "def rotate(xs, k):\n"
        "    if not xs:\n"
        "        return []\n"
        "    k %= len(xs)\n"
        "    return xs[-k:] + xs[:-k] if k else list(xs)\n")),
    "cg-anagram": ("cg_anagram.py", (
        "def is_anagram(a, b):\n"
        "    f = lambda s: sorted(c.lower() for c in s if c.isalpha())\n"
        "    return f(a) == f(b)\n")),
    "cg-flatten": ("cg_flatten.py", (
        "def flatten(x):\n"
        "    out = []\n"
        "    for item in x:\n"
        "        if isinstance(item, (list, tuple)):\n"
        "            out.extend(flatten(item))\n"
        "        else:\n"
        "            out.append(item)\n"
        "    return out\n")),
}


@pytest.mark.parametrize("task", sorted(json.loads(SPEC.read_text())))
def test_verify_oracles_solvable_and_falsifiable(task, tmp_path):
    spec = json.loads(SPEC.read_text())
    verify = _verify_line(spec[task])
    fname, code = REFERENCE[task]
    good = tmp_path / "good"
    good.mkdir()
    (good / fname).write_text(code)
    r = subprocess.run(["bash", "-lc", verify], cwd=str(good),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"oracle rejects its own reference: {r.stderr}"
    empty = tmp_path / "empty"
    empty.mkdir()
    r = subprocess.run(["bash", "-lc", verify], cwd=str(empty),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0, "an oracle that cannot fail cannot decide merit"


# ------------------------------------------------------------- grammar


def test_mm_grammar_attributes_from_att_alone():
    m = importlib.util.spec_from_file_location("inrepo", REPO / "l2" / "inrepo.py")
    mm = importlib.util.module_from_spec(m)
    m.loader.exec_module(mm)
    assert mm.mm_model_of("att-mm-glm.5.3f-example-host-a-abc123") == "glm.5.3f"
    assert mm.mm_model_of("att-mm-mimo-w2-deadbe") == "mimo"
    assert mm.mm_model_of("att-w1-abc123") is None          # pre-protocol
    assert mm.mm_model_of("att-mm-mimo-abc123") is None     # no role
    # the model token is the first after 'mm-' (protocol point 2 keeps
    # it '-'-free), so extra '-' tokens are the ROLE (seat host), not
    # ambiguity: the split stays decidable
    assert mm.mm_model_of("att-mm-glm-53f-x-abc123") == "glm"
    assert mm.mm_model_of("att-mm--x-abc123") is None      # empty model
    assert mm.mm_model_of("att-mm-mimo-notsix!)") is None   # malformed hex
    # refname safety: grammar-compatible labels stay in the safe alphabet
    for att in ("att-mm-glm.5.3f-example-host-a-abc123", "att-mm-mimo_w2-ce0s42"):
        assert re.fullmatch(r"[A-Za-z0-9._-]+", att)


# ------------------------------------------------------- reconstruction


def test_correction_graph_reconstructs_two_model_substrate(tmp_path):
    o, w = _bare_origin(tmp_path)
    mm = _load_inrepo(o)
    for t in ("cg-rle", "cg-secondmax"):
        _push(w, _root_commit(w, f"spec {t}\nverify: true"),
              f"refs/swarm/specs/{t}", o)

    # cg-secondmax first: mimo merit-fails (claim stays live) →
    # reconcile TTL re-leases → glm corrects (a correction CYCLE across
    # the pair). Reconcile runs BEFORE the cg-rle flow on purpose: it
    # sweeps EVERY stale incomplete shape, so a later cg-rle live claim
    # must not exist yet when it fires.
    with _committer_date("2005-06-15 11:00:00 +0000"):
        att_m2 = mm.claim("mm-mimo-example-host-a", "cg-secondmax")
    _push(w, _root_commit(w, mm.verdict_body("cg-secondmax", att_m2, False, "h", 0, 1)),
          "refs/swarm/verdicts/cg-secondmax", o)
    rec = mm.reconcile(str(o), ttl_s=1.0)
    assert rec["swept"] == [att_m2]
    with _committer_date("2005-06-15 11:30:00 +0000"):
        att_g2 = mm.claim("mm-glm.5.3f-example-host-a", "cg-secondmax")
    _push(w, _root_commit(w, mm.verdict_body("cg-secondmax", att_g2, True, "h", 0, 0)),
          "refs/swarm/verdicts/cg-secondmax", o)

    # cg-rle: glm env-dies (sweep archives), mimo heir corrects
    with _committer_date("2005-06-15 10:00:00 +0000"):
        att_g1 = mm.claim("mm-glm.5.3f-example-host-a", "cg-rle")
    assert att_g1.startswith("att-mm-glm.5.3f-example-host-a-")
    assert mm.sweep(str(o), "cg-rle")["archived"]
    with _committer_date("2005-06-15 10:05:00 +0000"):
        att_m1 = mm.claim("mm-mimo-example-host-a", "cg-rle")
    _push(w, _root_commit(w, mm.verdict_body("cg-rle", att_m1, True, "h", 0, 0)),
          "refs/swarm/verdicts/cg-rle", o)

    G = mm.correction_graph(str(o))
    assert [(e["task"], e["from_att"], e["to_att"]) for e in G["edges"]] == [
        ("cg-rle", att_g1, att_m1),
        ("cg-secondmax", att_m2, att_g2),
    ]
    assert [(e["from_model"], e["to_model"]) for e in G["edges"]] == [
        ("glm.5.3f", "mimo"),
        ("mimo", "glm.5.3f"),
    ]
    assert G["models"] == {
        "glm.5.3f": {"attempts": 2, "fixed": 1},
        "mimo": {"attempts": 2, "fixed": 1},
    }
    assert G["tasks"]["cg-rle"]["verdict_att"] == att_m1
    assert all(a["ts"] is not None
               for t in G["tasks"].values() for a in t["attempts"])
    # read-only by contract: the substrate is byte-identical across the
    # correction_graph call itself (the test's own writes are all before)
    before = _git("ls-remote", str(o)).stdout
    mm.correction_graph(str(o))
    assert _git("ls-remote", str(o)).stdout == before

    # CLI surface parses back to the same graph
    r = subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"),
         "correction-graph", str(o)],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["edges"] == G["edges"]


def test_correction_graph_empty_origin_is_empty_not_error(tmp_path):
    o, _w = _bare_origin(tmp_path)
    mm = _load_inrepo(o)
    G = mm.correction_graph(str(o))
    assert G == {"origin": str(o), "tasks": {}, "edges": [], "models": {}}
