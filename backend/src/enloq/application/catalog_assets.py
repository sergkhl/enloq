"""Lazy exact catalog assets in the shared, disposable private media cache."""

import asyncio
import hmac
import stat
from dataclasses import dataclass

from PIL import Image

from ..domain.catalog import (
    NativeCatalogSource,
    digest,
    source_identity,
    source_revision,
)
from ..domain.catalog_assets import (
    AssetRepresentation,
    CatalogAssetView,
    representation,
)
from ..domain.models import MediaTooLarge, Refused, Unavailable
from ..domain.selection_formats import (
    GIF_BYTES,
    STICKER_BYTES,
    _digest,
    validate_selection,
)
from ..transport.media import (
    MediaBounds,
    MediaValidationError,
    MediaWorkspace,
    validate_image,
)
from ..transport.web_media import fetch_web_media
from .media_cache import private_scratch

THUMBNAIL_BYTES = 5 * 1024**2
THUMBNAIL_BOUNDS = MediaBounds.narrowed(
    mime_types=frozenset({"image/jpeg", "image/png", "image/webp"}),
    image_bytes=THUMBNAIL_BYTES,
    image_dimension=1024,
    image_pixels=1024**2,
)


@dataclass(frozen=True)
class ThumbnailProof:
    format: str
    mime: str
    width: int
    height: int
    sha256: str
    size: int


def _identity(path):
    try:
        details = path.lstat()
    except OSError:
        raise Refused("Preview bytes unavailable") from None
    if not stat.S_ISREG(details.st_mode) or not details.st_size:
        raise Refused("Preview bytes unavailable")
    # Serving through a hardlink changes ctime, but never the byte identity.
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_mode,
    )


def _thumbnail(path):
    before = _identity(path)
    try:
        facts = validate_image(path, THUMBNAIL_BOUNDS)
        with Image.open(path) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise Refused("Catalog thumbnail must be a still image")
            image.verify()
        with Image.open(path) as image:
            image.load()
        digest_value, size = _digest(path, THUMBNAIL_BYTES)
    except (MediaValidationError, OSError, ValueError, Image.DecompressionBombError):
        raise Refused("Catalog thumbnail cannot be rendered here") from None
    if (
        before != _identity(path)
        or not hmac.compare_digest(digest_value, facts.sha256)
        or size != facts.size_bytes
    ):
        raise Refused("Preview bytes changed")
    return ThumbnailProof(
        format={"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp"}[
            facts.mime_type
        ],
        mime=facts.mime_type,
        width=facts.width,
        height=facts.height,
        sha256=facts.sha256,
        size=facts.size_bytes,
    )


class AssetProofs:
    """Reuse positive format proof only after cryptographic byte revalidation."""

    def __init__(self):
        self.proofs = {}
        self.locks = {}

    async def prove(self, path, kind):
        limit = GIF_BYTES if kind == "gif" else STICKER_BYTES
        async with self.locks.setdefault((str(path), kind), asyncio.Lock()):
            before = _identity(path)
            if before[2] > limit:
                raise MediaTooLarge("Preview exceeds its file limit")
            key = (kind, *before)
            facts = self.proofs.get(key)
            if facts is None:
                facts = (
                    await asyncio.to_thread(_thumbnail, path)
                    if kind == "thumbnail"
                    else await validate_selection(path, kind)
                )
                if before != _identity(path):
                    raise Refused("Preview bytes changed")
                if len(self.proofs) >= 1024:
                    self.proofs.clear()
                self.proofs[key] = facts
            else:
                try:
                    digest_value, size = await asyncio.to_thread(_digest, path, limit)
                except OSError:
                    raise Refused("Preview bytes unavailable") from None
                if (
                    before != _identity(path)
                    or size != facts.size
                    or not hmac.compare_digest(digest_value, facts.sha256)
                ):
                    self.proofs.pop(key, None)
                    raise Refused("Preview bytes changed")
            return facts


