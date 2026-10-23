"""Replied-to messages for Reply Quotes (ADR 41).

The message cache answers first. In private chats only, one bounded exact read
fetches the replied-to messages that are not cached and rereads replies captured
before their header details existed. Messages from the last 30 days join the
cache, as the composer's reply-target read already does; older ones are returned
without being stored. Group chats never read Telegram for a quote.
"""

import asyncio

from ..domain.models import REPLY_DETAILS, Refused, reply_target
from ..storage.store import RETENTION

LIMIT = 100
RETRY = 60


class Replies:
    def __init__(self, app):
        self.app = app
        self.store, self.transport = app.store, app.transport
        self.lock = asyncio.Lock()
        # A failed read is retried after a minute; a read that succeeded without
        # a message proves it unavailable until the service restarts.
        self.failed = {}
        self.absent = set()

    def _cached(self, key, identity, group):
        message = self.store.message(key, identity)
        if message is None or (group and message.date < self.store.clock() - RETENTION):
            return None
        return message

    async def _read(self, chat, replies):
        """One exact read of what the cache lacks; returns the messages it
        found that are too old to store."""
        key = chat.key
        now = self.store.clock()
        self.failed = {k: t for k, t in self.failed.items() if t > now}
        legacy = [
            m.message_id
            for m in replies
            if m.reply_to and not m.reply_read and not m.deleted
        ]
        # A legacy reply's stored number may name another chat's message; it is
        # read in the same request and kept only if the reply confirms it.
        targets = [
            t
            for m in replies
            if (t := reply_target(m)) and self.store.message(key, t) is None
        ]
        wanted = [
            i
            for i in dict.fromkeys(legacy + targets)
            if f"{key}/{i}" not in self.absent
            and self.failed.get(f"{key}/{i}", 0) <= now
        ][:LIMIT]
        if not wanted:
            return {}
        try:
            async with asyncio.timeout(60):
                page = await self.transport.exact_many(chat.peer, wanted)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Reading stays available while account recovery owns connectivity.
            self.failed.update(
                (f"{key}/{i}", self.store.clock() + RETRY) for i in wanted
            )
            return {}
        found = {
            m.message_id: m
            for m in page
            if m.account_id == self.store.account_id
            and m.peer == chat.peer
            and m.message_id in wanted
        }
        self.absent.update(f"{key}/{i}" for i in wanted if i not in found)
        for identity in legacy:
            if identity in found:
                self.store.learn_reply(found[identity])
        current = {
            reply_target(m)
            for i in (m.message_id for m in replies)
            if (m := self.store.message(key, i))
        }
        transient = {}
        for identity, message in found.items():
            if identity not in current or self.store.message(key, identity):
                continue
            if message.date >= self.store.clock() - RETENTION:
                self.app.sync.ingest(message, target=False)
            else:
                transient[identity] = message
        return transient

    async def resolve(self, key, identities):
        if not identities or len(identities) > LIMIT or any(i <= 0 for i in identities):
            raise Refused("Request between 1 and 100 cached message IDs")
        chat = self.store.chat(key)
        if chat.kind not in {"private", "group", "saved"}:
            raise Refused(
                "Only existing private conversations and groups are supported"
            )
        group = chat.kind == "group"
        identities = list(dict.fromkeys(identities))
        transient = {}
        async with self.lock:
            replies = [m for i in identities if (m := self._cached(key, i, group))]
            if not group:
                transient = await self._read(chat, replies)
            replies = [m for i in identities if (m := self._cached(key, i, group))]
            targets, missing, pending = [], [], []
            for identity in dict.fromkeys(t for m in replies if (t := reply_target(m))):
                target = self._cached(key, identity, group) or transient.get(identity)
                if target is not None:
                    targets.append(target)
                elif group or f"{key}/{identity}" in self.absent:
                    missing.append(identity)
                else:
                    pending.append(identity)
            annotate = (
                self.app.briefings.annotate if group else self.store.history.annotate
            )
            # A deleted target quotes its captured content (ADR 52 Q10); one
            # deleted before its content was seen is missing.
            shown = annotate(targets, quotes=True) if targets else []
            present = {value["message_id"] for value in shown}
            missing += [t.message_id for t in targets if t.message_id not in present]
            return {
                "replies": [
                    {
                        "message_id": m.message_id,
                        "reply_to": m.reply_to,
                        **{
                            field: getattr(m, field)
                            for field in REPLY_DETAILS
                            if field != "reply_elsewhere"
                        },
                        "reply_elsewhere": m.reply_elsewhere.model_dump()
                        if m.reply_elsewhere
                        else None,
                    }
                    for m in replies
                ],
                "targets": shown,
                "missing": missing,
                "pending": pending,
            }
