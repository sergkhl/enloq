"""Read-only provenance availability and the memory presentations: a chat's
Person Memory and the account's Account Memory (ADR 55)."""

from typing import Literal

from ..domain.models import Model


class MemorySource(Model):
    id: int
    availability: Literal["cached", "not_cached", "deleted"]


class OutdatedMemory(Model):
    citations: list[int]


class MemoryEntry(Model):
    id: str
    chat_key: str
    text: str
    citations: list[int]
    source: Literal["model", "owner"]
    outdated: OutdatedMemory | None
    created: float
    guidance: int | None
    defaults: int | None = None
    sources: list[MemorySource]


class MemoryView(Model):
    """One chat's Person Memory: what the other person cares about."""

    entries: list[MemoryEntry]
    revision: int
    coverage: int
    error: str | None
    enabled: bool
    waiting: int
    updating: bool


class AccountMemorySource(MemorySource):
    """An Account Memory citation names the peer of the chat it came from."""

    chat: str


class AccountMemoryEntry(Model):
    id: str
    text: str
    citations: list[int]
    source: Literal["model", "owner"]
    outdated: dict | None
    created: float
    guidance: int | None
    defaults: int | None = None
    sources: list[AccountMemorySource]


class AccountMemoryView(Model):
    """The account's Account Memory: what its holder cares about."""

    entries: list[AccountMemoryEntry]
    revision: int
    enabled: bool


class Remembering(Model):
    """A private chat's Remember this chat (ADR 55 Q7): on unless the chat
    chose otherwise."""

    enabled: bool
