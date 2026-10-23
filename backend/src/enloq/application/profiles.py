"""The Correspondent Profile (ADR 46 Q11): a private correspondent's
Telegram username, a phone Telegram shares and bio, read on each opening
through the account's session owner and stored nowhere. The Group Profile
(ADR 47 Q7) reads a group's title, public username, member count and
description the same way, and a group member's profile (ADR 51 Q14) the
person's, for a member the group's cached messages name as an author."""

import asyncio
import re

from ..domain.models import Refused
from ..storage.store import RETENTION


class Profiles:
    def __init__(self, app):
        self.store, self.transport = app.store, app.transport

    async def read(self, key):
        chat = self.store.chat(key)
        if chat.kind != "private" or not chat.peer.startswith("user:"):
            raise Refused("Only private conversations have a profile")
        if not self.transport.connected:
            raise Refused("Telegram disconnected; the profile is unavailable")
        try:
            async with asyncio.timeout(30):
                value = await self.transport.profile(chat.peer)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The transport's own reasons stay in its logs, not the profile.
            raise Refused("Telegram did not supply this profile right now") from None
        name = " ".join(
            part for part in (value.get("first_name"), value.get("last_name")) if part
        )
        return {
            "name": name or chat.title,
            "username": value.get("username") or None,
            "phone": value.get("phone") or None,
            "bio": value.get("bio") or None,
        }

    async def read_member(self, key, member):
        """A group member's profile, opened from their avatar or name: no
        emotion or memory, only what Telegram supplies now."""
        chat = self.store.chat(key)
        if chat.kind != "group":
            raise Refused("Only groups have members")
        if not re.fullmatch(r"user:[1-9][0-9]{0,19}", member) or not self.store.rows(
            "SELECT 1 FROM messages WHERE chat_key=? AND date>=? AND json_extract(body,'$.author_peer')=? LIMIT 1",
            (key, self.store.clock() - RETENTION, member),
        ):
            raise Refused("This member isn't among the group's recent authors")
        if not self.transport.connected:
            raise Refused("Telegram disconnected; the profile is unavailable")
        try:
            async with asyncio.timeout(30):
                value = await self.transport.profile(member)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise Refused("Telegram did not supply this profile right now") from None
        name = " ".join(
            part for part in (value.get("first_name"), value.get("last_name")) if part
        )
        return {
            "name": name or "Group member",
            "username": value.get("username") or None,
            "phone": value.get("phone") or None,
            "bio": value.get("bio") or None,
            "photo_id": self.store.author_photo(member),
        }

    async def read_group(self, key):
        chat = self.store.chat(key)
        if chat.kind != "group":
            raise Refused("Only groups have a group profile")
        state = self.store.groups.get(key)
        if not state.supported or state.access != "available":
            raise Refused("Group access was lost. Cached details remain.")
        if not self.transport.connected:
            raise Refused("Telegram disconnected; the profile is unavailable")
        try:
            async with asyncio.timeout(30):
                value = await self.transport.group_profile(chat.peer)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise Refused(
                "Telegram did not supply this group's profile right now"
            ) from None
        members = value.get("members")
        return {
            "title": value.get("title") or chat.title,
            "username": value.get("username") or None,
            "members": members if isinstance(members, int) and members >= 0 else None,
            "description": value.get("description") or None,
        }
