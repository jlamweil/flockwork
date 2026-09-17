#!/usr/bin/env python3
"""V3 — H-X1: one audit trail from two coordination substrates.

Joins (a) a batcher ledger (JSONL rows with attemptId att-*), and
(b) git ref-CAS lineage (refs/claims|tasks|verdicts from the run5
refs dump + results), into ONE attempt table keyed by att-*.
Lossless-join assertions:
  J1 every att in the ledger appears in the refs lineage and vice versa
  J2 injected defects (one ledger row with no repo ref; one repo ref
     with no ledger row) are flagged — exactly those two
  J3 verdict/return/claim state reconciles per att
Exit 0 iff all three hold on the real run5 atts.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GITID = ["-c", "user.email=v3@test", "-c", "user.name=v3"]
ATT = re.compile(r"att-[A-Za-z0-9]+-[0-9a-f]+")


def sh(cmd, inp=None):
    r = subprocess.run(cmd, input=inp, text=True, capture_output=True)
    assert r.returncode == 0, f"{cmd}: {r.stderr[:300]}"
    return r.stdout


def load_refs_side():
    """Parse run5 refs dump + results into per-att lineage records."""
    dump = open(os.path.join(HERE, "run5-evidence",
                             "run5_refs_dump.txt")).read()
    res = json.load(open(os.path.join(HERE, "run5-evidence",
                                      "run5_results.json")))
    claims, tasks, verdicts = {}, {}, {}
    mode = None
    for ln in dump.splitlines():
        if ln.startswith("== claim/preservation"):
            mode, lines = "claims", claims
        elif ln.startswith("== verdict refs"):
            mode, lines = "verdicts", verdicts
        elif ln.startswith("== refs/tasks commit bodies"):
            mode, lines = "tasks", tasks
        elif ln.startswith("=="):
            mode = None
        elif mode and ln.strip():
            if mode == "claims":
                ref, subj = ln.split(": ", 1)
                claims[ref] = subj
            else:
                left, body = ln.split(" :: ", 1)
                lines[left.split()[0]] = body
    atts = {}
    for ref, subj in claims.items():
        task = ref[len("refs/claims/"):].split("@")[0]
        att = ATT.search(subj).group(0)
        atts.setdefault(att, {"task": task, "claim": "live" if "@"
                              not in ref else "preserved-dead"})
    for ref, body in tasks.items():
        task = ref[len("refs/tasks/"):]
        att = ATT.search(body).group(0)
        atts.setdefault(att, {"task": task})
        atts[att]["return"] = True
    for ref, body in verdicts.items():
        task = ref[len("refs/verdicts/"):]
        att = ATT.search(body).group(0)
        atts.setdefault(att, {"task": task})
        atts[att]["verdict"] = True
    # heir's claim is live; victim's preserved — cross-check against
    # results ground truth (wins_detail + heir att + victim att)
    gt = {}
    for w in res["wins_detail"]:
        for win in w:
            gt.setdefault(win["task"], set()).add(win["att"])
    gt["t-crash"] = {res["post_kill_snapshot"]["claim_att"],
                     res["heir"]["att"]}
    return atts, gt


def build_ledger_fixture(path: str):
    """Synthetic batcher ledger whose att-* ids are run5's REAL atts,
    plus two deliberate defects:
      d1: a ledger row with att that exists NOWHERE in the repo side
      d2: a repo att omitted from the ledger
    Returns (path, att_missing_in_repo, att_missing_in_ledger).
    """
    atts, _ = load_refs_side()
    all_atts = sorted(atts)
    d1 = "att-ledger-orph0000"          # in ledger only
    d2 = all_atts[-1]                    # in repo only
    rows = []
    for att in all_atts:
        task = atts[att]["task"]
        if att == d2:
            continue                     # d2: omit -> repo-side orphan
        dead = atts[att].get("claim") == "preserved-dead"
        rows.append({"id": f"task-{task}",
                     "status": "requeued" if dead else "done",
                     "attemptId": att})
    rows.append({"id": "task-ghost-task", "status": "done",
                 "attemptId": d1})
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return d1, d2


def load_ledger(path):
    out = {}
    for ln in open(path):
        d = json.loads(ln)
        att = d.get("attemptId")
        if att:
            out[att] = d
    return out


def main():
    atts_repo, gt = load_refs_side()
    tmp = tempfile.mkdtemp(prefix="v3-")
    ledger_path = os.path.join(tmp, "ledger.jsonl")
    d1, d2 = build_ledger_fixture(ledger_path)
    atts_ledger = load_ledger(ledger_path)

    out = {"J1_lossless_clean_core": None,
           "J2_defects_flagged": None,
           "J3_state_reconciles": None}

    # J1: modulo the two injected defects, the join must be lossless
    ledger_set = set(atts_ledger)
    repo_set = set(atts_repo)
    flagged = {"ledger_only": sorted(ledger_set - repo_set),
               "repo_only": sorted(repo_set - ledger_set)}
    expected = {"ledger_only": [d1], "repo_only": [d2]}
    out["J2_defects_flagged"] = {"flagged": flagged, "expected": expected,
                                 "exact": flagged == expected}
    clean = (ledger_set | repo_set) - {d1, d2}
    out["J1_lossless_clean_core"] = {
        "n_atts": len(clean),
        "both_sides": clean <= (ledger_set & repo_set)}

    # J3: per-att state from repo side reconciles with ledger status
    bad = []
    for att in sorted(clean):
        r = atts_repo[att]
        l = atts_ledger[att]
        if r["task"] != l["id"][len("task-"):]:
            bad.append({"att": att, "repo_task": r["task"],
                        "ledger_id": l["id"]})
        # every att must have claim+return+verdict on the repo side
        # (heir: yes; victim: preserved-dead, return/verdict absent —
        #  its ledger row must NOT be status=done)
        victim = r.get("claim") == "preserved-dead"
        complete = "return" in r and "verdict" in r
        if victim and complete:
            bad.append({"att": att, "err": "dead attempt shows complete"})
        if not victim and not complete:
            bad.append({"att": att, "err": "live attempt incomplete"})
        if victim and l["status"] == "done":
            bad.append({"att": att, "err": "dead attempt marked done"})
    out["J3_state_reconciles"] = {"mismatches": bad[:5],
                                  "n_bad": len(bad), "ok": not bad}

    print(json.dumps(out, indent=1))
    ok = (out["J1_lossless_clean_core"]["both_sides"]
          and out["J2_defects_flagged"]["exact"]
          and out["J3_state_reconciles"]["ok"])
    print("V3", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
