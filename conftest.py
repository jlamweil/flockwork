"""Session fixtures for the flockwork test suite.

The tests drive real `git` operations in scratch repos. CI runners have no
global git identity, so default the author/committer identity for the whole
session without clobbering a developer's already-configured identity.
"""
import os

_identities = (
    ("GIT_AUTHOR_NAME", "flockwork-test"),
    ("GIT_AUTHOR_EMAIL", "flockwork-test@example.com"),
    ("GIT_COMMITTER_NAME", "flockwork-test"),
    ("GIT_COMMITTER_EMAIL", "flockwork-test@example.com"),
)
for _name, _value in _identities:
    os.environ.setdefault(_name, _value)
