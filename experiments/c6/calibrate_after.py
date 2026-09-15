#!/usr/bin/env python3
"""c6 — ladder recompute over the FULL night stream (c2..c6).

Same gate as c5 (unchanged thresholds): Wilson LCB90 z=1.6449,
accept .70 / agree .75, MIN_N=5, via tools/calibration.py.
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
C6 = HERE

LANE = "google/gemini-3.5-flash-lite"


def load_rows():
    paths = (
        glob.glob(f"{C2}/results_worker_ledger.jsonl")
        + glob.glob(f"{C3}/results_billing_probe_ledger.jsonl")
        + glob.glob(f"{C3}/results_c3_ledger.w*.jsonl")
        + glob.glob(f"{C4}/results_c4_ledger.*.jsonl")
        + glob.glob(f"{C6}/results_c6_ledger.*.jsonl")
    )
    rows = []
    for p in paths:
        rows.extend(json.loads(l) for l in open(p) if l.strip())
    return rows


def main() -> None:
    rows = load_rows()
    verdicts = [r for r in rows if r.get("event") == "verdict"]
    lanes = {}
    for v in verdicts:
        lane = v.get("model", "unknown")
        rec = lanes.setdefault(lane, {"n": 0, "successes": 0, "tasks": []})
        rec["n"] += 1
        rec["successes"] += 1 if v.get("fixed") else 0
        rec["tasks"].append(
            f"{v.get('task')}:{'fixed' if v.get('fixed') else 'failed'}")

    report = {"probe": "c6-calibrate", "n_ledger_rows": len(rows),
              "n_verdicts": len(verdicts), "lanes": {}}
    for lane, rec in sorted(lanes.items()):
        lcb = calibration.wilson_lcb(rec["successes"], rec["n"])
        report["lanes"][lane] = {
            **rec,
            "wilson_lcb90": round(lcb, 4),
            "gate_rung": calibration.gate_rung(rec["successes"], rec["n"]),
            "meets_min_n": rec["n"] >= calibration.MIN_N,
        }

    lane = report["lanes"].get(LANE, {})
    report["headline"] = {
        "worker_leg_lane": LANE,
        "record": f"{lane.get('successes')}/{lane.get('n')}",
        "lcb90": lane.get("wilson_lcb90"),
        "rung": lane.get("gate_rung"),
    }
    with open(os.path.join(HERE, "results_c6_calibration.json"), "w") as f:
        json.dump(report, f, indent=1)
    print(json.dumps(report["headline"]))
    for name, l in report["lanes"].items():
        print(json.dumps({"lane": name,
                          "record": f"{l['successes']}/{l['n']}",
                          "lcb": l["wilson_lcb90"], "rung": l["gate_rung"]}))


if __name__ == "__main__":
    main()
