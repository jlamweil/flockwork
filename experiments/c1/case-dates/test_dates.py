import pytest

from dates import is_iso_date

XF = "frozen c1 fixture: deliberately-buggy design-era artifact preserved as-is (C1 survey ledger fixed:false); INT-025 2026-09-28"

@pytest.mark.xfail(strict=True, reason=XF)
def test_full_year():
    assert is_iso_date("2026-09-15") is True

@pytest.mark.xfail(strict=True, reason=XF)
def test_short_year_rejected():
    assert is_iso_date("26-09-15") is False

def test_slashes_rejected():
    assert is_iso_date("2026/09/15") is False
