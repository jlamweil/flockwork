#!/usr/bin/env python3
"""V1 — offline reproduction of run4's audit failure + repair validation.

Builds a synthetic bare repo with the EXACT ref shape run4 produced
(from refs_dump_c8.txt), then drives the real Audit class against it:
  - OLD expression  n[len("refs/tasks/")]  -> must raise the run4 error
  - NEW expression  n[len("refs/tasks/"):] -> must parse the lineage
No ssh, no fleet: local-path git exec. Exit 0 only if every check passes.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "experiments", "c8"))
from c8_driver import Audit  # noqa: E402  the real class, unmodified

GITID = ["-c", "user.email=v1@test", "-c", "user.name=v1"]


def sh(cmd, inp=None):
    r = subprocess.run(cmd, input=inp, text=True, capture_output=True)
    assert r.returncode == 0, f"{cmd}: {r.stderr[:300]}"
    return r.stdout


def empty_commit(repo: str, message: str) -> str:
    tree = sh(["git", *GITID, "-C", repo, "hash-object", "-w", "-t",
               "tree", "--stdin"], inp="").strip()
    return sh(["git", *GITID, "-C", repo, "commit-tree", tree],
              inp=message + "\n").strip()


def build_repo(path: str) -> None:
    """Recreate run4's repo state from refs_dump_c8.txt (ground truth)."""
    sh(["git", "init", "-q", "--bare", path])
    dump = open(os.path.join(REPO, "experiments", "c8",
                             "refs_dump_c8.txt")).read()
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
                lines[ref] = subj
            else:
                left, body = ln.split(" :: ", 1)
                lines[left.split()[0]] = body  # "ref sha" -> ref
    for ref, subj in claims.items():
        sh(["git", "-C", path, "update-ref", ref, empty_commit(path, subj)])
    for ref, body in verdicts.items():
        sh(["git", "-C", path, "update-ref", ref,
            empty_commit(path, body.replace(" | ", "\n"))])
    for ref, body in tasks.items():
        sh(["git", "-C", path, "update-ref", ref,
            empty_commit(path, body.replace(" | ", "\n"))])


def main():
    tmp = tempfile.mkdtemp(prefix="v1-")
    repo = os.path.join(tmp, "coord.git")
    build_repo(repo)
    a = Audit.__new__(Audit)          # bypass ssh/__init__ assumptions

    def srv(*args, inp=None):         # local-lane stand-in, same contract
        r = subprocess.run(["git", "-C", repo] + list(args), input=inp,
                           text=True, capture_output=True)
        if r.returncode not in (0, 1):
            raise RuntimeError(f"audit {' '.join(args[:2])}: "
                               f"{r.stderr[:300]}")
        return r.stdout

    a.srv = srv

    out = {"V1a_old_expression": None, "V1a_new_expression": None,
           "V1b_lineage": None}
    refs = a.refs()

    # --- V1a-OLD: the committed-at-run4 slicing bug must reproduce -------
    try:
        tasks_old = {n[len("refs/tasks/")]: sha for n, sha in refs.items()
                     if n.startswith("refs/tasks/")}
        for t in tasks_old:
            a.returns_with_trailer(t)
        out["V1a_old_expression"] = {"reproduced": False,
                                     "error_head": "NO ERROR RAISED"}
    except RuntimeError as e:
        out["V1a_old_expression"] = {
            "reproduced": ("refs/tasks/t" in str(e)
                           and "unknown revision" in str(e)),
            "error_head": str(e)[:120]}

    # --- V1a-NEW: the harvested 1-char fix must parse cleanly ------------
    tasks_new = {n[len("refs/tasks/"):]: sha for n, sha in refs.items()
                 if n.startswith("refs/tasks/")}
    trailer = {t: a.returns_with_trailer(t) for t in tasks_new}
    out["V1a_new_expression"] = {
        "task_names_correct": sorted(tasks_new) == sorted(
            ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
             "t-crash"]),
        "attempt_parsed_once_each": all(len(v) == 1
                                        for v in trailer.values())}

    # --- V1b: full lineage reconstruction from the real run4 refs --------
    live_claims = {n[len("refs/claims/"):]: sha for n, sha in refs.items()
                   if n.startswith("refs/claims/") and "@" not in n}
    preserved = {n[len("refs/claims/"):]: sha
                 for n, sha in refs.items()
                 if n.startswith("refs/claims/") and "@" in n}
    verdicts = a.verdicts()
    verdict_att = {t: d.get("attempt") for t, d in verdicts.items()}
    claim_atts = {t: a.att_of_claim(s) for t, s in live_claims.items()}
    # run4 crashed before R["ground_truth"] was written; reconstruct the
    # expected lineage from the recorded worker reports (wins_detail) +
    # victim/heir atts — the same inputs the driver's audit would use.
    r4 = json.load(open(os.path.join(REPO, "experiments", "c8",
                                     "results_c8.json")))
    gt = {}
    for w in r4["wins_detail"]:
        for win in w:
            gt.setdefault(win["task"], []).append(win["att"])
    gt = {t: sorted(v) for t, v in gt.items()}
    gt["t-crash"] = sorted([r4["post_kill_snapshot"]["claim_att"],
                            r4["heir"]["att"]])
    all_tasks = ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
                 "t-crash"]
    recon_ok = True
    for t in all_tasks:
        atts = {claim_atts[t]} if t in claim_atts else set()
        for p in preserved:
            if p.split("@")[0] == t:
                atts.add(a.att_of_claim(preserved[p]))
        atts.update(trailer.get(t, []))
        if sorted(atts) != gt[t]:
            recon_ok = False
            out["V1b_lineage"] = {"mismatch_task": t,
                                  "reconstructed": sorted(atts),
                                  "expected": gt[t]}
            break
    if recon_ok:
        dead = "t-crash@att-example-host-bV-d5a499" in preserved
        heir_ok = (verdict_att.get("t-crash") == claim_atts.get("t-crash")
                   == "att-example-host-aH-f01afa")
        out["V1b_lineage"] = {
            "all_tasks_match_ground_truth": True,
            "dead_attempt_preserved": dead,
            "heir_verdict_matches_live_claim": heir_ok}

    print(json.dumps(out, indent=1))
    ok = (out["V1a_old_expression"]["reproduced"]
          and out["V1a_new_expression"]["task_names_correct"]
          and out["V1a_new_expression"]["attempt_parsed_once_each"]
          and out["V1b_lineage"].get("all_tasks_match_ground_truth")
          and out["V1b_lineage"].get("dead_attempt_preserved")
          and out["V1b_lineage"].get("heir_verdict_matches_live_claim"))
    print("V1", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
