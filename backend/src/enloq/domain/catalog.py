"""Safe picker projections and detached private exact-media source identities."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, field_validator

from .models import Model

HEX = r"^[a-f0-9]{32}$"
REVISION = r"^[a-f0-9]{64}$"
GIF_LIMIT = 50 * 1024**2
STICKER_LIMIT = 5 * 1024**2
LIST_LIMIT = 200
PACK_LIMIT = 100
SEARCH_LIMIT = 50


class Frozen(Model):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CatalogMetadata(Frozen):
    kind: Literal["gif", "sticker"]
    format: Literal["gif", "mp4", "static", "animated", "video", "unsupported"]
    mime: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=256)
    size: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    duration: float | None = Field(default=None, ge=0)
    emoji: str | None = Field(default=None, max_length=32)
    keywords: tuple[str, ...] = Field(default=(), max_length=16)
    requires_premium: bool = False
    unavailable: str | None = Field(default=None, max_length=256)


class NativePackReference(Frozen):
    id: str = Field(pattern=r"^[1-9]\d{0,19}$", repr=False)
    access_hash: str = Field(pattern=r"^-?\d{1,20}$", repr=False)
    hash: int = 0
    title: str = Field(max_length=256)
    short_name: str = Field(max_length=64)
    count: int = Field(ge=0)

    @field_validator("id", "access_hash")
    @classmethod
    def native_integer(cls, value, info):
        number = int(value)
        if not -(2**63) <= number < 2**63 or (info.field_name == "id" and number <= 0):
            raise ValueError("Native identity exceeds its protocol bound")
        return value


class CatalogOrigin(Frozen):
    kind: Literal["saved", "recent", "favorites", "pack", "search"]
    pack: NativePackReference | None = None
    short_name: str | None = Field(default=None, max_length=64)


class NativeThumbnail(Frozen):
    type: str = Field(pattern=r"^[A-Za-z0-9]{1,4}$")
    size: int = Field(ge=0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class NativeCatalogSource(Frozen):
    source: Literal["native"] = "native"
    account_id: int = Field(gt=0, lt=2**63)
    document_id: str = Field(pattern=r"^[1-9]\d{0,19}$", repr=False)
    access_hash: str = Field(pattern=r"^-?\d{1,20}$", repr=False)
    file_reference: str = Field(max_length=8192, repr=False)
    dc_id: int = Field(gt=0)
    metadata: CatalogMetadata
    origins: tuple[CatalogOrigin, ...] = Field(max_length=8)
    thumbnail: NativeThumbnail | None = None

    @field_validator("document_id", "access_hash")
    @classmethod
    def native_integer(cls, value, info):
        number = int(value)
        if not -(2**63) <= number < 2**63 or (
            info.field_name == "document_id" and number <= 0
        ):
            raise ValueError("Native identity exceeds its protocol bound")
        return value

    @field_validator("file_reference")
    @classmethod
    def reference_bytes(cls, value):
        if len(base64.b64decode(value, validate=True)) > 4096:
            raise ValueError("Native reference exceeds its bound")
        return value


class WebCatalogSource(Frozen):
    source: Literal["web"] = "web"
    account_id: int = Field(gt=0, lt=2**63)
    remote_id: str = Field(min_length=1, max_length=256, repr=False)
    url: str = Field(min_length=1, max_length=2048, repr=False)
    thumbnail_url: str | None = Field(default=None, max_length=2048, repr=False)
    metadata: CatalogMetadata


CatalogSource = Annotated[
    NativeCatalogSource | WebCatalogSource, Field(discriminator="source")
]
SOURCE = TypeAdapter(CatalogSource)


class CatalogLibrary(Frozen):
    premium: bool | None = None
    gifs: tuple[NativeCatalogSource, ...] = Field(default=(), max_length=LIST_LIMIT)
    recent: tuple[NativeCatalogSource, ...] = Field(default=(), max_length=LIST_LIMIT)
    favorites: tuple[NativeCatalogSource, ...] = Field(
        default=(), max_length=LIST_LIMIT
    )
    packs: tuple[NativePackReference, ...] = Field(default=(), max_length=PACK_LIMIT)
    truncated: bool = False


class CatalogPackData(Frozen):
    pack: NativePackReference
    items: tuple[NativeCatalogSource, ...] = Field(max_length=LIST_LIMIT)
    truncated: bool = False


class CatalogSearchData(Frozen):
    items: tuple[CatalogSource, ...] = Field(max_length=SEARCH_LIMIT)
    next_offset: str | None = Field(default=None, max_length=256)
    truncated: bool = False


class CatalogPreview(Model):
    token: str = Field(pattern=HEX)
    revision: str = Field(pattern=REVISION)
    kind: Literal["thumbnail", "media", "unavailable"]
    format: Literal["gif", "mp4", "static", "animated", "video", "unsupported"]
    # Descriptor only. Bytes still require later authenticated preparation.
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)


class CatalogItem(Model):
    token: str = Field(pattern=HEX)
    revision: str = Field(pattern=REVISION)
    metadata: CatalogMetadata
    selectable: bool
    unavailable: str | None = None
    preview: CatalogPreview


class CatalogPack(Model):
    id: str = Field(pattern=HEX)
    revision: str = Field(pattern=REVISION)
    title: str
    short_name: str
    count: int = Field(ge=0)
    cached: bool
    stale: bool


class CatalogView(Model):
    revision: str = Field(pattern=REVISION)
    updated_at: float | None
    stale: bool
    premium: bool | None
    gifs: list[CatalogItem]
    recent: list[CatalogItem]
    favorites: list[CatalogItem]
    packs: list[CatalogPack]
    truncated: bool
    error: str | None = None


class CatalogPackView(Model):
    pack: CatalogPack
    items: list[CatalogItem]
    updated_at: float | None
    truncated: bool
    error: str | None = None


class CatalogRefresh(Model):
    revision: str = Field(pattern=REVISION)


class GifSearch(Model):
    query: str = Field(max_length=128)
    epoch: str = Field(pattern=HEX)
    request: int = Field(ge=0, le=2**53 - 1)
    offset: str = Field(default="", max_length=256)


class GifSearchView(Model):
    epoch: str = Field(pattern=HEX)
    request: int = Field(ge=0)
    items: list[CatalogItem]
    next_offset: str | None
    truncated: bool


class GifSearchScope(Model):
    epoch: str = Field(pattern=HEX)


class CatalogOpen(Model):
    """Explicit JSON picker-open action; no persisted browser query state."""

    pass


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def source_identity(source: CatalogSource) -> str:
    if isinstance(source, NativeCatalogSource):
        return f"{source.account_id}/document/{source.document_id}"
    return f"{source.account_id}/web/{digest([source.remote_id, source.url])}"


def source_revision(source: CatalogSource) -> str:
    # Ephemeral native reference renewal never changes the selected document.
    return digest([source_identity(source), source.metadata.model_dump(mode="json")])


def pack_identity(pack: NativePackReference, account_id: int) -> str:
    return digest([account_id, "pack", pack.id])[:32]


def pack_revision(pack: NativePackReference) -> str:
    return digest([pack.id, pack.hash, pack.title, pack.short_name, pack.count])
