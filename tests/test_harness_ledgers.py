"""Contracts for the harness ledgers + durable session map (C12).

Everything is OUT-OF-TREE (XDG, keyed by origin): the run ledger, the gate
ledger, and the durable session->[(task,att)] map. The map is the bridge that
lets the idle backstop (C11) know which claims a live opencode session is
holding — and because it lives on disk (not in a process global), it survives
an opencode restart.
"""
import importlib.util
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "harness_c12", REPO / "l2" / "harness.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_ledgers_are_out_of_tree_and_append(tmp_path, monkeypatch):
    """The run + gate ledgers are JSONL files under the out-of-tree XDG dir
    (never in the repo), and append is durable/readable."""
    m = _load()
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    origin = "git@board.example:repo.git"
    m.append_run_ledger(origin, "sess-1", {"task": "T", "leg": "work_started"})
    m.append_run_ledger(origin, "sess-1", {"task": "T", "leg": "work_done"})
    m.append_gate_ledger(origin, "sess-1", {"task": "T", "decision": "allow"})
    run = m.ledger_dir(origin) / "run-ledger.jsonl"
    gate = m.ledger_dir(origin) / "gate-ledger.jsonl"
    assert str(run).startswith(str(xdg))  # out-of-tree
    assert not str(run).startswith(str(tmp_path / "repo"))
    run_entries = m.read_run_ledger(origin)
    assert len(run_entries) == 2
    assert [e["leg"] for e in run_entries] == ["work_started", "work_done"]
    assert [e["decision"] for e in m.read_gate_ledger(origin)] == ["allow"]
    # both are real JSONL on disk, keyed by origin
    assert run.is_file() and gate.is_file()
    json.loads(run.read_text().splitlines()[0])


def test_session_map_put_get_drop(tmp_path, monkeypatch):
    """session_put records a live claim; session_claims returns it;
    session_drop removes it (and drops an emptied session)."""
    m = _load()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    origin = "git@board.example:repo.git"
    m.session_put(origin, "sess-1", "T1", "att-a")
    m.session_put(origin, "sess-1", "T2", "att-b")
    assert m.session_claims(origin, "sess-1") == {"T1": "att-a", "T2": "att-b"}
    assert m.session_claims(origin, "other-sess") == {}
    m.session_drop(origin, "sess-1", "T1")
    assert m.session_claims(origin, "sess-1") == {"T2": "att-b"}
    m.session_drop(origin, "sess-1", "T2")
    assert m.session_claims(origin, "sess-1") == {}


def test_session_map_survives_a_restart(tmp_path, monkeypatch):
    """The map is on disk, not a process global: a claim recorded by one
    'process' (module load) is still visible to a fresh 'process' (a second,
    independent module load) — the restart-surviving guarantee C11 relies on
    (the idle backstop must find the session's live claims after a restart).
    """
    m1 = _load()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    origin = "git@board.example:repo.git"
    m1.session_put(origin, "sess-restart", "T9", "att-9")
    # 'restart': an independent module load with a clean module cache.
    spec = importlib.util.spec_from_file_location(
        "harness_restarted", REPO / "l2" / "harness.py"
    )
    m2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m2)
    assert m2.session_claims(origin, "sess-restart") == {"T9": "att-9"}
    assert m2.session_map(origin) == {"sess-restart": {"T9": "att-9"}}