class CatalogAssets:
    def __init__(self, catalog, cache, transport, *, web_fetcher=fetch_web_media):
        if catalog.store is not cache.store:
            raise Refused("Catalog cache belongs to another account")
        self.catalog, self.cache, self.transport = catalog, cache, transport
        self.web_fetcher = web_fetcher
        self.proofs = AssetProofs()
        self.workspace = MediaWorkspace(cache.store.path.parent / "media")
        self.workspace.prepare()

    @staticmethod
    def identity(source, variant):
        thumbnail = (
            source.thumbnail.model_dump(mode="json")
            if isinstance(source, NativeCatalogSource) and source.thumbnail
            else getattr(source, "thumbnail_url", None)
        )
        return f"{source.account_id}/catalog/{digest([source_identity(source), variant, thumbnail if variant == 'thumbnail' else None])}"

    def source(self, key, session, token, revision, variant, *, guard=None):
        if guard:
            guard()
        if variant not in ("thumbnail", "media"):
            raise Refused("Catalog asset variant unavailable")
        source = self.catalog.resolve(key, session, token, revision)
        if source.account_id != self.cache.store.account_id:
            raise Refused("Catalog source belongs to another account")
        if variant == "thumbnail" and not (
            source.thumbnail
            if isinstance(source, NativeCatalogSource)
            else source.thumbnail_url
        ):
            raise Refused("Catalog thumbnail unavailable")
        return source

    def binding(self, key, session, token, revision, variant, source, guard):
        def current():
            fresh = self.source(key, session, token, revision, variant, guard=guard)
            if self.identity(fresh, variant) != self.identity(
                source, variant
            ) or source_revision(fresh) != source_revision(source):
                raise Refused("Catalog asset changed; select it again")

        return current

    @staticmethod
    def limit(source, variant):
        return (
            THUMBNAIL_BYTES
            if variant == "thumbnail"
            else GIF_BYTES
            if source.metadata.kind == "gif"
            else STICKER_BYTES
        )

    async def facts(self, found, source, variant):
        try:
            facts = await self.proofs.prove(
                found["path"],
                "thumbnail" if variant == "thumbnail" else source.metadata.kind,
            )
            if (
                variant == "media"
                and source.metadata.kind == "sticker"
                and source.metadata.format != facts.sticker
            ):
                return None
            return facts
        except Unavailable:
            raise
        except Refused:
            return None

    def view(self, source, token, revision, variant, facts, *, missing=False):
        kind = "thumbnail" if variant == "thumbnail" else source.metadata.kind
        value = (
            AssetRepresentation(state="missing", kind=kind)
            if missing
            else representation(facts, kind=kind)
        )
        return CatalogAssetView(
            token=token, revision=revision, variant=variant, **value.model_dump()
        )

    async def describe(
        self, key, session, token, revision, *, variant="thumbnail", guard=None
    ):
        source = self.source(key, session, token, revision, variant, guard=guard)
        current = self.binding(key, session, token, revision, variant, source, guard)
        found = self.cache.get_catalog(
            self.identity(source, variant),
            revision,
            variant,
            limit=self.limit(source, variant),
        )
        if found is None:
            return self.view(source, token, revision, variant, None, missing=True)
        facts = await self.facts(found, source, variant)
        current()
        return self.view(source, token, revision, variant, facts)

    async def prepare(
        self, key, session, token, revision, *, variant="thumbnail", guard=None
    ):
        source = self.source(key, session, token, revision, variant, guard=guard)
        current = self.binding(key, session, token, revision, variant, source, guard)
        limit = self.limit(source, variant)
        declared = (
            source.thumbnail.size
            if variant == "thumbnail" and isinstance(source, NativeCatalogSource)
            else source.metadata.size
            if variant == "media"
            else None
        )
        if declared is not None and declared > limit:
            raise MediaTooLarge("Catalog asset exceeds its file limit")

        async def download(path):
            current()
            if isinstance(source, NativeCatalogSource):
                fresh = await self.transport.catalog_download(
                    source, path, max_bytes=limit, thumb=variant == "thumbnail"
                )
                if (
                    source_identity(fresh) != source_identity(source)
                    or source_revision(fresh) != revision
                    or self.identity(fresh, variant) != self.identity(source, variant)
                ):
                    raise Refused("Catalog asset changed during transfer")
            else:
                await self.web_fetcher(
                    source.thumbnail_url if variant == "thumbnail" else source.url,
                    path,
                    limit=limit,
                    purpose=variant,
                )
            current()

        try:
            found = await self.cache.fetch_catalog(
                self.identity(source, variant),
                revision,
                variant,
                limit=limit,
                download=download,
                guard=current,
            )
        except asyncio.CancelledError:
            raise
        except Refused:
            raise
        except Exception:
            current()
            raise Unavailable("Catalog preview unavailable; retry") from None
        facts = await self.facts(found, source, variant)
        current()
        return self.view(source, token, revision, variant, facts)

    async def serve(
        self, key, session, token, revision, *, variant="thumbnail", guard=None
    ):
        source = self.source(key, session, token, revision, variant, guard=guard)
        current = self.binding(key, session, token, revision, variant, source, guard)
        found = self.cache.get_catalog(
            self.identity(source, variant),
            revision,
            variant,
            limit=self.limit(source, variant),
        )
        if found is None:
            raise Refused("Catalog preview is not prepared locally")
        facts = await self.facts(found, source, variant)
        current()
        view = representation(
            facts, kind="thumbnail" if variant == "thumbnail" else source.metadata.kind
        )
        if view.state != "ready" or facts.format == "tgs":
            raise Refused("Catalog preview cannot be rendered here")
        path = private_scratch(self.workspace.path, suffix=".bin")
        try:
            self.cache.link(found, path)
            current()
        except OSError:
            path.unlink(missing_ok=True)
            raise Refused("Catalog preview unavailable locally; retry") from None
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path, facts.mime, '"' + facts.sha256 + '"'

    async def animation(self, key, session, token, revision, *, guard=None):
        source = self.source(key, session, token, revision, "media", guard=guard)
        current = self.binding(key, session, token, revision, "media", source, guard)
        found = self.cache.get_catalog(
            self.identity(source, "media"),
            revision,
            "media",
            limit=self.limit(source, "media"),
        )
        if found is None:
            raise Refused("Catalog animation is not prepared locally")
        facts = await self.facts(found, source, "media")
        current()
        if (
            facts is None
            or facts.format != "tgs"
            or not representation(facts, kind=source.metadata.kind).renderer_supported
        ):
            raise Refused("Catalog animation cannot be rendered here")
        return facts.animation_json
