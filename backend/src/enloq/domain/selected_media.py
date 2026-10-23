"""Safe selected-media projections and explicit revision-bound actions."""

from types import SimpleNamespace
from typing import Literal

from pydantic import Field

from .catalog import HEX, REVISION
from .catalog_assets import representation
from .models import Model


class SelectedMediaView(Model):
    id: str = Field(pattern=HEX)
    kind: Literal["gif", "sticker"]
    format: Literal["gif", "mp4", "webp", "tgs", "webm", "unsupported"]
    status: Literal["preparing", "ready", "failed"]
    mime: str | None = None
    size: int | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    sticker: Literal["static", "animated", "video"] | None = None
    emoji: str | None = None
    requires_premium: bool = False
    error: str | None = None
    retryable: bool = False
    renderer_supported: bool = False
    features: tuple[str, ...] = ()
    content_url: str | None = None
    animation_url: str | None = None


class SelectMedia(Model):
    revision: int = Field(ge=0)
    token: str = Field(pattern=HEX)
    catalog_revision: str = Field(pattern=REVISION)


class SelectedMediaAction(Model):
    revision: int = Field(ge=0)
    id: str = Field(pattern=HEX)


def sticker_selected(draft):
    return (draft.get("selected_media") or {}).get("kind") == "sticker"


def public_media(value, *, base=None):
    """An allowlisted projection, also used for immutable pending sends."""
    facts = value.get("facts") or value
    source = value.get("source") or {}
    metadata = source.get("metadata") or {}
    kind = facts.get("kind") or metadata.get("kind") or value["kind"]
    format_ = facts.get("format")
    if format_ is None:
        format_ = {"static": "webp", "animated": "tgs", "video": "webm"}.get(
            metadata.get("format"), metadata.get("format", "unsupported")
        )
    status = value.get("status", "ready")
    ready = status == "ready" and bool(facts.get("sha256"))
    rendered = representation(SimpleNamespace(**facts), kind=kind) if ready else None
    return SelectedMediaView(
        id=value["id"],
        kind=kind,
        format=format_,
        status=status,
        **{
            name: facts.get(name)
            for name in ("mime", "size", "width", "height", "duration", "sticker")
        },
        emoji=value.get("emoji", metadata.get("emoji")),
        requires_premium=bool(
            value.get("requires_premium") or metadata.get("requires_premium")
        ),
        error=value.get("error"),
        retryable=status == "failed" and value.get("retryable", True),
        renderer_supported=bool(rendered and rendered.renderer_supported),
        features=rendered.features if rendered else (),
        content_url=base + "/content" if base and ready else None,
        animation_url=base + "/animation"
        if base and ready and format_ == "tgs" and rendered.renderer_supported
        else None,
    ).model_dump(mode="json")
