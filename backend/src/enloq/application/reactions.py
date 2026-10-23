"""The owner's native reactions on exact messages (ADR 26).

Choosing or removing a reaction is a native action through the account's
existing session owner. It is never inference, never touches a draft and never
guesses the account's palette or reaction limit.
"""

import asyncio
import json

from ..domain.models import Reaction, Refused

PALETTE = "reaction_palette"
PALETTE_TTL = 86400


class Reactions:
    def __init__(self, app):
        self.app, self.store, self.transport = app, app.store, app.transport

    async def options(self):
        """Telegram's selectable ordinary emoji and the account's limit."""
        rows = self.store.rows("SELECT value FROM cursors WHERE key=?", (PALETTE,))
        cached = json.loads(rows[0]["value"]) if rows else None
        if cached and self.store.clock() - cached["fetched"] < PALETTE_TTL:
            return {k: cached[k] for k in ("emoji", "limit")}
        if not self.transport.connected:
            raise Refused("Telegram disconnected; reactions are unavailable")
        try:
            async with asyncio.timeout(60):
                value = await self.transport.reaction_options()
        except (Refused, asyncio.CancelledError):
            raise
        except Exception:
            raise Refused("Telegram did not supply reactions right now") from None
        value = {
            "emoji": list(value["emoji"]),
            "limit": int(value["limit"]),
            "fetched": self.store.clock(),
        }
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                (PALETTE, json.dumps(value)),
            )
        return {k: value[k] for k in ("emoji", "limit")}

    def target(self, key, identity):
        chat = self.store.chat(key)
        if chat.kind != "private":
            raise Refused("Only private conversations are supported")
        message = self.store.message(key, identity)
        if message is None or message.deleted or message.service:
            raise Refused("Return to a current original to react")
        return chat, message

    async def choose(self, key, identity, emoji, selected):
        """Add (``selected``) or remove one ordinary emoji on one exact source,
        keeping the owner's other reactions within Telegram's limit."""
        chat, message = self.target(key, identity)
        if not self.transport.connected:
            raise Refused("Telegram disconnected; reactions are unavailable")
        options = await self.options()
        if selected and emoji not in options["emoji"]:
            raise Refused("This reaction is not available for this account")
        snapshot = self.store.reactions.snapshot(message.key)
        own = snapshot.own() if snapshot else None
        if own is None and (not selected or options["limit"] > 1):
            # Which reactions are the owner's is unknown: read only this message.
            try:
                async with asyncio.timeout(60):
                    fresh = await self.transport.exact(chat.peer, identity)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise Refused("Telegram could not be checked right now") from None
            if (
                fresh is None
                or fresh.deleted
                or fresh.account_id != chat.account_id
                or fresh.peer != chat.peer
                or fresh.message_id != identity
            ):
                raise Refused("Return to a current original to react")
            self.app.sync.ingest(fresh, target=False)
            snapshot = self.store.reactions.snapshot(message.key)
            own = snapshot.own() if snapshot else []
            if own is None:
                raise Refused("Telegram did not say which reactions are yours")
        own = [r for r in own or [] if not r.paid]
        reaction = Reaction(emoji=emoji)
        if selected:
            if reaction in own:
                return self.store.reactions.view(message.key)
            # As in native clients, a new choice replaces the oldest at the limit.
            chosen = (own + [reaction])[-options["limit"] :]
        else:
            if reaction not in own:
                return self.store.reactions.view(message.key)
            chosen = [r for r in own if r != reaction]
        try:
            async with asyncio.timeout(60):
                result = await self.transport.send_reaction(chat.peer, identity, chosen)
        except (Refused, asyncio.CancelledError):
            raise
        except Exception:
            raise Refused(
                "Telegram did not confirm the reaction; check the message"
            ) from None
        if result is not None:
            self.store.reactions.apply(message.key, result)
        return self.store.reactions.view(message.key)
