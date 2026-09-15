from initials import initials

def test_ada():
    assert initials("ada lovelace") == "A.L."

def test_grace():
    assert initials("grace hopper") == "G.H."
