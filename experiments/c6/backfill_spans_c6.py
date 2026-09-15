#!/usr/bin/env python3
"""c6 lineage backfill — replay the 8 fresh-task verdicts as OTLP/JSON
spans, same wire shape and deterministic trace identity as c5's
backfill (pillar-3 increment; idempotent per attemptId).

Requires the c2 receiver (experiments/c2/otel_receiver.py) listening on
127.0.0.1:4318; run under the otel-venv python alongside it.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "c5"))
import backfill_spans as c5b  # noqa: E402


def load_c6_verdicts():
    rows = []
    for p in sorted(glob.glob(f"{HERE}/results_c6_ledger.*.jsonl")):
        rows.extend(json.loads(l) for l in open(p) if l.strip())
    return [r for r in rows if r.get("event") == "verdict"]


def main() -> None:
    verdicts = load_c6_verdicts()
    spans = []
    import time
    base = int(time.time_ns())
    for i, v in enumerate(verdicts):
        att = v["attemptId"]
        trace_id, root_id = c5b.ids_for(att)
        t = base + i * 1000
        common = {"task": v.get("task"), "attemptId": att,
                  "model": v.get("model", ""), "fixed": v.get("fixed")}
        if v.get("commit_sha"):
            common["commit_sha"] = v["commit_sha"]
        spans.append(c5b.span("worker-dispatch", trace_id, root_id, None, t,
                              common))
        spans.append(c5b.span("host-verify", trace_id,
                              c5b.__dict__["hashlib"].sha256(
                                  (att + "verify").encode())
                              .hexdigest()[:16],
                              root_id, t + 1,
                              {"host_passed": v.get("host_passed", 0),
                               "host_failed": v.get("host_failed", 0)}))

    resource = {"attributes": [
        {"key": "service.name",
         "value": {"stringValue": "swarmo-worker-leg"}}]}
    n_batches = 0
    import urllib.request
    for i in range(0, len(spans), 40):
        payload = {"resourceSpans": [{"resource": resource,
                                      "scopeSpans": [{"spans":
                                                      spans[i:i + 40]}]}]}
        req = urllib.request.Request(
            c5b.ENDPOINT, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
        n_batches += 1
    print(json.dumps({"verdicts_replayed": len(verdicts),
                      "spans_sent": len(spans), "batches": n_batches}))


if __name__ == "__main__":
    main()
