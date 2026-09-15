from dates import is_iso_date

def test_full_year():
    assert is_iso_date("2026-09-15") is True

def test_short_year_rejected():
    assert is_iso_date("26-09-15") is False

def test_slashes_rejected():
    assert is_iso_date("2026/09/15") is False
