import pytest

from sumto import sum_to

XF = "frozen c1 fixture: deliberately-buggy design-era artifact preserved as-is (C1 survey ledger fixed:false); INT-025 2026-09-28"

@pytest.mark.xfail(strict=True, reason=XF)
def test_one():
    assert sum_to(1) == 1

@pytest.mark.xfail(strict=True, reason=XF)
def test_ten():
    assert sum_to(10) == 55

def test_zero():
    assert sum_to(0) == 0
