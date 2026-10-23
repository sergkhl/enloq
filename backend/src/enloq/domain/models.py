from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatKey(Model):
    account_id: int = Field(gt=0)
    peer: str

    @field_validator("peer")
    @classmethod
    def typed_peer(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or parts[0] not in {"user", "chat", "channel"}:
            raise ValueError("A typed native peer is required")
        if (
            not parts[1].isdigit()
            or int(parts[1]) <= 0
            or str(int(parts[1])) != parts[1]
        ):
            raise ValueError("A positive canonical peer ID is required")
        return value

    @property
    def key(self) -> str:
        return f"{self.account_id}/{self.peer}"


class MessageKey(ChatKey):
    message_id: int = Field(gt=0)

    @property
    def key(self) -> str:
        return f"{super().key}/{self.message_id}"


class Chat(ChatKey):
    title: str = Field(max_length=256)
    kind: Literal["private", "group", "bot", "service", "saved", "broadcast", "secret"]
    muted: bool = False
    archived: bool = False
    ai_eligible: bool = True
    eligibility_source: Literal["default", "explicit"] = "default"
    context_revision: int = 0
    read_through: int = 0
    # Telegram's outgoing read cursor: the recipient has read through this ID.
    read_outbox: int = Field(default=0, ge=0)
    latest_message_id: int | None = Field(default=None, gt=0)
    photo_id: str | None = Field(default=None, max_length=32, pattern=r"^-?\d+$")


class GroupState(ChatKey):
    """Native access and owner opt-in, independent of account connectivity."""

    processing_enabled: bool = False
    first_enabled_at: float | None = None
    supported: bool = True
    access: Literal["unknown", "available", "lost"] = "unknown"
    access_revision: int = Field(default=0, ge=0)
    observed_at: float = 0
    reason: str = Field(default="unconfirmed", max_length=80)
    migrated_to: str | None = None

    @field_validator("migrated_to")
    @classmethod
    def migration_peer(cls, value):
        if value is not None:
            ChatKey.typed_peer(value)
            if not value.startswith("channel:"):
                raise ValueError("Group migration requires a typed channel peer")
        return value


class GroupProcessing(Model):
    processing_enabled: bool
    account_processing_enabled: bool
    effective: bool
    access: Literal["unknown", "available", "lost"]
    supported: bool
    reason: str


class GroupIntake(ChatKey):
    """A bounded native window; completion never describes older history."""

    epoch: int = Field(default=0, ge=0)
    pts: int | None = Field(default=None, ge=0)
    before: int = Field(default=0, ge=0)
    top: int = Field(default=0, ge=0)
    count: int = Field(default=0, ge=0)
    omitted: int = Field(default=0, ge=0)
    cutoff: float | None = None
    oldest: float | None = None
    complete: bool = False
    gap: bool = False
    replay_pending: bool = False


def profile_photo_id(entity):
    identity = getattr(getattr(entity, "photo", None), "photo_id", None)
    # Native IDs are 64-bit: strings preserve their identity in browsers.
    return str(identity) if isinstance(identity, int) and identity else None


MediaKind = Literal[
    "image",
    "audio",
    "video_note",
    "video",
    "gif",
    "sticker",
    "document",
    "poll",
    "location",
    "contact",
    "link",
    "other",
    # Stored before typed normalization; refreshed lazily by exact message ID.
    "unsupported",
]
# Kinds whose content local preparation can turn into model input. A video
# circle contributes only its locally transcribed audio, never frames.
ANALYSED_MEDIA = frozenset({"image", "audio", "video_note"})
SPOKEN_MEDIA = frozenset({"audio", "video_note"})


class MediaInfo(Model):
    """Safe display/placeholder metadata: never phones, vCards or coordinates."""

    file_name: str | None = Field(default=None, max_length=256)
    mime: str | None = Field(default=None, max_length=128)
    size: int | None = Field(default=None, ge=0)
    duration: float | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    voice: bool = False
    sticker: Literal["static", "animated", "video"] | None = None
    emoji: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=256)
    site: str | None = Field(default=None, max_length=256)
    question: str | None = Field(default=None, max_length=512)
    options: list[str] = Field(default_factory=list, max_length=12)
    live: bool = False
    # Display-only (ADR 27): Telegram's voice-note waveform as 5-bit levels and
    # an audio file's performer. Never model input or content identity.
    waveform: list[Annotated[int, Field(ge=0, le=31)]] | None = Field(
        default=None, max_length=256
    )
    performer: str | None = Field(default=None, max_length=256)

    def content(self):
        return self.model_dump(exclude=DISPLAY_ONLY)


