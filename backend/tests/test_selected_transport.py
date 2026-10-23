"""Exact selected bytes and final native sends against authored in-memory peers."""

import asyncio
import base64
import os
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from enloq.domain.catalog import (
    CatalogMetadata,
    CatalogOrigin,
    WebCatalogSource,
)
from enloq.domain.delivery import ReceiptBatch, SendDescription, SendMedia
from enloq.domain.models import (
    DeliveryRefused,
    MediaTooLarge,
    Refused,
    Unavailable,
)
from enloq.domain.selection_formats import validate_selection
from enloq.transport import selected_media
from enloq.transport.catalog import native_source
from enloq.transport.fake import FakeTransport
from enloq.transport.mtproto import FILE_PRIORITY, PREPARATION
from PIL import Image
from telethon import errors, functions, types

from backend.tests.test_native_catalog import BIG_ID, RPC, document, owner
from backend.tests.test_selection_formats import containers as containers
from backend.tests.test_selection_formats import write_animation


def image_bytes(kind="gif"):
    buffer = BytesIO()
    Image.new(
        "RGBA" if kind == "sticker" else "RGB",
        (512, 512) if kind == "sticker" else (16, 16),
        "blue",
    ).save(buffer, "WEBP" if kind == "sticker" else "GIF", lossless=True)
    return buffer.getvalue()


async def retained(tmp_path, *, kind="gif", premium=False, web=False, payload=None):
    directory = tmp_path / "retained"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / ("selected.gif" if kind == "gif" else "selected.webp")
    path.write_bytes(payload if payload is not None else image_bytes(kind))
    path.chmod(0o600)
    facts = await validate_selection(path, kind)
    doc = document(kind=kind, mime=facts.mime, size=facts.size, premium=premium)
    if facts.format == "mp4":
        doc.attributes.append(types.DocumentAttributeAnimated())
    source = native_source(
        doc, 1, kind, CatalogOrigin(kind="saved" if kind == "gif" else "recent")
    )
    if web:
        source = WebCatalogSource(
            account_id=1,
            remote_id="authored",
            url="https://example.org/authored.gif",
            metadata=CatalogMetadata(
                kind="gif", format="gif", mime="image/gif", size=facts.size
            ),
        )
    media = SendMedia(
        id="a" * 32,
        path=path,
        directory=directory,
        sha256=facts.sha256,
        mime=facts.mime,
        size=facts.size,
        width=facts.width,
        height=facts.height,
        kind=kind,
        format=facts.format,
        source=source,
        duration=facts.duration,
        sticker=facts.sticker,
        emoji="🙂" if kind == "sticker" else None,
        requires_premium=premium,
    )
    return media, doc


def description(media, text=None):
    return SendDescription(
        "b" * 32,
        1,
        "user:42",
        text if text is not None else "Authored caption" if media.kind == "gif" else "",
        77,
        (2**53 + 900,),
        media=media,
    )


class Evidence:
    def __init__(self, guard=None):
        self.events = []
        self.refusals = []
        self._guard = guard

    def guard(self):
        if self._guard:
            self._guard()

    def calling(self):
        self.events.append("calling")

    def watching(self):
        self.events.append("watched")

    def handing_off(self):
        self.events.append("exposed")

    def refused(self, reason):
        self.refusals.append(reason)


