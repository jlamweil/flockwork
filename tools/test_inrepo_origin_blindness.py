"""Operator-authored contract for origin-blind automation reads (wave 11,
INT-013, 2026-09-29). sweep(), reconcile(), correction_graph() and
relabel_orphan_archives() parsed `git ls-remote` stdout WITHOUT checking
the rc: an unreachable origin yielded an empty dict and every one of
them returned a SUCCESS-SHAPED EMPTY result — sweep a silent no-op (the
repair path reporting "nothing live" while it could not see anything at
all), reconcile a clean empty census (the automation layer declaring
health while blind), correction_graph an empty graph (DECISION-GRADE:
the INT-032 frozen rule reads "FALSIFIED iff all chains single-vertex",
so a dead origin during the two-model window would have graded the
substrate falsified on the strength of a network blip),
relabel_orphan_archives a quiet no-op. The lane's own law — 'queue
empty' and 'cannot see the queue' must not look alike (open_tasks
raises RuntimeError, measured 2026-09-20) — already existed one layer
down; these four reads now obey it too.

Contract (local fixtures; no fleet, no pushes to any live origin):
  - each of the four reads RAISES RuntimeError naming the unreachable
    origin (never a success-shaped empty result)
  - sweep's legitimate idempotent path is preserved: a REACHABLE origin
    with nothing live still returns empty lists (not an error)
  - reconciliation of a reachable origin is unchanged (frozen contracts
    pin the fresh/healthy/stale shapes)

All in-parent calls here target LOCAL fixture paths — no env-bound
ORIGIN is touched (the wave-6/8 hermeticity law).
"""
import importlib.util
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    p = pathlib.Path(REPO / "l2" / "inrepo.py")
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def lane(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    w = tmp_path / "w"
    _git("init", "-q", "-b", "main", str(w))
    (w / "f.txt").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "seed")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "push", "-q", str(o), "main")
    return str(o), tmp_path


def _dead(tmp_path):
    d = tmp_path / "dead-origin.git"
    return str(d)  # never created: git ls-remote fails loudly


def test_sweep_refuses_success_shaped_noop_on_unreachable_origin(lane):
    o, tmp = lane
    with pytest.raises(RuntimeError) as ei:
        inrepo.sweep(_dead(tmp), "T-blind")
    assert "origin unreachable" in str(ei.value)


def test_reconcile_refuses_clean_census_on_unreachable_origin(lane):
    o, tmp = lane
    with pytest.raises(RuntimeError) as ei:
        inrepo.reconcile(_dead(tmp), ttl_s=1.0)
    assert "origin unreachable" in str(ei.value)


def test_correction_graph_refuses_empty_graph_on_unreachable_origin(lane):
    """The INT-032 decision rule reads this output — a dead origin must
    never grade as an empty (all-single-vertex) graph."""
    o, tmp = lane
    with pytest.raises(RuntimeError) as ei:
        inrepo.correction_graph(_dead(tmp))
    assert "origin unreachable" in str(ei.value)


def test_relabel_refuses_quiet_noop_on_unreachable_origin(lane):
    o, tmp = lane
    with pytest.raises(RuntimeError) as ei:
        inrepo.relabel_orphan_archives(_dead(tmp))
    assert "origin unreachable" in str(ei.value)


def test_sweep_idempotent_path_preserved_on_reachable_origin(lane):
    """The failure is distinguishable from the legitimate empty state:
    a reachable origin with nothing live still sweeps to empty lists."""
    o, tmp = lane
    res = inrepo.sweep(o, "T-nothing")
    assert res == {
        "task": "T-nothing", "att": None, "archived": [], "deleted": []
    }
