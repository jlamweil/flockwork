#!/usr/bin/env python3
"""c7 — cheapest separating check of the B-vs-C head-to-head (FREEZE-C7.md).

Replays c4's proven crash/revive flow against a git-native substrate
(Design C's mechanism) using ONLY git primitives: ref-CAS claims,
attempt refs, return commits with `Attempt:` trailers, verdict notes.
No flock, no JSONL ledger — asserted at runtime (no_b_machinery).

Flow (mirrors experiments/c4/crash_revive.py):
  1. clean worker t-a claims (CAS create), returns commit + verdict note
  2. two workers race the t-b claim (watched: exactly 1 accept); winner
     returns + notes. The rejected racer is NOT an attempt (E2: attempt
     = won claim = send-intent) and leaves no repo trace — by design.
  3. victim worker claims t-victim, spawns its tagged dispatch stand-in
     child; parent SIGKILLs the victim mid-dispatch (child orphaned = the
     c4 "leaked container" analogue); snapshot: claim-without-verdict
  4. sweep: kill orphan by tag (names embed task+attempt — same reason
     c4 killed containers by name), preserve the dead attempt in
     refs/claims/<task>@<att>, CAS-delete the live claim ref; run twice
     (idempotence watch)
  5. heir worker claims fresh (CAS create on the deleted ref), returns +
     notes
  6. audit: c4's flags + attempts_countable_from_repo (H2 reconstruction
     from git reads alone) + no_b_machinery
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "gnq")
ZERO = "0" * 40
TASKS = ["t-victim", "t-a", "t-b"]
GITID = ["-c", "user.email=c7@local", "-c", "user.name=c7"]


def run(cmd, inp=None, check=True, env=None):
    r = subprocess.run(cmd, cwd=REPO, input=inp, text=True,
                       capture_output=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} rc={r.returncode}\n{r.stderr[:2000]}")
    return r


def git(*args, inp=None, check=True, env=None):
    return run(["git", *GITID, *args], inp=inp, check=check, env=env)


def attempt_id(role: str) -> str:
    return f"att-{role}-{os.urandom(3).hex()}"


def cas_claim(task: str, att: str, old: str) -> int:
    """Claim = CAS refs/claims/<task> old->att-blob (c2 H-C1 shape:
    the compare-and-swap lives in update's <oldvalue> argument)."""
    new = git("hash-object", "-w", "--stdin", inp=att + "\n").stdout.strip()
    txn = (f"start\nupdate refs/claims/{task} {new} {old}\n"
           "prepare\ncommit\n")
    return git("update-ref", "--stdin", inp=txn, check=False).returncode


def claim_value(ref: str):
    r = git("rev-parse", "--verify", "--quiet", ref, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def att_of(value: str) -> str:
    return git("cat-file", "blob", value).stdout.strip()


def scan_tagged(tag: str) -> list:
    """Processes whose cmdline embeds tag (the docker-free container
    stand-in; container names embed task+attemptId — this is why)."""
    hits = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if any(tag.encode() in a for a in args):
            hits.append(int(pid))
    return hits


def verdicts_text() -> str:
    """Concatenated verdict-note bodies — git reads only."""
    lst = git("notes", "--ref=verdicts", "list", check=False).stdout.split()
    if not lst:
        return ""
    check = git("cat-file", "--batch-check",
                inp="\n".join(lst[:2])).stdout.splitlines()
    blob_col = 0 if check[0].split()[1] == "blob" else 1
    blobs = lst[blob_col::2]
    return git("cat-file", "--batch",
               inp="".join(b + "\n" for b in blobs)).stdout


def has_final_verdict(text: str, task: str) -> bool:
    return f"task: {task}\n" in text and "fixed: true" in text


def return_and_note(task: str, att: str) -> str:
    """The dispatch stand-in's success: a return commit with the
    `Attempt:` trailer on refs/tasks/<task> + a verdict note."""
    base = git("rev-parse", "main").stdout.strip()
    base_tree = git("rev-parse", "main^{tree}").stdout.strip()
    blob = git("hash-object", "-w", "--stdin",
               inp=f"fix for {task} by {att}\n").stdout.strip()
    idx = os.path.join(REPO, ".git", f"idx-{task}-{att}")
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    git("read-tree", base_tree, env=env)
    git("update-index", "--add",
        "--cacheinfo", f"100644,{blob},fix-{task}.txt", env=env)
    tree = git("write-tree", env=env).stdout.strip()
    os.unlink(idx)
    msg = f"fix {task}\n\nTask: {task}\nAttempt: {att}\n"
    sha = git("commit-tree", tree, "-p", base, "-m", msg).stdout.strip()
    git("update-ref", f"refs/tasks/{task}", sha)
    git("notes", "--ref=verdicts", "add", "-m",
        f"task: {task}\nattempt: {att}\nfixed: true", sha)
    return sha


def worker_mode(task: str, role: str) -> None:
    """Worker subprocess: claim -> (victim: hold until killed) ->
    return commit + verdict note. Prints one JSON line."""
    att = attempt_id(role)
    rc = cas_claim(task, att, ZERO)
    if rc != 0:
        print(json.dumps({"task": task, "att": att, "event": "claim-lost"}))
        sys.exit(3)
    if role == "victim":
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)",
             f"gnq-{task}-{att}"],
            start_new_session=True)
        time.sleep(120)  # hold the claim until the parent SIGKILLs us
        sys.exit(0)
    sha = return_and_note(task, att)
    print(json.dumps({"task": task, "att": att, "event": "done",
                      "return": sha}))


