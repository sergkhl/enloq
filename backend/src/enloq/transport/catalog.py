"""Bounded catalog metadata through the existing, paced Telethon owner only."""

import asyncio
import base64
import math
import re

from telethon import errors, functions, types, utils

from ..domain.catalog import (
    GIF_LIMIT,
    LIST_LIMIT,
    PACK_LIMIT,
    SEARCH_LIMIT,
    STICKER_LIMIT,
    CatalogLibrary,
    CatalogMetadata,
    CatalogOrigin,
    CatalogPackData,
    CatalogSearchData,
    NativeCatalogSource,
    NativePackReference,
    NativeThumbnail,
    WebCatalogSource,
)
from ..domain.models import Refused, Unavailable

ENTITLEMENT_TIMEOUT = 15


class ExactReferenceUnavailable(Unavailable):
    """Bounded known sources no longer contain the exact chosen document."""


def text(value, bound):
    return value[:bound] if isinstance(value, str) else None


def positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**63


def signed(value):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and -(2**63) <= value < 2**63
    )


def native_number(value, *, identity=False):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise Refused("Native media identity is unavailable") from None
    if not signed(number) or (identity and number <= 0):
        raise Refused("Native media identity is unavailable")
    return number


def pack_reference(value):
    if (
        not isinstance(value, types.StickerSet)
        or not positive(value.id)
        or not signed(value.access_hash)
    ):
        raise Refused("Sticker pack metadata unavailable")
    return NativePackReference(
        id=str(value.id),
        access_hash=str(value.access_hash),
        hash=value.hash,
        title=text(value.title, 256) or "Sticker pack",
        short_name=text(value.short_name, 64) or "",
        count=max(0, value.count),
    )


def origin_for_sticker(attribute):
    value = attribute.stickerset
    if (
        isinstance(value, types.InputStickerSetID)
        and positive(value.id)
        and signed(value.access_hash)
    ):
        return CatalogOrigin(
            kind="pack",
            pack=NativePackReference(
                id=str(value.id),
                access_hash=str(value.access_hash),
                title="",
                short_name="",
                count=0,
            ),
        )
    if isinstance(value, types.InputStickerSetShortName):
        return CatalogOrigin(kind="pack", short_name=text(value.short_name, 64))
    return None


def native_source(document, account_id, kind, origin, *, keywords=()):
    if (
        not isinstance(document, types.Document)
        or not positive(document.id)
        or not signed(document.access_hash)
        or not positive(document.dc_id)
    ):
        return None
    if (
        not isinstance(document.file_reference, bytes)
        or len(document.file_reference) > 4096
    ):
        return None
    mime = text(document.mime_type, 128) or ""
    formats = (
        {"image/gif": "gif", "video/mp4": "mp4"}
        if kind == "gif"
        else {
            "image/webp": "static",
            "application/x-tgsticker": "animated",
            "video/webm": "video",
        }
    )
    sticker = next(
        (
            a
            for a in document.attributes
            if isinstance(a, types.DocumentAttributeSticker)
        ),
        None,
    )
    invalid = None
    if mime not in formats or (kind == "sticker" and (sticker is None or sticker.mask)):
        invalid = "This media format is unavailable"
    if any(
        isinstance(a, types.DocumentAttributeCustomEmoji) for a in document.attributes
    ):
        invalid = "Custom emoji selection is unavailable"
    size = (
        document.size if isinstance(document.size, int) and document.size >= 0 else None
    )
    if size is not None and size > (GIF_LIMIT if kind == "gif" else STICKER_LIMIT):
        invalid = "This media exceeds the size limit"
    dimensions = next(
        (
            a
            for a in document.attributes
            if isinstance(
                a, (types.DocumentAttributeVideo, types.DocumentAttributeImageSize)
            )
        ),
        None,
    )
    duration = getattr(dimensions, "duration", None)
    duration = (
        duration
        if isinstance(duration, (int, float))
        and math.isfinite(duration)
        and duration >= 0
        else None
    )
    filename = next(
        (
            a.file_name
            for a in document.attributes
            if isinstance(a, types.DocumentAttributeFilename)
        ),
        None,
    )
    thumbs = [
        t
        for t in document.thumbs or ()
        if isinstance(t, (types.PhotoSize, types.PhotoSizeProgressive))
    ]
    thumbnail = None
    if thumbs:
        best = max(thumbs, key=lambda t: t.w * t.h)
        declared = (
            max(best.sizes, default=0)
            if isinstance(best, types.PhotoSizeProgressive)
            else best.size
        )
        if 0 <= declared <= STICKER_LIMIT:
            thumbnail = NativeThumbnail(
                type=best.type,
                size=declared,
                width=max(0, best.w),
                height=max(0, best.h),
            )
    origins = [origin]
    if sticker and (associated := origin_for_sticker(sticker)) and associated != origin:
        origins.append(associated)
    return NativeCatalogSource(
        account_id=account_id,
        document_id=str(document.id),
        access_hash=str(document.access_hash),
        file_reference=base64.b64encode(document.file_reference).decode(),
        dc_id=document.dc_id,
        origins=tuple(origins),
        thumbnail=thumbnail,
        metadata=CatalogMetadata(
            kind=kind,
            format=formats.get(mime, "unsupported"),
            mime=mime,
            name=text(filename, 256),
            size=size,
            duration=duration,
            width=max(0, dimensions.w) if dimensions else None,
            height=max(0, dimensions.h) if dimensions else None,
            emoji=text(sticker.alt, 32) if sticker else None,
            keywords=tuple(
                text(word, 64) for word in keywords[:16] if isinstance(word, str)
            ),
            requires_premium=bool(
                kind == "sticker"
                and any(
                    getattr(t, "type", None) == "f" for t in document.video_thumbs or ()
                )
            ),
            unavailable=invalid,
        ),
    )


