#!/usr/bin/env python3
"""C-http probe (H-A3, DESIGNS.md §3): can a single-host HTTP coordinator
absorb fleet-scale concurrent brief pulls without error?

Frozen measurement: 60 concurrent processes x 5 GETs of a ~2KB brief from
`python3 http.server` on localhost; count statuses, p50/p95.
Frozen threshold: 0 non-200 AND p95 < 500 ms.
"""
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time

SELF = os.path.abspath(__file__)
HERE = os.path.dirname(SELF)
PORT = 8431
N_WORKERS = 60
N_GETS = 5


def worker(port: int, n: int) -> None:
    """Child: n sequential brief pulls; one JSON line per pull."""
    import urllib.request
    for _ in range(n):
        t0 = time.perf_counter()
        status = 0
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/brief.md", timeout=30) as r:
                status = r.status
                r.read()
        except Exception as e:  # noqa: BLE001 — record the failure class
            print(json.dumps({"status": status, "error": type(e).__name__}),
                  flush=True)
            continue
        wall_ms = (time.perf_counter() - t0) * 1000.0
        print(json.dumps({"status": status, "wall_ms": round(wall_ms, 1)}),
              flush=True)


def serve(root: str, port: int, ready: threading.Event,
          backlog: int = 5) -> None:
    import functools

    class Srv(socketserver.ThreadingTCPServer):
        request_queue_size = backlog  # socketserver default is 5
        daemon_threads = True
        allow_reuse_address = True

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=root)
    handler.log_message = lambda *a, **k: None  # request log stays out of artifacts
    with Srv(("127.0.0.1", port), handler) as srv:
        ready.set()
        srv.serve_forever(poll_interval=0.05)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(int(sys.argv[2]), int(sys.argv[3]))
        return

    backlog = 5
    label = "frozen"
    if "--backlog" in sys.argv:  # post-hoc repair check, labeled in output
        backlog = int(sys.argv[sys.argv.index("--backlog") + 1])
        label = f"posthoc-backlog-{backlog}"

    brief = os.path.join(HERE, "brief.md")
    with open(brief, "w") as f:
        f.write("# task-brief t-042\n\n" + ("Fix the bug so that the test suite passes. " * 60))
    assert 1500 < os.path.getsize(brief) < 3000, os.path.getsize(brief)  # ~2KB

    ready = threading.Event()
    thread = threading.Thread(target=serve, args=(HERE, PORT, ready, backlog),
                              daemon=True)
    thread.start()
    ready.wait(timeout=10)

    procs = [subprocess.Popen(
        [sys.executable, SELF, "worker", str(PORT), str(N_GETS)],
        stdout=subprocess.PIPE, text=True) for _ in range(N_WORKERS)]
    records = []
    for p in procs:
        out, _ = p.communicate(timeout=120)
        records.extend(json.loads(line) for line in out.splitlines() if line.strip())

    import statistics
    oks = [r["wall_ms"] for r in records if r.get("status") == 200]
    non200 = [r for r in records if r.get("status") != 200]
    oks_sorted = sorted(oks)
    p50 = statistics.median(oks) if oks else None
    p95 = oks_sorted[int(0.95 * (len(oks_sorted) - 1))] if oks else None
    result = {
        "probe": "C-http", "hypothesis": "H-A3", "label": label,
        "backlog": backlog,
        "n_expected": N_WORKERS * N_GETS, "n_received": len(records),
        "n_200": len(oks), "n_non200": len(non200),
        "non200_detail": non200[:10],
        "p50_ms": round(p50, 1) if p50 is not None else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "verdict": "PASS" if (len(non200) == 0 and p95 is not None and p95 < 500) else "FAIL",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    out_path = os.path.join(
        HERE, "results_http.json" if label == "frozen" else f"results_http_{label}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: result[k] for k in
                      ("label", "n_200", "n_non200", "p50_ms", "p95_ms", "verdict")}))


if __name__ == "__main__":
    main()
