"""Immutable values and native evidence capabilities for deliberate delivery."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .catalog import SOURCE, CatalogSource
from .models import DeliveryRefused, Message, Refused


@dataclass(frozen=True)
class SendImage:
    path: Path
    directory: Path
    sha256: str
    mime: str
    size: int
    width: int
    height: int


@dataclass(frozen=True)
class SendMedia:
    """One retained exact GIF or sticker, detached from its picker and draft.

    These paths and source references are private transport capabilities. Public
    operation projections use the opaque owner id and safe format facts only.
    """

    id: str
    path: Path
    directory: Path
    sha256: str
    mime: str
    size: int
    width: int
    height: int
    kind: Literal["gif", "sticker"]
    format: Literal["gif", "mp4", "webp", "tgs", "webm"]
    source: CatalogSource
    duration: float | None = None
    sticker: Literal["static", "animated", "video"] | None = None
    emoji: str | None = None
    requires_premium: bool = False

    def __post_init__(self):
        source = SOURCE.validate_python(self.source.model_dump(mode="json"))
        object.__setattr__(self, "source", source)
        object.__setattr__(
            self,
            "requires_premium",
            bool(self.requires_premium or source.metadata.requires_premium),
        )
        if (
            not self.id
            or source.metadata.kind != self.kind
            or (self.kind == "gif" and self.format not in {"gif", "mp4"})
            or (self.kind == "sticker" and self.format not in {"webp", "tgs", "webm"})
            or (self.kind == "sticker" and self.sticker is None)
            or self.size <= 0
        ):
            raise Refused("Invalid retained send media")


@dataclass(frozen=True)
class SendDescription:
    attempt_id: str
    account_id: int
    peer: str
    text: str
    reply_to: int | None
    random_ids: tuple[int, ...]
    images: tuple[SendImage, ...] = ()
    topic_id: int | None = None
    media: SendMedia | None = None

    def __post_init__(self):
        object.__setattr__(self, "random_ids", tuple(self.random_ids))
        object.__setattr__(self, "images", tuple(self.images))
        if (
            not self.attempt_id
            or type(self.account_id) is not int
            or self.account_id <= 0
            or not self.random_ids
            or any(type(i) is not int or i <= 0 for i in self.random_ids)
            or len(set(self.random_ids)) != len(self.random_ids)
            or len(self.random_ids) != (len(self.images) or 1)
            or any(not isinstance(image, SendImage) for image in self.images)
            or (self.media is not None and not isinstance(self.media, SendMedia))
            or (self.media is not None and bool(self.images))
            or (
                self.media is not None
                and self.media.source.account_id != self.account_id
            )
            or (
                self.media is not None
                and self.media.kind == "sticker"
                and self.text != ""
            )
        ):
            raise Refused("Invalid native send description")


@dataclass(frozen=True)
class ReceiptBatch:
    """Native correlation pairs, detached from both wire objects and storage."""

    pairs: tuple[tuple[int, int], ...] = ()

    def __post_init__(self):
        pairs = tuple(tuple(pair) for pair in self.pairs)
        if any(
            len(pair) != 2
            or type(pair[0]) is not int
            or not pair[0]
            or type(pair[1]) is not int
            or pair[1] <= 0
            for pair in pairs
        ) or len({pair[0] for pair in pairs}) != len(pairs):
            raise Refused("Invalid native receipts")
        object.__setattr__(self, "pairs", pairs)

    def __bool__(self):
        return bool(self.pairs)


class SendEvidence(Protocol):
    """Synchronous observations; mandatory writes precede native side effects.

    RequestPolicy retains responsibility for best-effort narrowing callbacks.
    A failed observation never supplies stronger evidence of non-delivery.
    """

    def guard(self) -> None: ...
    def calling(self) -> None: ...
    def watching(self) -> None: ...
    def holding(self, until: float) -> None: ...
    def handing_off(self) -> None: ...
    def not_handed(self) -> None: ...
    def flood_refused(self, error: str, until: float) -> None: ...
    def refused(self, reason: str) -> None: ...


class ReplySendBlocked(Refused):
    """An unhanded reply must retain its target and wait or be refused.

    This local guard is not a native rejection. Earlier possible exposure must
    continue to use ordinary delivery evidence rather than this verdict.
    """

    def __init__(self, status):
        self.status = status
        super().__init__(
            {
                "pending": "Reply target is being deleted; sending is on hold.",
                "unresolved": "Reply target deletion is unresolved; check its result before sending.",
                "deleted": "Reply target deleted.",
                "unavailable": "Native reply target is unavailable.",
            }[status]
        )


class StickerReselectionRequired(DeliveryRefused):
    """The exact original sticker cannot be sent; retain it without conversion."""


class GroupSendBlocked(Refused):
    """Confirmed group access changes require a reviewed, deliberate Resume.

    This local verdict never turns a possible native handoff into non-delivery.
    The send owner must first settle its independent handoff evidence.
    """

    def __init__(self, status):
        self.status = status
        super().__init__(
            {
                "lost": "Native group access was lost; review this exact item after access returns.",
                "unknown": "Native group access is unconfirmed; sending is on hold.",
                "unsupported": "This native group no longer supports sending; review this item.",
                "changed": "Native group access changed; review this exact item before resuming.",
                "target": "The exact native group target changed; sending is on hold.",
            }[status]
        )


class ReceiptSink(Protocol):
    def __call__(self, receipts: ReceiptBatch) -> None: ...


@dataclass(frozen=True)
class ReceiptCheck:
    receipts: ReceiptBatch
    needs_refresh: bool


@dataclass(frozen=True)
class Confirmation:
    complete: bool
    messages: tuple[Message, ...]