class MediaRPC(RPC):
    """`prep` holds the reference probes' outcomes, an error or a chunk; a
    probe without one reads b"probe"."""

    def __init__(self, doc, *, prep=None, send=None, premium=False):
        super().__init__(premium=premium)
        self.doc = doc
        self.prep = list(prep or [])
        self.send_answers = list(send or [55])
        self.uploads = []
        self.probes = []
        self.closed = 0
        self.on_prepare = None
        self.on_final = None

    def iter_download(self, location, **kwargs):
        self.probes.append((location, kwargs))
        outcome = self.prep.pop(0) if self.prep else b"probe"

        async def iterator():
            try:
                assert FILE_PRIORITY.get() == PREPARATION
                if isinstance(outcome, BaseException):
                    raise outcome
                if self.on_prepare:
                    self.on_prepare()
                if outcome:
                    yield outcome
            finally:
                self.closed += 1

        return iterator()

    async def get_input_entity(self, peer):
        return types.InputPeerUser(peer.user_id, 99)

    async def upload_file(self, path, **kwargs):
        self.uploads.append((Path(path).read_bytes(), kwargs))
        return types.InputFile(700, 1, kwargs["file_name"], "authored")

    async def __call__(self, request):
        self.requests.append(request)
        assert bytes(request)
        if isinstance(request, functions.help.GetConfigRequest):
            return SimpleNamespace(caption_length_max=1024)
        if isinstance(request, functions.messages.GetSavedGifsRequest):
            return types.messages.SavedGifs(1, [self.doc])
        if isinstance(request, functions.messages.GetRecentStickersRequest):
            return types.messages.RecentStickers(1, [], [self.doc], [])
        if isinstance(request, functions.messages.UploadMediaRequest):
            if not isinstance(request.media, types.InputMediaUploadedDocument):
                # Telegram's answer for an existing document (2026-10-17).
                raise errors.MediaInvalidError(request)
            answer = document(12345, mime="video/mp4")
            answer.attributes.append(types.DocumentAttributeAnimated())
            return types.MessageMediaDocument(document=answer)
        if isinstance(request, functions.messages.SendMediaRequest):
            if self.on_final:
                self.on_final(request)
                request._enloq_final_guard(request)
            answer = self.send_answers.pop(0)
            if answer is None:
                await asyncio.Future()
            if isinstance(answer, BaseException):
                raise answer
            return types.UpdateShortSentMessage(answer, 1, 1, None, out=True)
        raise AssertionError(type(request))


def sender(client):
    transport = owner(client)
    receipts = []
    transport.bind_receipts(receipts.append)
    return transport, receipts


def source_for(data=b"authored", *, thumb=False):
    doc = document(size=len(data))
    if thumb:
        doc.size = 50 * 1024**2 + 1
        doc.thumbs = [types.PhotoSize("m", 64, 64, len(data))]
    return native_source(doc, 1, "gif", CatalogOrigin(kind="saved"))


class DownloadRPC(RPC):
    def __init__(self, chunks, refreshed=None):
        super().__init__()
        self.chunks = list(chunks)
        self.locations = []
        self.refreshed = refreshed
        self.closed = 0

    def iter_download(self, location, **kwargs):
        self.locations.append((location, kwargs))

        async def iterator():
            try:
                for value in self.chunks.pop(0):
                    assert FILE_PRIORITY.get() == PREPARATION
                    if value is None:
                        await asyncio.Future()
                    if isinstance(value, BaseException):
                        raise value
                    yield value
            finally:
                self.closed += 1

        return iterator()

    async def __call__(self, request):
        self.requests.append(request)
        assert isinstance(request, functions.messages.GetSavedGifsRequest)
        return types.messages.SavedGifs(1, [self.refreshed] if self.refreshed else [])


async def test_exact_download_uses_private_file_dc_location_and_selected_thumb_cap(
    tmp_path,
):
    tmp_path.chmod(0o700)
    for thumb in (False, True):
        source = source_for(thumb=thumb)
        client = DownloadRPC([[b"auth", b"ored"]])
        result = await owner(client).catalog_download(
            source, tmp_path / str(thumb), max_bytes=100, thumb=thumb
        )
        assert result.document_id == str(BIG_ID) and result is not source
        path = tmp_path / str(thumb)
        assert path.read_bytes() == b"authored" and path.stat().st_mode & 0o777 == 0o600
        location, kwargs = client.locations[0]
        assert location.id == BIG_ID and location.access_hash == -17
        assert location.thumb_size == ("m" if thumb else "")
        assert location.file_reference == b"authored-ref" and kwargs["dc_id"] == 2
        assert bytes(location) and client.closed == 1


async def test_download_renews_only_same_document_once_and_resets_partial(tmp_path):
    tmp_path.chmod(0o700)
    source = source_for()
    refreshed = document(size=8, reference=b"new-reference")
    expired = errors.FileReferenceExpiredError(None)
    client = DownloadRPC([[b"bad", expired], [b"authored"]], refreshed)
    result = await owner(client).catalog_download(
        source, tmp_path / "renewed", max_bytes=100
    )
    assert (tmp_path / "renewed").read_bytes() == b"authored"
    assert result.file_reference == base64.b64encode(b"new-reference").decode()
    assert len(client.locations) == 2 and len(client.requests) == 1
    assert all(location.id == BIG_ID for location, _ in client.locations)
    assert client.closed == 2


