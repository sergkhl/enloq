"""One global limit on concurrent model calls, served by priority (ADR 15).

Classes, highest first: new incoming work (including a
per-block Rewrite or Retry click), unread catch-up, Reaction Assessment, the
chat-level older run, response suggestions, group briefings and chat-memory
updates. Memory is admitted only while communication is idle. With a limit of 2
or more the four lowest classes never take the last free slot; at 1, work runs strictly by
priority.
"""

import asyncio
import contextlib
from collections import Counter

from ..domain.models import ProcessingPaused

# All private roles precede group briefings, which precede idle memory (ADR 34).
CLASSES = (
    "new",
    "catchup",
    "reaction",
    "older",
    "suggestions",
    "group",
    "memory",
)
RANK = {name: rank for rank, name in enumerate(CLASSES)}
LOW = frozenset({"memory", "older", "suggestions", "group"})
DEFAULT_LIMIT = 4
MAX_LIMIT = 8


def rank_sql(column="payload"):
    """SQL ordering a job by its class; unmarked jobs are new work."""
    cases = " ".join(f"WHEN '{name}' THEN {rank}" for name, rank in RANK.items())
    return f"CASE json_extract({column},'$.class') {cases} ELSE {RANK['new']} END"


class Capacity:
    def __init__(self, limit=lambda: DEFAULT_LIMIT):
        self.limit = limit  # Read on every decision: a new limit needs no restart.
        self.running = 0
        self.classes = Counter()
        self._waiters: list[tuple[int, int, str, asyncio.Future]] = []
        self._order = 0

    def admits(self, klass, *, running=None):
        limit = max(1, min(MAX_LIMIT, int(self.limit())))
        free = limit - (self.running if running is None else running)
        if free <= 0:
            return False
        if klass == "memory" and (self.running or self._waiters):
            return False
        return not (klass in LOW and limit >= 2 and free < 2)

    @contextlib.asynccontextmanager
    async def slot(self, klass="new"):
        await self._acquire(klass)
        try:
            yield
        finally:
            self.running -= 1
            self.classes[klass] -= 1
            self.wake()

    async def _acquire(self, klass):
        if klass == "memory":
            if not self.admits(klass):
                raise ProcessingPaused("Waiting for idle memory capacity")
            self.running += 1
            self.classes[klass] += 1
            return
        self._order += 1
        future = asyncio.get_running_loop().create_future()
        entry = (RANK.get(klass, RANK["new"]), self._order, klass, future)
        self._waiters.append(entry)
        self._waiters.sort(key=lambda w: w[:2])
        self.wake()
        try:
            await future
        except BaseException:
            if entry in self._waiters:
                self._waiters.remove(entry)
            elif future.done() and not future.cancelled():
                self.running -= 1  # Granted as we were cancelled: give it back.
                self.classes[klass] -= 1
                self.wake()
            raise

    def wake(self):
        """Grant waiting slots in priority order, e.g. after a higher limit."""
        for entry in list(self._waiters):
            if not self.admits(entry[2]):
                if entry[2] in LOW:
                    continue  # A low class may not block others behind it.
                break
            self._waiters.remove(entry)
            if not entry[3].done():
                self.running += 1
                self.classes[entry[2]] += 1
                entry[3].set_result(None)
