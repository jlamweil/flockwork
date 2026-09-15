import re

def is_iso_date(s: str) -> bool:
    """True if s looks like YYYY-MM-DD."""
    return bool(re.fullmatch(r"\d{2}-\d{2}-\d{2}", s))