@pytest.mark.parametrize(
    "case", ["overflow", "partial", "reference", "wrong", "timeout", "revoked"]
)
async def test_failed_download_never_retains_partial_or_substitutes_document(
    tmp_path, monkeypatch, case
):
    tmp_path.chmod(0o700)
    source = source_for()
    expired = errors.FileReferenceExpiredError(None)
    chunks = [[b"authored"]]
    refreshed = document(size=8, reference=b"new")
    if case == "overflow":
        chunks = [[b"x" * 101]]
    elif case == "partial":
        chunks = [[b"short"]]
    elif case == "reference":
        chunks = [[expired], [expired]]
    elif case == "wrong":
        chunks, refreshed = [[expired]], document(123, size=8)
    elif case == "timeout":
        chunks = [[None]]
        monkeypatch.setattr(selected_media, "DOWNLOAD_TIMEOUT", 0.01)
    client = DownloadRPC(chunks, refreshed)
    transport = owner(client)
    if case == "revoked":
        original = client.iter_download

        def revoke(location, **kwargs):
            transport.verified = False
            return original(location, **kwargs)

        client.iter_download = revoke
    with pytest.raises((Refused, Unavailable, MediaTooLarge)):
        await transport.catalog_download(source, tmp_path / "failed", max_bytes=100)
    assert not (tmp_path / "failed").exists()
    assert len(client.locations) <= 2 and len(client.requests) <= 1


@pytest.mark.parametrize("fault", ["fchmod", "fstat"])
async def test_download_setup_failure_closes_fd_and_only_removes_own_file(
    tmp_path, monkeypatch, fault
):
    tmp_path.chmod(0o700)
    closed, original_close = [], os.close
    monkeypatch.setattr(
        selected_media.os,
        "close",
        lambda descriptor: (closed.append(descriptor), original_close(descriptor))[1],
    )
    monkeypatch.setattr(
        selected_media.os,
        fault,
        lambda *args: (_ for _ in ()).throw(OSError("authored setup failure")),
    )
    with pytest.raises(Refused):
        await owner(DownloadRPC([[b"authored"]])).catalog_download(
            source_for(), tmp_path / "partial", max_bytes=100
        )
    assert closed and not (tmp_path / "partial").exists()
    existing = tmp_path / "existing"
    existing.write_bytes(b"keep")
    with pytest.raises(Refused):
        await owner(DownloadRPC([[b"authored"]])).catalog_download(
            source_for(), existing, max_bytes=100
        )
    assert existing.read_bytes() == b"keep"


async def test_download_cancellation_preserves_existing_and_removes_owned_partial(
    tmp_path,
):
    tmp_path.chmod(0o700)
    client = DownloadRPC([[b"part", None]])
    work = asyncio.create_task(
        owner(client).catalog_download(
            source_for(), tmp_path / "cancelled", max_bytes=100
        )
    )
    while not client.locations:
        await asyncio.sleep(0)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert not (tmp_path / "cancelled").exists() and client.closed == 1


async def test_native_gif_and_sticker_prepare_then_one_stable_guarded_send(tmp_path):
    for kind in ("gif", "sticker"):
        media, doc = await retained(tmp_path, kind=kind)
        client = MediaRPC(doc)
        transport, receipts = sender(client)
        evidence = Evidence()
        result = await transport.send(description(media), evidence)
        assert result == ReceiptBatch(((2**53 + 900, 55),)) and receipts == [result]
        assert not client.uploads and client.me_reads == 1
        # The chosen document goes out as itself: nothing is associated with
        # the chat first, which Telegram refuses (MEDIA_INVALID).
        requests = client.requests
        assert [type(request) for request in requests] == [
            functions.help.GetConfigRequest,
            functions.messages.SendMediaRequest,
        ]
        # One 4 KB read from the file's own DC proves its reference first.
        [(location, kwargs)] = client.probes
        assert (location.id, location.access_hash) == (BIG_ID, -17)
        assert location.file_reference == b"authored-ref"
        assert location.thumb_size == "" and client.closed == 1
        assert kwargs == {"dc_id": 2, "request_size": 4096, "limit": 1}
        final = requests[-1]
        assert final.random_id == 2**53 + 900 and final.media.id.id == BIG_ID
        assert final.message == ("Authored caption" if kind == "gif" else "")
        assert (
            final.reply_to.reply_to_msg_id == 77
            and final.allow_paid_stars == 0
            and final.allow_paid_floodskip is False
        )
        assert final.media.query is None and evidence.events == [
            "calling",
            "watched",
            "exposed",
        ]


