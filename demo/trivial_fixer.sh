#!/usr/bin/env bash
# swarmo test kit — deterministic stand-in for the model dispatch leg.
#
# The real lane dispatches the task brief with `opencode run --pure -m
# <model> <brief>` (or the freebuff PTY driver). This shim ignores its
# arguments and performs the demo task's fix directly, so the full
# claim -> spec-fetch -> dispatch -> oracle -> main-push -> verdict
# chain runs with zero model spend and zero network. It exits 0 leaving
# the tree dirty, which the lane records as a completed dispatch
# (oc_rc 0) — the oracle then judges the change exactly as it would
# judge a model's.
#
# Point a worker at it with: OPENCODE_BIN=$PWD/demo/trivial_fixer.sh
echo hi > hello.txt
exit 0