def sources(values, account_id, kind, origin, *, keywords=None):
    found = []
    for document in values[:LIST_LIMIT]:
        value = native_source(
            document,
            account_id,
            kind,
            origin,
            keywords=(keywords or {}).get(getattr(document, "id", None), ()),
        )
        if value is not None:
            found.append(value)
    return tuple(found)


def web_source(result, account_id):
    from .web_media import validate_web_url

    content = result.content
    if not isinstance(content, (types.WebDocument, types.WebDocumentNoProxy)):
        return None
    mime = text(content.mime_type, 128) or ""
    unavailable = None
    try:
        url = validate_web_url(content.url)
    except Refused:
        # Unsafe source URLs never enter a catalog ticket.
        return None
    if mime not in {"image/gif", "video/mp4", "application/octet-stream"}:
        unavailable = "This media format is unavailable"
    if content.size > GIF_LIMIT:
        unavailable = "This media exceeds the size limit"
    thumb = None
    if getattr(result, "thumb", None):
        try:
            thumb = validate_web_url(result.thumb.url)
        except Refused:
            pass
    dimension = next(
        (
            a
            for a in content.attributes
            if isinstance(
                a, (types.DocumentAttributeVideo, types.DocumentAttributeImageSize)
            )
        ),
        None,
    )
    return WebCatalogSource(
        account_id=account_id,
        remote_id=result.id,
        url=url,
        thumbnail_url=thumb,
        metadata=CatalogMetadata(
            kind="gif",
            format={"image/gif": "gif", "video/mp4": "mp4"}.get(mime, "unsupported"),
            mime=mime,
            size=max(0, content.size),
            name=text(result.title, 256),
            width=max(0, dimension.w) if dimension else None,
            height=max(0, dimension.h) if dimension else None,
            unavailable=unavailable,
        ),
    )