async def test_expired_preparation_renews_same_id_without_final_refusal_cursor(
    tmp_path,
):
    media, doc = await retained(tmp_path)
    doc.file_reference = b"renewed"
    client = MediaRPC(
        doc,
        prep=[errors.FileReferenceExpiredError(None)],
        send=[ConnectionError("answer lost")],
    )
    transport, _ = sender(client)
    evidence = Evidence()
    with pytest.raises(ConnectionError):
        await transport.send(description(media), evidence)
    assert not evidence.refusals and evidence.events[-1] == "exposed"
    finals = [
        r for r in client.requests if isinstance(r, functions.messages.SendMediaRequest)
    ]
    assert len(finals) == 1 and finals[0].random_id == 2**53 + 900
    assert finals[0].media.id.file_reference == b"renewed" and not client.uploads
    assert [location.file_reference for location, _ in client.probes] == [
        b"authored-ref",
        b"renewed",
    ]


async def test_unrecoverable_gif_uploads_exact_bytes_with_native_animation_attributes(
    tmp_path,
):
    media, doc = await retained(tmp_path)
    client = MediaRPC(
        doc,
        prep=[
            errors.FileReferenceExpiredError(None),
            errors.FileReferenceExpiredError(None),
        ],
    )
    transport, _ = sender(client)
    evidence = Evidence()
    await transport.send(description(media), evidence)
    assert len(client.uploads) == 1 and client.uploads[0][0] == media.path.read_bytes()
    upload = next(
        r
        for r in client.requests
        if isinstance(r, functions.messages.UploadMediaRequest)
        and isinstance(r.media, types.InputMediaUploadedDocument)
    )
    assert upload.media.mime_type == "image/gif" and upload.media.nosound_video
    assert any(
        isinstance(a, types.DocumentAttributeAnimated) for a in upload.media.attributes
    )
    assert all(
        not isinstance(a, types.DocumentAttributeAudio) for a in upload.media.attributes
    )
    assert not evidence.refusals and client.requests[-1].media.id.id == 12345


async def test_unrecoverable_sticker_requires_reselection_without_byte_upload(tmp_path):
    media, doc = await retained(tmp_path, kind="sticker")
    client = MediaRPC(
        doc,
        prep=[
            errors.FileReferenceExpiredError(None),
            errors.FileReferenceExpiredError(None),
        ],
    )
    transport, _ = sender(client)
    with pytest.raises(DeliveryRefused, match="select it again"):
        await transport.send(description(media), Evidence())
    assert not client.uploads and not any(
        isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
    )


