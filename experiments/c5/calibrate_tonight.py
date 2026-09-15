#!/usr/bin/env python3
"""c5 — the ladder consumes tonight's verdict stream (Design B closure).

Design B's causal chain ends at: worker leg -> ledger rows -> Wilson
ladder (E7). This runs tools/calibration.py over every ledger row
written tonight by the new leg (c2 batch, c3 swarm, c4 crash/revive,
billing probes) and reports the lane rungs. No thresholds changed; the
gate is production's (LCB90 accept .70 / agree .75, MIN_N=5).
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import calibration  # noqa: E402

C2 = os.path.join(HERE, "..", "c2")
C3 = os.path.join(HERE, "..", "c3")
C4 = os.path.join(HERE, "..", "c4")


def load_ledgers():
    paths = (
        glob.glob(f"{C2}/results_worker_ledger.jsonl")
        + glob.glob(f"{C3}/results_billing_probe_ledger.jsonl")
        + glob.glob(f"{C3}/results_c3_ledger.w*.jsonl")
        + glob.glob(f"{C4}/results_c4_ledger.*.jsonl")
    )
    rows = []
    for p in paths:
        rows.extend(json.loads(l) for l in open(p) if l.strip())
    return rows


def main() -> None:
    rows = load_ledgers()
    verdicts = [r for r in rows if r.get("event") == "verdict"]
    lanes = {}
    for v in verdicts:
        lane = v.get("model", "unknown")
        rec = lanes.setdefault(lane, {"n": 0, "successes": 0,
                                      "tasks": []})
        rec["n"] += 1
        rec["successes"] += 1 if v.get("fixed") else 0
        rec["tasks"].append(
            f"{v.get('task')}:{'fixed' if v.get('fixed') else 'failed'}")

    report = {"probe": "c5-calibrate", "n_ledger_rows": len(rows),
              "n_verdicts": len(verdicts), "lanes": {}}
    for lane, rec in sorted(lanes.items()):
        lcb = calibration.wilson_lcb(rec["successes"], rec["n"])
        rung = calibration.gate_rung(rec["successes"], rec["n"])
        report["lanes"][lane] = {
            **rec,
            "wilson_lcb90": round(lcb, 4),
            "gate_rung": rung,
            "meets_min_n": rec["n"] >= calibration.MIN_N,
        }

    google = report["lanes"].get("google/gemini-3.5-flash-lite", {})
    report["headline"] = {
        "worker_leg_lane": "google/gemini-3.5-flash-lite",
        "record": f"{google.get('successes')}/{google.get('n')}",
        "rung_tonight": google.get("gate_rung"),
        "note": ("PROVEN requires LCB90 >= .75 (agree gate); a single "
                 "failure in 13 keeps the lane at VERIFIED tonight — "
                 "more nights of data promote it."),
    }
    with open(os.path.join(HERE, "results_calibration.json"), "w") as f:
        json.dump(report, f, indent=1)
    print(json.dumps(report["headline"]))
    for lane, l in report["lanes"].items():
        print(json.dumps({"lane": lane, "record": f"{l['successes']}/{l['n']}",
                          "lcb": l["wilson_lcb90"], "rung": l["gate_rung"]}))


if __name__ == "__main__":
    main()
