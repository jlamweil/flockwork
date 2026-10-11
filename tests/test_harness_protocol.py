"""Contracts for the canonical 2-step swarm protocol text (C10/C14).

The protocol is the ordered, canonical instructions the plugin injects into
the agent's system prompt. It lives in the substrate (one source of truth,
TDD'd here) so the plugin, the /flockwork skill, and the docs all reference
the SAME text. The structure is pinned: the two steps in order, and the
leg tools in their required order (a wrong order is a red test — it would
teach the agent a broken loop).
"""
import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _protocol():
    spec = importlib.util.spec_from_file_location(
        "harness_c10", REPO / "l2" / "harness.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.protocol()


def test_two_steps_in_order():
    p = _protocol()
    i1 = p.index("Step 1")
    i2 = p.index("Step 2")
    assert i1 < i2, "Step 2 must follow Step 1"


def test_claim_sequence_is_ordered():
    """Understand->claim, then work->prove->land->settle, in that order."""
    p = _protocol()
    order = ["fw_brief", "fw_claim", "fw_work_started", "fw_judge",
             "fw_land", "fw_verdict", "fw_work_done"]
    idx = [p.index(t) for t in order]
    assert idx == sorted(idx), f"protocol legs out of order: {list(zip(order, idx))}"


def test_honesty_rules_present():
    """The four honesty invariants the whole substrate exists to enforce."""
    p = _protocol()
    assert "renew" in p.lower(), "lease renewal must be taught for long work"
    assert "oracle" in p.lower() and "honest" in p.lower(), (
        "a failing oracle is an honest result, not an error")
    assert "verify" in p.lower(), "the verify command comes from the brief"
    assert "force" in p.lower() or "reject" in p.lower(), (
        "a rejected push must be surfaced, never force-pushed")


def test_protocol_is_stable_text():
    """The protocol is deterministic (same text every call) so an injected
    system prompt is byte-stable across turns (no churn / no diff noise)."""
    assert _protocol() == _protocol()
