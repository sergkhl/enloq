"""Bounded, reading-only repair of cached forwarding metadata (ADR 27)."""

import asyncio

from ..domain.forwarding import details_missing
from ..domain.models import Refused
from ..storage.store import RETENTION
from .group_access import native_access

FIELDS = (
    "forwarded",
    "forwarded_from",
    "forward_date",
    "forward_signature",
    "imported",
    "forward_read",
)


class Forwarding:
    def __init__(self, store, transport):
        self.store, self.transport = store, transport
        self.lock = asyncio.Lock()
        self.retry_after = {}

    def _learn(self, peer, wanted, page):
        for message in page:
            if (
                message.account_id == self.store.account_id
                and message.peer == peer
                and message.message_id in wanted
            ):
                self.store.learn_forwarding(message)

    def _native_guard(self, chat, revision=None):
        if chat.kind != "group":
            return None
        if not any(row["key"] == chat.key for row in self.store.recent_chats("group")):
            raise Refused("Inactive group forwarding remains cache-only")
        return native_access(self.store, chat.key, revision)

    async def refresh(self, key, identities):
        if not identities or len(identities) > 100 or any(i <= 0 for i in identities):
            raise Refused("Request between 1 and 100 cached message IDs")
        chat = self.store.chat(key)
        identities = list(dict.fromkeys(identities))
        async with self.lock:
            now = self.store.clock()
            self.retry_after = {k: t for k, t in self.retry_after.items() if t > now}
            wanted = [
                m.message_id
                for i in identities
                if (m := self.store.message(key, i))
                and details_missing(m)
                and not m.deleted
                and m.date >= now - RETENTION
                and self.retry_after.get(m.key, 0) <= now
            ]
            if wanted:
                # Shared across tabs; failures/missing rows never become False.
                try:
                    access_revision = self._native_guard(chat)
                    async with asyncio.timeout(60):
                        page = await self.transport.exact_many(chat.peer, wanted)
                    self._native_guard(chat, access_revision)
                    self._learn(chat.peer, wanted, page)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Keep reading available while normal account recovery owns
                    # connectivity. The UI can retry missing attribution later.
                    pass
                finally:
                    self.retry_after.update(
                        (f"{key}/{i}", self.store.clock() + 60) for i in wanted
                    )
            return [
                {"message_id": m.message_id, **{f: getattr(m, f) for f in FIELDS}}
                for i in identities
                if (m := self.store.message(key, i))
                and (chat.kind != "group" or m.date >= self.store.clock() - RETENTION)
            ]

    async def intake(self, message, *, deadline=25):
        """One exact read of a live forward that arrived without a source name,
        inside its quiet seconds, so new work can attribute it. A failed read
        never delays processing; the fill-in invalidates nothing."""
        if self.retry_after.get(message.key, 0) > self.store.clock():
            return
        self.retry_after[message.key] = self.store.clock() + 60
        try:
            async with asyncio.timeout(deadline):
                page = await self.transport.exact_many(
                    message.peer, [message.message_id]
                )
            self._learn(message.peer, [message.message_id], page)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
