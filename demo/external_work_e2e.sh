#!/usr/bin/env bash
# flockwork external-work E2E — the interactive/plugin path, end to end.
#
# The owner_test.sh kit proves the HEADLESS worker path. This one proves the
# EXTERNAL-WORK path the opencode plugin drives (the fw_* legs, here invoked
# directly as CLI verbs — the same commands the plugin shells out to) on a
# fresh scratch origin, with a deterministic stand-in for the agent's coding
# work, so a run costs zero model spend and zero network. It asserts the
# plan's C16 invariants:
#
#   A. board is truthful — the external-work run lands the fix on main, writes
#      the create-once verdict, pushes the H1 return ref, and `audit` h1_pass.
#   B. exactly-once under a duplicate claim — two agents race; one wins, one
#      loses the CAS; exactly one verdict.
#   C. a simulated mid-work crash is LEGIBLE (flockwork debug) + BOUNDED (one
#      heir — a fresh session — re-claims and produces the honest terminal
#      verdict). Scripted across two sessions (CORR-12).
#
# Each task targets its own file (so the shared main branch is not conflated).
# Your real origin is NEVER touched (a fresh scratch board under /tmp).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

BASE="${TMPDIR:-/tmp}/flockwork-ew-e2e"
rm -rf "$BASE"; mkdir -p "$BASE"
export SWARM_ORIGIN="$BASE/origin.git"
export SWARM_MODEL=demo-model
export SWARM_LEASE_TTL=60
export PYTHONDONTWRITEBYTECODE=1

fw() { python3 l2/inrepo.py "$@"; }
att_of() { python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("att") or "")'; }
field()  { python3 -c 'import sys,json; v=json.load(sys.stdin).get(sys.argv[1]); print(v if v is not None else "")' "$1"; }
step()   { printf '\n==== %s ====\n' "$*"; }
fail()   { echo "FAIL: $*" >&2; exit 1; }
fresh_tree() { git clone -q "$SWARM_ORIGIN" "$1"; }
mainfile()   { git -C "$SWARM_ORIGIN" show "main:$1"; }

step "0. fresh scratch board = $SWARM_ORIGIN"
git init -q --bare "$SWARM_ORIGIN"
git -C "$SWARM_ORIGIN" symbolic-ref HEAD refs/heads/main
SRC="$BASE/src"; mkdir "$SRC"
( cd "$SRC"; git init -q -b main .
  for f in happy.txt race.txt crash.txt; do echo "broken" > "$f"; done
  git add -A
  git -c user.email=a@b -c user.name=a commit -qm seed
  git push -q "$SWARM_ORIGIN" main )

cat > "$BASE/spec.json" <<'JSON'
{
  "ew-happy": "Fix happy.txt so its only line is: fixed\n\nverify: grep -qx fixed happy.txt",
  "ew-race":  "Fix race.txt so its only line is: fixed\n\nverify: grep -qx fixed race.txt",
  "ew-crash": "Fix crash.txt so its only line is: fixed\n\nverify: grep -qx fixed crash.txt"
}
JSON
fw seed "$BASE/spec.json" >/dev/null

step "A. external-work happy run: claim -> work -> judge -> land -> verdict"
ATTA=$(fw claim ew-happy | att_of)
[ -n "$ATTA" ] || fail "claim ew-happy returned no att"
fresh_tree "$BASE/t-happy"
echo "fixed" > "$BASE/t-happy/happy.txt"            # the agent's coding work
JRES=$(fw judge ew-happy "$BASE/t-happy"); echo "  judge: $(echo "$JRES" | tr -d '\n' | tr -s ' ')"
[ "$(echo "$JRES" | field rc)" = "0" ] || fail "oracle did not pass on the fixed tree"
LRES=$(fw land ew-happy "$ATTA" "$BASE/t-happy" happy.txt); echo "  land: $(echo "$LRES" | tr -d '\n' | tr -s ' ')"
[ "$(echo "$LRES" | field main_pushed)" = "True" ] || fail "land did not push to main"
fw verdict ew-happy "$ATTA" true 0 0 >/dev/null || fail "verdict not written"

