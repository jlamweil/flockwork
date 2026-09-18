"""Operator-authored contract for verdict classification (hardening round,
2026-09-18). The dogfood run left environmental deaths (dispatch timeout,
freebuff SingletonBusy) recorded as bare `fixed: false` — indistinguishable
from merit failures in the refs. Contract: verdict bodies carry the
dispatch exit code (oc_rc) and the oracle exit code (pytest_rc) so the
error table (timeout→requeue, model-death→requeue+exit, 0+fail→merit)
applies from the refs alone. Pure-format tests: no fleet, no network.
"""
import importlib.util
import pathlib


def _load():
    p = pathlib.Path(__file__).resolve().parent.parent / "l2" / "inrepo.py"
    spec = importlib.util.spec_from_file_location("inrepo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inrepo = _load()


def test_verdict_body_carries_dispatch_and_oracle_rc():
    body = inrepo.verdict_body(
        "T-x", "att-w1-aaaa01", True, "example-host-c", oc_rc=0, pytest_rc=0
    )
    lines = body.splitlines()
    assert lines[0] == "verdict"  # L1 root-commit shape: subject is `verdict`
    assert "task: T-x" in lines
    assert "attempt: att-w1-aaaa01" in lines
    assert "fixed: true" in lines
    assert "host: example-host-c" in lines
    assert "oc_rc: 0" in lines
    assert "pytest_rc: 0" in lines


def test_verdict_body_separates_environmental_from_merit():
    merit = inrepo.verdict_body(
        "T-x", "att-w1-aaaa01", False, "example-host-b", oc_rc=0, pytest_rc=1
    )
    env = inrepo.verdict_body(
        "T-x", "att-w1-bbbb02", False, "example-host-b", oc_rc=124, pytest_rc=1
    )
    assert "fixed: false" in merit and "oc_rc: 0" in merit
    assert "fixed: false" in env and "oc_rc: 124" in env
    # the discriminator the error table keys on:
    assert merit != env and "oc_rc: 0" not in env and "oc_rc: 124" not in merit


def test_audit_parser_still_reads_classified_verdicts():
    """audit() keys on fixed/attempt lines; the new fields must neither
    break it nor be required by it (old verdicts lack them)."""
    body = inrepo.verdict_body(
        "T-x", "att-w1-aaaa01", False, "example-host-b", oc_rc=124, pytest_rc=1
    )
    vd = {}
    for ln in body.splitlines():
        if ":" in ln:
            k, _, val = ln.partition(":")
            vd[k.strip()] = val.strip()
    assert vd["fixed"] == "false"
    assert vd["attempt"] == "att-w1-aaaa01"
    assert vd.get("oc_rc") == "124"  # available to downstream classifiers
