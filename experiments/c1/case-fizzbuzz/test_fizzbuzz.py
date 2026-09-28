import pytest

from fizzbuzz import classify

XF = "frozen c1 fixture: deliberately-buggy design-era artifact preserved as-is (C1 survey ledger fixed:false); INT-025 2026-09-28"

@pytest.mark.xfail(strict=True, reason=XF)
def test_fifteen_is_fizzbuzz():
    assert classify(15) == "FizzBuzz"

def test_three_is_fizz():
    assert classify(3) == "Fizz"

def test_five_is_buzz():
    assert classify(5) == "Buzz"

def test_seven_is_plain():
    assert classify(7) == "7"