async def test_final_reference_rejection_is_terminal_with_no_automatic_resend(tmp_path):
    media, doc = await retained(tmp_path)
    client = MediaRPC(doc, send=[errors.FileReferenceExpiredError(None)])
    transport, _ = sender(client)
    evidence = Evidence()
    with pytest.raises(DeliveryRefused):
        await transport.send(description(media), evidence)
    assert evidence.refusals == ["FileReferenceExpiredError"]
    assert (
        len(
            [
                r
                for r in client.requests
                if isinstance(r, functions.messages.SendMediaRequest)
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "change", ["premium", "account", "bytes", "reply", "paid", "document"]
)
async def test_final_guard_rejects_changes_before_possible_handoff(tmp_path, change):
    media, doc = await retained(tmp_path, kind="sticker", premium=change == "premium")
    client = MediaRPC(doc, premium=change == "premium")
    transport, _ = sender(client)
    evidence = Evidence()
    original_dispatch = transport._dispatch

    async def changed(witness, request, **kwargs):
        if change == "premium":
            transport.identity.premium = False
        elif change == "account":
            transport.verified = False
        elif change == "bytes":
            media.path.write_bytes(b"changed")
        elif change == "reply":
            request.reply_to.reply_to_msg_id = 78
        elif change == "paid":
            request.allow_paid_stars = 1
        else:
            request.media.id.id = 123
        return await original_dispatch(witness, request, **kwargs)

    transport._dispatch = changed
    with pytest.raises((DeliveryRefused, Refused, Unavailable)):
        await transport.send(description(media), evidence)
    assert "exposed" not in evidence.events
    assert not any(
        isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
    )


async def test_current_premium_loss_after_preparation_and_copied_false_flag(tmp_path):
    media, doc = await retained(tmp_path, kind="sticker", premium=True)
    media = replace(media, requires_premium=False)
    client = MediaRPC(doc, premium=True)
    client.on_prepare = lambda: setattr(client.user, "premium", False)
    transport, _ = sender(client)
    with pytest.raises(DeliveryRefused, match="Premium"):
        await transport.send(description(media), Evidence())
    assert client.me_reads == 2 and not any(
        isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
    )


@pytest.mark.parametrize(
    "change",
    ["symlink", "escape", "digest", "size", "facts", "corrupt", "world_readable"],
)
async def test_invalid_retained_capability_is_rejected_before_native_preparation(
    tmp_path, change
):
    media, doc = await retained(tmp_path)
    if change == "symlink":
        other = media.directory / "link"
        other.symlink_to(media.path)
        media = replace(media, path=other)
    elif change == "escape":
        media = replace(media, directory=tmp_path)
    elif change == "digest":
        media = replace(media, sha256="0" * 64)
    elif change == "size":
        media = replace(media, size=50 * 1024**2 + 1)
    elif change == "facts":
        media = replace(media, width=17)
    elif change == "world_readable":
        media.path.chmod(0o644)
    else:
        media.path.write_bytes(b"broken GIF")
    client = MediaRPC(doc)
    transport, _ = sender(client)
    with pytest.raises((Refused, DeliveryRefused)):
        await transport.send(description(media), Evidence())
    assert not client.requests and not client.uploads and not client.probes


async def test_fake_transport_records_truthful_selected_kind_caption_reply_and_bytes(
    tmp_path,
):
    for kind in ("gif", "sticker"):
        media, _ = await retained(tmp_path, kind=kind)
        fake = FakeTransport()
        fake.bind_receipts(lambda _: None)
        await fake.send(description(media), Evidence())
        sent = fake.messages[0]
        assert sent.media == kind and sent.text == (
            "Authored caption" if kind == "gif" else ""
        )
        assert sent.reply_to == 77 and sent.media_info.mime == media.mime
        assert fake.media["user:42", sent.message_id] == media.path.read_bytes()
        assert sent.media_info.sticker == media.sticker


@pytest.mark.parametrize("changed", ["type", "custom emoji"])
async def test_a_renewed_reference_cannot_change_the_exact_media(tmp_path, changed):
    media, doc = await retained(tmp_path)
    if changed == "type":
        doc.mime_type = "video/mp4"
    else:
        doc.attributes.append(
            types.DocumentAttributeCustomEmoji("🙂", types.InputStickerSetEmpty())
        )
    client = MediaRPC(doc, prep=[errors.FileReferenceExpiredError(None)])
    transport, _ = sender(client)
    with pytest.raises(DeliveryRefused, match="Exact native media is unavailable"):
        await transport.send(description(media), Evidence())
    assert len(client.probes) == 1 and not client.uploads
    assert not any(
        isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
    )


@pytest.mark.parametrize("kind", ["gif", "sticker"])
async def test_telegrams_definite_answer_while_preparing_is_named(tmp_path, kind):
    # The first live send (2026-10-17) read only "Stopped before reaching
    # Telegram" after Telegram answered MEDIA_INVALID during preparation.
    media, doc = await retained(tmp_path, kind=kind)
    client = MediaRPC(doc, prep=[errors.MediaInvalidError(None)])
    transport, _ = sender(client)
    evidence = Evidence()
    with pytest.raises(DeliveryRefused) as refusal:
        await transport.send(description(media), evidence)
    what = "this GIF" if kind == "gif" else "this sticker"
    assert str(refusal.value) == (
        f"Telegram refused {what} (MEDIA_INVALID). Nothing was sent."
    )
    assert "calling" not in evidence.events and not client.uploads
    assert not any(
        isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
    )


async def test_an_empty_probe_refuses_without_a_send(tmp_path):
    media, doc = await retained(tmp_path)
    client = MediaRPC(doc, prep=[b""])
    transport, _ = sender(client)
    with pytest.raises(DeliveryRefused, match="no bytes for this media"):
        await transport.send(description(media), Evidence())
    assert client.closed == 1 and not any(
        isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
    )


async def test_a_flood_wait_while_preparing_keeps_its_own_handling(tmp_path):
    media, doc = await retained(tmp_path)
    client = MediaRPC(doc, prep=[errors.FloodWaitError(None, capture=30)])
    transport, _ = sender(client)
    with pytest.raises(errors.FloodWaitError):
        await transport.send(description(media), Evidence())


@pytest.mark.parametrize("outcome", ["lost", "timeout", "cancel"])
async def test_final_possible_exposure_never_automatically_resends(tmp_path, outcome):
    media, doc = await retained(tmp_path)
    client = MediaRPC(
        doc, send=[ConnectionError("lost answer") if outcome == "lost" else None]
    )
    transport, _ = sender(client)
    evidence = Evidence()
    if outcome == "lost":
        with pytest.raises(ConnectionError):
            await transport.send(description(media), evidence)
    elif outcome == "timeout":
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.03):
                await transport.send(description(media), evidence)
    else:
        work = asyncio.create_task(transport.send(description(media), evidence))
        while not any(
            isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
        ):
            await asyncio.sleep(0)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
    assert evidence.events[-1] == "exposed" and not evidence.refusals
    assert (
        len(
            [
                r
                for r in client.requests
                if isinstance(r, functions.messages.SendMediaRequest)
            ]
        )
        == 1
    )


async def test_caption_bound_is_current_native_utf16_and_sticker_is_captionless(
    tmp_path,
):
    media, doc = await retained(tmp_path)
    client = MediaRPC(doc)
    transport, _ = sender(client)
    with pytest.raises(DeliveryRefused, match="Caption"):
        await transport.send(description(media, text="🙂" * 513), Evidence())
    assert len(client.requests) == 1 and not client.uploads
    sticker, _ = await retained(tmp_path, kind="sticker")
    with pytest.raises(Refused):
        description(sticker, text="Hidden caption")


async def test_existing_and_symlink_download_paths_are_preserved(tmp_path):
    tmp_path.chmod(0o700)
    existing = tmp_path / "existing"
    existing.write_bytes(b"keep")
    link = tmp_path / "link"
    link.symlink_to(existing)
    for path in (existing, link):
        client = DownloadRPC([[b"authored"]])
        with pytest.raises(Refused):
            await owner(client).catalog_download(source_for(), path, max_bytes=100)
        assert not client.locations
    assert existing.read_bytes() == b"keep" and link.is_symlink()


async def test_request_policy_applies_selected_integrity_guard_before_handoff(tmp_path):
    from backend.tests.test_transport import Clock

    media, doc = await retained(tmp_path)
    client = MediaRPC(doc)
    clock = Clock()
    policy = clock.policy(tmp_path / "paced.sqlite3")
    client.request_policy = policy
    original = client.__class__.__call__

    async def paced(request):
        if isinstance(request, functions.messages.SendMediaRequest):
            media.path.write_bytes(b"changed before packer")
            return await policy.execute(request, lambda: original(client, request))
        return await original(client, request)

    class Wrapper:
        request_policy = policy

        def __getattr__(self, name):
            return getattr(client, name)

        async def __call__(self, request):
            return await paced(request)

    transport, _ = sender(Wrapper())
    evidence = Evidence()
    with pytest.raises(DeliveryRefused):
        await transport.send(description(media), evidence)
    assert evidence.events == ["calling", "watched"] and not evidence.refusals
    assert not any(
        isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
    )


@pytest.mark.parametrize("variant", ["mp4", "tgs", "webm"])
async def test_native_animated_formats_preserve_exact_identity_and_caption_rules(
    tmp_path, containers, variant
):
    kind = "gif" if variant == "mp4" else "sticker"
    path = write_animation(tmp_path) if variant == "tgs" else containers[variant]
    payload = path.read_bytes()
    media, doc = await retained(tmp_path, kind=kind, payload=payload)
    client = MediaRPC(doc)
    transport, _ = sender(client)
    await transport.send(description(media), Evidence())
    assert media.path.read_bytes() == payload and not client.uploads
    final = client.requests[-1]
    assert final.media.id.id == BIG_ID and final.message == (
        "Authored caption" if kind == "gif" else ""
    )
    assert media.format == variant and bytes(final)


async def test_web_mp4_upload_fallback_has_verified_silent_native_video_attrs(
    tmp_path, containers
):
    media, doc = await retained(
        tmp_path, web=True, payload=containers["mp4"].read_bytes()
    )
    client = MediaRPC(doc)
    transport, _ = sender(client)
    await transport.send(description(media), Evidence())
    assert client.uploads[0][0] == containers["mp4"].read_bytes()
    upload = next(
        r
        for r in client.requests
        if isinstance(r, functions.messages.UploadMediaRequest)
    )
    animated = next(
        a
        for a in upload.media.attributes
        if isinstance(a, types.DocumentAttributeAnimated)
    )
    video = next(
        a
        for a in upload.media.attributes
        if isinstance(a, types.DocumentAttributeVideo)
    )
    assert animated and video.nosound and video.supports_streaming
    assert (video.w, video.h, video.duration) == (
        media.width,
        media.height,
        media.duration,
    )
    assert upload.media.nosound_video and upload.media.force_file is False


async def test_transient_sticker_reference_refresh_failure_keeps_deliberate_retry(
    tmp_path,
):
    media, doc = await retained(tmp_path, kind="sticker")
    client = MediaRPC(doc, prep=[errors.FileReferenceExpiredError(None)])
    original = client.__class__.__call__

    class OfflineSource(MediaRPC):
        async def __call__(self, request):
            if isinstance(request, functions.messages.GetRecentStickersRequest):
                raise Unavailable("Authored metadata unavailable")
            return await original(self, request)

    client.__class__ = OfflineSource
    transport, _ = sender(client)
    from enloq.domain.delivery import StickerReselectionRequired

    with pytest.raises(Unavailable) as error:
        await transport.send(description(media), Evidence())
    assert not isinstance(error.value, StickerReselectionRequired)
    assert not client.uploads and not any(
        isinstance(r, functions.messages.SendMediaRequest) for r in client.requests
    )


async def test_sdk_style_download_iterator_close_is_used_on_failure(tmp_path):
    tmp_path.chmod(0o700)
    client = DownloadRPC([[b"short"]])
    closed = []
    original = client.iter_download

    def iterate(location, **kwargs):
        inner = original(location, **kwargs)

        class SDKIterator:
            async def __anext__(self):
                return await inner.__anext__()

            async def close(self):
                closed.append(True)
                await inner.aclose()

        return SDKIterator()

    client.iter_download = iterate
    with pytest.raises(Unavailable):
        await owner(client).catalog_download(
            source_for(), tmp_path / "partial", max_bytes=100
        )
    assert closed == [True] and not (tmp_path / "partial").exists()


def test_a_refusal_carries_telegrams_own_error_name():
    from enloq.transport.selected_media import named_refusal

    def line(error):
        return str(named_refusal(error, "this GIF"))

    assert line(errors.MediaInvalidError(None)) == (
        "Telegram refused this GIF (MEDIA_INVALID). Nothing was sent."
    )
    assert "(CHAT_SEND_GIFS_FORBIDDEN)" in line(errors.ChatSendGifsForbiddenError(None))
    # Unknown to Telethon: the instance keeps Telegram's name.
    assert "(SOME_NEW_ANSWER)" in line(errors.BadRequestError(None, "SOME_NEW_ANSWER"))
    # Matched by a pattern: named by its class.
    assert "(FilePartMissingError)" in line(
        errors.FilePartMissingError(None, capture=3)
    )
