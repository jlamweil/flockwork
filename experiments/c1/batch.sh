#!/usr/bin/env bash
# C1 batch: 5 independent bug-fix cases, opencode headless inside swarmo-worker:c1.
# Appends one JSONL result row per case to results.jsonl (survey-A ledger pattern).
# Host verifies each patch with pytest after the container exits.
set -u
cd "$(dirname "$0")"
MODEL="google/gemini-3.5-flash-lite"
IMAGE="swarmo-worker:c1"
LEDGER="results.jsonl"

make_fixture() {
  local dir="$1" name="$2"
  rm -rf "$dir"; mkdir -p "$dir"
  case "$name" in
    fizzbuzz)
      cat > "$dir/fizzbuzz.py" <<'PYEOF'
def classify(n: int) -> str:
    """Return FizzBuzz classification for a single integer."""
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    if n % 15 == 0:
        return "FizzBuzz"
    return str(n)
PYEOF
      cat > "$dir/test_fizzbuzz.py" <<'PYEOF'
from fizzbuzz import classify

def test_fifteen_is_fizzbuzz():
    assert classify(15) == "FizzBuzz"

def test_three_is_fizz():
    assert classify(3) == "Fizz"

def test_five_is_buzz():
    assert classify(5) == "Buzz"

def test_seven_is_plain():
    assert classify(7) == "7"
PYEOF
      echo "fizzbuzz.py";;
    sumto)
      cat > "$dir/sumto.py" <<'PYEOF'
def sum_to(n: int) -> int:
    """Sum the integers 1..n inclusive."""
    total = 0
    for i in range(1, n):
        total += i
    return total
PYEOF
      cat > "$dir/test_sumto.py" <<'PYEOF'
from sumto import sum_to

def test_one():
    assert sum_to(1) == 1

def test_ten():
    assert sum_to(10) == 55

def test_zero():
    assert sum_to(0) == 0
PYEOF
      echo "sumto.py";;
    initials)
      cat > "$dir/initials.py" <<'PYEOF'
def initials(full_name: str) -> str:
    """First letters of the first two words, uppercase, dot-separated."""
    parts = full_name.strip().split()
    return (parts[0][0] + "." + part[1][0] + ".").upper()
PYEOF
      cat > "$dir/test_initials.py" <<'PYEOF'
from initials import initials

def test_ada():
    assert initials("ada lovelace") == "A.L."

def test_grace():
    assert initials("grace hopper") == "G.H."
PYEOF
      echo "initials.py";;
    addtag)
      cat > "$dir/tags.py" <<'PYEOF'
def add_tag(tag: str, bucket=[]) -> list:
    """Append tag to bucket, defaulting to a fresh list."""
    bucket.append(tag)
    return bucket
PYEOF
      cat > "$dir/test_tags.py" <<'PYEOF'
from tags import add_tag

def test_first_call_fresh():
    assert add_tag("a") == ["a"]

def test_second_call_fresh():
    assert add_tag("b") == ["b"]
PYEOF
      echo "tags.py";;
    dates)
      cat > "$dir/dates.py" <<'PYEOF'
import re

def is_iso_date(s: str) -> bool:
    """True if s looks like YYYY-MM-DD."""
    return bool(re.fullmatch(r"\d{2}-\d{2}-\d{2}", s))
PYEOF
      cat > "$dir/test_dates.py" <<'PYEOF'
from dates import is_iso_date

def test_full_year():
    assert is_iso_date("2026-09-15") is True

def test_short_year_rejected():
    assert is_iso_date("26-09-15") is False

def test_slashes_rejected():
    assert is_iso_date("2026/09/15") is False
PYEOF
      echo "dates.py";;
  esac
}

for name in fizzbuzz sumto initials addtag dates; do
  wsdir="case-$name/workspace"
  make_fixture "case-$name" "$name"
  target=$(make_fixture "case-$name/.tmp" "$name"); rm -rf "case-$name/.tmp"
  (cd "$wsdir" && git init -q -b main && git add -A \
    && git -c user.email=c@example-host-b.local -c user.name=c commit -qm "fixture: $name")
  brief="Fix the bug in $target so that \`python3 -m pytest\` passes. Do not modify the test file."
  echo "=== case $name ==="
  start=$(date +%s.%N)
  timeout 240 docker run --rm --user 1004:1004 \
    -v "$PWD/$wsdir:/work" -w /work \
    -v "$HOME/.opencode/bin/opencode:/usr/local/bin/opencode:ro" \
    -v "$HOME/.local/share/opencode/auth.json:/secrets/auth.json:ro" \
    -e HOME=/home/worker "$IMAGE" \
    bash -c 'mkdir -p ~/.local/share/opencode && cp /secrets/auth.json ~/.local/share/opencode/auth.json \
      && opencode run --pure -m '"$MODEL"' "'"$brief"'"' \
    > "case-$name/stdout.txt" 2> "case-$name/stderr.txt"
  cexit=$?
  end=$(date +%s.%N)
  wall=$(echo "$end $start" | awk '{printf "%.1f", $1-$2}')
  git -C "$wsdir" diff > "case-$name/patch.diff"
  hres=$(cd "$wsdir" && python3 -m pytest -q 2>&1 | tail -1)
  hpass=$(echo "$hres" | grep -oE '^[0-9]+ passed' | awk '{print $1}')
  hfail=$(echo "$hres" | grep -oE '[0-9]+ failed' | awk '{print $1}')
  [ -z "$hpass" ] && hpass=0; [ -z "$hfail" ] && hfail=0
  [ "$hfail" = "0" ] && [ "$hpass" != "0" ] && fixed=true || fixed=false
  ts=$(date -Is)
  printf '{"ts":"%s","case":"%s","model":"%s","image":"%s","container_exit":%s,"wall_s":%s,"host_passed":%s,"host_failed":%s,"fixed":%s}\n' \
    "$ts" "$name" "$MODEL" "$IMAGE" "$cexit" "$wall" "$hpass" "$hfail" "$fixed" >> "$LEDGER"
  echo "case=$name exit=$cexit wall=${wall}s host=[$hres] fixed=$fixed"
done
echo "--- ledger ---"; cat "$LEDGER"
