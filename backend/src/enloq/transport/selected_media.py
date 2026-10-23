"""Exact selected media through the existing Session Owner and file lane."""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import os
import stat
from pathlib import Path

from telethon import errors, functions, types
from telethon.errors.rpcerrorlist import rpc_errors_dict

from ..domain.catalog import (
    GIF_LIMIT,
    STICKER_LIMIT,
    CatalogOrigin,
    NativeCatalogSource,
)
from ..domain.delivery import ReceiptBatch, SendMedia, StickerReselectionRequired
from ..domain.models import DeliveryRefused, MediaTooLarge, Refused, Unavailable
from ..domain.selection_formats import validate_selection
from . import group_sending
from .catalog import (
    ExactReferenceUnavailable,
    NativeCatalog,
    native_number,
    native_source,
)
from .mtproto import FILE_PRIORITY, PREPARATION, native_request_guard

DOWNLOAD_TIMEOUT = 120
# The read that proves a native document's file reference before its send.
PROBE = 4096
REFERENCE_ERRORS = (errors.FileReferenceExpiredError, errors.FileReferenceInvalidError)
# Telegram's definite answers while media is prepared: a bad request or a
# refusal. Flood waits, server errors and lost authorization keep their own
# handling (ADR 32).
DEFINITE = (errors.BadRequestError, errors.ForbiddenError)
# Telegram's own name for each error Telethon knows, such as MEDIA_INVALID.
# Telethon's instances of these report only BAD_REQUEST or FORBIDDEN.
ANSWERS = {kind: name for name, kind in rpc_errors_dict.items()}


def named_refusal(error, what):
    """The Not sent line for Telegram's definite answer during preparation."""
    answer = ANSWERS.get(type(error))
    if answer is None:
        # An error Telethon doesn't know keeps Telegram's name on the
        # instance; one it matches by pattern is named by its class.
        answer = error.message if type(error) in DEFINITE else type(error).__name__
    return DeliveryRefused(f"Telegram refused {what} ({answer}). Nothing was sent.")


@contextlib.asynccontextmanager
async def _preparation_scope(owner, description, target, evidence):
    try:
        with native_request_guard(group_sending.preparation_guard(target, evidence)):
            yield
    except group_sending.ACCESS_ERRORS as error:
        group_sending.lost(owner, description.peer)
        evidence.guard()
        raise DeliveryRefused(type(error).__name__) from None
    except DEFINITE as error:
        evidence.guard()
        what = "this GIF" if description.media.kind == "gif" else "this sticker"
        raise named_refusal(error, what) from None


def source_location(source, *, thumb=False, max_bytes):
    if not isinstance(source, NativeCatalogSource):
        raise Refused("An exact native catalog source is required")
    ceiling = STICKER_LIMIT if thumb or source.metadata.kind == "sticker" else GIF_LIMIT
    if type(max_bytes) is not int or not 0 < max_bytes <= ceiling:
        raise Refused("Selected media bound is invalid")
    if thumb:
        if source.thumbnail is None:
            raise Unavailable("Native media thumbnail is unavailable")
        declared, selector = source.thumbnail.size, source.thumbnail.type
    else:
        declared, selector = source.metadata.size, ""
    if declared is not None and declared > max_bytes:
        raise MediaTooLarge("Selected media exceeds the file limit")
    try:
        reference = base64.b64decode(source.file_reference, validate=True)
    except ValueError:
        raise Refused("Native media reference is unavailable") from None
    return types.InputDocumentFileLocation(
        id=native_number(source.document_id, identity=True),
        access_hash=native_number(source.access_hash),
        file_reference=reference,
        thumb_size=selector,
    ), declared


def new_private_file(path):
    path = Path(path).absolute()
    descriptor = None
    identity = None
    try:
        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_mode & 0o077
            or path.parent.resolve() != path.parent
        ):
            raise Refused("Selected media workspace is unavailable")
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        created = path.lstat()
        identity = (created.st_dev, created.st_ino)
        os.fchmod(descriptor, 0o600)
        status = os.fstat(descriptor)
        if (status.st_dev, status.st_ino) != identity:
            raise OSError("Selected media file changed")
        return path, descriptor, (status.st_dev, status.st_ino)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        if identity is not None:
            remove_owned(path, identity)
        raise Refused("Selected media workspace is unavailable") from None


