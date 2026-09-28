import pytest

from initials import initials

XF = "frozen c1 fixture: deliberately-buggy design-era artifact preserved as-is (C1 survey ledger fixed:false); INT-025 2026-09-28"

@pytest.mark.xfail(strict=True, reason=XF)
def test_ada():
    assert initials("ada lovelace") == "A.L."

@pytest.mark.xfail(strict=True, reason=XF)
def test_grace():
    assert initials("grace hopper") == "G.H."