DISPLAY_ONLY = {"waveform", "performer"}


def media_class(kind):
    """The source-identity view of a media kind.

    Block revisions predate typed kinds, when everything but images and audio
    was stored as ``unsupported``. Mapping every newer kind back keeps a lazy
    metadata refresh from changing a block revision.
    """
    return kind if kind in (None, "image", "audio") else "unsupported"


class TextLink(Model):
    """Native UTF-16 offsets, as used by Telegram and browser strings."""

    offset: int = Field(ge=0, le=131072)
    length: int = Field(gt=0, le=131072)
    url: str = Field(min_length=1, max_length=8192)


class Reaction(Model):
    """Exactly one native reaction kind: an ordinary emoji, a custom emoji
    (its document ID; never rendered here) or a paid reaction."""

    emoji: str | None = Field(default=None, min_length=1, max_length=32)
    custom: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=r"^-?\d+$"
    )
    paid: bool = False

    @model_validator(mode="after")
    def one_kind(self):
        if sum((self.emoji is not None, self.custom is not None, self.paid)) != 1:
            raise ValueError("Exactly one reaction kind is required")
        return self


class ReactionChip(Model):
    reaction: Reaction
    count: int = Field(ge=1, le=1_000_000)
    # None: Telegram's min snapshot does not say which reactions are the owner's.
    mine: bool | None = None
    # Telegram's chosen_order for the owner's reactions: larger is newer.
    order: int | None = None


class ReactionActor(Model):
    actor: Literal["own", "peer"]
    reaction: Reaction
    # Native addition time. None when Telegram supplied none; local observation
    # time never substitutes for it.
    date: float | None = None


class ReactionSnapshot(Model):
    """Reaction metadata of one native message (ADR 26), never its content."""

    chips: list[ReactionChip] = Field(default_factory=list, max_length=64)
    # Attributed reactions, or None when Telegram supplied no attribution.
    actors: list[ReactionActor] | None = Field(default=None, max_length=64)
    # Every counted reaction is attributed to the owner or the correspondent.
    complete: bool = False
    # Telegram's min flag: the owner's own choices are unknown.
    minimal: bool = False
    # Local time the native read started or the update arrived. It orders
    # snapshots of one message and is never a reaction's addition time.
    observed: float = Field(ge=0)

    def count(self, reaction):
        return sum(c.count for c in self.chips if c.reaction == reaction)

    def peer_lacks(self, reaction):
        """Proof that the correspondent does not currently have ``reaction``.
        Partial or unknown snapshots never prove a removal."""
        if not self.count(reaction):
            return True
        if self.complete and self.actors is not None:
            return not any(
                a.actor == "peer" and a.reaction == reaction for a in self.actors
            )
        if not self.minimal and self.actors is None:
            # Two participants: a single counted reaction chosen by the owner
            # cannot also be the correspondent's.
            return self.count(reaction) == 1 and any(
                c.reaction == reaction and c.mine for c in self.chips
            )
        return False

    def own(self):
        """The owner's current reactions, oldest first, or None if unknown."""
        if not self.minimal:
            chosen = [c for c in self.chips if c.mine]
            return [c.reaction for c in sorted(chosen, key=lambda c: c.order or 0)]
        if self.complete and self.actors is not None:
            return [a.reaction for a in self.actors if a.actor == "own"]
        return None


class ReplyElsewhere(Model):
    """What a reply header supplies about a message in another chat (ADR 41)."""

    peer: str | None = Field(default=None, max_length=64)
    sender_peer: str | None = Field(default=None, max_length=64)
    sender: str | None = Field(default=None, max_length=256)
    date: float | None = None