def remove_owned(path, identity):
    try:
        status = path.lstat()
        if (status.st_dev, status.st_ino) == identity:
            path.unlink()
    except OSError:
        pass


async def download_selected(owner, source, path, *, max_bytes, thumb=False):
    native = NativeCatalog(owner)
    native.current()
    source_location(source, thumb=thumb, max_bytes=max_bytes)
    if source.account_id != owner.account_id:
        raise Refused("Selected media belongs to another account")
    # Reject unsupported paths/declared oversize before creating scratch bytes.
    path, descriptor, identity = new_private_file(path)
    complete = False
    try:
        with os.fdopen(descriptor, "wb") as output:
            async with asyncio.timeout(DOWNLOAD_TIMEOUT):
                for attempt in range(2):
                    location, declared = source_location(
                        source, thumb=thumb, max_bytes=max_bytes
                    )
                    parts = native.current().iter_download(
                        location, dc_id=source.dc_id, request_size=512 * 1024
                    )
                    total = 0
                    try:
                        while True:
                            token = FILE_PRIORITY.set(PREPARATION)
                            try:
                                chunk = await parts.__anext__()
                            except StopAsyncIteration:
                                break
                            finally:
                                FILE_PRIORITY.reset(token)
                            native.current()
                            total += len(chunk)
                            if total > max_bytes:
                                raise MediaTooLarge(
                                    "Selected media exceeds the file limit"
                                )
                            output.write(chunk)
                    except REFERENCE_ERRORS:
                        if attempt:
                            raise Unavailable(
                                "Exact media reference is unavailable; select it again"
                            ) from None
                        fresh = await native.refresh_same_document(source)
                        if (
                            fresh.account_id != source.account_id
                            or fresh.document_id != source.document_id
                            or fresh.metadata.kind != source.metadata.kind
                        ):
                            raise Refused("Exact selected media changed")
                        source = fresh
                        output.seek(0)
                        output.truncate()
                        continue
                    finally:
                        close = getattr(parts, "aclose", None) or getattr(
                            parts, "close", None
                        )
                        if close:
                            await close()
                    native.current()
                    if not total or (declared is not None and total != declared):
                        raise Unavailable("Complete selected media is unavailable")
                    break
        detached = NativeCatalogSource.model_validate(source.model_dump(mode="json"))
        complete = True
        return detached
    except TimeoutError:
        raise Unavailable("Selected media download is unavailable; try again") from None
    finally:
        if not complete:
            remove_owned(path, identity)


