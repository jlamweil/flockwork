#!/usr/bin/env python3
"""c5b — lineage backfill: replay tonight's ledger verdicts as OTLP/JSON
spans to a local collector (pillar-3 as an optional increment on Design B).

Wire format: real OTLP/HTTP JSON (application/json, resourceSpans
shape). Trace identity is derived deterministically from the attemptId
(sha256), so a replay is idempotent at the collector. Each attempt gets
a 'worker-dispatch' root span and a 'host-verify' child span — the
parent-child shape H-A2 demanded, now carrying ledger facts
(task/attemptId/fixed/commit_sha) as attributes.
"""
import glob
import hashlib
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENDPOINT = "http://127.0.0.1:4318/v1/traces"

C2 = os.path.join(HERE, "..", "c2")
C3 = os.path.join(HERE, "..", "c3")
C4 = os.path.join(HERE, "..", "c4")


def load_verdicts():
    paths = (
        glob.glob(f"{C2}/results_worker_ledger.jsonl")
        + glob.glob(f"{C3}/results_billing_probe_ledger.jsonl")
        + glob.glob(f"{C3}/results_c3_ledger.w*.jsonl")
        + glob.glob(f"{C4}/results_c4_ledger.*.jsonl")
    )
    rows = []
    for p in paths:
        rows.extend(json.loads(l) for l in open(p) if l.strip())
    return [r for r in rows if r.get("event") == "verdict"]


def ids_for(attempt_id: str):
    h = hashlib.sha256(attempt_id.encode()).hexdigest()
    return h[:32], h[32:64]  # trace_id(16B), root span_id(8B)


def span(name, trace_id, span_id, parent, t_ns, attrs):
    return {
        "traceId": trace_id, "spanId": span_id,
        **({"parentSpanId": parent} if parent else {}),
        "name": name, "kind": 1,
        "startTimeUnixNano": str(t_ns), "endTimeUnixNano": str(t_ns + 1),
        "attributes": [{"key": k, "value": {"stringValue": str(v)}}
                       for k, v in attrs.items()],
    }


def main() -> None:
    verdicts = load_verdicts()
    spans = []
    base = int(time.time_ns())
    for i, v in enumerate(verdicts):
        att = v["attemptId"]
        trace_id, root_id = ids_for(att)
        t = base + i * 1000
        common = {"task": v.get("task"), "attemptId": att,
                  "model": v.get("model", ""), "fixed": v.get("fixed")}
        if v.get("commit_sha"):
            common["commit_sha"] = v["commit_sha"]
        spans.append(span("worker-dispatch", trace_id, root_id, None, t,
                          common))
        spans.append(span("host-verify", trace_id,
                          hashlib.sha256((att + "verify")
                                         .encode()).hexdigest()[:16],
                          root_id, t + 1,
                          {"host_passed": v.get("host_passed", 0),
                           "host_failed": v.get("host_failed", 0)}))

    resource = {"attributes": [
        {"key": "service.name",
         "value": {"stringValue": "swarmo-worker-leg"}}]}
    n_batches = 0
    for i in range(0, len(spans), 40):
        payload = {"resourceSpans": [{"resource": resource,
                                      "scopeSpans": [{"spans":
                                                      spans[i:i + 40]}]}]}
        req = urllib.request.Request(
            ENDPOINT, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
        n_batches += 1
    print(json.dumps({"verdicts_replayed": len(verdicts),
                      "spans_sent": len(spans), "batches": n_batches}))


if __name__ == "__main__":
    main()
