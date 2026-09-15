#!/usr/bin/env python3
"""c6 fixtures — EIGHT FRESH bug-fix tasks, never run earlier tonight.

Purpose (see FREEZE.md, committed before any run): independent evidence
units for the worker lane's Wilson ladder. These are NOT reruns of the
c2 five — new code, new bugs, same micro-difficulty class (one seeded
defect, one-line-class fix, pytest-decided), so the lane's task mix
stays comparable while every attempt is a first execution.

Bug taxonomy spread mirrors c2's (crash, off-by-one, wrong-operator,
wrong-slice, incomplete-set, first-vs-last, logic-swap, missing-split).
"""
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys_c2 = os.path.join(HERE, "..", "c2")
import sys  # noqa: E402
sys.path.insert(0, sys_c2)
from c2_driver import IMAGE, MODEL, OPENCODE, AUTH  # noqa: E402,F401

FRESH_BUGS = {
    "palindrome": (
        "palindrome.py",
        'def is_palindrome(s: str) -> bool:\n'
        '    """True if s reads the same forwards and backwards."""\n'
        '    return s == s[-1:0:-1]\n',
        {"test_palindrome.py":
         'from palindrome import is_palindrome\n\n'
         'def test_odd():\n    assert is_palindrome("racecar") is True\n\n'
         'def test_even():\n    assert is_palindrome("abba") is True\n\n'
         'def test_not():\n    assert is_palindrome("road") is False\n\n'
         'def test_empty():\n    assert is_palindrome("") is True\n\n'
         'def test_single():\n    assert is_palindrome("a") is True\n'},
    ),
    "clamp": (
        "clamp.py",
        'def clamp(v: int, lo: int, hi: int) -> int:\n'
        '    """Restrict v to the inclusive range [lo, hi]."""\n'
        '    return max(hi, min(v, lo))\n',
        {"test_clamp.py":
         'from clamp import clamp\n\n'
         'def test_inside():\n    assert clamp(5, 0, 10) == 5\n\n'
         'def test_below():\n    assert clamp(-1, 0, 10) == 0\n\n'
         'def test_above():\n    assert clamp(11, 0, 10) == 10\n\n'
         'def test_edges():\n    assert clamp(0, 0, 0) == 0\n'},
    ),
    "average": (
        "average.py",
        'def average(nums: list[int]) -> float:\n'
        '    """Arithmetic mean of nums."""\n'
        '    return sum(nums) // len(nums)\n',
        {"test_average.py":
         'from average import average\n\n'
         'def test_whole():\n    assert average([1, 2, 3]) == 2.0\n\n'
         'def test_fraction():\n    assert average([1, 2]) == 1.5\n\n'
         'def test_single():\n    assert average([5]) == 5.0\n'},
    ),
    "revwords": (
        "revwords.py",
        'def reverse_words(s: str) -> str:\n'
        '    """Reverse the order of words in s (single-spaced words)."""\n'
        '    return s[::-1]\n',
        {"test_revwords.py":
         'from revwords import reverse_words\n\n'
         'def test_two():\n'
         '    assert reverse_words("hello world") == "world hello"\n\n'
         'def test_three():\n    assert reverse_words("a b c") == "c b a"\n\n'
         'def test_single():\n    assert reverse_words("solo") == "solo"\n'},
    ),
    "vowels": (
        "vowels.py",
        'def count_vowels(s: str) -> int:\n'
        '    """Count the vowels (aeiou) in s, case-insensitively."""\n'
        '    return sum(1 for c in s.lower() if c in "aei")\n',
        {"test_vowels.py":
         'from vowels import count_vowels\n\n'
         'def test_word():\n    assert count_vowels("education") == 5\n\n'
         'def test_none():\n    assert count_vowels("xyz") == 0\n\n'
         'def test_empty():\n    assert count_vowels("") == 0\n\n'
         'def test_upper():\n    assert count_vowels("AEIOU") == 5\n'},
    ),
    "lastindex": (
        "lastindex.py",
        'def last_index(nums: list[int], x: int) -> int:\n'
        '    """Index of the LAST occurrence of x in nums, or -1 if absent."""\n'
        '    try:\n'
        '        return nums.index(x)\n'
        '    except ValueError:\n'
        '        return -1\n',
        {"test_lastindex.py":
         'from lastindex import last_index\n\n'
         'def test_repeat():\n    assert last_index([1, 2, 2, 3], 2) == 2\n\n'
         'def test_absent():\n    assert last_index([1, 2, 3], 9) == -1\n\n'
         'def test_single():\n    assert last_index([7], 7) == 0\n\n'
         'def test_all_same():\n    assert last_index([1, 1, 1], 1) == 2\n'},
    ),
    "evens": (
        "evens.py",
        'def evens(n: int) -> list[int]:\n'
        '    """Even numbers from 0 to n inclusive (when n is even)."""\n'
        '    return list(range(1, n, 2))\n',
        {"test_evens.py":
         'from evens import evens\n\n'
         'def test_six():\n    assert evens(6) == [0, 2, 4, 6]\n\n'
         'def test_five():\n    assert evens(5) == [0, 2, 4]\n\n'
         'def test_zero():\n    assert evens(0) == [0]\n\n'
         'def test_one():\n    assert evens(1) == [0]\n'},
    ),
    "lookup": (
        "lookup.py",
        'def lookup(d: dict, key: str, default=None):\n'
        '    """d[key] if present, else default (None when omitted)."""\n'
        '    return d[key]\n',
        {"test_lookup.py":
         'from lookup import lookup\n\n'
         'def test_present():\n    assert lookup({"a": 1}, "a") == 1\n\n'
         'def test_default():\n    assert lookup({"a": 1}, "b", 0) == 0\n\n'
         'def test_none_default():\n    assert lookup({"a": 1}, "z") is None\n'},
    ),
}

GITIGNORE = ".pytest_cache/\n__pycache__/\n"


def make_fixture(name: str, ws: str) -> tuple[str, str]:
    """c2's proven recipe (a)+(b) over FRESH_BUGS: workspace pre-built on
    host by uid 1004 BEFORE the mount, git-inited, tests must start
    failing (a fixture that passes would fake a verdict)."""
    shutil.rmtree(ws, ignore_errors=True)
    os.makedirs(ws)  # uid 1004, before docker ever sees it
    target, bug_src, tests = FRESH_BUGS[name]
    with open(os.path.join(ws, target), "w") as f:
        f.write(bug_src)
    for fn, src in tests.items():
        with open(os.path.join(ws, fn), "w") as f:
            f.write(src)
    with open(os.path.join(ws, ".gitignore"), "w") as f:
        f.write(GITIGNORE)
    env = dict(os.environ,
               GIT_AUTHOR_NAME="c", GIT_AUTHOR_EMAIL="c@c",
               GIT_COMMITTER_NAME="c", GIT_COMMITTER_EMAIL="c@c")
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=ws,
                   check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", f"fixture: {name}"], cwd=ws,
                   check=True, env=env)
    base = subprocess.run(["python3", "-m", "pytest", "-q"], cwd=ws,
                          capture_output=True, text=True)
    assert base.returncode != 0, f"{name}: fixture must start failing"
    return ws, target
