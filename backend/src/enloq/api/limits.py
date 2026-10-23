"""Refusal limits for the public sign-in surface of a VPS service (ADR 40).

Only refused attempts count, so a browser polling its own pairing is never
slowed. The client address comes from X-Forwarded-For only when the immediate
peer is the configured edge proxy; any other peer is keyed by its own address.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections import deque

REFUSALS = 30
WINDOW = 600
RESOLVE_AGE = 60


class RefusalLimit:
    def __init__(self, proxy: str, *, clock=time.monotonic):
        self.proxy = proxy
        self.clock = clock
        self.refusals: dict[str, deque[float]] = {}
        self.proxy_addresses: set[str] = set()
        self.resolved_at = float("-inf")

    async def client(self, peer: str, forwarded: str) -> str:
        if self.proxy and self.clock() - self.resolved_at >= RESOLVE_AGE:
            try:
                infos = await asyncio.to_thread(socket.getaddrinfo, self.proxy, None)
                self.proxy_addresses = {info[4][0] for info in infos}
            except OSError:
                self.proxy_addresses = set()
            self.resolved_at = self.clock()
        if peer in self.proxy_addresses:
            # The edge replaces untrusted forwarding headers, so its last
            # entry is the address that connected to it.
            last = forwarded.rsplit(",", 1)[-1].strip()
            if last:
                return last
        return peer

    def _recent(self, address: str) -> deque[float]:
        entries = self.refusals.get(address)
        if entries is None:
            return deque()
        while entries and self.clock() - entries[0] >= WINDOW:
            entries.popleft()
        if not entries:
            del self.refusals[address]
        return entries

    def blocked(self, address: str) -> int:
        """Seconds until the next attempt may be made, or 0."""
        entries = self._recent(address)
        if len(entries) < REFUSALS:
            return 0
        return max(1, int(WINDOW - (self.clock() - entries[0])))

    def record(self, address: str):
        if len(self.refusals) > 10_000:
            for key in list(self.refusals):
                self._recent(key)
        self.refusals.setdefault(address, deque()).append(self.clock())
