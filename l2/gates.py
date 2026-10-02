#!/usr/bin/env python3
"""flockwork verdict-count gate — n-of-m independent verdicts flip an
integration ref (docs/GATES.md; INT-081 bridge, INT-085b pilot 1).

Design landed through flockwork itself (pilot wave A); this module is
its implementation. Self-contained on purpose: stdlib only, its own
git plumbing, no l2 imports — the gate must be liftable to any lane
without carrying the wire's law file (the lawgate lone-law lesson).

Substrate laws it reuses, never redefines:
  - create-once CAS: `git push --force-with-lease=<ref>:` (empty lease
    base) — the ref database decides races, exactly like claim_detail.
  - the @-law: per-instance refs are `<base>/<task>@<instance>` (the
    heirs_count archive shape); the subpath form is illegal beside a
    live leaf ref (git D/F conflict).
  - JSON-event prints: one json.dumps line per operation, like the kit.

Verdict body schema (docs/GATES.md §2 — the schema IS the instruction;
a body failing any check is not a verdict and is never counted):

    review-verdict
    task: <task>
    reviewer: <reviewer>
    outcome: agree|veto
    evidence: <a git ref the reviewer examined>

The gate reads n/m from the task's spec body (`n: <int>` / `m: <int>`
lines), counts valid reviewer verdicts under
refs/swarm/verdicts/<task>@*, and fires iff count(agree)==n AND
count(veto)==0 AND every counted verdict's evidence ref resolves. The
flip writes refs/swarm/integrated/<task> create-once; an existing flip
makes any later gate run an honest idempotent no-op.
"""
import json
import os
import subprocess
import sys

try:
    import refschema
except ImportError:  # loaded by path (tests, drivers): resolve the sibling
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import refschema
    except ImportError:
        # staged alone (the lawgate lone-law lesson): the gate keeps its
        # own inline §2 checks below — same refusals, no module typing.
        refschema = None

LAW = "docs/GATES.md"


def sh(cmd, cwd=None, inp=None, timeout=120):
    return subprocess.run(cmd, cwd=cwd, input=inp, capture_output=True,
                          text=True, timeout=timeout)


def ok(r):
    return r.returncode == 0


def git(*args, cwd=None, inp=None):
    return sh(["git", *args], cwd=cwd, inp=inp)


def origin():
    return os.environ.get("SWARM_ORIGIN", "origin")


def emit(event):
    print(json.dumps(event), flush=True)
    return event


def commit_tree(*args, cwd=None):
    """Empty-tree commit carrying a body; its sha is pushable from cwd
    (the object store lives in the repo the process runs in — the
    test_claim_loss lesson)."""
    t = git("hash-object", "-t", "tree", "/dev/null", cwd=cwd)
    if not ok(t):
        raise RuntimeError(f"tree: {t.stderr.strip()[:120]}")
    c = git("commit-tree", t.stdout.strip(), "-m", "\n".join(args),
            cwd=cwd)
    if not ok(c):
        raise RuntimeError(f"commit-tree: {c.stderr.strip()[:120]}")
    return c.stdout.strip()


def push_sha_ref(sha, ref, orig, cwd=None):
    """Create-once CAS push. Returns (pushed, rejected, stderr)."""
    r = git("push", orig, f"--force-with-lease={ref}:", f"{sha}:{ref}",
            cwd=cwd)
    return ok(r), not ok(r), (r.stderr or "")


def ref_exists(orig, ref):
    r = git("ls-remote", "--exit-code", orig, ref)
    return ok(r)


# --------------------------------------------------------------- verdicts


def _check_name(kind, name):
    """Refname safety: '@' is the archive-marker separator (an @-named
    instance would collide with the @-law parse), '/' would make the
    instance a path (and a task a namespace)."""
    if not name or "@" in name or "/" in name:
        return f"{kind} name must be nonempty and free of '@' and '/': {name!r}"
    return None


