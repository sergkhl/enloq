"""New Message (ADR 51 Q7, Q16-Q18): Telegram's way to start writing.

The search lists the account's existing private chats, newest first and older
than seven days included, then its Telegram contacts it has never chatted
with; typed text filters both by name and @username and adds Telegram's own
people search, which finds public usernames. People only: no bots, channels,
groups or phone numbers. Choosing someone admits their private chat without
making it a Recent Chat: its latest history is read as originals, nothing in
it goes to a model, and it joins the list once a message is sent or received
there. A member's account may use it, as their own request (ADR 48).
"""

import asyncio
import json

from ..domain.models import Chat, Refused

# Contacts are read from Telegram at most once a minute while someone types.
CONTACTS_AGE = 60
CHATS = 200
GLOBAL_QUERY = 3


def matches(person, needle):
    return not needle or any(
        needle in (value or "").lower()
        for value in (person["name"], person.get("username"))
    )


class NewMessage:
    def __init__(self, app):
        self.app = app
        self.store, self.transport = app.store, app.transport
        self._contacts = None
        self._read = 0.0

    def chats(self):
        """The account's private chats, newest first, whatever their age."""
        rows = self.store.rows(
            "SELECT body FROM chats WHERE key LIKE ? ORDER BY activity DESC",
            (f"{self.store.account_id}/user:%",),
        )
        found = []
        for row in rows:
            chat = Chat.model_validate_json(row["body"])
            if chat.kind != "private":
                continue
            found.append(
                {
                    "peer": chat.peer,
                    "name": chat.title,
                    "username": None,
                    "photo_id": chat.photo_id,
                    "chat": True,
                }
            )
            if len(found) == CHATS:
                break
        return found

    async def contacts(self):
        now = self.store.clock()
        if self._contacts is None or now - self._read > CONTACTS_AGE:
            async with asyncio.timeout(30):
                self._contacts = await self.transport.contacts()
            self._read = now
        return self._contacts

    async def search(self, query: str) -> dict:
        needle = query.strip().lstrip("@").lower()
        if not self.transport.connected:
            raise Refused("Telegram disconnected; New Message is unavailable")
        try:
            people = await self.contacts()
        except Exception:
            raise Refused("Telegram did not supply the contacts right now") from None
        # A contact with a chat stays among the chats, with its @username.
        usernames = {value["peer"]: value.get("username") for value in people}
        chats = [
            value | {"username": usernames.get(value["peer"])} for value in self.chats()
        ]
        chats = [value for value in chats if matches(value, needle)]
        known = {value["peer"] for value in chats}
        contacts = sorted(
            (
                value | {"chat": False}
                for value in people
                if value["peer"] not in known and matches(value, needle)
            ),
            key=lambda value: value["name"].lower(),
        )
        found = []
        if len(needle) >= GLOBAL_QUERY:
            seen = known | {value["peer"] for value in contacts}
            try:
                async with asyncio.timeout(30):
                    results = await self.transport.search_people(query)
            except Exception:
                results = []  # The chats and contacts still answer.
            found = [
                value | {"chat": False}
                for value in results
                if value["peer"] not in seen
            ]
        return {"chats": chats, "contacts": contacts, "search": found}

    async def open(self, peer: str) -> dict:
        """Admit a person's private chat and read its latest page."""
        key = f"{self.store.account_id}/{peer}"
        try:
            existing = self.store.chat(key)
        except Refused:
            existing = None
        if existing is not None and existing.kind != "private":
            raise Refused("Only a person's private chat opens here")
        if existing is None:
            if not self.transport.connected:
                raise Refused("Telegram disconnected; New Message is unavailable")
            try:
                async with asyncio.timeout(30):
                    found = await self.transport.resolve_person(peer)
            except Refused:
                raise
            except Exception:
                raise Refused("Telegram did not find this person right now") from None
            # A new chat starts with no activity: a message makes it recent.
            self.store.put_chat(
                Chat(
                    account_id=self.store.account_id,
                    peer=peer,
                    title=found["name"],
                    kind="private",
                    photo_id=found.get("photo_id"),
                ),
                activity=0,
            )
        if self.transport.connected:
            try:
                await self.app.history(key, 0)
            except Exception:
                pass  # Load earlier reads it again on request.
        chat = self.store.chat(key)
        return json.loads(chat.model_dump_json()) | {"key": chat.key}
