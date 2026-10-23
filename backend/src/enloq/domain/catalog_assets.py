"""Safe, authenticated preview facts without private catalog/source metadata."""

from typing import Literal

from pydantic import Field

from .catalog import HEX, REVISION
from .models import Model


class AssetRepresentation(Model):
    state: Literal["missing", "ready", "unsupported"]
    kind: Literal["thumbnail", "gif", "sticker"]
    format: Literal["jpeg", "png", "webp", "gif", "mp4", "tgs", "webm"] | None = None
    mime: (
        Literal[
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
            "video/mp4",
            "video/webm",
            "application/x-tgsticker",
        ]
        | None
    ) = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration: float | None = Field(default=None, ge=0)
    fps: float | None = Field(default=None, gt=0)
    frames: int | None = Field(default=None, gt=0)
    renderer_supported: bool = False
    features: tuple[str, ...] = ()


class CatalogAssetView(AssetRepresentation):
    token: str = Field(pattern=HEX)
    revision: str = Field(pattern=REVISION)
    variant: Literal["thumbnail", "media"]


class CatalogAssetPreparation(Model):
    revision: str = Field(pattern=REVISION)
    variant: Literal["thumbnail", "media"] = "thumbnail"


class ReceivedAssetPreparation(Model):
    revision: int = Field(ge=0)
    version: int | None = Field(default=None, gt=0)


class ReceivedAssetView(AssetRepresentation):
    revision: int = Field(ge=0)
    version: int | None = Field(default=None, gt=0)


def representation(facts, *, kind):
    """Format proof and bounded renderer support remain separate facts."""
    if facts is None:
        return AssetRepresentation(state="unsupported", kind=kind)
    features = getattr(facts, "features", ())
    supported = not {"unsupported_mask_mode", "inverted_luminance_matte"}.intersection(
        features
    )
    return AssetRepresentation(
        state="ready" if supported else "unsupported",
        kind=kind,
        format=facts.format,
        mime=facts.mime,
        width=facts.width,
        height=facts.height,
        duration=getattr(facts, "duration", None),
        fps=getattr(facts, "fps", None),
        frames=getattr(facts, "frames", 1),
        renderer_supported=supported,
        features=features,
    )