class Message(MessageKey):
    revision: int = Field(ge=0)
    # Native timestamps order updates; the local revision also distinguishes
    # meaningful changes that Telegram reports within the same timestamp.
    native_revision: int | None = Field(default=None, ge=0)
    native_edited: bool = False
    # Telegram's edit_hide: this capture's edit date is not shown as an edit.
    # It travels with a native read or update and is never stored.
    edit_hidden: bool = Field(default=False, exclude=True)
    # None is legacy/unknown; False means Telegram supplied no forward header.
    forwarded: bool | None = None
    forwarded_from: str | None = Field(default=None, max_length=256)
    # The forward header's original date, channel post signature and imported
    # flag (ADR 27). None is not yet captured; they are only ever filled in.
    forward_date: float | None = None
    forward_signature: str | None = Field(default=None, max_length=256)
    imported: bool | None = None
    # A successful exact read already supplied this forward's header, so a
    # still-missing source name is final.
    forward_read: bool = False
    attachment_id: str | None = Field(default=None, max_length=128)
    date: float
    text: str = Field(default="", max_length=65536)
    links: list[TextLink] = Field(default_factory=list, max_length=1024)
    outgoing: bool = False
    service: bool = False
    mentioned: bool = False
    reply_to_self: bool = False
    # The reply header's message number. It names a message in this chat only
    # while ``reply_elsewhere`` and ``reply_story`` are unset; use reply_target.
    reply_to: int | None = Field(default=None, gt=0)
    # ADR 41: the header's quoted fragment, another chat's message and story
    # replies. ``reply_read`` marks a capture with these details; earlier rows
    # lack them, and a capture without them never erases them.
    reply_quote: str | None = Field(default=None, max_length=4096)
    reply_elsewhere: ReplyElsewhere | None = None
    reply_story: bool = False
    reply_read: bool = False
    topic_id: int | None = Field(default=None, gt=0)
    media: MediaKind | None = None
    media_info: MediaInfo | None = None
    deleted: bool = False
    author: str = Field(default="", max_length=256)
    author_peer: str | None = None
    # Trusted full native User.bot provenance only; legacy/missing/minimal or
    # anonymous senders remain unknown. This is metadata, not content identity.
    sender_is_bot: bool | None = Field(default=None, strict=True)
    author_signature: str | None = Field(default=None, max_length=256)
    reply_status: Literal["none", "available", "missing", "unknown"] = "none"
    # Observed reaction metadata travelling with a native read or update. It is
    # never stored with, compared as or digested into the message content.
    reactions: ReactionSnapshot | None = Field(default=None, exclude=True)


def reply_target(message: Message) -> int | None:
    """The replied-to message in this chat, if any (ADR 41). A reply to another
    chat's message or to a story names none, whatever its header number."""
    if message.reply_elsewhere is not None or message.reply_story:
        return None
    return message.reply_to


REPLY_DETAILS = ("reply_quote", "reply_elsewhere", "reply_story", "reply_read")


def provisional(message: Message) -> bool:
    """The record of a message this service just sent, written from Telegram's
    receipt before Telegram's own copy is read (SendOperations.confirm). Every
    native capture has a positive revision."""
    return (
        message.outgoing and message.revision == 0 and message.native_revision is None
    )


def same_content(left: Message, right: Message) -> bool:
    """Proven visible equality. Legacy attachments have unknown identity."""
    if any(
        getattr(left, field) != getattr(right, field)
        for field in ("text", "links", "deleted", "reply_to", "outgoing")
    ):
        return False
    if left.media is None or right.media is None:
        return left.media is right.media
    return bool(
        left.attachment_id
        and left.attachment_id == right.attachment_id
        and left.media == right.media
        and (left.media_info and left.media_info.content())
        == (right.media_info and right.media_info.content())
    )


class Conflict(Exception):
    """The caller must reload without discarding its local edits."""


class Refused(Exception):
    """A content-free refusal that may be shown in the UI."""


class DeliveryRefused(Refused):
    """A definitive native rejection, distinct from a missing receipt."""


class ProcessingPaused(Refused):
    """No AI stage was dispatched; pending inputs remain eligible to resume."""


class Syncing(ProcessingPaused):
    """Telegram state is still being read or proven; new work resumes once it is.

    Transient covers the mark held only while one catch-up request runs; a
    reading view keeps its previous state through it rather than flickering.
    """

    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = transient


class MediaTooLarge(Refused):
    """Source media exceeds a preparation bound; retrying cannot change that."""


class Unavailable(Refused):
    pass


class GapTooLong(Unavailable):
    """Telegram cannot replay the missed updates. Only the sync loop restarts
    from the latest state; other callers see an ordinary Unavailable."""


class Unreadable(Refused):
    """Source bytes cannot be decoded; retrying cannot change that."""
