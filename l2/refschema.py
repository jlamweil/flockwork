#!/usr/bin/env python3
"""flockwork ref schemas — the schema IS the instruction (INT-081
TAKE 1, landed by WQ-054).

A ref the lane acts on must carry the fields the actor needs; a ref
that fails its schema is refused LOUDLY at the moment of use, naming
the missing field — never a silent pass. Two schemas, both minimal:

  spec (refs/swarm/specs/<task>, validated at claim time — the
  WQ-025 pilot class this kills: attempts dispatched on
  underspecified seeds and stalled):
      what      the brief body itself — non-empty (the wave-9 law,
                now a named schema field)
      verify    the done-bar, one machine-checkable `verify:` line
                (the L4 oracle reads it; a constraint the verify
                chain cannot check is decoration — the WQ-025
                lesson-a law, now enforced)

  review-verdict (refs/swarm/verdicts/<task>@<reviewer>, validated
  before counting — docs/GATES.md §2, here as the definition of
  record): the type marker, then task, reviewer, outcome (agree|veto),
  evidence — each check names its field.

Minimality is deliberate: everything else on a ref (n:/m:,
deliverable:, kind:, host/oc_rc/pytest_rc lines, prose) stays
optional data. The spec 'where' class stays optional too — every
healthy seed in the wild names its paths in prose or a deliverable:
line, and a dedicated required line would reject the kit's own demo
spec; the done-bar is where the stall class actually lived.

Self-contained on purpose (stdlib only, no l2 imports) — liftable
beside l2/gates.py, which delegates here when the sibling resolves
and falls back to its own inline §2 checks when staged alone. A
schema refusal is a refusal to ACT on a ref, never a deletion: the
ref stays on the board as data, and audit() counts it.
"""

SPEC_REQUIRED = ("what", "verify")
VERDICT_REQUIRED = ("task", "reviewer", "outcome", "evidence")
VERDICT_OUTCOMES = ("agree", "veto")
VERDICT_MARKER = "review-verdict"


import re

# A field line is `key: value` with a SINGLE-TOKEN key (verify:, n:, m:,
# deliverable:, kind:, ...) — the house body convention. Prose lines
# ("task T: add hello.txt", sentences) never match: their pre-colon
# span isn't one token.
_FIELD_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*:")


def parse_fields(body):
    """The house body convention: `key: value` lines."""
    fields = {}
    for ln in body.strip().splitlines():
        if ": " in ln:
            k, v = ln.split(": ", 1)
            fields[k.strip()] = v.strip()
    return fields


def validate_spec(body):
    """Spec schema. Returns [] when the seed is actionable, else typed
    errors, each naming the missing field."""
    errs = []
    what = any(
        ln.strip() and not _FIELD_LINE.match(ln)
        for ln in (body or "").splitlines()
    )
    if not what:
        errs.append("missing required field: what (no task statement — "
                    "the brief is empty or field lines only, a worker "
                    "cannot act on nothing)")
    if not any(ln.strip().startswith("verify:") and
               ln.strip()[len("verify:"):].strip()
               for ln in (body or "").splitlines()):
        errs.append("missing required field: verify (the done-bar — "
                    "no machine-checkable oracle line, the WQ-025 "
                    "stall class)")
    return errs


def validate_review_verdict(body, task, reviewer):
    """Review-verdict schema (docs/GATES.md §2). Returns (fields, errs):
    fields is None unless every check passes; each error names its
    field."""
    lines = body.strip().splitlines()
    if not lines or lines[0].strip() != VERDICT_MARKER:
        return None, [f"missing required field: {VERDICT_MARKER} type "
                      "marker (not a review-verdict object)"]
    fields = parse_fields(body)
    errs = []
    if fields.get("task") != task:
        errs.append(f"missing or mismatched field: task "
                    f"(ref says {task!r}, body says {fields.get('task')!r})")
    if fields.get("reviewer") != reviewer:
        errs.append(f"missing or mismatched field: reviewer "
                    f"(ref says {reviewer!r}, "
                    f"body says {fields.get('reviewer')!r})")
    if fields.get("outcome") not in VERDICT_OUTCOMES:
        errs.append(f"missing or illegal field: outcome "
                    f"(must be {'|'.join(VERDICT_OUTCOMES)}, "
                    f"got {fields.get('outcome')!r})")
    if not fields.get("evidence"):
        errs.append("missing required field: evidence (the ref the "
                    "reviewer examined)")
    return (fields if not errs else None), errs
