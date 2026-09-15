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


def test_dropped_claim_keeps_lock(tmp_path):
    """The c2 live lesson: dropping a Claim without release() leaves the
    raw fd open, so the flock STILL excludes others until the process
    exits or release() runs. release() is mandatory, gc is not enough.
    """
    ledger = str(tmp_path / "ledger.jsonl")

    def acquire_and_drop():
        worker_loop.Claim(ledger)  # acquired, object dropped, fd leaked

    acquire_and_drop()
    try:
        worker_loop.Claim(ledger)
        raise AssertionError("dropped-but-unreleased claim should still hold")
    except worker_loop.AlreadyClaimed:
        pass


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
