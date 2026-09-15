#!/usr/bin/env python3
"""C-git probe (H-C1, H-C3, H-C4, DESIGNS.md §3): can git itself be the
coordination substrate?

H-C1: ref compare-and-swap gives exactly-once claims. 32 processes race
`git update-ref --stdin` (verify old + update) on one ref, x10 trials.
Frozen threshold: 10/10 trials — exactly 1 accept, 31 rejects, final ref
value = winner's value.

H-C3: lineage queries stay fast at fleet-scale history. Synthesize
~50k-commit repo via `git fast-import` (fixed tree, trailer per commit);
time `git log --grep` for one task's ancestry. Frozen: < 5 s.

H-C4: claims-table and verdict read paths stay fast at fleet scale.
Create 10k refs/claims/t-* (batched update-ref) + 200 refs/notes/verdicts
annotations; time `for-each-ref` full list and note reads. Frozen: both
< 10 s combined.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "gitdata")
N_RACERS = 32
N_TRIALS = 10
N_COMMITS = 50_000
N_REFS = 10_000
N_NOTES = 200


def run(cmd, cwd=None, inp=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, input=inp, text=True,
                       capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd} rc={r.returncode}\n{r.stderr[:2000]}")
    return r


def racer(repo: str, old: str, new: str) -> int:
    """One claim attempt: CAS refs/claims/t-1 old->new via a txn.

    Note: a --stdin transaction may touch a ref once only, so the
    compare-and-swap lives in update's <oldvalue> argument (verify+
    update on the same ref is rejected: 'multiple updates not allowed').
    """
    txn = (f"start\nupdate refs/claims/t-1 {new} {old}\n"
           "prepare\ncommit\n")
    r = run(["git", "update-ref", "--stdin"], cwd=repo, inp=txn, check=False)
    return r.returncode


def h_c1() -> dict:
    repo = os.path.join(DATA, "cas")
    run(["rm", "-rf", repo])
    run(["git", "init", "-q", "-b", "main", repo])
    with open(os.path.join(repo, "base.txt"), "w") as f:
        f.write("base\n")
    run(["git", "add", "-A"], cwd=repo)
    run(["git", "-c", "user.email=c@c", "-c", "user.name=c",
         "commit", "-qm", "base"], cwd=repo)
    old = run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    # 32 distinct candidate new values (in-repo blobs)
    news = []
    for i in range(N_RACERS):
        blob = os.path.join(repo, f"cand-{i}.txt")
        with open(blob, "w") as f:
            f.write(f"claimant {i}\n")
        news.append(run(["git", "hash-object", "-w", f"cand-{i}.txt"],
                        cwd=repo).stdout.strip())
        os.unlink(blob)

    trials = []
    for t in range(N_TRIALS):
        run(["git", "update-ref", f"refs/claims/t-1", old], cwd=repo)
        procs = [subprocess.Popen(
            ["git", "update-ref", "--stdin"], cwd=repo, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE) for _ in range(N_RACERS)]
        for p, new in zip(procs, news):
            txn = (f"start\nupdate refs/claims/t-1 {new} {old}\n"
                   "prepare\ncommit\n")
            p.stdin.write(txn)
            p.stdin.close()
        rcs = [p.wait(timeout=60) for p in procs]
        errs = [p.stderr.read()[:120] for p in procs if p.returncode != 0]
        final = run(["git", "rev-parse", "refs/claims/t-1"],
                    cwd=repo).stdout.strip()
        winners = [n for n, rc in zip(news, rcs) if rc == 0]
        trials.append({
            "accepts": len(winners),
            "rejects": sum(1 for rc in rcs if rc != 0),
            "final_matches_winner": (len(winners) == 1 and final == winners[0]),
            "sample_err": errs[0] if errs else "",
        })
    c1_pass = all(t["accepts"] == 1 and t["rejects"] == N_RACERS - 1
                  and t["final_matches_winner"] for t in trials)
    return {"trials": trials, "pass": c1_pass}


def h_c3() -> dict:
    repo = os.path.join(DATA, "lineage")
    run(["rm", "-rf", repo])
    os.makedirs(repo)
    run(["git", "init", "-q", "-b", "main", repo])
    gitdir = run(["git", "rev-parse", "--absolute-git-dir"],
                 cwd=repo).stdout.strip()
    assert gitdir.startswith(repo), f"guard: git-dir escaped probe repo: {gitdir}"
    t0 = time.perf_counter()
    lines = ["blob", "mark :1", "data 6", "hello\n"]
    ts = 1_700_000_000
    for i in range(N_COMMITS):
        msg = f"step {i}\n\nAttempt: att-{i}\nTask: t-{i % 100:03d}\n"
        lines += [
            f"commit refs/heads/main",
            f"mark :{i + 10}",
            f"author c <c@local> {ts + i} +0000",
            f"committer c <c@local> {ts + i} +0000",
            f"data {len(msg.encode())}",
            msg,
            "M 100644 :1 file.txt",
        ]
    run(["git", "fast-import", "--quiet"], cwd=repo, inp="\n".join(lines) + "\n")
    import_s = time.perf_counter() - t0
    run(["git", "rev-parse", "main"], cwd=repo)  # sanity

    t0 = time.perf_counter()
    r = run(["git", "log", "--grep", "Task: t-007", "--format=%H"],
            cwd=repo)
    grep_s = time.perf_counter() - t0
    n_matched = len(r.stdout.strip().splitlines())
    size = run(["git", "count-objects", "-vH"], cwd=repo).stdout
    return {"import_s": round(import_s, 1), "grep_s": round(grep_s, 2),
            "commits_matched": n_matched,
            "expected_matches": N_COMMITS // 100,
            "repo_size": [l for l in size.splitlines() if "size-pack" in l],
            "pass": grep_s < 5.0 and n_matched == N_COMMITS // 100}


def h_c4(lineage_repo: str) -> dict:
    repo = os.path.join(DATA, "claims")
    run(["rm", "-rf", repo])
    run(["git", "init", "-q", "-b", "main", repo])
    with open(os.path.join(repo, "f.txt"), "w") as f:
        f.write("x\n")
    run(["git", "add", "-A"], cwd=repo)
    run(["git", "-c", "user.email=c@c", "-c", "user.name=c",
         "commit", "-qm", "base"], cwd=repo)
    sha = run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    t0 = time.perf_counter()
    batch = "".join(f"update refs/claims/t-{i:05d} {sha}\n"
                    for i in range(N_REFS))
    run(["git", "update-ref", "--stdin"], cwd=repo, inp=batch)
    create_s = time.perf_counter() - t0

    # 200 verdict notes on the lineage repo's commits (one-time cost)
    heads = run(["git", "rev-list", "--max-count", str(N_NOTES), "main"],
                cwd=lineage_repo).stdout.split()
    t0 = time.perf_counter()
    for c in heads:
        run(["git", "notes", "--ref=verdicts", "add", "-m",
             "rung: proven gate=agree", c], cwd=lineage_repo)
    notes_create_s = time.perf_counter() - t0

    # timed read paths
    t0 = time.perf_counter()
    r = run(["git", "for-each-ref", "--format=%(refname) %(objectname)",
             "refs/claims"], cwd=repo)
    foreach_s = time.perf_counter() - t0
    n_refs = len(r.stdout.strip().splitlines())

    t0 = time.perf_counter()
    lst = run(["git", "notes", "--ref=verdicts", "list"],
              cwd=lineage_repo).stdout.split()
    # 'git notes list' prints two shas per line (annotated object, note
    # blob) in some order; pick the blob column by type, not by position.
    check = run(["git", "cat-file", "--batch-check"], cwd=lineage_repo,
                inp="\n".join(lst[:2])).stdout.splitlines()
    blob_col = 0 if check[0].split()[1] == "blob" else 1
    blobs = lst[blob_col::2]
    note_bodies = run(["git", "cat-file", "--batch"],
                      cwd=lineage_repo,
                      inp="".join(b + "\n" for b in blobs)).stdout
    notes_s = time.perf_counter() - t0
    n_notes = sum(1 for b in blobs if f"rung: proven" in note_bodies)

    return {
        "n_refs": n_refs, "n_notes_read": len(blobs), "n_notes_ok": n_notes,
        "create_10k_refs_s": round(create_s, 2),
        "create_200_notes_s": round(notes_create_s, 1),
        "foreachref_full_list_s": round(foreach_s, 3),
        "notes_read_s": round(notes_s, 3),
        "combined_reads_s": round(foreach_s + notes_s, 3),
        "pass": n_refs == N_REFS and n_notes == N_NOTES
                and (foreach_s + notes_s) < 10.0,
    }


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
    result = {"probe": "C-git", "hypotheses": ["H-C1", "H-C3", "H-C4"]}
    result["h_c1"] = h_c1()
    result["h_c3"] = h_c3()
    result["h_c4"] = h_c4(os.path.join(DATA, "lineage"))
    result["verdict"] = ("PASS" if result["h_c1"]["pass"] and result["h_c3"]["pass"]
                         and result["h_c4"]["pass"] else "FAIL")
    result["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(os.path.join(HERE, "results_git.json"), "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({
        "h_c1": result["h_c1"]["pass"],
        "h_c1_trials": [(t["accepts"], t["rejects"], t["final_matches_winner"])
                        for t in result["h_c1"]["trials"]],
        "h_c3": {k: result["h_c3"][k] for k in ("import_s", "grep_s", "pass")},
        "h_c4": {k: result["h_c4"][k] for k in
                 ("n_refs", "combined_reads_s", "pass")},
        "verdict": result["verdict"],
    }))


if __name__ == "__main__":
    main()
