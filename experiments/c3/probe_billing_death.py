#!/usr/bin/env python3
"""Failure-class probe: billing/model death through the worker leg.

E9 (c0): glm-5.3-flash died in 2.0s with 'Insufficient balance'. Run the
same model class through tools/worker_loop on a fresh sumto fixture:
expected = fast container death, fixed=false, ledger rows still cleanly
appended (claim + verdict), i.e. the error table's REQUEUE+session-exit
row has a live measured instance.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
sys.path.insert(0, os.path.join(HERE, "..", "c2"))
import worker_loop  # noqa: E402
from c2_driver import BUGS, IMAGE, OPENCODE, AUTH, make_fixture  # noqa: E402

LEDGER = os.path.join(HERE, "results_billing_probe_ledger.jsonl")

ws = os.path.join(HERE, "ws-billing-sumto")
_, target = make_fixture("sumto", ws=ws)
row = worker_loop.run_case(
    task="sumto", ws=ws, ledger_path=LEDGER, image=IMAGE,
    model="github-copilot/glm-5.3-flash",
    brief=(f"Fix the bug in {target} so that `python3 -m pytest` passes. "
           f"Do not modify the test file. Work only inside /work."),
    opencode_bin=OPENCODE, auth_json=AUTH, timeout_s=240)
summary = {k: row.get(k) for k in
           ("task", "attemptId", "container_exit", "wall_s", "commit_sha",
            "patch_bytes", "host_passed", "host_failed", "fixed")}
with open(os.path.join(HERE, "results_billing_probe.json"), "w") as f:
    json.dump(summary, f, indent=1)
print(json.dumps(summary))
