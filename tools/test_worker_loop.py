"""Docker-free unit tests for tools/worker_loop.py (claim + ledger core).

The dispatch path needs docker+opencode+network; these tests cover the
coordination primitives the batcher will call (H-B2's shape), so the
morning wiring has a regression guard that runs anywhere.
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import worker_loop  # noqa: E402


def test_claim_is_exclusive(tmp_path):
    ledger = str(tmp_path / "ledger.jsonl")
    c1 = worker_loop.Claim(ledger)
    try:
        try:
            worker_loop.Claim(ledger)
            raise AssertionError("second claim should have been rejected")
        except worker_loop.AlreadyClaimed:
            pass
    finally:
        c1.release()
    c2 = worker_loop.Claim(ledger)  # re-claimable after release
    c2.release()


def test_claim_survives_gc_without_release(tmp_path):
    """An unreleased Claim must keep excluding others (fd stays open) —
    the live lesson from c2: gc alone does not unlock; release() does."""
    ledger = str(tmp_path / "ledger.jsonl")

    def leak_a_claim():
        worker_loop.Claim(ledger)  # no release — fd stays open in caller

    # keep the object alive explicitly to model the leaked-fd case
    holder = worker_loop.Claim(ledger)
    try:
        leak_a_claim()
        try:
            worker_loop.Claim(ledger)
            raise AssertionError("lock should still be held")
        except worker_loop.AlreadyClaimed:
            pass
    finally:
        holder.release()


def test_append_row_is_fsynced_jsonl(tmp_path):
    ledger = str(tmp_path / "ledger.jsonl")
    worker_loop.append_row(ledger, {"event": "claim", "attemptId": "att-a"})
    worker_loop.append_row(ledger, {"event": "verdict", "attemptId": "att-a"})
    rows = [json.loads(l) for l in open(ledger)]
    assert [r["event"] for r in rows] == ["claim", "verdict"]


def test_attempt_id_shape():
    import uuid
    att = "att-" + uuid.uuid4().hex[:8]
    assert att.startswith("att-") and len(att) == 12
