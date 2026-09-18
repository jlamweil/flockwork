"""T2 oracle tests (swarm lane): FileLock must release on context exit
and be re-acquirable. Seeded state: test 2 FAILS (fd leaked, never
unlocked). Workers must make both pass WITHOUT editing this file."""
import os
import tempfile

from tools.flockbug import FileLock


def test_first_acquire_works():
    with tempfile.NamedTemporaryFile() as f:
        with FileLock(f.name):
            pass
        assert True  # reaching here at all means no exception


def test_reacquire_after_release():
    with tempfile.NamedTemporaryFile() as f:
        with FileLock(f.name):
            pass
        with FileLock(f.name):  # BUG path: BlockingIOError here
            pass
        assert True
