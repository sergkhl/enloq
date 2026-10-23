"""Bound child lifetime even if the application is forcibly terminated."""

import fcntl
import os
import signal
import subprocess
import sys
import time


def main():
    parent, timeout, mutex, *command = sys.argv[1:]
    parent, deadline = int(parent), time.monotonic() + float(timeout)
    fd = None
    if mutex != "-":
        fd = os.open(mutex, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 73
    process = subprocess.Popen(
        command,
        stdin=sys.stdin.buffer,
        stdout=sys.stdout.buffer,
        stderr=sys.stderr.buffer,
    )
    try:
        while process.poll() is None:
            if os.getppid() != parent or time.monotonic() >= deadline:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            time.sleep(0.1)
        return process.returncode
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