def validate_retained(media):
    """Synchronous private-file/digest guard, including the final wire boundary."""
    if not isinstance(media, SendMedia):
        raise DeliveryRefused("An exact retained media description is required")
    limit = GIF_LIMIT if media.kind == "gif" else STICKER_LIMIT
    try:
        directory = Path(media.directory).absolute()
        path = Path(media.path).absolute()
        parent, status = directory.lstat(), path.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_mode & 0o077
            or directory.resolve() != directory
            or path.parent != directory
            or not stat.S_ISREG(status.st_mode)
            or status.st_mode & 0o077
            or not 0 < status.st_size == media.size <= limit
        ):
            raise ValueError("Invalid retained file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            digest, total = hashlib.sha256(), 0
            for chunk in iter(lambda: stream.read(65536), b""):
                total += len(chunk)
                if total > limit:
                    raise ValueError("Oversize retained media")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        current = path.lstat()

        def identity(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
                value.st_mode,
            )

        if (
            identity(status) != identity(before)
            or identity(before) != identity(after)
            or identity(after) != identity(current)
            or total != media.size
            or not hmac.compare_digest(digest.hexdigest(), media.sha256)
        ):
            raise ValueError("Changed retained media")
    except (OSError, ValueError, TypeError):
        raise DeliveryRefused(
            "Exact selected media is unavailable or changed; select it again"
        ) from None


async def validate_send_media(media):
    validate_retained(media)
    facts = await validate_selection(
        media.path, media.kind, expected_sha256=media.sha256
    )
    if any(
        getattr(facts, key) != getattr(media, key)
        for key in (
            "kind",
            "format",
            "mime",
            "size",
            "width",
            "height",
            "duration",
            "sticker",
        )
    ):
        raise DeliveryRefused("Captured selected media facts changed")
    validate_retained(media)
    return facts


def input_document(source):
    # Download location validation also bounds every native identity/reference.
    location, _ = source_location(
        source, max_bytes=GIF_LIMIT if source.metadata.kind == "gif" else STICKER_LIMIT
    )
    return types.InputMediaDocument(
        types.InputDocument(location.id, location.access_hash, location.file_reference)
    )


async def probe_reference(owner, source):
    """Read the first 4 KB of the exact document. The final send needs its
    file reference to be current; this proves it without sending anything."""
    location, _ = source_location(
        source,
        max_bytes=GIF_LIMIT if source.metadata.kind == "gif" else STICKER_LIMIT,
    )
    parts = (
        NativeCatalog(owner)
        .current()
        .iter_download(location, dc_id=source.dc_id, request_size=PROBE, limit=1)
    )
    try:
        token = FILE_PRIORITY.set(PREPARATION)
        try:
            await parts.__anext__()
        except StopAsyncIteration:
            raise DeliveryRefused(
                "Telegram has no bytes for this media; select it again"
            ) from None
        finally:
            FILE_PRIORITY.reset(token)
    finally:
        close = getattr(parts, "aclose", None) or getattr(parts, "close", None)
        if close:
            await close()


async def prepare_document(owner, media, evidence):
    """The exact native document for the final send: its reference proved by a
    4 KB read and renewed at most once through its known source. Telegram
    answers `messages.uploadMedia` with an existing document with
    MEDIA_INVALID (the first live send, 2026-10-17), so nothing is associated
    with the chat first (ADR 32)."""
    source = media.source
    if not isinstance(source, NativeCatalogSource):
        if media.kind == "sticker":
            raise StickerReselectionRequired(
                "Original sticker reference is unavailable; select it again"
            )
        return None, media.requires_premium
    for attempt in range(2):
        evidence.guard()
        try:
            await probe_reference(owner, source)
        except REFERENCE_ERRORS:
            if attempt:
                break
            try:
                renewed = await NativeCatalog(owner).refresh_same_document(source)
            except ExactReferenceUnavailable:
                break
            if (
                renewed.document_id != source.document_id
                or renewed.account_id != source.account_id
                or renewed.metadata.kind != source.metadata.kind
            ):
                raise DeliveryRefused("Exact native media changed")
            if renewed.metadata.unavailable or renewed.metadata.mime != media.mime:
                raise DeliveryRefused("Exact native media is unavailable")
            source = renewed
            continue
        evidence.guard()
        return (
            input_document(source),
            media.requires_premium or source.metadata.requires_premium,
        )
    if media.kind == "sticker":
        raise StickerReselectionRequired(
            "Original sticker reference is unavailable; select it again"
        )
    return None, False


async def upload_exact_gif(owner, peer, media, evidence):
    validate_retained(media)
    evidence.guard()
    client = NativeCatalog(owner).current()
    file = await client.upload_file(
        media.path, file_name="animation." + media.format, file_size=media.size
    )
    evidence.guard()
    validate_retained(media)
    attributes = [
        types.DocumentAttributeFilename("animation." + media.format),
        types.DocumentAttributeAnimated(),
    ]
    if media.format == "mp4":
        attributes.append(
            types.DocumentAttributeVideo(
                duration=media.duration or 0,
                w=media.width,
                h=media.height,
                nosound=True,
                supports_streaming=True,
            )
        )
    else:
        attributes.append(types.DocumentAttributeImageSize(media.width, media.height))
    uploaded = types.InputMediaUploadedDocument(
        file=file,
        mime_type=media.mime,
        attributes=attributes,
        nosound_video=True,
        force_file=False,
    )
    # Associating uploaded bytes is preparation only; it supplies no send receipt.
    result = await NativeCatalog(owner).current()(
        functions.messages.UploadMediaRequest(peer, uploaded)
    )
    evidence.guard()
    if not isinstance(result, types.MessageMediaDocument) or not isinstance(
        result.document, types.Document
    ):
        raise DeliveryRefused("Uploaded GIF preparation is unavailable")
    document = result.document
    if (
        not any(
            isinstance(attribute, types.DocumentAttributeAnimated)
            for attribute in document.attributes
        )
        or document.mime_type != "video/mp4"
    ):
        raise DeliveryRefused("Uploaded GIF native format is unavailable")
    # Telegram converts a GIF upload on its server; the application uploads the exact retained bytes.
    source = native_source(
        document, owner.account_id, "gif", CatalogOrigin(kind="saved")
    )
    if source is None or source.metadata.unavailable:
        raise DeliveryRefused("Uploaded GIF native reference is unavailable")
    return input_document(source)


async def send_selected(owner, description, evidence):
    native = NativeCatalog(owner)
    native.current()
    media = description.media
    if (
        description.account_id != owner.account_id
        or media.source.account_id != owner.account_id
        or len(description.random_ids) != 1
        or description.images
        or (media.kind == "sticker" and description.text)
    ):
        raise DeliveryRefused("Connected selected-media sender does not match")
    group_sending.captured_peer(description.peer)
    # Exact account/chat/reply/browser operation guards are checked again by evidence.
    evidence.guard()
    await validate_send_media(media)
    evidence.guard()
    target = await group_sending.prepare(owner, description, evidence)
    peer = target.peer
    async with (
        asyncio.timeout(120),
        _preparation_scope(owner, description, target, evidence),
    ):
        config = await native.current()(functions.help.GetConfigRequest())
        bound = min(1024, getattr(config, "caption_length_max", 0))
        if not bound or len(description.text.encode("utf-16-le")) // 2 > bound:
            raise DeliveryRefused("Caption exceeds the current native limit")
        if media.requires_premium or media.source.metadata.requires_premium:
            if not await native.entitlement():
                raise DeliveryRefused(
                    "Telegram Premium is required to send this sticker"
                )
        prepared, requires_premium = await prepare_document(owner, media, evidence)
        if prepared is None:
            prepared = await upload_exact_gif(owner, peer, media, evidence)
        # Refresh current identity and entitlement after every preparation await.
        premium = await native.entitlement()
        if requires_premium and not premium:
            raise DeliveryRefused("Telegram Premium is required to send this sticker")
    if target.proof:
        target = await group_sending.prepare(owner, description, evidence)
        if bytes(target.peer) != bytes(peer):
            raise DeliveryRefused("Captured selected-media send peer changed")
        peer = target.peer
    evidence.guard()
    validate_retained(media)
    reply = (
        types.InputReplyToMessage(description.reply_to, top_msg_id=description.topic_id)
        if description.reply_to
        else None
    )
    request = functions.messages.SendMediaRequest(
        peer=peer,
        media=prepared,
        message=description.text,
        random_id=description.random_ids[0],
        reply_to=reply,
        send_as=target.send_as,
        allow_paid_stars=0,
        allow_paid_floodskip=False,
    )

    def final_guard(request):
        native.current()
        validate_retained(media)
        if (
            request.message != description.text
            or bytes(request.media) != prepared_bytes
            or request.allow_paid_stars != 0
            or request.allow_paid_floodskip is not False
            or getattr(request.reply_to, "top_msg_id", None)
            != (description.topic_id if description.reply_to else None)
        ):
            raise DeliveryRefused("Captured selected-media send changed")
        if requires_premium and (
            not getattr(owner.identity, "premium", False)
            or owner.store.clock() - owner.identity_checked_at > 15
        ):
            raise DeliveryRefused("Current Telegram Premium entitlement is unavailable")
        evidence.guard()

    prepared_bytes = bytes(prepared)
    request._enloq_media_guard = final_guard
    request._enloq_group_send_proof = target.proof
    try:
        result = await owner._dispatch(evidence, request, description=description)
    except (
        *REFERENCE_ERRORS,
        *group_sending.SEND_REFUSALS,
        errors.MediaCaptionTooLongError,
        errors.MediaInvalidError,
        errors.StickerInvalidError,
        errors.PremiumAccountRequiredError,
    ) as error:
        # This is the final definite rejection. Deliberate Retry starts a fresh guarded preparation.
        raise group_sending.refusal(owner, description, evidence, error) from None
    except errors.RPCError as error:
        if group_sending.paid_refusal(error):
            raise owner._refused(evidence, error) from None
        raise
    receipts = {}
    if isinstance(result, types.UpdateShortSentMessage):
        receipts[description.random_ids[0]] = result.id
        await owner._receipt(
            types.UpdateMessageID(result.id, description.random_ids[0])
        )
    for update in getattr(result, "updates", ()):
        if (
            isinstance(update, types.UpdateMessageID)
            and update.random_id == description.random_ids[0]
        ):
            await owner._receipt(update)
            receipts[update.random_id] = update.id
    return ReceiptBatch(tuple(receipts.items()))
