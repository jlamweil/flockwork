"""Operator-authored contract for the open_tasks CLI receipt (wave-2,
2026-09-21). LOOP-2026-09-19 documents `open_tasks` as the operator's
queue view, but __main__ never wired it: `python3 l2/inrepo.py
open_tasks` silently no-ops (rc 0, no output — measured live while
dispatching T6/T7), and ANY unknown mode falls through the if/elif
chain the same silent way — a typo'd operator command looks exactly
like an empty queue. Contract (local bare origin, no fleet):
  - `open_tasks` prints open_tasks()'s list as a JSON receipt (rc 0)
  - an unreachable origin fails LOUDLY (rc != 0, stderr says why) —
    'queue empty' and 'cannot see the queue' must not look alike
  - an unknown mode exits 2 with a usage line on stderr, never rc 0
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, text=True, capture_output=True)


@pytest.fixture
def origin(tmp_path):
    o = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(o))
    _git("-C", str(o), "symbolic-ref", "HEAD", "refs/heads/main")
    return str(o)


def _cli(origin, *args):
    return subprocess.run(
        [sys.executable, "l2/inrepo.py", *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        env=dict(os.environ, SWARM_ORIGIN=origin),
        timeout=120,
    )


def test_open_tasks_prints_json_receipt(origin):
    r = _cli(origin, "open_tasks")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == []


def test_unreachable_origin_fails_loudly(tmp_path):
    r = _cli(str(tmp_path / "nope.git"), "open_tasks")
    assert r.returncode != 0
    assert "unreachable" in r.stderr


def test_unknown_mode_is_a_usage_error_not_silence(origin):
    r = _cli(origin, "opentasks")
    assert r.returncode == 2
    assert "unknown mode" in r.stderr