def parse_review_verdict(body, task, reviewer):
    """docs/GATES.md §2 schema check. Returns (fields, error). The
    schema's definition of record is l2/refschema.py (the schema IS the
    instruction, WQ-054); this delegation keeps one implementation, the
    inline body below is the staged-alone fallback."""
    if refschema is not None:
        fields, errs = refschema.validate_review_verdict(body, task,
                                                         reviewer)
        return fields, ("; ".join(errs) if errs else None)
    lines = body.strip().splitlines()
    if not lines or lines[0].strip() != "review-verdict":
        return None, "not a review-verdict object"
    fields = {}
    for ln in lines[1:]:
        if ": " in ln:
            k, v = ln.split(": ", 1)
            fields[k.strip()] = v.strip()
    if fields.get("task") != task:
        return None, "task field mismatch"
    if fields.get("reviewer") != reviewer:
        return None, "reviewer field mismatch"
    if fields.get("outcome") not in ("agree", "veto"):
        return None, "outcome not agree|veto"
    if not fields.get("evidence"):
        return None, "evidence missing"
    return fields, None


def review_verdict(task, reviewer, outcome, evidence, orig=None, cwd=None):
    """Create-once reviewer verdict at refs/swarm/verdicts/<task>@<reviewer>."""
    orig = orig or origin()
    err = _check_name("task", task) or _check_name("reviewer", reviewer)
    if err:
        return emit({"event": "review_verdict", "task": task,
                     "reviewer": reviewer, "pushed": False,
                     "refused": True, "reason": err})
    if outcome not in ("agree", "veto"):
        return emit({"event": "review_verdict", "task": task,
                     "reviewer": reviewer, "pushed": False,
                     "refused": True,
                     "reason": "outcome must be agree|veto"})
    ref = f"refs/swarm/verdicts/{task}@{reviewer}"
    body = (f"review-verdict\ntask: {task}\nreviewer: {reviewer}\n"
            f"outcome: {outcome}\nevidence: {evidence}\n")
    sha = commit_tree(body, cwd=cwd)
    pushed, rejected, stderr = push_sha_ref(sha, ref, orig, cwd=cwd)
    return emit({
        "event": "review_verdict", "task": task, "reviewer": reviewer,
        "outcome": outcome, "ref": ref, "sha": sha[:12],
        "pushed": pushed, "rejected": rejected and not pushed,
        "reason": None if pushed else stderr.strip()[:200],
    })


# -------------------------------------------------------------------- gate


def _spec_nm(orig, task):
    """n and m from the spec body's `n: <int>` / `m: <int>` lines."""
    fr = git("fetch", "-q", orig, f"refs/swarm/specs/{task}")
    if not ok(fr):
        raise RuntimeError(f"spec fetch: {fr.stderr.strip()[:160]}")
    body = git("log", "-1", "--format=%B", "FETCH_HEAD").stdout
    n = m = None
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("n:"):
            n = int(s[2:].strip())
        elif s.startswith("m:"):
            m = int(s[2:].strip())
    if n is None or m is None:
        raise RuntimeError(f"spec {task} carries no n:/m: lines")
    return n, m


def _reviewer_refs(orig, task):
    r = git("ls-remote", orig, f"refs/swarm/verdicts/{task}@*")
    if not ok(r):
        raise RuntimeError(f"board read: {r.stderr.strip()[:160]}")
    out = []
    for ln in r.stdout.splitlines():
        if ln.strip():
            sha, ref = ln.split()
            out.append((ref, sha))
    return out


def _read_ref_body(orig, sha_or_ref):
    """Commit message body of a sha (the house verdict-read: %B, never
    raw cat-file — the commit header is not the schema)."""
    r = git("log", "-1", "--format=%B", sha_or_ref)
    return r.stdout if ok(r) else None