class NativeCatalog:
    def __init__(self, owner):
        self.owner = owner

    def current(self):
        self.owner.ensure_authorized()
        if not self.owner.connected:
            raise Unavailable("Telegram account is not connected and verified")
        return self.owner.client

    async def entitlement(self):
        try:
            async with asyncio.timeout(ENTITLEMENT_TIMEOUT):
                me = await self.current().get_me()
                self.current()
                self.owner._accept_identity(me)
                self.current()
                return bool(getattr(me, "premium", False))
        except TimeoutError:
            raise Unavailable("Account entitlement is unavailable; try again") from None

    async def library(self):
        async with asyncio.timeout(45):
            premium = await self.entitlement()
            saved = await self.current()(functions.messages.GetSavedGifsRequest(hash=0))
            installed = await self.current()(
                functions.messages.GetAllStickersRequest(hash=0)
            )
            recent = await self.current()(
                functions.messages.GetRecentStickersRequest(hash=0, attached=False)
            )
            favorites = await self.current()(
                functions.messages.GetFavedStickersRequest(hash=0)
            )
            if not all(
                hasattr(value, field)
                for value, field in (
                    (saved, "gifs"),
                    (installed, "sets"),
                    (recent, "stickers"),
                    (favorites, "stickers"),
                )
            ):
                raise Unavailable("Library refresh unavailable")
            self.current()
            packs = [
                p for p in installed.sets if not (p.archived or p.masks or p.emojis)
            ]
            gifs = sources(
                saved.gifs, self.owner.account_id, "gif", CatalogOrigin(kind="saved")
            )
            recent_items = sources(
                recent.stickers,
                self.owner.account_id,
                "sticker",
                CatalogOrigin(kind="recent"),
            )
            favorite_items = sources(
                favorites.stickers,
                self.owner.account_id,
                "sticker",
                CatalogOrigin(kind="favorites"),
            )
            return CatalogLibrary(
                premium=premium,
                gifs=gifs,
                recent=recent_items,
                favorites=favorite_items,
                packs=tuple(pack_reference(p) for p in packs[:PACK_LIMIT]),
                truncated=len(saved.gifs) > len(gifs)
                or len(recent.stickers) > len(recent_items)
                or len(favorites.stickers) > len(favorite_items)
                or len(packs) > PACK_LIMIT,
            )

    async def pack(self, pack):
        async with asyncio.timeout(30):
            native = types.InputStickerSetID(
                id=native_number(pack.id, identity=True),
                access_hash=native_number(pack.access_hash),
            )
            result = await self.current()(
                functions.messages.GetStickerSetRequest(stickerset=native, hash=0)
            )
            self.current()
            if (
                not isinstance(result, types.messages.StickerSet)
                or str(result.set.id) != pack.id
            ):
                raise Refused("The exact sticker pack is unavailable")
            current = pack_reference(result.set)
            keywords = {
                k.document_id: list(k.keyword[:16])
                for k in result.keywords[:LIST_LIMIT]
            }
            for item in result.packs[:LIST_LIMIT]:
                for identity in item.documents[:LIST_LIMIT]:
                    if len(keywords.setdefault(identity, [])) < 16:
                        keywords[identity].append(item.emoticon)
            items = sources(
                result.documents,
                self.owner.account_id,
                "sticker",
                CatalogOrigin(kind="pack", pack=current),
                keywords=keywords,
            )
            return CatalogPackData(
                pack=current,
                items=items,
                truncated=len(result.documents) > len(items),
            )

    async def search(self, query, offset=""):
        async with asyncio.timeout(30):
            client = self.current()
            config = await client(functions.help.GetConfigRequest())
            username = getattr(config, "gif_search_username", None)
            if not isinstance(username, str) or not re.fullmatch(
                r"[A-Za-z0-9_]{1,64}", username
            ):
                raise Unavailable("Telegram GIF search is unavailable")
            bot = utils.get_input_user(await client.get_input_entity(username))
            result = await self.current()(
                functions.messages.GetInlineBotResultsRequest(
                    bot=bot,
                    peer=types.InputPeerEmpty(),
                    query=query,
                    offset=offset,
                    geo_point=None,
                )
            )
            self.current()
            found = []
            for item in result.results[:SEARCH_LIMIT]:
                if item.type != "gif":
                    continue
                if isinstance(item, types.BotInlineMediaResult) and isinstance(
                    item.document, types.Document
                ):
                    value = native_source(
                        item.document,
                        self.owner.account_id,
                        "gif",
                        CatalogOrigin(kind="search"),
                    )
                elif isinstance(item, types.BotInlineResult):
                    value = web_source(item, self.owner.account_id)
                else:
                    value = None
                if value is not None:
                    found.append(value)
            return CatalogSearchData(
                items=tuple(found),
                next_offset=text(result.next_offset, 256) or None,
                truncated=len(result.results) > len(found),
            )

    async def refresh_same_document(self, source):
        if source.account_id != self.owner.account_id:
            raise Refused("Media belongs to another account")
        native_number(source.document_id, identity=True)
        native_number(source.access_hash)
        async with asyncio.timeout(45):
            for origin in source.origins[:4]:
                try:
                    if origin.kind == "pack" and (origin.pack or origin.short_name):
                        locator = (
                            types.InputStickerSetID(
                                native_number(origin.pack.id, identity=True),
                                native_number(origin.pack.access_hash),
                            )
                            if origin.pack
                            else types.InputStickerSetShortName(origin.short_name)
                        )
                        result = await self.current()(
                            functions.messages.GetStickerSetRequest(locator, hash=0)
                        )
                        documents = getattr(result, "documents", ())
                    elif origin.kind in {"saved", "search"}:
                        result = await self.current()(
                            functions.messages.GetSavedGifsRequest(hash=0)
                        )
                        documents = getattr(result, "gifs", ())
                    elif origin.kind == "recent":
                        result = await self.current()(
                            functions.messages.GetRecentStickersRequest(
                                hash=0, attached=False
                            )
                        )
                        documents = getattr(result, "stickers", ())
                    elif origin.kind == "favorites":
                        result = await self.current()(
                            functions.messages.GetFavedStickersRequest(hash=0)
                        )
                        documents = getattr(result, "stickers", ())
                    else:
                        continue
                except errors.BadRequestError:
                    # A disappeared known list/pack cannot substitute another document.
                    continue
                self.current()
                chosen = next(
                    (
                        d
                        for d in documents[:LIST_LIMIT]
                        if str(getattr(d, "id", None)) == source.document_id
                    ),
                    None,
                )
                if chosen:
                    fresh = native_source(
                        chosen, self.owner.account_id, source.metadata.kind, origin
                    )
                    if fresh and fresh.document_id == source.document_id:
                        return fresh.model_copy(update={"origins": source.origins})
            raise ExactReferenceUnavailable(
                "Exact media reference unavailable; select it again"
            )
