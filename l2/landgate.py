#!/usr/bin/env python3
"""flockwork settle/land gate — the server-side authorization leg (P3 / F3).

The substrate already enforces the MERIT leg: an oracle fail in
``work_external`` writes an honest ``fixed:false`` and publishes nothing
(the merit-fail law). This module is the AUTHORIZATION leg that the merit
leg cannot give you: before a task is settled (``write_verdict``) or a fix
published (``land_fix``), the principal must present a token that is
validly signed under the gate's secret and whose role may settle. It is the
check that still holds when the client cannot be trusted — "never trust the
client to grant itself the tier":

- the verification ``secret`` is held by the gate (the "server"), never
  presented by the worker; the worker only ever supplies ``presented_token``;
- there is **no unsigned fallback** — a missing secret, a missing token, an
  unsigned / expired / forged token, or a too-low tier all default-deny;
- the verdict is machine-readable (CI / CLI consumable): allow -> exit 0,
  deny -> exit 2.

Parity with ``lakekit.enforce`` / ``lakekit.agent_gate``: the same
``ROLE_MATRIX`` (public ⊂ team ⊂ owner), the same token shape
(``role:subject:expires:sig``, HMAC-SHA256), the same default-deny.
Self-contained on purpose (stdlib only, liftable to any lane — the
``gates.py`` / lawgate lone-law discipline).

LOCAL POSTURE (read before relying on this): when the secret is reachable
from the worker — it is, on a box the agent runs on — the gate is
bypassable by a principal that ALSO owns the secret. It stops unprivileged
workers and honest mistakes; it does not stop an agent that reads the key.
The un-bypassable property comes from hosting the secret where the agent
cannot reach it (the excluded host). ``landgate serve`` IS that control
plane: it holds the secret and performs the privileged settle/land itself,
so a worker holding only a scoped token (read + push returns) can neither
forge the check nor write a verdict / advance main. The settle contract is
identical whether the secret is local (bypassable) or on the host
(un-bypassable): the gate reads the secret from wherever it lives.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass

# Parity with lakekit.agent_gate.ROLE_MATRIX: public ⊂ team ⊂ owner.
READ, WRITE, LAUNCH, MANAGE_SECRETS = (
    "read", "write", "launch", "manage_secrets")
ROLE_MATRIX: dict[str, set[str]] = {
    "public": {READ},
    "team": {READ, WRITE},
    "owner": {READ, WRITE, LAUNCH, MANAGE_SECRETS},
}

#: The capability a settle/land must hold. Only team+ (WRITE) may settle a
#: task or publish a fix; a read-only (public) worker is denied.
SETTLE_CAPABILITY = WRITE


class GateError(Exception):
    """A malformed / expired / forged token (mirrors lakekit AgentGateError)."""


def _sig(role: str, subject: str, expires: str, secret: str) -> str:
    return hmac.new(secret.encode(),
                    f"{role}:{subject}:{expires}".encode(),
                    hashlib.sha256).hexdigest()


def sign_token(role: str, subject: str, secret: str,
               ttl_seconds: int = 3600) -> str:
    """Mint ``role:subject:expires:sig``. Only a holder of ``secret`` can
    produce a token :func:`verify_token` will accept — a worker cannot flip
    its own role field to a higher tier (the signature covers it)."""
    if not role or not subject or not secret:
        raise GateError("sign_token: role, subject and secret are all required")
    expires = str(int(time.time()) + int(ttl_seconds))
    return f"{role}:{subject}:{expires}:{_sig(role, subject, expires, secret)}"


def verify_token(token: str, secret: str) -> tuple[str, str]:
    """Verify ``role:subject:expires:sig`` under ``secret`` -> (role, subject).

    Raises :class:`GateError` on a malformed, expired, or signature-mismatch
    token. The presented string is trusted only to ROUTE a tier, never to
    GRANT — the role is trusted only once the HMAC checks out under the
    gate's own secret.
    """
    if not secret:
        raise GateError("no verification secret (server default-deny)")
    if not isinstance(token, str):
        raise GateError(f"invalid token: {token!r}")
    parts = token.split(":")
    if len(parts) != 4:
        raise GateError(
            f"invalid token (expected role:subject:expires:sig): {token!r}")
    role, subject, expires, sig = parts
    if not role or not subject:
        raise GateError(f"invalid token (empty role/subject): {token!r}")
    try:
        exp = int(expires)
    except ValueError:
        raise GateError(f"invalid token (bad expiry): {token!r}") from None
    if time.time() > exp:
        raise GateError(f"token expired (role={role} subject={subject})")
    expected = _sig(role, subject, expires, secret)
    if not hmac.compare_digest(expected, sig):
        raise GateError(
            f"token signature mismatch (role={role} subject={subject})")
    return role, subject


@dataclass(frozen=True)
class Verdict:
    """A machine-readable gate decision (CI / CLI consumable)."""

    action: str
    allowed: bool
    role: str
    reason: str
    ts: int

    def to_json(self) -> str:
        return json.dumps({"action": self.action, "allowed": self.allowed,
                           "role": self.role, "reason": self.reason,
                           "ts": self.ts})


def settle_check(presented_token: str | None, secret: str,
                 fixed: bool = False, oracle_rc: int | None = None,
                 action: str = "settle") -> Verdict:
    """Fail-closed settle/land check.

    - **auth** (always): ``presented_token`` must verify under the gate's
      ``secret`` and the verified role must hold ``SETTLE_CAPABILITY``
      (team+). A missing secret, a missing token, an unsigned / expired /
      forged token, or a too-low tier all default-deny.
    - **merit** (only when claiming ``fixed=True``): the oracle rc must be 0;
      a failed (or unknown) oracle cannot back a ``fixed`` claim.
      ``oracle_rc`` is the verify/oracle rc (write_verdict's ``pytest_rc``);
      it is ignored when not claiming fixed — an honest ``fixed:false`` needs
      authorization only.
    """
    now = int(time.time())

    def deny(role: str, reason: str) -> Verdict:
        return Verdict(action, False, role, reason, now)

    # A misconfigured gate is never open (default-deny).
    if not secret:
        return deny("<server>",
                    "no verification secret configured (server default-deny)")
    # Client bypass: the worker skipped the gate entirely -> no token.
    if not presented_token:
        return deny("<none>", "no agent token presented (client bypass)")
    # The gate never trusts an unsigned token: verify under its own secret.
    try:
        role, _subject = verify_token(presented_token, secret)
    except GateError as e:
        return deny("<unverified>", f"token rejected: {e}")
    # Verified role -> enforce the matrix (default-deny within the tier).
    if SETTLE_CAPABILITY not in ROLE_MATRIX.get(role, set()):
        return deny(role,
                    f"capability not in tier: '{role}' lacks "
                    f"'{SETTLE_CAPABILITY}' (strict 403)")
    # Merit only binds a fixed claim.
    if fixed:
        if oracle_rc is None:
            return deny(role, "fixed claimed but oracle rc unknown (fail-closed)")
        if int(oracle_rc) != 0:
            return deny(
                role, f"fixed claimed but oracle failed (oracle_rc={oracle_rc})")
    reason = "authorized + oracle pass" if fixed else "authorized"
    return Verdict(action, True, role, reason, now)


def _make_gate_host(port: int, secret: str, host: str = "127.0.0.1"):
    """Build (but do not serve) the gate host. Returns ``(httpd, actual_port)``;
    call ``httpd.serve_forever()`` (or :func:`_run_server`) to serve. Tests use
    this to run the host on an ephemeral port.

    The handler holds the ``secret`` and, per request, runs the authoritative
    :func:`settle_check` and — only if authorized — performs the privileged
    settle/land via ``inrepo`` under this host's own git identity (the only
    credential the origin's protected refs accept for a verdict / main push).
    Inside the handler the local gate + delegation are switched off for the
    push (this host IS the gate), then restored.
    """
    import http.server

    try:
        import inrepo
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import inrepo

    class _Handler(http.server.BaseHTTPRequestHandler):
        server_version = "landgate/1"

        def _send(self, code: int, obj) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args) -> None:  # noqa: A002
            pass  # denials are in the response body; keep the host quiet

        def _with_gate_off(self, fn):
            """Run ``fn`` with the local gate + delegation off (this host IS
            the gate) so the settlement push is not re-gated / re-POSTed to
            itself; the env is restored afterwards."""
            saved = {"FLOCKWORK_LAND_GATE":
                     os.environ.get("FLOCKWORK_LAND_GATE"),
                     "FLOCKWORK_LAND_GATE_URL":
                         os.environ.get("FLOCKWORK_LAND_GATE_URL")}
            os.environ.pop("FLOCKWORK_LAND_GATE", None)
            os.environ.pop("FLOCKWORK_LAND_GATE_URL", None)
            try:
                return fn()
            finally:
                for k, val in saved.items():
                    if val is not None:
                        os.environ[k] = val

        def _settle(self, body: dict) -> None:
            v = settle_check(body.get("token"), secret,
                             fixed=bool(body.get("fixed", False)),
                             oracle_rc=body.get("oracle_rc"))
            if not v.allowed:
                return self._send(200, {"settled": False,
                                        "role": v.role, "reason": v.reason})
            r = self._with_gate_off(
                lambda: inrepo.write_verdict(
                    body["origin"], body["task"], body["att"],
                    bool(body.get("fixed", False)),
                    body.get("worker") or "gate-host",
                    0, body.get("oracle_rc") or 0))
            self._send(200, {"settled": bool(inrepo.ok(r)),
                             "detail": (r.stderr or "")[:300]})

        def _land(self, body: dict) -> None:
            v = settle_check(body.get("token"), secret, fixed=False,
                             action="land")
            if not v.allowed:
                return self._send(200, {"landed": False,
                                        "role": v.role, "reason": v.reason})
            out = self._with_gate_off(
                lambda: inrepo.land_fix(body["origin"], body["tree"],
                                        body["task"], body["att"],
                                        body.get("paths")))
            okd = bool(out.get("main_pushed") or out.get("already_on_main"))
            clean = {k: out.get(k) for k in
                     ("main_pushed", "already_on_main", "return_pushed",
                      "no_diff", "tip")}
            if "gate_denied" in out:
                clean["gate_denied"] = out["gate_denied"]
            self._send(200, {"landed": okd, "out": clean})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError, json.JSONDecodeError):
                return self._send(400, {"error": "bad request body (want json)"})
            if self.path == "/settle":
                return self._settle(body)
            if self.path == "/land":
                return self._land(body)
            return self._send(404, {"error": f"no such endpoint: {self.path}"})

    httpd = http.server.ThreadingHTTPServer((host, port), _Handler)
    return httpd, httpd.server_address[1]


def _run_server(port: int, secret: str, host: str = "127.0.0.1") -> None:
    """Serve the gate host until interrupted."""
    httpd, _port = _make_gate_host(port, secret, host)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    """CLI.

    - ``landgate settle --token <tok> [--fixed] [--oracle-rc <rc>]`` — gate a
      terminal verdict (auth + merit-if-fixed).
    - ``landgate land --token <tok>`` — gate a publish (auth only).
    - ``landgate mint --role <r> --subject <s>`` — mint a signed token
      (issuer side; used by the demo, not the worker path).
    - ``landgate serve --port N [--secret ...]`` — run the gate host (the
      control plane): holds the secret, and on each settle/land POST performs
      the authoritative check + the privileged push itself.

    The secret is read server-side from ``--secret`` or
    ``$FLOCKWORK_LAND_SECRET``; in deployment it is read by the gate host,
    never presented by the worker. Prints the verdict JSON; exit 0 allow,
    2 deny (so a CI / board step can fail closed on any denial).
    """
    import argparse

    ap = argparse.ArgumentParser(prog="landgate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("settle", help="gate a terminal verdict")
    s.add_argument("--token", default=os.environ.get("FLOCKWORK_LAND_TOKEN"))
    s.add_argument("--fixed", action="store_true")
    s.add_argument("--oracle-rc", type=int, default=None)
    s.add_argument("--secret", default=os.environ.get("FLOCKWORK_LAND_SECRET"))

    l = sub.add_parser("land", help="gate a publish (auth only)")
    l.add_argument("--token", default=os.environ.get("FLOCKWORK_LAND_TOKEN"))
    l.add_argument("--secret", default=os.environ.get("FLOCKWORK_LAND_SECRET"))

    m = sub.add_parser("mint", help="mint a signed token (issuer side)")
    m.add_argument("--role", default="owner")
    m.add_argument("--subject", required=True)
    m.add_argument("--secret", default=os.environ.get("FLOCKWORK_LAND_SECRET"))

    sv = sub.add_parser(
        "serve", help="run the gate host (control plane: holds the secret; "
                      "does the privileged settle/land)")
    sv.add_argument("--port", type=int,
                    default=int(os.environ.get("FLOCKWORK_LAND_GATE_PORT",
                                                "8477")))
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--secret", default=os.environ.get("FLOCKWORK_LAND_SECRET"))

    a = ap.parse_args(argv)

    if a.cmd == "serve":
        if not a.secret:
            print(json.dumps(
                {"error": "serve requires a secret (--secret or "
                           "FLOCKWORK_LAND_SECRET)"}))
            return 2
        _run_server(a.port, a.secret, a.host)
        return 0

    if a.cmd == "mint":
        try:
            print(sign_token(a.role, a.subject, a.secret))
        except GateError as e:
            print(f"mint failed: {e}")
            return 2
        return 0

    action = "settle" if a.cmd == "settle" else "land"
    fixed = a.fixed if a.cmd == "settle" else False
    oracle_rc = a.oracle_rc if a.cmd == "settle" else None
    v = settle_check(a.token, a.secret, fixed=fixed,
                     oracle_rc=oracle_rc, action=action)
    print(v.to_json())
    return 0 if v.allowed else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
