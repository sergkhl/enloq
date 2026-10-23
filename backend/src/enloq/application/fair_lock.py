"""One holder at a time, handed to waiting members in turn (ADR 48 Q14).

Transcription runs one at a time for the whole service. A member with several
notes waiting gets one turn and then waits behind every other member who is
waiting, so one member's backlog never holds another's note for long.
"""

import asyncio
from collections import deque


class FairLock:
    def __init__(self):
        self._held = False
        self._waiting: dict[str, deque[asyncio.Future]] = {}
        # Members with waiters, in the order their next turn comes.
        self._turns: deque[str] = deque()

    def locked(self):
        return self._held

    def handle(self, member):
        return _Handle(self, member)

    async def acquire(self, member):
        if not self._held and not self._turns:
            self._held = True
            return
        future = asyncio.get_running_loop().create_future()
        queue = self._waiting.setdefault(member, deque())
        if not queue:
            self._turns.append(member)
        queue.append(future)
        try:
            await future
        except BaseException:
            if future.done() and not future.cancelled():
                self.release()  # Granted as it was cancelled: pass it on.
            else:
                queue.remove(future)
                if not queue:
                    del self._waiting[member]
                    self._turns.remove(member)
            raise

    def release(self):
        while self._turns:
            member = self._turns.popleft()
            queue = self._waiting[member]
            future = queue.popleft()
            if queue:
                self._turns.append(member)  # Their next note waits its turn.
            else:
                del self._waiting[member]
            if not future.done():
                future.set_result(None)
                return  # Still held, now by that waiter.
        self._held = False


class _Handle:
    """A member's view of the shared lock, used like an asyncio.Lock."""

    def __init__(self, lock, member):
        self.lock, self.member = lock, member

    def locked(self):
        return self.lock.locked()

    async def acquire(self):
        await self.lock.acquire(self.member)
        return True

    def release(self):
        self.lock.release()

    async def __aenter__(self):
        await self.lock.acquire(self.member)
        return self

    async def __aexit__(self, *exc):
        self.lock.release()
