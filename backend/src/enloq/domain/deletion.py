"""Captured native deletion membership and truthful read-only evidence."""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from .models import Message, Refused


def deletion_ids(identities):
    values = tuple(identities)
    if any(
        type(identity) is not int or not 0 < identity < 2**31 for identity in values
    ) or len(set(values)) != len(values):
        raise Refused("Invalid native deletion identities")
    return values


def private_peer(peer):
    if not isinstance(peer, str):
        raise Refused("Deletion requires a private conversation")
    parts = peer.split(":")
    if (
        len(parts) != 2
        or parts[0] != "user"
        or not parts[1].isascii()
        or not parts[1].isdigit()
        or int(parts[1]) <= 0
        or str(int(parts[1])) != parts[1]
    ):
        raise Refused("Deletion requires a private conversation")


def deletion_peer(peer):
    """Only exact canonical private/basic/supergroup peers, never an alias."""
    if not isinstance(peer, str):
        raise Refused("Deletion requires an exact native conversation")
    parts = peer.split(":")
    if (
        len(parts) != 2
        or parts[0] not in {"user", "chat", "channel"}
        or not parts[1].isascii()
        or not parts[1].isdigit()
        or int(parts[1]) <= 0
        or str(int(parts[1])) != parts[1]
    ):
        raise Refused("Deletion requires an exact native conversation")


def deletion_fingerprint(source):
    """Retain exact review evidence without retaining full wording past expiry."""
    fields = (
        "account_id",
        "peer",
        "message_id",
        "revision",
        "native_revision",
        "date",
        "text",
        "links",
        "outgoing",
        "service",
        "deleted",
        "reply_to",
        "media",
        "attachment_id",
        "author_peer",
        "author_signature",
    )
    value = source.model_dump(include=set(fields))
    value["media_info"] = source.media_info.content() if source.media_info else None
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class DeleteDescription:
    """One individually awaited chunk; membership is already durably reviewed."""

    operation_id: str
    account_id: int
    peer: str
    identities: tuple[int, ...]
    access_revision: int | None = None
    sources: tuple[Message, ...] = ()

    def __post_init__(self):
        deletion_peer(self.peer)
        values = deletion_ids(self.identities)
        if (
            not isinstance(self.operation_id, str)
            or not self.operation_id
            or type(self.account_id) is not int
            or self.account_id <= 0
            or not 1 <= len(values) <= 100
        ):
            raise Refused("Invalid native deletion description")
        sources = tuple(self.sources)
        if self.peer.startswith(("chat:", "channel:")):
            if (
                type(self.access_revision) is not int
                or self.access_revision < 0
                or any(not isinstance(source, Message) for source in sources)
                or tuple(source.message_id for source in sources) != values
                or any(
                    not isinstance(source, Message)
                    or source.account_id != self.account_id
                    or source.peer != self.peer
                    or source.author_peer != f"user:{self.account_id}"
                    or source.service
                    or source.deleted
                    for source in sources
                )
            ):
                raise Refused("Group deletion requires reviewed own-user originals")
        elif self.access_revision is not None or sources:
            raise Refused("Private deletion cannot carry group access evidence")
        object.__setattr__(self, "identities", values)
        object.__setattr__(
            self, "sources", tuple(s.model_copy(deep=True) for s in sources)
        )


@dataclass(frozen=True)
class DeleteReceipt:
    """Acknowledgement of exact native removal, without an audience verdict.

    Private/basic requests use revoke=True; supergroup removal is native for
    everyone. Acceptance is not independent observation of others' views.
    """

    pts: int
    pts_count: int

    def __post_init__(self):
        if any(
            type(value) is not int or value < 0 for value in (self.pts, self.pts_count)
        ):
            raise Refused("Invalid native deletion receipt")


class DeleteEvidence(Protocol):
    """Mandatory writes and source/owner validation before final wire hand-off."""

    def guard(self) -> None: ...
    def calling(self) -> None: ...
    def watching(self) -> None: ...
    def holding(self, until: float) -> None: ...
    def handing_off(self) -> None: ...
    def not_handed(self) -> None: ...
    def flood_refused(self, error: str, until: float) -> None: ...
    def refused(self, reason: str) -> None: ...


@dataclass(frozen=True)
class DeletionRead:
    """Every captured ID is native-present, native-absent or unavailable.

    Absence is a connected-account read fact, never a for-both receipt. Missing
    cache rows and omitted/unusable native results belong to unavailable.
    """

    account_id: int
    peer: str
    identities: tuple[int, ...]
    present: tuple[Message, ...] = ()
    absent: tuple[int, ...] = ()
    unavailable: tuple[int, ...] = ()

    def __post_init__(self):
        deletion_peer(self.peer)
        identities = deletion_ids(self.identities)
        absent, unavailable = deletion_ids(self.absent), deletion_ids(self.unavailable)
        present = tuple(self.present)
        if (
            type(self.account_id) is not int
            or self.account_id <= 0
            or any(
                not isinstance(message, Message)
                or message.account_id != self.account_id
                or message.peer != self.peer
                or message.deleted
                for message in present
            )
        ):
            raise Refused("Invalid native deletion read")
        covered = (
            tuple(message.message_id for message in present) + absent + unavailable
        )
        if len(set(covered)) != len(covered) or set(covered) != set(identities):
            raise Refused("Native deletion read must cover every captured identity")
        object.__setattr__(self, "identities", identities)
        object.__setattr__(self, "present", present)
        object.__setattr__(self, "absent", absent)
        object.__setattr__(self, "unavailable", unavailable)