def wait_tagged(tag: str, timeout: float = 15.0) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if scan_tagged(tag):
            return True
        time.sleep(0.2)
    return False


def sweep_once(text: str) -> list:
    """Revive sweep, git-native: reconcile by probe (kill orphan by
    tag), preserve the dead attempt in refs/claims/<task>@<att>, then
    CAS-delete the live claim ref. Order is crash-safe: interrupted
    between the two steps leaves the attempt ref present AND the claim
    live; re-running resolves ((2) no-ops, (3) retries)."""
    requeued = []
    for task in TASKS:
        val = claim_value(f"refs/claims/{task}")
        if val is None or has_final_verdict(text, task):
            continue
        att = att_of(val)
        for pid in scan_tagged(f"gnq-{task}-{att}"):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        git("update-ref", f"refs/claims/{task}@{att}", val, ZERO)
        txn = (f"start\ndelete refs/claims/{task} {val}\n"
               "prepare\ncommit\n")
        rc = git("update-ref", "--stdin", inp=txn, check=False).returncode
        if rc == 0:
            requeued.append(f"{task}.{att}")
    return requeued


def dump_refs(path: str) -> None:
    with open(path, "w") as f:
        f.write("== for-each-ref ==\n")
        f.write(git("for-each-ref", "--format=%(refname) %(objectname)",
                    "refs/claims", "refs/tasks").stdout)
        for task in TASKS:
            f.write(f"== reflog refs/claims/{task} ==\n")
            f.write(git("reflog", f"refs/claims/{task}",
                        check=False).stdout)
        f.write("== notes list ==\n")
        f.write(git("notes", "--ref=verdicts", "list").stdout)
        for task in TASKS:
            f.write(f"== log refs/tasks/{task} ==\n")
            f.write(git("log", "--format=%H %B",
                        f"refs/tasks/{task}", check=False).stdout)


def reconstruct() -> dict:
    """H2: {task -> attempts/returns/verdicts} from git reads alone."""
    attempts = {}
    for line in git("for-each-ref", "--format=%(refname) %(objectname)",
                    "refs/claims").stdout.splitlines():
        refname, val = line.split()
        task = refname.split("/", 2)[2].split("@")[0]
        attempts.setdefault(task, set()).add(att_of(val))
    text = verdicts_text()
    returns = {}
    for task in TASKS:
        shas = git("rev-list", f"refs/tasks/{task}",
                   check=False).stdout.split()
        returns[task] = shas
        for sha in shas:
            body = git("log", "-1", "--format=%B", sha).stdout
            for ln in body.splitlines():
                if ln.startswith("Attempt: "):
                    attempts.setdefault(task, set()).add(
                        ln.split("Attempt: ", 1)[1].strip())
    return {t: {"attempts": sorted(attempts.get(t, set())),
                "returns": returns.get(t, []),
                "verdict_note_count": text.count(f"task: {t}\n")}
            for t in TASKS}