def verdicts(orig, task):
    """Parsed reviewer verdicts for task: (reviewer, outcome, evidence,
    invalid_reason or None, sha). Applies the full §2 schema."""
    out = []
    for ref, sha in _reviewer_refs(orig, task):
        reviewer = ref.rsplit("@", 1)[1]
        body = _read_ref_body(orig, sha)
        fields, err = (None, f"unreadable object {sha[:12]}")
        if body is not None:
            fields, err = parse_review_verdict(body, task, reviewer)
        if fields is None:
            out.append((reviewer, None, None, err, sha))
        else:
            out.append((reviewer, fields["outcome"], fields["evidence"],
                        None, sha))
    return out


def flip(task, agreed, evidence=None, n=None, m=None, orig=None, cwd=None):
    """Create-once CAS flip of refs/swarm/integrated/<task>. Returns the
    push result dict (property 4's referee). evidence/n/m resolve from
    the board when not given (first agreeing verdict's evidence ref;
    the spec's n:/m: lines)."""
    orig = orig or origin()
    if n is None or m is None:
        n, m = _spec_nm(orig, task)
    if evidence is None:
        vs = verdicts(orig, task)
        agree = [v for v in vs if v[1] == "agree"]
        if not agree:
            raise RuntimeError("flip with no agreeing verdict to cite")
        evidence = agree[0][2]
    ref = f"refs/swarm/integrated/{task}"
    body = (f"integrated\ntask: {task}\nn: {n}\nm: {m}\n"
            f"agreed: {' '.join(agreed)}\nevidence: {evidence}\n")
    sha = commit_tree(body, cwd=cwd)
    pushed, rejected, stderr = push_sha_ref(sha, ref, orig, cwd=cwd)
    return {"event": "flip", "task": task, "ref": ref, "sha": sha[:12],
            "pushed": pushed,
            "rejected": rejected and not pushed,
            "reason": None if pushed else stderr.strip()[:200]}


def gate(task, orig=None, cwd=None):
    """The gate: count, check evidence, fire iff agree==n and veto==0.
    Idempotent: an existing integration ref is an honest no-op."""
    orig = orig or origin()
    intref = f"refs/swarm/integrated/{task}"
    if ref_exists(orig, intref):
        return emit({"event": "gate", "task": task,
                     "already_integrated": True, "fired": False,
                     "flipped": False})
    n, m = _spec_nm(orig, task)
    vs = verdicts(orig, task)
    agree = [v for v in vs if v[1] == "agree"]
    veto = [v for v in vs if v[1] == "veto"]
    invalid = [v for v in vs if v[1] is None]
    missing = [v for v in agree + veto
               if not ref_exists(orig, v[2])]
    fired = len(agree) == n and len(veto) == 0 and not missing
    ev = {"event": "gate", "task": task, "n": n, "m": m,
          "agree": len(agree), "veto": len(veto),
          "invalid": len(invalid), "evidence_missing": len(missing),
          "fired": fired, "flipped": False,
          "already_integrated": False}
    if invalid:
        # the typed schema refusals, loud in the event (WQ-054): a body
        # that is not a verdict is never counted, and the field it is
        # missing is named. Absent entirely on a clean count — a
        # well-formed board's event gains no keys.
        ev["invalid_reasons"] = sorted({v[3] for v in invalid if v[3]})
    if fired:
        evidence = agree[0][2]
        res = flip(task, [v[0] for v in agree], evidence, n, m,
                   orig=orig, cwd=cwd)
        ev["flipped"] = res["pushed"]
        ev["flip"] = res
    return emit(ev)


# --------------------------------------------------------------------- CLI


def main(argv):
    if len(argv) >= 2 and argv[0] == "review":
        task, reviewer, outcome = argv[1], argv[2], argv[3]
        evidence = argv[4] if len(argv) > 4 else f"refs/swarm/tasks/{task}"
        ev = review_verdict(task, reviewer, outcome, evidence)
        return 0 if ev["pushed"] else 1
    if len(argv) >= 2 and argv[0] == "gate":
        ev = gate(argv[1])
        return 0 if (ev["fired"] or ev["already_integrated"]) else 1
    print(f"usage: {sys.argv[0]} review <task> <reviewer> <agree|veto> "
          f"[evidence-ref] | gate <task>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
