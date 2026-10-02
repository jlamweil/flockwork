"""Contract for the flockwork metrics layer (WQ-024, INT-085a).

We cannot judge what we cannot measure: the pilots (next queue entry)
will be judged with this layer, so its own contract is frozen first.
The layer OBSERVES, never alters wire behavior:

  - env-switched: FLOCKWORK_METRICS=1 enables; OFF (default) means no
    file is ever created and hook sites cost one env lookup — zero
    cost, zero behavior change.
  - JSONL per operation: one JSON object per line carrying `ts`, the
    `event` kind, and the operation's own fields. Event vocabulary:
    claim (attempt + CAS outcome won/race/structural), verdict
    (outcome + push result), heir (crash archived, heir requeued),
    crash (work_task_error, claim freed), eviction (sweep),
    lease_expired (reconcile TTL recovery).
  - a summary (python3 -m l2.metrics [files...]): counts, claim reject
    rate, claim->verdict latency percentiles (joined on att), verdict
    outcomes. A missing metrics file is an honest distinct exit (rc 2)
    — 'no data recorded' and 'cannot read the data' must not look
    alike.
  - observe-only: a metrics failure (unwritable sink) never propagates
    into a lane operation, and a metrics-on run lands the same refs as
    a metrics-off run.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _metrics():
    p = REPO / "l2" / "metrics.py"
    spec = importlib.util.spec_from_file_location("flockwork_metrics_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_inrepo():
    p = REPO / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def _scratch_worker_repo(tmp_path):
    """A repo cwd for the object store: commit-tree/push run in the
    process cwd, so the claim/seed legs need one (the house lesson from
    test_claim_loss: push from a non-repo cwd is rc-128 structural)."""
    w = tmp_path / "node"
    _git("init", "-q", "-b", "main", str(w))
    (w / ".seed").write_text("x\n")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-qm", "s")
    return w


def _read_jsonl(path):
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


# ------------------------------------------------------------ the switch


def test_off_by_default_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("FLOCKWORK_METRICS", raising=False)
    monkeypatch.delenv("FLOCKWORK_METRICS_FILE", raising=False)
    m = _metrics()
    f = tmp_path / "m.jsonl"
    m.emit("claim", task="T", worker="w1")
    assert not f.exists()  # nothing recorded, nothing created
    # even pointed at a path explicitly, off means off
    monkeypatch.setenv("FLOCKWORK_METRICS_FILE", str(f))
    m.emit("verdict", task="T")
    assert not f.exists()


def test_env_switch_appends_jsonl(monkeypatch, tmp_path):
    f = tmp_path / "m.jsonl"
    monkeypatch.setenv("FLOCKWORK_METRICS", "1")
    monkeypatch.setenv("FLOCKWORK_METRICS_FILE", str(f))
    m = _metrics()
    m.emit("claim", task="T", worker="w1", att="att-w1-aaaa01",
           outcome="won", rc=0)
    m.emit("verdict", task="T", worker="w1", att="att-w1-aaaa01",
           fixed=True)
    evs = _read_jsonl(f)  # appended, not truncated
    assert len(evs) == 2
    assert evs[0]["event"] == "claim"
    assert evs[0]["att"] == "att-w1-aaaa01"
    assert evs[1]["fixed"] is True
    # every line carries an epoch ts — the latency join depends on it
    assert isinstance(evs[0]["ts"], float) and evs[0]["ts"] > 0
    assert evs[1]["ts"] >= evs[0]["ts"]


def test_observe_only_unwritable_sink_never_propagates(monkeypatch, tmp_path):
    """A broken metrics sink must never break a lane operation: the
    layer watches, it does not participate (the acceptance bar's 'zero
    wire changes' holds even when the instrumentation itself fails)."""
    monkeypatch.setenv("FLOCKWORK_METRICS", "1")
    monkeypatch.setenv("FLOCKWORK_METRICS_FILE",
                       str(tmp_path / "no-such-dir" / "m.jsonl"))
    m = _metrics()
    m.emit("claim", task="T")  # must not raise
    # and the lane keeps working with the sink broken:
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    w = _scratch_worker_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.chdir(w)
    inrepo = _load_inrepo()
    d = inrepo.claim_detail("w1", "T")
    assert d["att"] and d["att"].startswith("att-w1-")
    assert "refs/swarm/claims/T" in _git("ls-remote", str(o)).stdout


# ------------------------------------------------- claim metrics + wire


def test_claim_won_and_cas_rejection_recorded(monkeypatch, tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    w = _scratch_worker_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.setenv("FLOCKWORK_METRICS", "1")
    monkeypatch.setenv("FLOCKWORK_METRICS_FILE", str(tmp_path / "m.jsonl"))
    monkeypatch.chdir(w)
    inrepo = _load_inrepo()
    d1 = inrepo.claim_detail("w1", "T")
    d2 = inrepo.claim_detail("w2", "T")  # the deliberate duplicate
    assert d1["att"] and d2["att"] is None
    evs = [e for e in _read_jsonl(tmp_path / "m.jsonl") if e["event"] == "claim"]
    assert len(evs) == 2  # both the attempt and the rejection are data
    assert evs[0]["outcome"] == "won" and evs[0]["att"] == d1["att"]
    assert evs[1]["outcome"] == "race"  # the CAS duplicate-rejection
    assert evs[1]["att"] is None        # a loser records no att
    # wire unchanged: exactly one claim ref on the board
    refs = _git("ls-remote", str(o), "refs/swarm/claims/*").stdout.splitlines()
    assert len(refs) == 1 and refs[0].split()[1] == "refs/swarm/claims/T"


def test_wire_identical_metrics_on_vs_off(monkeypatch, tmp_path):
    """The same lane operation with the layer on and off must land the
    same refs and the same return shape — instrumentation is invisible
    on the wire."""
    boards = {}
    for tag, on in (("off", "0"), ("on", "1")):
        o = tmp_path / f"origin-{tag}.git"
        _git("init", "-q", "--bare", str(o))
        w = _scratch_worker_repo(tmp_path / tag)
        monkeypatch.setenv("SWARM_ORIGIN", str(o))
        monkeypatch.setenv("FLOCKWORK_METRICS", on)
        monkeypatch.chdir(w)
        inrepo = _load_inrepo()
        d = inrepo.claim_detail("w1", "T")
        boards[tag] = (
            sorted(d.keys()), d["rc"],
            sorted(ln.split()[1] for ln in
                   _git("ls-remote", str(o), "refs/swarm/*").stdout.splitlines()
                   if ln.strip()),
        )
    assert boards["off"][0] == boards["on"][0]      # same return shape
    assert boards["off"][1] == boards["on"][1]      # same rc
    assert boards["off"][2] == boards["on"][2]      # same refnames on the
    # wire (shas differ by design: the att token is per-attempt random)


# ------------------------------------------------------- the summary


def _synthetic_events():
    return [
        {"ts": 1000.0, "event": "claim", "task": "T1", "worker": "w1",
         "att": "att-w1-a1", "outcome": "won", "rc": 0},
        {"ts": 1001.0, "event": "claim", "task": "T1", "worker": "w2",
         "att": None, "outcome": "race", "rc": 1},
        {"ts": 1002.0, "event": "claim", "task": "T2", "worker": "w1",
         "att": "att-w1-b2", "outcome": "won", "rc": 0},
        {"ts": 1003.0, "event": "claim", "task": "T3", "worker": "w3",
         "att": None, "outcome": "structural", "rc": 128},
        {"ts": 1060.0, "event": "verdict", "task": "T1", "worker": "w1",
         "att": "att-w1-a1", "fixed": True, "oc_rc": 0, "pytest_rc": 0,
         "verdict_pushed": True},
        {"ts": 1122.0, "event": "verdict", "task": "T2", "worker": "w1",
         "att": "att-w1-b2", "fixed": False, "oc_rc": 124, "pytest_rc": 1,
         "verdict_pushed": True, "heir_exhausted": True},
        {"ts": 1061.0, "event": "heir", "task": "T2", "worker": "w9",
         "att": "att-w9-x0", "requeued": True},
        {"ts": 1062.0, "event": "crash", "task": "T4", "worker": "w9",
         "att": "att-w9-c4", "claim_freed": True},
        {"ts": 1063.0, "event": "eviction", "task": "T2", "att": "att-w9-x0",
         "archived": 1, "deleted": 1},
        {"ts": 1064.0, "event": "lease_expired", "task": "T2",
         "age_s": 1801.0, "swept": "att-w9-x0"},
    ]


def test_summary_counts_reject_rate_and_latency():
    m = _metrics()
    s = m.summarize(_synthetic_events())
    assert s["events"] == 10 and s["malformed"] == 0
    assert s["counts"] == {"seed": 0, "claim": 4, "verdict": 2, "heir": 1,
                           "crash": 1, "eviction": 1, "lease_expired": 1}
    assert s["claims"]["attempts"] == 4
    assert s["claims"]["won"] == 2
    assert s["claims"]["race_rejected"] == 1
    assert s["claims"]["structural"] == 1
    assert s["claims"]["reject_rate"] == pytest.approx(0.5)
    # claim->verdict latencies joined on att: 60s and 120s
    assert s["latency_s"]["n"] == 2
    assert s["latency_s"]["p50"] == pytest.approx(90.0)
    assert s["latency_s"]["p90"] == pytest.approx(114.0)
    assert s["latency_s"]["max"] == pytest.approx(120.0)
    assert s["verdicts"]["total"] == 2
    assert s["verdicts"]["fixed_true"] == 1
    assert s["verdicts"]["fixed_false"] == 1
    assert s["verdicts"]["push_failures"] == 0
    assert s["heirs"] == {"requeues": 1, "exhausted": 1}


def test_summary_of_empty_stream_is_a_zero_board():
    m = _metrics()
    s = m.summarize([])
    assert s["events"] == 0
    assert s["claims"]["attempts"] == 0
    assert s["claims"]["reject_rate"] == 0.0
    assert s["latency_s"]["n"] == 0


def test_summary_cli(tmp_path, monkeypatch):
    f = tmp_path / "m.jsonl"
    monkeypatch.setenv("FLOCKWORK_METRICS", "1")
    monkeypatch.setenv("FLOCKWORK_METRICS_FILE", str(f))
    m = _metrics()
    m.emit("claim", task="T", worker="w1", att="att-w1-aaaa01",
           outcome="won", rc=0)
    m.emit("verdict", task="T", worker="w1", att="att-w1-aaaa01",
           fixed=True, oc_rc=0, pytest_rc=0, verdict_pushed=True)
    r = subprocess.run(
        [sys.executable, "-m", "l2.metrics", str(f)],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    assert s["claims"]["attempts"] == 1 and s["claims"]["won"] == 1
    assert s["latency_s"]["n"] == 1
    # an empty file is a zero board, exit 0; a MISSING file is a
    # different honest state — rc 2, never an empty-looking summary
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    r2 = subprocess.run(
        [sys.executable, "-m", "l2.metrics", str(empty)],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert r2.returncode == 0 and json.loads(r2.stdout)["events"] == 0
    r3 = subprocess.run(
        [sys.executable, "-m", "l2.metrics", str(tmp_path / "absent.jsonl")],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert r3.returncode == 2 and r3.stdout.strip() == ""
    assert "absent" in r3.stderr


# -------------------------------------- lease expiry + eviction events


def test_lease_expiry_and_eviction_recorded(monkeypatch, tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    w = _scratch_worker_repo(tmp_path)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.setenv("FLOCKWORK_METRICS", "1")
    monkeypatch.setenv("FLOCKWORK_METRICS_FILE", str(tmp_path / "m.jsonl"))
    monkeypatch.chdir(w)
    inrepo = _load_inrepo()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"T-lease": "body\n\nverify: true"}))
    assert inrepo.seed(str(spec))["T-lease"] is True
    d = inrepo.claim_detail("wl", "T-lease")
    assert d["att"]
    res = inrepo.reconcile(str(o), 0.0)  # ttl 0: the lease is stale at once
    assert res["stale"] and res["swept"] == [d["att"]]
    evs = _read_jsonl(tmp_path / "m.jsonl")
    lease = [e for e in evs if e["event"] == "lease_expired"]
    evict = [e for e in evs if e["event"] == "eviction"]
    assert len(lease) == 1 and lease[0]["task"] == "T-lease"
    assert lease[0]["swept"] == d["att"] and lease[0]["age_s"] >= 0
    assert len(evict) == 1 and evict[0]["task"] == "T-lease"
    assert evict[0]["att"] == d["att"]
    # wire behavior intact: the sweep's archive+free transaction happened
    refs = _git("ls-remote", str(o), "refs/swarm/*").stdout.split()
    assert "refs/swarm/claims/T-lease" not in refs
    assert any(r.startswith("refs/swarm/archive/claims/T-lease@")
               for r in refs)


# ------------------------------------------------- the DORA five (WQ-043)
#
# The mapping of record is the program's ops/DORA-METRICS-MAP.md layer 1:
# deployment frequency = landed verdicts per day; lead time = seed ->
# verdict (NOT just claim -> verdict); CFR = merit-fail verdict ratio;
# recovery = crash/lease-expiry -> heir re-claim -> verdict span; rework
# = requeues + duplicate-claim races + verdicts re-done. --dora is the
# same observer: it adds a section to the summary, it changes nothing.

def _dora_events():
    """Two UTC days; every DORA shape at least once. Day boundary at
    epoch 86400 (1970-01-02 UTC) keeps the bucketing test date-obvious."""
    return [
        # T1: seeded, won, landed green on day 1 — a deployment
        {"ts": 10.0, "event": "seed", "task": "T1", "ok": True},
        {"ts": 20.0, "event": "claim", "task": "T1", "worker": "w1",
         "att": "att-w1-a1", "outcome": "won", "rc": 0},
        {"ts": 100.0, "event": "verdict", "task": "T1", "worker": "w1",
         "att": "att-w1-a1", "fixed": True, "verdict_pushed": True},
        # T2: seeded, raced claim (rework), then won; merit-FAIL verdict
        # day 1 (a change failure, not a deployment), heir requeued it
        {"ts": 12.0, "event": "seed", "task": "T2", "ok": True},
        {"ts": 21.0, "event": "claim", "task": "T2", "worker": "w2",
         "att": None, "outcome": "race", "rc": 1},
        {"ts": 22.0, "event": "claim", "task": "T2", "worker": "w1",
         "att": "att-w1-b2", "outcome": "won", "rc": 0},
        {"ts": 500.0, "event": "verdict", "task": "T2", "worker": "w1",
         "att": "att-w1-b2", "fixed": False, "verdict_pushed": True},
        {"ts": 501.0, "event": "heir", "task": "T2", "worker": "w1",
         "att": "att-w1-b2", "requeued": True},
        # T3: never seeded, crash -> re-claim -> green verdict day 2
        # (the recovery span), and a SECOND verdict on the task (re-done)
        {"ts": 90000.0, "event": "crash", "task": "T3", "worker": "w3",
         "att": "att-w3-c3", "claim_freed": True},
        {"ts": 90010.0, "event": "claim", "task": "T3", "worker": "w4",
         "att": "att-w4-d4", "outcome": "won", "rc": 0},
        {"ts": 90100.0, "event": "verdict", "task": "T3", "worker": "w4",
         "att": "att-w4-d4", "fixed": True, "verdict_pushed": True},
        {"ts": 95000.0, "event": "verdict", "task": "T3", "worker": "w4",
         "att": "att-w4-d4", "fixed": True, "verdict_pushed": True},
        # T4: lease-expiry failure origin, never recovered (no later
        # claim) — a span that must NOT be counted
        {"ts": 91000.0, "event": "lease_expired", "task": "T4",
         "age_s": 1801.0, "swept": "att-x"},
        # T5: fixed but never pushed — landed NOWHERE (not a deployment)
        {"ts": 92000.0, "event": "verdict", "task": "T5", "worker": "w5",
         "att": "att-w5-e5", "fixed": True, "verdict_pushed": False},
    ]


def test_dora_deployment_frequency_buckets_landed_verdicts_per_day():
    m = _metrics()
    d = m.dora(_dora_events())
    df = d["deployment_frequency"]
    assert df["landed_total"] == 3  # T1 + both T3 verdicts; T5 unpushed NO
    assert df["per_day"] == {"1970-01-01": 1, "1970-01-02": 2}
    assert df["days_active"] == 2
    assert df["mean_per_active_day"] == pytest.approx(1.5)


def test_dora_lead_time_joins_seed_to_verdict_per_task():
    m = _metrics()
    d = m.dora(_dora_events())
    lt = d["lead_time_s"]
    # joined: T1 (100-10=90) and T2 (500-12=488); T3/T5 never seeded —
    # 'no seed recorded' must not silently become a measured lead time
    assert lt["n"] == 2
    assert lt["p50"] == pytest.approx(289.0)
    assert lt["max"] == pytest.approx(488.0)


def test_dora_change_failure_rate_is_merit_fail_ratio():
    m = _metrics()
    d = m.dora(_dora_events())
    cfr = d["change_failure_rate"]
    assert cfr["merit_fails"] == 1 and cfr["total"] == 5
    assert cfr["rate"] == pytest.approx(0.2)


def test_dora_recovery_spans_crash_to_verdict():
    m = _metrics()
    d = m.dora(_dora_events())
    rec = d["recovery_s"]
    # T3 only: crash 90000 -> verdict 90100 = 100s; the lease-expiry on
    # T4 never re-claimed, so it is an unrecovered failure, not a span
    assert rec["failures"] == 2
    assert rec["n"] == 1
    assert rec["p50"] == pytest.approx(100.0)
    assert rec["max"] == pytest.approx(100.0)


def test_dora_rework_counts_races_requeues_and_redone_verdicts():
    m = _metrics()
    d = m.dora(_dora_events())
    rw = d["rework_rate"]
    assert rw["race_rejections"] == 1
    assert rw["requeues"] == 1
    assert rw["redone_verdicts"] == 1  # T3's second verdict
    assert rw["events"] == 3
    # denominator: claim attempts (4 = 3 won + 1 race)
    assert rw["attempts"] == 4
    assert rw["rate"] == pytest.approx(0.75)


def test_dora_empty_board_is_honest_zeros_and_nones():
    m = _metrics()
    d = m.dora([])
    assert d["deployment_frequency"]["landed_total"] == 0
    assert d["deployment_frequency"]["per_day"] == {}
    assert d["deployment_frequency"]["mean_per_active_day"] is None
    assert d["lead_time_s"] == {"n": 0, "p50": None, "p90": None, "max": None}
    assert d["change_failure_rate"]["rate"] is None
    assert d["recovery_s"]["n"] == 0 and d["recovery_s"]["p50"] is None
    assert d["recovery_s"]["failures"] == 0
    assert d["rework_rate"]["rate"] is None and d["rework_rate"]["events"] == 0


def test_dora_cli_flag_adds_section_default_omits_it(tmp_path):
    f = tmp_path / "m.jsonl"
    f.write_text(json.dumps(_synthetic_events()[0]) + "\n")
    base = subprocess.run(
        [sys.executable, "-m", "l2.metrics", str(f)],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert base.returncode == 0 and "dora" not in json.loads(base.stdout)
    dor = subprocess.run(
        [sys.executable, "-m", "l2.metrics", "--dora", str(f)],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert dor.returncode == 0, dor.stderr
    s = json.loads(dor.stdout)
    assert "dora" in s
    # the base sections are byte-stable under the flag: one observer,
    # not a second summary
    for k in json.loads(base.stdout):
        assert s[k] == json.loads(base.stdout)[k]


def test_seed_event_recorded_and_wire_identical(monkeypatch, tmp_path):
    """seed() joins the layer: per-task seed events (ok carries the push
    result) under the switch, nothing without it, same refs either way —
    the DORA lead-time join needs the seed anchor (map: 'extend the join
    to the seed event')."""
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    w = _scratch_worker_repo(tmp_path)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"T-seed": "b\n\nverify: true",
                                "T-bad@name": "b"}))
    seen = {}
    for tag, on in (("off", "0"), ("on", "1")):
        o2 = tmp_path / f"origin-{tag}.git"
        _git("init", "-q", "--bare", str(o2))
        monkeypatch.setenv("SWARM_ORIGIN", str(o2))
        monkeypatch.setenv("FLOCKWORK_METRICS", on)
        monkeypatch.setenv("FLOCKWORK_METRICS_FILE", str(tmp_path / f"m-{tag}.jsonl"))
        monkeypatch.chdir(w)
        inrepo = _load_inrepo()
        seen[tag] = inrepo.seed(str(spec))
    assert seen["off"] == seen["on"]  # same results shape
    assert seen["on"]["T-seed"] is True and "refused" in seen["on"]["T-bad@name"]
    refs_off = _git("ls-remote", str(tmp_path / "origin-off.git"), "refs/swarm/*").stdout.split()
    refs_on = _git("ls-remote", str(tmp_path / "origin-on.git"), "refs/swarm/*").stdout.split()
    assert sorted(r for r in refs_off) == sorted(refs_on)
    evs = _read_jsonl(tmp_path / "m-on.jsonl")
    seeds = [e for e in evs if e["event"] == "seed"]
    assert len(seeds) == 2  # one per task, refused ones included
    by_task = {e["task"]: e for e in seeds}
    assert by_task["T-seed"]["ok"] is True
    assert by_task["T-bad@name"]["ok"] is False
    assert all(isinstance(e["ts"], float) for e in seeds)
    assert not (tmp_path / "m-off.jsonl").exists()  # off means off


def test_dora_seed_events_counted_in_summary_counts():
    m = _metrics()
    s = m.summarize([{"ts": 1.0, "event": "seed", "task": "T", "ok": True}])
    assert s["counts"]["seed"] == 1


# ------------------------------------------- e2e: the pilot-shaped runs


def _shim(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)
    return p


def _run_worker(tmp_path, monkeypatch, shim_body):
    """A full worker() run on a scratch board (empty bare origin: the
    law gate reads 'absent' and passes through — no divergence trap),
    with the deterministic dispatch shim. Returns (rc, stdout events)."""
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    w = _scratch_worker_repo(tmp_path)
    shim = _shim(tmp_path, "dispatch.sh", shim_body)
    monkeypatch.setenv("SWARM_ORIGIN", str(o))
    monkeypatch.setenv("FLOCKWORK_METRICS", "1")
    monkeypatch.setenv("FLOCKWORK_METRICS_FILE", str(tmp_path / "m.jsonl"))
    monkeypatch.setenv("OPENCODE_BIN", str(shim))
    monkeypatch.setenv("SWARM_WORKER_MAX_FAILS", "3")
    monkeypatch.chdir(w)
    inrepo = _load_inrepo()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(
        {"hello-demo": "demo task\n\nadd hello.txt saying hi\n\n"
                       "verify: grep -qx hi hello.txt"}))
    assert inrepo.seed(str(spec))["hello-demo"] is True
    r = subprocess.run(
        [sys.executable, str(REPO / "l2" / "inrepo.py"), "worker", "wm"],
        cwd=str(w),
        env=dict(os.environ),  # monkeypatched env travels to the child
        capture_output=True, text=True, timeout=120)
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    return r.returncode, events


def test_e2e_success_run_yields_joinable_claim_and_verdict(
        tmp_path, monkeypatch):
    rc, events = _run_worker(
        tmp_path, monkeypatch, "echo hi > hello.txt; exit 0")
    assert rc == 0
    assert events[-1]["event"] == "worker_done"
    evs = _read_jsonl(tmp_path / "m.jsonl")
    claims = [e for e in evs if e["event"] == "claim"]
    verdicts = [e for e in evs if e["event"] == "verdict"]
    assert len(claims) == 1 and claims[0]["outcome"] == "won"
    assert len(verdicts) == 1 and verdicts[0]["fixed"] is True
    assert verdicts[0]["verdict_pushed"] is True
    # the layer's whole point: claim->verdict latency is joinable on att
    assert verdicts[0]["att"] == claims[0]["att"]
    s = _metrics().summarize(evs)
    assert s["latency_s"]["n"] == 1 and s["latency_s"]["max"] >= 0
    assert s["verdicts"]["fixed_true"] == 1


def test_e2e_env_death_yields_heir_then_exhausted_verdict(
        tmp_path, monkeypatch):
    """A dispatch that dies leaving no work is an environmental death:
    first death requeues (the heir law), second spends the budget and
    writes the final fixed:false verdict. The metrics must show the
    crash->revive shape the pilots will be judging."""
    rc, events = _run_worker(tmp_path, monkeypatch, "exit 1")
    assert rc == 0
    attempted = [e for e in events if e.get("event") == "attempted"]
    assert len(attempted) == 2
    assert attempted[0]["env_death"] is True and attempted[0]["requeued"] is True
    assert attempted[1]["heir_exhausted"] is True
    evs = _read_jsonl(tmp_path / "m.jsonl")
    claims = [e for e in evs if e["event"] == "claim"]
    heirs = [e for e in evs if e["event"] == "heir"]
    verdicts = [e for e in evs if e["event"] == "verdict"]
    assert len(claims) == 2 and all(c["outcome"] == "won" for c in claims)
    assert len(heirs) == 1 and heirs[0]["requeued"] is True
    assert heirs[0]["att"] == attempted[0]["att"]  # the crash that requeued
    assert len(verdicts) == 1 and verdicts[0]["fixed"] is False
    assert verdicts[0]["att"] == attempted[1]["att"]  # the heir's verdict
    s = _metrics().summarize(evs)
    assert s["heirs"] == {"requeues": 1, "exhausted": 1}
    assert s["verdicts"]["fixed_false"] == 1
    assert s["latency_s"]["n"] == 1  # the heir's claim->verdict pair