def main() -> None:
    t0 = time.perf_counter()
    src = open(os.path.join(HERE, "gn_crash_revive.py")).read()
    needle = "import " + "fcntl"
    no_b_machinery = (needle not in src
                      and not [f for f in os.listdir(HERE)
                               if f.endswith(".jsonl")])
    df = subprocess.run(["df", "-h", "/"], capture_output=True,
                        text=True).stdout.splitlines()[-1]

    shutil.rmtree(REPO, ignore_errors=True)
    os.makedirs(REPO)
    git("init", "-q", "-b", "main")
    with open(os.path.join(REPO, "base.txt"), "w") as f:
        f.write("base\n")
    git("add", "-A")
    git("commit", "-qm", "base")

    me = [sys.executable, os.path.abspath(__file__), "worker"]

    # 1. clean solo worker t-a
    w_a = subprocess.Popen(me + ["t-a", "clean"],
                           stdout=subprocess.PIPE, text=True)
    out_a = json.loads(w_a.stdout.readline())
    w_a.wait()

    # 2. two workers race the t-b claim (watched, not gate)
    racers = [subprocess.Popen(me + ["t-b", "racer"],
                               stdout=subprocess.PIPE, text=True)
              for _ in range(2)]
    outs = [json.loads(p.stdout.readline()) for p in racers]
    for p in racers:
        p.wait()
    accepts = [o for o in outs if o["event"] == "done"]
    lost = [o for o in outs if o["event"] == "claim-lost"]

    # 3. victim: claim + tagged dispatch child, then SIGKILL mid-dispatch
    victim = subprocess.Popen(me + ["t-victim", "victim"],
                              stdout=subprocess.PIPE, text=True)
    tag_seen, victim_att = False, None
    deadline = time.perf_counter() + 20
    while time.perf_counter() < deadline:
        val = claim_value("refs/claims/t-victim")
        if val:
            victim_att = att_of(val)
            if wait_tagged(f"gnq-t-victim-{victim_att}", timeout=2):
                tag_seen = True
                break
        time.sleep(0.1)
    assert tag_seen and victim_att, "victim never dispatched"
    victim.kill()
    victim.wait()
    # snapshot AFTER the kill, BEFORE the sweep (c4's claim-no-verdict)
    val = claim_value("refs/claims/t-victim")
    snapshot = {
        "claim_value_present": val is not None,
        "claim_att": att_of(val) if val else None,
        "return_ref_absent": claim_value("refs/tasks/t-victim") is None,
        "no_verdict_note": not has_final_verdict(verdicts_text(),
                                                 "t-victim"),
    }

    # 4. sweep, twice (idempotence watch)
    requeued_1 = sweep_once(verdicts_text())
    requeued_2 = sweep_once(verdicts_text())

    # 5. heir claims fresh on the deleted ref and completes
    heir = subprocess.Popen(me + ["t-victim", "heir"],
                            stdout=subprocess.PIPE, text=True)
    out_h = json.loads(heir.stdout.readline())
    heir.wait()

    # 6. audit
    text = verdicts_text()
    verdicts_per_task = {t: text.count(f"task: {t}\n") for t in TASKS}
    heir_trailer_ok = False
    if out_h.get("return"):
        body = git("log", "-1", "--format=%B", out_h["return"]).stdout
        heir_trailer_ok = f"Attempt: {out_h['att']}" in body
    heir_note_ok = f"attempt: {out_h['att']}\n" in text
    preserved = claim_value(f"refs/claims/t-victim@{snapshot['claim_att']}")

    # ground truth as the driver observed it (E2: attempt = won claim;
    # the rejected racer is a contender, not an attempt)
    ground_truth = {
        "t-victim": {"attempts": sorted([snapshot["claim_att"],
                                         out_h["att"]]),
                     "verdict_note_count": 1},
        "t-a": {"attempts": [out_a["att"]], "verdict_note_count": 1},
        "t-b": {"attempts": [accepts[0]["att"]], "verdict_note_count": 1},
    }
    reconstructed = reconstruct()
    attempts_countable = all(
        reconstructed[t]["attempts"] == ground_truth[t]["attempts"]
        and reconstructed[t]["verdict_note_count"]
        == ground_truth[t]["verdict_note_count"] for t in TASKS)

    result = {
        "probe": "c7-gn-crash-revive",
        "freeze": "experiments/c7/FREEZE-C7.md",
        "hypotheses": ["H1", "H2"],
        "b_side_by_citation": "experiments/c4/results_c4.json",
        "df_pre_run": df.strip(),
        "victim_killed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "post_kill_snapshot": snapshot,
        "victim_claim_no_verdict": all([
            snapshot["claim_value_present"],
            snapshot["return_ref_absent"],
            snapshot["no_verdict_note"]]),
        "sweep_requeued": requeued_1,
        "sweep_idempotent_second_run": requeued_2,
        "heir_fresh_claim": out_h.get("event") == "done",
        "heir_fixed": (out_h.get("event") == "done"
                       and heir_trailer_ok and heir_note_ok),
        "verdicts_per_task": verdicts_per_task,
        "exactly_one_final_verdict": all(
            v == 1 for v in verdicts_per_task.values()),
        "dead_attempt_preserved": (preserved is not None and
                                   att_of(preserved)
                                   == snapshot["claim_att"]),
        "race_watch_exactly_one_accept":
            len(accepts) == 1 and len(lost) == 1,
        "orphans_left": scan_tagged("gnq-"),
        "reconstruction": reconstructed,
        "ground_truth": ground_truth,
        "attempts_countable_from_repo": attempts_countable,
        "no_b_machinery": no_b_machinery,
        "wall_s": round(time.perf_counter() - t0, 1),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    h1_flags = ["victim_claim_no_verdict", "heir_fresh_claim", "heir_fixed",
                "exactly_one_final_verdict", "dead_attempt_preserved"]
    result["h1_pass"] = (all(result[f] for f in h1_flags)
                         and result["sweep_requeued"]
                         == [f"t-victim.{snapshot['claim_att']}"]
                         and result["sweep_idempotent_second_run"] == []
                         and not result["orphans_left"])
    result["h2_pass"] = attempts_countable
    result["verdict"] = ("PASS" if result["h1_pass"] and result["h2_pass"]
                         else "FAIL")

    dump_refs(os.path.join(HERE, "refs_dump_c7.txt"))
    with open(os.path.join(HERE, "results_c7.json"), "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: result[k] for k in (
        "h1_pass", "h2_pass", "verdict", "sweep_requeued",
        "exactly_one_final_verdict", "race_watch_exactly_one_accept",
        "attempts_countable_from_repo", "no_b_machinery", "wall_s")}))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "worker":
        worker_mode(sys.argv[2], sys.argv[3])
    else:
        main()
