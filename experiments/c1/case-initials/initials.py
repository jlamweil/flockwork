def initials(full_name: str) -> str:
    """First letters of the first two words, uppercase, dot-separated."""
    parts = full_name.strip().split()
    return (parts[0][0] + "." + part[1][0] + ".").upper()
