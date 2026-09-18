"""c9 fixtures — four real broken tasks (bug + host pytest each)."""

FIXTURES = {
    "fizzbuzz-order": {
        "broken": '''def classify(n: int) -> str:
    """Return FizzBuzz classification for a single integer."""
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    if n % 15 == 0:
        return "FizzBuzz"
    return str(n)
''',
        "tests": '''from fizzbuzz_order import classify

def test_fifteen():
    assert classify(15) == "FizzBuzz"

def test_three():
    assert classify(3) == "Fizz"

def test_five():
    assert classify(5) == "Buzz"

def test_seven():
    assert classify(7) == "7"
''',
        "module": "fizzbuzz_order.py",
        "testfile": "test_fizzbuzz_order.py",
        "brief": ("BUG: in fizzbuzz_order.py, classify(15) returns 'Fizz' "
                  "but must return 'FizzBuzz' — the modulo checks are in "
                  "the wrong order. Fix fizzbuzz_order.py only. Verify "
                  "with: python3 -m pytest test_fizzbuzz_order.py -q"),
    },
    "sum-off-by-one": {
        "broken": '''def sum_to(n: int) -> int:
    """Sum integers 1..n inclusive."""
    total = 0
    for i in range(1, n):
        total += i
    return total
''',
        "tests": '''from sum_off_by_one import sum_to

def test_one():
    assert sum_to(1) == 1

def test_ten():
    assert sum_to(10) == 55

def test_hundred():
    assert sum_to(100) == 5050
''',
        "module": "sum_off_by_one.py",
        "testfile": "test_sum_off_by_one.py",
        "brief": ("BUG: in sum_off_by_one.py, sum_to(10) returns 45 not "
                  "55 — the range excludes n. Fix sum_off_by_one.py only. "
                  "Verify with: python3 -m pytest test_sum_off_by_one.py -q"),
    },
    "palindrome-edge": {
        "broken": '''def is_palindrome(s: str) -> bool:
    """True if s reads the same forwards, ignoring case/punctuation."""
    cleaned = "".join(ch.lower() for ch in s if ch.isalnum())
    return cleaned == cleaned.reverse()
''',
        "tests": '''from palindrome_edge import is_palindrome

def test_simple():
    assert is_palindrome("racecar") is True

def test_not():
    assert is_palindrome("hello") is False

def test_mixed():
    assert is_palindrome("A man, a plan, a canal: Panama") is True

def test_empty():
    assert is_palindrome("") is True
''',
        "module": "palindrome_edge.py",
        "testfile": "test_palindrome_edge.py",
        "brief": ("BUG: in palindrome_edge.py, is_palindrome crashes with "
                  "AttributeError — str has no .reverse(). Fix "
                  "palindrome_edge.py only (hint: [::-1]). Verify with: "
                  "python3 -m pytest test_palindrome_edge.py -q"),
    },
    "sort-stability": {
        "broken": '''def by_rank(items):
    """Sort (name, score) tuples by score DESC, stable on name ASC."""
    return sorted(items, key=lambda t: t[1])
''',
        "tests": '''from sort_stability import by_rank

def test_desc():
    assert by_rank([("a", 3), ("b", 1)]) == [("a", 3), ("b", 1)]

def test_stable():
    rows = [("b", 2), ("a", 2), ("c", 1)]
    assert by_rank(rows) == [("a", 2), ("b", 2), ("c", 1)]

def test_empty():
    assert by_rank([]) == []
''',
        "module": "sort_stability.py",
        "testfile": "test_sort_stability.py",
        "brief": ("BUG: in sort_stability.py, by_rank sorts ascending and "
                  "ignores the name tiebreak. It must sort score "
                  "DESCENDING with name ASCENDING among equal scores. Fix "
                  "sort_stability.py only. Verify with: python3 -m pytest "
                  "test_sort_stability.py -q"),
    },
}
