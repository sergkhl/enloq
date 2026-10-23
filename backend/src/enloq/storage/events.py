"""Content-free wakeups after local commits; SQLite owns the change cursors."""

import asyncio
import threading
from contextlib import contextmanager


class CommitEvents:
    def __init__(self):
        self._listeners = set()
        self._lock = threading.Lock()
        # Advances with every changing local commit that a derived answer could
        # have read from: an unchanged value proves nothing it read was written.
        self.sequence = 0

    @contextmanager
    def subscribe(self):
        event = asyncio.Event()
        listener = (asyncio.get_running_loop(), event)
        with self._lock:
            self._listeners.add(listener)
        try:
            yield event
        finally:
            with self._lock:
                self._listeners.discard(listener)

    def publish(self, *, quiet=False):
        # HTTP handlers also commit from worker threads. Schedule the wake on
        # each subscriber's loop; no correspondence or SQLite connection crosses.
        # A quiet commit wrote only rows its writer knows no derived answer
        # reads; browsers still hear of it.
        with self._lock:
            self.sequence += not quiet
            listeners = tuple(self._listeners)
        for loop, event in listeners:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass  # The subscriber's event loop already closed.


commits = CommitEvents()
