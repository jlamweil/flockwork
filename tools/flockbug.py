"""Seeded swarm task T2: flock release bug (do not fix by hand — this
file is the task fixture for the swarm lane; workers must fix it)."""

import fcntl
import os


class FileLock:
    """Context-manager flock. BUG: acquire() stores the fd but never
    unlocks on exit, and re-entering raises BlockingIOError."""

    def __init__(self, path):
        self.path = path
        self.fd = None

    def acquire(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self):
        # BUG: implemented but never called by __exit__
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
