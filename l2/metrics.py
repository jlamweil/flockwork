#!/usr/bin/env python3
"""flockwork metrics layer (INT-085a) — measure the swarm, touch no wire.

We cannot judge what we cannot measure: the pilots are judged with this
layer's numbers, so the layer itself is contract-frozen
(tests/test_metrics.py) and STRICTLY an observer. Lane operations
determine their results exactly as before; the layer records them.

Switch: FLOCKWORK_METRICS=1 enables. Off (the default) means hook sites
cost one env lookup, no file is ever created, and behavior is
byte-identical — zero cost, zero wire change.

Event vocabulary (one JSON object per JSONL line; `ts` is an epoch
float on every line):

  claim         task/worker/att/rc/outcome — outcome is won, race
                (the CAS duplicate-rejection: exactly-once held) or
                structural (broken environment; a re-scan cannot heal it)
  verdict       task/worker/att/fixed/oc_rc/pytest_rc/verdict_pushed —
                the attempt's judged outcome as the refs carry it
  heir          a crash archived and its heir requeued (c6 law):
                requeued/swept/sweep_error/reason
  crash         work_task_error: the attempt died unnaturally,
                claim_freed records whether the best-effort release won
  eviction      sweep archived+freed a task's live refs
  lease_expired reconcile recovered a stale lease: task/age_s/swept

Sink: FLOCKWORK_METRICS_FILE (default `flockwork-metrics.jsonl` in the
process cwd — workers on one host racing one board should share a cwd
or set the same absolute path so one summary sees the whole run). A
failed write is reported once on stderr and otherwise ignored: a broken
instrument must never break the lane.

Summary — the pilots' judge:
  python3 -m l2.metrics [files...]     # default: the sink file
prints counts, the claim reject rate, claim->verdict latency
percentiles (joined on att across the scanned files), and verdict
outcomes. A MISSING file exits 2 with nothing on stdout — 'no data
recorded' and 'cannot read the data' must not look alike. An empty
file is a zero board: exit 0.
"""

from __future__ import annotations

import json
import os
import sys
import time

KINDS = ("claim", "verdict", "heir", "crash", "eviction", "lease_expired")


def enabled() -> bool:
    return os.environ.get("FLOCKWORK_METRICS") == "1"


def metrics_file() -> str:
    return os.environ.get("FLOCKWORK_METRICS_FILE") or "flockwork-metrics.jsonl"


def emit(event: str, **fields):
    """Append one observation. Disabled → nothing, no file. A failed
    write never propagates (observe-only, even when the sink breaks)."""
    if not enabled():
        return None
    rec = {"ts": time.time(), "event": event}
    rec.update(fields)
    try:
        with open(metrics_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        print(f"metrics: emit failed (ignored): {e}", file=sys.stderr)
    return rec


def observe_attempt(out: dict) -> None:
    """Classify one attempt outcome (the dict _work_task returns) into
    the layer's vocabulary. A verdict-carrying outcome emits `verdict`;
    a requeue decision (the c6 heir machinery, including the rejected
    main-push requeue) also emits `heir`. Never called when disabled."""
    if out.get("event") != "attempted":
        return
    base = {
        "task": out.get("task"),
        "worker": out.get("worker"),
        "att": out.get("att"),
    }
    if "fixed" in out:
        emit(
            "verdict",
            fixed=out["fixed"],
            oc_rc=out.get("oc_rc"),
            pytest_rc=out.get("pytest_rc"),
            verdict_pushed=out.get("verdict_pushed"),
            heir_exhausted=out.get("heir_exhausted"),
            env_death=out.get("env_death"),
            **base,
        )
    if out.get("requeued") is not None:
        emit(
            "heir",
            requeued=out["requeued"],
            swept=out.get("swept"),
            sweep_error=out.get("sweep_error"),
            reason=out.get("reason"),
            **base,
        )


# ------------------------------------------------------------- summary


def load(path: str) -> tuple[list[dict], int]:
    """(events, malformed) from one JSONL file. Malformed lines are
    counted, never silently dropped on the floor of a lie: the summary
    reports how much of the stream it could not read."""
    events, bad = [], 0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except ValueError:
                bad += 1
                continue
            if isinstance(ev, dict):
                events.append(ev)
            else:
                bad += 1
    return events, bad


def _pctl(sorted_vals: list[float], q: float):
    """Percentile with linear interpolation (numpy's default). None
    when there is no data — 'no measurement', never a measured zero."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def summarize(events: list[dict], malformed: int = 0) -> dict:
    """The pilots' judge: counts, reject rate, latency percentiles,
    verdict outcomes. Pure over the event list — files are a CLI
    concern."""
    counts = {k: 0 for k in KINDS}
    claims = {"attempts": 0, "won": 0, "race_rejected": 0, "structural": 0}
    verdicts = {"total": 0, "fixed_true": 0, "fixed_false": 0,
                "push_failures": 0}
    heirs = {"requeues": 0, "exhausted": 0}
    claim_ts: dict[str, float] = {}   # att -> last won-claim ts
    verdict_ts: dict[str, float] = {}  # att -> last verdict ts
    for ev in events:
        kind = ev.get("event")
        if kind in counts:
            counts[kind] += 1
        if kind == "claim":
            claims["attempts"] += 1
            outcome = ev.get("outcome")
            if outcome == "won":
                claims["won"] += 1
                att = ev.get("att")
                if att and isinstance(ev.get("ts"), (int, float)):
                    claim_ts[att] = float(ev["ts"])
            elif outcome == "race":
                claims["race_rejected"] += 1
            elif outcome == "structural":
                claims["structural"] += 1
        elif kind == "verdict":
            verdicts["total"] += 1
            if ev.get("fixed") is True:
                verdicts["fixed_true"] += 1
            elif ev.get("fixed") is False:
                verdicts["fixed_false"] += 1
            if ev.get("verdict_pushed") is False:
                verdicts["push_failures"] += 1
            if ev.get("heir_exhausted"):
                heirs["exhausted"] += 1
            att = ev.get("att")
            if att and isinstance(ev.get("ts"), (int, float)):
                verdict_ts[att] = float(ev["ts"])
        elif kind == "heir":
            if ev.get("requeued") is True:
                heirs["requeues"] += 1
    lats = sorted(
        verdict_ts[a] - claim_ts[a]
        for a in verdict_ts
        if a in claim_ts and verdict_ts[a] >= claim_ts[a]
    )
    attempts = claims["attempts"]
    rejected = claims["race_rejected"] + claims["structural"]
    return {
        "events": len(events),
        "malformed": malformed,
        "counts": counts,
        "claims": {
            **claims,
            "reject_rate": round(rejected / attempts, 4) if attempts else 0.0,
        },
        "latency_s": {
            "n": len(lats),
            "p50": round(_pctl(lats, 0.50), 3) if lats else None,
            "p90": round(_pctl(lats, 0.90), 3) if lats else None,
            "max": round(lats[-1], 3) if lats else None,
        },
        "verdicts": verdicts,
        "heirs": heirs,
    }


def main(argv: list[str]) -> int:
    paths = argv or [metrics_file()]
    events: list[dict] = []
    malformed = 0
    for p in paths:
        if not os.path.isfile(p):
            print(f"metrics: no such metrics file: {p}", file=sys.stderr)
            return 2
        got, bad = load(p)
        events.extend(got)
        malformed += bad
    s = summarize(events, malformed)
    s["files"] = paths
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