# board truthfulness: the fix is on main, the verdict + return ref exist, audit h1_pass
[ "$(mainfile happy.txt)" = "fixed" ] || fail "main does not carry the fix (got: $(mainfile happy.txt))"
git ls-remote "$SWARM_ORIGIN" "refs/swarm/verdicts/ew-happy" | grep -q . || fail "no verdict ref"
git ls-remote "$SWARM_ORIGIN" "refs/swarm/tasks/ew-happy"    | grep -q . || fail "no H1 return ref"
AUDIT=$(fw audit ew-happy)
echo "$AUDIT" | grep -q '"h1_pass": true' || fail "audit h1_pass not true: $AUDIT"
echo "  A OK: fix on main, verdict + return ref present, audit h1_pass=true"

step "B. exactly-once under a duplicate claim (two agents race ew-race)"
ATTA1=$(fw claim ew-race | att_of)
[ -n "$ATTA1" ] || fail "race: agent 1 did not win the claim"
ATTA2=$(fw claim ew-race | att_of || true)          # duplicate: must lose the CAS
[ -z "$ATTA2" ] || fail "race: agent 2 won a duplicate claim (exactly-once broken)"
echo "  agent1 att=$ATTA1  agent2 att='${ATTA2}' (lost the CAS)"
# only the winner proceeds to a verdict
fresh_tree "$BASE/t-race"
echo "fixed" > "$BASE/t-race/race.txt"
fw judge ew-race "$BASE/t-race" >/dev/null
fw land ew-race "$ATTA1" "$BASE/t-race" race.txt >/dev/null
fw verdict ew-race "$ATTA1" true 0 0 >/dev/null
NDUP=$(git ls-remote "$SWARM_ORIGIN" "refs/swarm/verdicts/ew-race" | grep -c . || true)
[ "$NDUP" = "1" ] || fail "exactly-once broken: $NDUP verdict refs on ew-race"
echo "  B OK: exactly one claim won, exactly one verdict"

step "C. mid-work crash is legible + bounded (two sessions on ew-crash)"
# session 1 (a fresh interactive session): claims + starts work, then DIES
ATTC1=$(fw claim ew-crash | att_of)
[ -n "$ATTC1" ] || fail "crash: session 1 did not claim"
fw work-started ew-crash "$ATTC1" >/dev/null || fail "crash: work-started not recorded"
echo "  session 1 claimed + work_started, then crashed (no judge/land/verdict)"
# legibility: flockwork debug shows the claim + work_started, NO verdict, main untouched
DBG=$(fw debug ew-crash)
echo "$DBG" | grep -q 'work_started'        || fail "crash not legible (no work_started in debug)"
echo "$DBG" | grep -q '"verdict": null'     || fail "crash: debug should show no verdict"
[ "$(mainfile crash.txt)" = "broken" ] || fail "crash: main should be untouched (got: $(mainfile crash.txt))"
echo "  crash is legible: claim + work_started, no verdict, crash.txt still broken"
# the lease expires -> sweep frees the task (the TTL+heir hand-off point)
fw sweep ew-crash >/dev/null || fail "sweep could not free the crashed task"
git ls-remote "$SWARM_ORIGIN" "refs/swarm/claims/ew-crash" | grep -q . \
  && fail "crash: the live claim was not freed by the sweep"
# session 2 (the heir — ANOTHER fresh human-initiated session): re-claims
ATTC2=$(fw claim ew-crash | att_of)
[ -n "$ATTC2" ] || fail "heir: session 2 could not re-claim the crashed task"
[ "$ATTC2" != "$ATTC1" ] || fail "heir: re-used the dead session's att"
fresh_tree "$BASE/t-crash"
echo "fixed" > "$BASE/t-crash/crash.txt"
fw judge ew-crash "$BASE/t-crash" >/dev/null
fw land ew-crash "$ATTC2" "$BASE/t-crash" crash.txt >/dev/null
fw verdict ew-crash "$ATTC2" true 0 0 >/dev/null || fail "heir: honest verdict not written"
NVERD=$(git ls-remote "$SWARM_ORIGIN" "refs/swarm/verdicts/ew-crash" | grep -c . || true)
[ "$NVERD" = "1" ] || fail "heir: expected exactly one honest verdict, got $NVERD"
[ "$(mainfile crash.txt)" = "fixed" ] || fail "heir: crash.txt not fixed on main"
echo "  C OK: crash legible + one heir re-claimed + honest terminal verdict (bounded)"

step "teardown"
rm -rf "$BASE"

echo
echo "PASS: external-work path — board truthful (main+verdict+return ref+audit h1_pass),"
echo "      exactly-once under a duplicate claim, crash legible + bounded heir (two sessions)."
