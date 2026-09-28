import pytest

from tags import add_tag

XF = "frozen c1 fixture: deliberately-buggy design-era artifact preserved as-is (C1 survey ledger fixed:false); INT-025 2026-09-28"

def test_first_call_fresh():
    assert add_tag("a") == ["a"]

@pytest.mark.xfail(strict=True, reason=XF)
def test_second_call_fresh():
    assert add_tag("b") == ["b"]
