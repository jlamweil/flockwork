#!/usr/bin/env python3
"""L2 — the dispatch-backend seam (DESIGN-NEXT §3, post-c9 build item).

The one marginal build Design B ever required: a DispatchBackend
interface that can sit BEHIND the batcher's spawn step (the flip point,
HQ-INTEGRATION.md — still owner-gated) with two proven implementations:

  DockerBackend    — the REAL worker leg: worker_policy.run_case_with_policy
                     (flock claim already upstream -> docker dispatch ->
                     git-show harvest -> host pytest -> att-* rows + error
                     classification). The toy_docker shim replays the
                     container side docker-free (HQ harness pattern), so
                     the same host-side code path runs in CI/rehearsal.

  HeadlessBackend  — opencode headless, the V4/V6 recipe on example-host-c: own
                     git-root workspace, PWD/OLDPWD pinned (opencode/Bun
                     anchors by $PWD), never raises on model failure,
                     host pytest decides. Same row schema as Docker.

Both backends speak the row schema of worker_loop: claim row + verdict
row keyed by task/attemptId, decisions from the error table (deliver /
requeue_fresh / requeue_session_exit / quarantine_recipe / recipe_bug).

SHADOW-ONLY: nothing in the live batcher imports this yet.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

# both proven implementations live in the HQ driver repo; import by
# namespace package so this module works from any cwd
_ZRD = "/home/you/the-queue-driver"
if _ZRD not in sys.path:
    sys.path.insert(0, _ZRD)

from tools import worker_loop                      # noqa: E402
from tools import worker_policy                    # noqa: E402


class DispatchBackend:
    """Contract the batcher spawn step would call (post-flip).

    run_case(task, ws, ledger_path, brief, attempt_id) -> verdict row.
    The claim is already held upstream (claim = send-intent, A.4/A.2);
    the backend dispatches, verifies on the host, and appends the
    verdict row. Never raises on model/environment failure — classify
    and record (the batcher's error table, in code).
    """

    name = "abstract"

    def run_case(self, task, ws, ledger_path, brief,
                 attempt_id=None) -> dict:
        raise NotImplementedError


class DockerBackend(DispatchBackend):
    """The real worker leg end to end (toy shim stands in for docker)."""

    name = "docker"

    def __init__(self, image="swarmo-worker:c1", model="toy-model",
                 opencode_bin="opencode", auth_json="/dev/null",
                 timeout_s=240):
        self.image = image
        self.model = model
        self.opencode_bin = opencode_bin
        self.auth_json = auth_json
        self.timeout_s = timeout_s

    def run_case(self, task, ws, ledger_path, brief,
                 attempt_id=None) -> dict:
        return worker_policy.run_case_with_policy(
            task=task, ws=ws, ledger_path=ledger_path,
            image=self.image, model=self.model, brief=brief,
            opencode_bin=self.opencode_bin,
            auth_json=self.auth_json, timeout_s=self.timeout_s,
            attempt_id=attempt_id)


class HeadlessBackend(DispatchBackend):
    """opencode headless per the V4/V6 constraints (example-host-c-proven).

    Uses the same ledger primitives as Docker (one row schema, V3-join
    preserved) but dispatches on the host: no container, no image —
    the fast path for tasks that do not need isolation.
    """

    name = "headless"

    def __init__(self, model="hpc-glm/zai-org/GLM-5.3-Flash",
                 opencode_bin="opencode", timeout_s=420):
        self.model = model
        self.opencode_bin = opencode_bin
        self.timeout_s = timeout_s

    def _dispatch(self, ws, brief):
        env = dict(os.environ, PWD=ws, OLDPWD=ws)  # V4: $PWD pinning
        t0 = time.perf_counter()
        try:
            r = subprocess.run(
                [self.opencode_bin, "run", "--pure", "-m",
                 self.model, brief],
                cwd=ws, env=env, text=True, capture_output=True,
                timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout after {self.timeout_s}s"
        return r.returncode, r.stdout or "", r.stderr or ""

    def run_case(self, task, ws, ledger_path, brief,
                 attempt_id=None) -> dict:
        claim = worker_loop.Claim(ledger_path)
        try:
            if attempt_id is None:
                attempt_id = worker_policy.mint_attempt_id()
            worker_loop.append_row(ledger_path, {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "event": "claim", "task": task,
                "attemptId": attempt_id})
            t0 = time.perf_counter()
            rc, out, err = self._dispatch(ws, brief)
            # harvest like the container leg: worktree state IS the patch
            dirty = subprocess.run(
                ["git", "status", "--porcelain"], cwd=ws,
                capture_output=True, text=True)
            patch = subprocess.run(
                ["git", "diff"], cwd=ws, capture_output=True, text=True)
            d = {"container_exit": rc,   # same schema field for the
                 "wall_s": round(time.perf_counter() - t0, 1),  # classifier
                 "commit_sha": None, "trailer_ok": False,
                 "patch_bytes": len(patch.stdout.encode()),
                 "worktree_clean": dirty.stdout.strip() == ""}
            cls = worker_policy.classify_dispatch(d, out, err)
            v = worker_loop.host_verify(ws)
            fixed = (cls["decision"] == worker_policy.DELIVER
                     and d["patch_bytes"] > 0
                     and v["host_passed"] > 0 and v["host_failed"] == 0)
            row = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "event": "verdict", "task": task,
                "attemptId": attempt_id, "model": self.model,
                "image": None, **d, **v, "fixed": fixed,
                "error_class": cls["error_class"],
                "decision": cls["decision"],
                "session_exit": cls["session_exit"],
                "detail": cls["detail"],
            }
            worker_loop.append_row(ledger_path, row)
            return row
        finally:
            claim.release()


def select_backend(name: str) -> DispatchBackend:
    if name == "docker":
        return DockerBackend()
    if name == "headless":
        return HeadlessBackend()
    raise ValueError(f"unknown backend {name!r} (docker|headless)")
