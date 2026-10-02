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

  seed          one per task a seed pushed: task/ok (the push result —
                refused specs are data too); the DORA lead-time anchor
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
  python3 -m l2.metrics --dora [files...]
prints counts, the claim reject rate, claim->verdict latency
percentiles (joined on att across the scanned files), and verdict
outcomes; --dora adds the five DORA keys (ops/DORA-METRICS-MAP.md
layer 1) as a "dora" section — deployments per day (landed verdicts),
seed->verdict lead time, merit-fail ratio, crash->recovery spans,
rework (races + requeues + re-done verdicts). A MISSING file exits 2
with nothing on stdout — 'no data recorded' and 'cannot read the data'
must not look alike. An empty file is a zero board: exit 0.
"""

from __future__ import annotations

import json
import os
import sys
import time

KINDS = ("seed", "claim", "verdict", "heir", "crash", "eviction",
         "lease_expired")


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


def dora(events: list[dict]) -> dict:
    """The five DORA keys over the stream (ops/DORA-METRICS-MAP.md,
    layer 1). Pure over the event list, same observer discipline as
    summarize():

    deployment frequency  verdicts LANDED per UTC day — fixed AND
                        verdict_pushed (an unpushed green landed nothing)
    lead time           seed->verdict joined on task (the claim join is
                        NOT enough: delivery starts at the seed, not the
                        CAS win); verdicts on never-seeded tasks are not
                        silently counted as measured
    change failure rate merit-fail verdicts (fixed false) / verdicts
    recovery time       crash/lease-expiry -> heir re-claim -> verdict
                        span per task; an unrecovered failure is a
                        counted failure and no span
    rework rate         requeues + duplicate-claim races + verdicts
                        re-done (2nd+ verdict on a task), over claim
                        attempts

    No measurement is a measured zero: rates/percentiles are None when
    their denominator is empty.
    """
    per_day: dict[str, int] = {}
    landed = 0
    seed_ts: dict[str, float] = {}      # task -> earliest ok-seed ts
    attempts = races = requeues = merit_fail = total_verdicts = 0
    won_claims: list[tuple] = []        # (task, ts, att) in stream order
    failures: list[tuple] = []          # (task, ts) crash/lease-expiry
    verdicts: dict[str, list[float]] = {}  # task -> [ts, ...]
    verdict_by_att: dict[str, float] = {}  # att -> ts
    for ev in events:
        kind = ev.get("event")
        ts = ev.get("ts")
        t = ev.get("ts") if isinstance(ts, (int, float)) else None
        if kind == "seed":
            task = ev.get("task")
            if (ev.get("ok") is True and task
                    and t is not None
                    and (task not in seed_ts or t < seed_ts[task])):
                seed_ts[task] = t
        elif kind == "claim":
            attempts += 1
            if ev.get("outcome") == "race":
                races += 1
            elif ev.get("outcome") == "won":
                won_claims.append((ev.get("task"), t, ev.get("att")))
        elif kind == "verdict":
            total_verdicts += 1
            if ev.get("fixed") is False:
                merit_fail += 1
            if ev.get("fixed") is True and ev.get("verdict_pushed") is True:
                landed += 1
                day = time.strftime("%Y-%m-%d", time.gmtime(t))
                per_day[day] = per_day.get(day, 0) + 1
            task = ev.get("task")
            if task and t is not None:
                verdicts.setdefault(task, []).append(t)
            att = ev.get("att")
            if att and t is not None and (att not in verdict_by_att
                                          or t < verdict_by_att[att]):
                # earliest verdict per att: recovery closes at the FIRST
                # green, a later re-done does not extend the outage
                verdict_by_att[att] = t
        elif kind == "heir":
            if ev.get("requeued") is True:
                requeues += 1
        elif kind in ("crash", "lease_expired"):
            task = ev.get("task")
            if task and t is not None:
                failures.append((task, t))
    leads = sorted(
        vt - seed_ts[task]
        for task, vts_list in verdicts.items() if task in seed_ts
        for vt in vts_list if vt >= seed_ts[task])
    recovered: list[float] = []
    for task, f_ts in failures:
        re_claim = next(((c_ts, att) for c_task, c_ts, att in won_claims
                         if c_task == task and c_ts is not None
                         and att and c_ts >= f_ts), None)
        if re_claim is None:
            continue
        v_ts = verdict_by_att.get(re_claim[1])
        if v_ts is not None and v_ts >= re_claim[0]:
            recovered.append(v_ts - f_ts)
    redone = sum(max(0, len(v) - 1) for v in verdicts.values())
    rework_events = requeues + races + redone
    days_active = len(per_day)
    return {
        "deployment_frequency": {
            "landed_total": landed,
            "per_day": dict(sorted(per_day.items())),
            "days_active": days_active,
            "mean_per_active_day": round(landed / days_active, 2)
            if days_active else None,
        },
        "lead_time_s": {
            "n": len(leads),
            "p50": round(_pctl(leads, 0.50), 3) if leads else None,
            "p90": round(_pctl(leads, 0.90), 3) if leads else None,
            "max": round(leads[-1], 3) if leads else None,
        },
        "change_failure_rate": {
            "merit_fails": merit_fail,
            "total": total_verdicts,
            "rate": round(merit_fail / total_verdicts, 4)
            if total_verdicts else None,
        },
        "recovery_s": {
            "failures": len(failures),
            "n": len(recovered),
            "p50": round(_pctl(sorted(recovered), 0.50), 3)
            if recovered else None,
            "p90": round(_pctl(sorted(recovered), 0.90), 3)
            if recovered else None,
            "max": round(max(recovered), 3) if recovered else None,
        },
        "rework_rate": {
            "requeues": requeues,
            "race_rejections": races,
            "redone_verdicts": redone,
            "events": rework_events,
            "attempts": attempts,
            "rate": round(rework_events / attempts, 4) if attempts else None,
        },
    }


def main(argv: list[str]) -> int:
    dora_flag = "--dora" in argv
    paths = [a for a in argv if a != "--dora"]
    paths = paths or [metrics_file()]
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
    if dora_flag:
        s["dora"] = dora(events)
    s["files"] = paths
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
