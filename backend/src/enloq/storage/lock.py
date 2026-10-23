"""OS-released exclusive ownership; the lock file is never deleted on release."""

import fcntl
import os
from pathlib import Path

from ..config import private_directory
from ..domain.models import Refused


class OwnerLock:
    def __init__(self, path: Path):
        self.path, self.fd = path, None

    def acquire(self):
        private_directory(self.path.parent)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            os.close(fd)
            raise Refused("Session or service already has an owner") from error
        self.fd = fd
        return self

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.close()
