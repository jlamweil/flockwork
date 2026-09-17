#!/usr/bin/env python3
"""c8 driver — the standing two-host discriminator (FREEZE-C8.md).

Orchestrates the counted run: seed bare repo on example-host-a -> 6-worker
(3 example-host-b over ssh + 3 example-host-a local) contention over 9 clean tasks ->
example-host-b victim SIGKILLed mid-dispatch on t-crash -> sweep run ON example-host-a
(kills the example-host-b orphan over ssh) -> heir on example-host-a -> audit with
git-reads-only from example-host-b -> results_c8.json + refs_dump_c8.txt.

Every audit read goes through ssh-exec against the bare repo: the repo
alone reconstructs the lineage; worker reports are used only as
driver-recorded ground truth to diff against.

Any phase failure marks the run discarded (never counted); leftover
processes are killed and disclosed.
"""
import json
import os
import selectors
import shlex
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SWARMO = os.path.dirname(os.path.dirname(HERE))
CM_DIR = "/tmp/c8/cm"
COORD_URL = "ssh://example-host-a/tmp/c8/coord.git"
COORD_PATH = "/tmp/c8/coord.git"
CRASH = "t-crash"
CLEAN = [f"t{i}" for i in range(1, 10)]
SSH_T = ["ssh", "-o", "BatchMode=yes", "example-host-a"]
GIT_SSH_CM = ("ssh -o BatchMode=yes "
              f"-o ControlPath={CM_DIR}/cm-%r@%h:%p "
              "-o ControlMaster=auto -o ControlPersist=600")
ENV = dict(os.environ, GIT_SSH_COMMAND=GIT_SSH_CM)
GITID = ["-c", "user.email=c8@twohost", "-c", "user.name=c8"]


def sh(cmd, inp=None, timeout=90, env=None):
    return subprocess.run(cmd, input=inp, text=True, capture_output=True,
                          timeout=timeout, env=env)


class Audit:
    """git reads ONLY, all via ssh-exec from example-host-b (transport-agnostic)."""

    def srv(self, *args, inp=None):
        # ssh joins its command args and hands them to the REMOTE shell:
        # quote-join ourselves so %(rounds)parens etc. survive intact
        cmd = " ".join(shlex.quote(a) for a in ["git", "-C", COORD_PATH]
                       + list(args))
        r = sh(SSH_T + [cmd], inp=inp)
        if r.returncode not in (0, 1):
            raise RuntimeError(f"audit {' '.join(args[:2])}: {r.stderr[:300]}")
        return r.stdout

    def refs(self) -> dict:
        by_ref = {}
        for line in self.srv("for-each-ref",
                             "--format=%(refname) %(objectname)").splitlines():
            name, sha = line.split()
            by_ref[name] = sha
        return by_ref

    def subject(self, sha: str) -> str:
        return self.srv("log", "-1", "--format=%s", sha).strip()

    def att_of_claim(self, sha: str) -> str:
        return self.subject(sha).split()[-1]

    def returns_with_trailer(self, task: str) -> list:
        out = []
        for s in self.srv("rev-list", f"refs/tasks/{task}").split():
            body = self.srv("log", "-1", "--format=%B", s)
            for ln in body.splitlines():
                if ln.startswith("Attempt: "):
                    out.append(ln.split("Attempt: ", 1)[1].strip())
        return out

    def verdicts(self) -> dict:
        """Post-repair substrate: refs/verdicts/<task> at verdict commits;
        returns {task: {task, attempt, fixed}} parsed from messages."""
        out = {}
        for n, sha in self.refs().items():
            if not n.startswith("refs/verdicts/"):
                continue
            body = self.srv("log", "-1", "--format=%B", sha)
            d = {}
            for ln in body.splitlines():
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    d[k.strip()] = v.strip()
            out[n[len("refs/verdicts/"):]] = d
        return out


def scan_tagged_local(tag: str) -> list:
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


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def kill_leftovers() -> None:
    for pid in scan_tagged_local("c8_worker.py"):
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    sh(SSH_T + ["pkill", "-9", "-f", "c8_worke[r].py"])


def json_line(proc, what: str, timeout: float) -> dict:
    """Read one JSON line from a spawned (possibly remote) worker with a
    wall-clock timeout (readline alone could block forever)."""
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.perf_counter() + timeout
    line = None
    while time.perf_counter() < deadline:
        if sel.select(0.5):
            line = proc.stdout.readline()
            if line:
                break
        if proc.poll() is not None and not sel.select(0.1):
            break
    sel.close()
    if not line:
        raise RuntimeError(f"{what} produced no JSON line")
    return json.loads(line)


def per_lane_op_ms(workers: list) -> dict:
    agg = {}
    for w in workers:
        for op, ms in w["ops"]:
            agg.setdefault((w["lane"], op), []).append(ms)
    return {f"{lane}:{op}": {"n": len(v),
                             "median_ms": round(statistics.median(v), 1)}
            for (lane, op), v in sorted(agg.items())}


def write_refs_dump() -> None:
    lines = []
    try:
        a = Audit()
        by_ref = a.refs()
        lines.append("== for-each-ref (all) ==")
        lines += sorted(f"{k} {v}" for k, v in by_ref.items())
        lines.append("== claim/preservation subjects ==")
        for n, sha in sorted(by_ref.items()):
            if n.startswith("refs/claims/"):
                lines.append(f"{n}: {a.subject(sha)}")
        lines.append("== verdict refs (messages) ==")
        for n in sorted(k for k in by_ref if k.startswith("refs/verdicts/")):
            body = a.srv("log", "-1", "--format=%B", by_ref[n])
            lines.append(f"{n} :: " + body.strip().replace("\n", " | "))
        lines.append("== refs/tasks commit bodies ==")
        for n in sorted(k for k in by_ref if k.startswith("refs/tasks/")):
            for s in a.srv("rev-list", n).split():
                body = a.srv("log", "-1", "--format=%B", s)
                if "Attempt: " in body:
                    lines.append(f"{n} {s} :: "
                                 + body.strip().replace("\n", " | "))
    except Exception as e:  # dump what exists, even in a discarded run
        lines.append(f"dump error: {type(e).__name__}: {e}")
    with open(os.path.join(HERE, "refs_dump_c8.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    t0 = time.perf_counter()
    R = {"probe": "c8-two-host-claim-cas",
         "freeze": "experiments/c8/FREEZE-C8.md",
         "driver_started_at": iso_now(), "discarded": False,
         # disclosure (c6/c7 discipline): runs discarded post-freeze,
         # never counted; audit plumbing only, no protocol flag evaluated
         "disclosed_discarded_runs": [
             "run1 ~08:05+02:00: AttributeError Audit.att_of_claim "
             "(helper dropped in final driver rewrite; call sites kept it) "
             "— died at post-kill snapshot; contention phase had already "
             "finished 9/9 wins, all 6 workers done, W=0.48s; victim's "
             "tagged child killed by hand after, scratch cleaned, nothing "
             "counted",
             "run2 ~08:09+02:00: AttributeError 'str'.stdout — "
             "Audit.verdicts() called .stdout on srv()'s return (already "
             "the stdout string); died at post-kill snapshot again; "
             "contention 9/9 wins, W=0.63s; same hand-cleanup. Also "
             "found+fixed pre-run3: heir.wait → hw.wait (latent), and "
             "pgrep-over-ssh self-match (remote wrapper shell carries the "
             "literal pattern → would phantom-match orphan scans and "
             "self-kill the sweep's kill leg) — bracket-pattern trick "
             "applied to sweep pgrep/kill/verify, orphans_left_example-host-a, "
             "kill_leftovers",
             "run3 ~08:14+02:00: audit for-each-ref --format=%(refname) "
             "died — ssh joins argv and the remote shell eats the parens; "
             "all earlier phases ran (contention 9/9 wins W=0.57s; "
             "victim_claim_no_verdict True; sweep killed the example-host-b orphan "
             "cross-host pids=[1066023] gone=true; sweep2 idempotent; "
             "heir done). RUN-FOUND PROTOCOL DEFECT + REPAIR (disclosed, "
             "no threshold touched): sweep requeued t4/t6/t9 — clean-"
             "phase return commits had landed but their refs/notes/"
             "verdicts annotations were silently lost: git notes is "
             "read-modify-write per tree (last writer wins), so the "
             "6-worker note burst dropped 3 of 9. Repair: verdicts moved "
             "to CAS-created refs/verdicts/<task> verdict commits (same "
             "create-once lease as claims — cannot lose an update, "
             "cannot double-land); audit/dump read verdict messages from "
             "refs. Evidence for the discarded run preserved as "
             "results_c8_run3_discarded.json; shlex.quote-join applied "
             "to every ssh-transported git command"]}
    spawned = []
    try:
        fr = sh(["git", "-C", SWARMO, "log", "-1", "--format=%H %cI",
                 "--", "experiments/c8/FREEZE-C8.md"])
        R["freeze_commit_sha"], R["freeze_committed_at"] = fr.stdout.strip().split()
        assert R["freeze_commit_sha"], "freeze file not committed"

        # ---------- preflight ----------
        R["df_example-host-b_pre"] = sh(["df", "-h", "/"]).stdout.splitlines()[-1].strip()
        R["df_example-host-a_pre"] = sh(SSH_T + ["df", "-h", "/"]).stdout.splitlines()[-1].strip()
        example-host-b_t = float(sh(["date", "+%s.%N"]).stdout)
        example-host-a_t = float(sh(SSH_T + ["date", "+%s.%N"]).stdout)
        R["clock_skew_s"] = round(example-host-a_t - example-host-b_t, 3)
        sh(SSH_T + ["rm", "-rf", "/tmp/c8"])
        sh(["rm", "-rf", "/tmp/c8"])
        sh(SSH_T + ["mkdir", "-p", "/tmp/c8"])
        os.makedirs(CM_DIR, exist_ok=True)
        r = sh(["scp", "-o", "BatchMode=yes",
                os.path.join(HERE, "c8_worker.py"), "example-host-a:/tmp/c8/"])
        assert r.returncode == 0, f"scp worker failed: {r.stderr[:200]}"
        needle = "imp" + "ort fcnt" + "l"
        srcs = (open(os.path.join(HERE, "c8_worker.py")).read()
                + open(os.path.join(HERE, "c8_driver.py")).read())
        R["no_b_machinery"] = needle not in srcs
        assert R["no_b_machinery"], "B-machinery found in sources"

        # ---------- seed ----------
        seed = "/tmp/c8/seed"
        sh(["git", "init", "-q", "-b", "main", seed])
        with open(os.path.join(seed, "base.txt"), "w") as f:
            f.write("base\n")
        sh(["git", "-C", seed, "add", "-A"])
        sh(["git", *GITID, "-C", seed, "commit", "-qm", "base"])
        sh(SSH_T + ["git", "init", "-q", "--bare", COORD_PATH])
        r = sh(["git", "-C", seed, "push", "-q", COORD_URL, "main"], env=ENV)
        assert r.returncode == 0, f"seed push failed: {r.stderr[:300]}"

        # ---------- phase C: contention, 3 example-host-b + 3 example-host-a, 9 tasks ------
        for i in (1, 2, 3):
            spawned.append(subprocess.Popen(
                [sys.executable, "-u", os.path.join(HERE, "c8_worker.py"),
                 "worker", COORD_URL, str(i), CM_DIR],
                stdout=subprocess.PIPE, text=True, env=ENV))
        for i in (1, 2, 3):
            spawned.append(subprocess.Popen(
                SSH_T + ["python3", "-u", "/tmp/c8/c8_worker.py",
                         "worker", COORD_PATH, str(i)],
                stdout=subprocess.PIPE, text=True))
        spawn_wall = time.perf_counter()
        workers = [json_line(p, f"worker{i}", 120)
                   for i, p in enumerate(spawned)]
        W_contention = time.perf_counter() - spawn_wall
        for p in spawned:
            p.wait(timeout=30)
        R["workers"] = [{k: w[k] for k in ("event", "host", "lane", "idx",
                                           "losses", "startup_ms")}
                        for w in workers]
        R["wins_detail"] = [w["wins"] for w in workers]
        R["W_contention_s"] = round(W_contention, 2)
        assert all(w["event"] == "done" for w in workers), "a worker got stuck"
        wins_sum = sum(len(w["wins"]) for w in workers)
        all_won = sorted((win["task"], win["att"])
                         for w in workers for win in w["wins"])
        R["wins_sum"] = wins_sum
        assert wins_sum == 9, f"wins_sum {wins_sum} != 9"
        qcheck = sh(["git", "ls-remote", COORD_URL,
                     "refs/claims/t*", "refs/tasks/t*"], env=ENV)
        n_returns = sum(1 for ln in qcheck.stdout.splitlines()
                        if "\trefs/tasks/" in ln)
        assert n_returns == 9, f"server shows {n_returns}/9 returns"

        # ---------- phase X: crash across hosts ----------
        victim = subprocess.Popen(
            [sys.executable, "-u", os.path.join(HERE, "c8_worker.py"),
             "victim", COORD_URL, CRASH, CM_DIR],
            stdout=subprocess.PIPE, text=True, env=ENV)
        spawned.append(victim)
        vline = json_line(victim, "victim", 20)
        assert vline["event"] == "claimed", f"victim {vline}"
        victim_att = vline["att"]
        att_seen, tag_seen = None, False
        deadline = time.perf_counter() + 15
        while time.perf_counter() < deadline and not att_seen:
            for pid in scan_tagged_local(f"c8-{CRASH}-"):
                tag_seen = True
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        for a in f.read().split(b"\0"):
                            if a.startswith(f"c8-{CRASH}-".encode()):
                                att_seen = a.decode()[len(f"c8-{CRASH}-"):]
                except OSError:
                    pass
            time.sleep(0.1)
        assert att_seen == victim_att, (
            f"victim never dispatched (tag_seen={tag_seen}, att={att_seen})")
        R["victim_killed_at"] = iso_now()
        victim.kill()
        victim.wait()

        # snapshot: cross-host git reads ONLY
        a = Audit()
        claim_val = a.srv("rev-parse", "--verify", "--quiet",
                          f"refs/claims/{CRASH}").strip()
        snapshot = {
            "claim_value_present": bool(claim_val),
            "claim_att": a.att_of_claim(claim_val) if claim_val else None,
            "return_ref_absent": not a.srv(
                "rev-parse", "--verify", "--quiet",
                f"refs/tasks/{CRASH}").strip(),
            "verdict_ref_absent": not a.srv(
                "rev-parse", "--verify", "--quiet",
                f"refs/verdicts/{CRASH}").strip(),
        }
        R["post_kill_snapshot"] = snapshot
        R["snapshot_att_match"] = snapshot["claim_att"] == victim_att
        victim_claim_no_verdict = all([
            snapshot["claim_value_present"], snapshot["return_ref_absent"],
            snapshot["verdict_ref_absent"], R["snapshot_att_match"]])
        R["victim_claim_no_verdict"] = victim_claim_no_verdict

        # ---------- sweep x2, run ON example-host-a ----------
        sweep_cmd = SSH_T + ["python3", "-u", "/tmp/c8/c8_worker.py",
                             "sweep", COORD_PATH, "you@example-host-b"]
        R["sweep_spawn_cmd"] = " ".join(sweep_cmd)
        sw1 = subprocess.Popen(sweep_cmd, stdout=subprocess.PIPE, text=True)
        spawned.append(sw1)
        s1 = json_line(sw1, "sweep1", 60)
        sw1.wait(timeout=20)
        sw2 = subprocess.Popen(sweep_cmd, stdout=subprocess.PIPE, text=True)
        spawned.append(sw2)
        s2 = json_line(sw2, "sweep2", 60)
        sw2.wait(timeout=20)
        R["sweep1"], R["sweep2"] = s1, s2
        orphan_killed = any(k["pids"] and k["gone"]
                            for k in s1["killed_example-host-b"].values())

        # ---------- heir on example-host-a ----------
        hw = subprocess.Popen(
            SSH_T + ["python3", "-u", "/tmp/c8/c8_worker.py",
                     "heir", COORD_PATH, CRASH],
            stdout=subprocess.PIPE, text=True)
        spawned.append(hw)
        heir = json_line(hw, "heir", 60)
        hw.wait(timeout=20)
        heir_att = heir["att"]
        R["heir"] = {k: heir.get(k) for k in ("event", "att", "task",
                                              "return_pushed",
                                              "verdict_pushed",
                                              "verdict_retries")}
        assert heir["event"] == "done", f"heir failed: {heir}"

        # ---------- audit (git reads only, via ssh from example-host-b) ---------
        by_ref = a.refs()
        live_claims = {n[len("refs/claims/"):]: sha
                       for n, sha in by_ref.items()
                       if n.startswith("refs/claims/") and "@" not in n}
        preserved = {n[len("refs/claims/"):]: sha
                     for n, sha in by_ref.items()
                     if n.startswith("refs/claims/") and "@" in n}
        tasks = {n[len("refs/tasks/"):]: sha for n, sha in by_ref.items()
                 if n.startswith("refs/tasks/")}
        verdicts = a.verdicts()
        verdict_count = {t: 1 for t, d in verdicts.items()
                         if d.get("fixed") == "true"}
        verdict_att = {t: d.get("attempt") for t, d in verdicts.items()}
        claim_atts = {t: a.att_of_claim(sha)
                      for t, sha in live_claims.items()}
        trailer = {t: a.returns_with_trailer(t) for t in tasks}

        R["h1"] = {
            "clean_claims_9": (
                all(t in live_claims for t in CLEAN)
                and set(live_claims) == set(CLEAN) | {CRASH}),
            "no_preserved_in_clean":
                not [p for p in preserved if p.split("@")[0] in CLEAN],
            "return_once_and_match": all(
                len(trailer.get(t, [])) == 1 and trailer[t][0] == claim_atts[t]
                for t in CLEAN),
            "verdicts_one_per_task": all(
                verdict_count.get(t, 0) == 1 for t in CLEAN + [CRASH]),
            "verdict_att_matches_claim": all(
                verdict_att.get(t) == claim_atts[t] for t in CLEAN),
            "wins_sum_9_and_unique":
                wins_sum == 9 and len({x[1] for x in all_won}) == 9
                and all(claim_atts.get(t) == att for t, att in all_won),
        }
        gt = {t: sorted(att for tt, att in all_won if tt == t) for t in CLEAN}
        gt[CRASH] = sorted([victim_att, heir_att])
        recon = {}
        for t in CLEAN + [CRASH]:
            atts = {claim_atts[t]} if t in claim_atts else set()
            for p, sha in preserved.items():
                if p.split("@")[0] == t:
                    atts.add(a.att_of_claim(sha))
            atts.update(trailer.get(t, []))
            recon[t] = {"attempts": sorted(atts),
                        "verdict_count": verdict_count.get(t, 0)}
        R["reconstruction"] = recon
        R["ground_truth"] = gt
        attempts_countable = all(
            recon[t]["attempts"] == gt[t]
            and recon[t]["verdict_count"] == 1
            for t in CLEAN + [CRASH])
        dead_preserved = (
            preserved.get(f"{CRASH}@{victim_att}") is not None
            and a.att_of_claim(preserved[f"{CRASH}@{victim_att}"]) == victim_att)
        R["h2"] = {
            "victim_claim_no_verdict": victim_claim_no_verdict,
            "sweep_ran_on_example-host-a": True,
            "sweep_requeued": s1["requeued"],
            "sweep_requeued_exact":
                s1["requeued"] == [f"{CRASH}.{victim_att}"],
            "orphan_killed_from_example-host-a": orphan_killed,
            "sweep_idempotent_second_run": s2["requeued"] == [],
            "heir_fresh_claim": heir["event"] == "done",
            "heir_fixed": (heir["event"] == "done"
                           and trailer.get(CRASH) == [heir_att]
                           and verdict_att.get(CRASH) == heir_att),
            "exactly_one_final_verdict": all(
                verdict_count.get(t, 0) == 1 for t in CLEAN + [CRASH]),
            "dead_attempt_preserved": dead_preserved,
            "attempts_countable_from_repo": attempts_countable,
            "orphans_left_example-host-b": scan_tagged_local("c8-"),
            "orphans_left_example-host-a":
                sh(SSH_T + ["pgrep", "-f", "c8[_]"]).stdout.split(),
        }
        R["h1_pass"] = all(R["h1"].values())
        R["h2_pass"] = all([
            R["h2"]["victim_claim_no_verdict"],
            R["h2"]["sweep_requeued_exact"],
            R["h2"]["orphan_killed_from_example-host-a"],
            R["h2"]["sweep_idempotent_second_run"],
            R["h2"]["heir_fresh_claim"], R["h2"]["heir_fixed"],
            R["h2"]["exactly_one_final_verdict"],
            R["h2"]["dead_attempt_preserved"],
            R["h2"]["attempts_countable_from_repo"],
            not R["h2"]["orphans_left_example-host-b"],
            not R["h2"]["orphans_left_example-host-a"]])

        baseline_gen_s = 0.6 / 4  # c7 whole-flow wall / 4 won generations
        per_gen = W_contention / 9
        ratio = per_gen / baseline_gen_s
        R["h3"] = {
            "c7_baseline_wall_s": 0.6, "c7_baseline_gen_s": baseline_gen_s,
            "W_contention_s": R["W_contention_s"],
            "per_generation_s": round(per_gen, 3),
            "ratio_vs_c7": round(ratio, 2),
            "label": "<10x" if ratio < 10 else ">=10x",
            "per_lane_op_ms": per_lane_op_ms(workers),
        }
        R["verdict"] = ("c-proven-cross-host"
                        if R["h1_pass"] and R["h2_pass"] else
                        "H1FAIL-c-killed-cross-host"
                        if not R["h1_pass"] else
                        "crash-revive-dented-cross-host")
    except Exception as e:
        import traceback
        R["discarded"] = True
        R["discard_reason"] = (f"{type(e).__name__}: {e}\n"
                               + traceback.format_exc())
        R["verdict"] = "DISCARDED-NOT-COUNTED"
        kill_leftovers()
    finally:
        for p in spawned:
            try:
                if p.poll() is None:
                    p.kill()
            except OSError:
                pass
        R["wall_s"] = round(time.perf_counter() - t0, 1)
        R["driver_finished_at"] = iso_now()
        write_refs_dump()
        with open(os.path.join(HERE, "results_c8.json"), "w") as f:
            json.dump(R, f, indent=1)
        print(json.dumps({k: R.get(k) for k in (
            "verdict", "h1_pass", "h2_pass", "discarded", "discard_reason",
            "h3", "W_contention_s")}, default=str))


if __name__ == "__main__":
    main()
