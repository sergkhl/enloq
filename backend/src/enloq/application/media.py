"""Validated source media, short-lived downloads and source-bound derivatives."""

import asyncio
import base64
import hashlib
import json
import time
import uuid

from ..domain.catalog_assets import AssetRepresentation, representation
from ..domain.group_briefings import BriefingSpeechLimit
from ..domain.models import (
    SPOKEN_MEDIA,
    MediaTooLarge,
    ProcessingPaused,
    Refused,
    Unavailable,
    Unreadable,
)
from ..domain.selection_formats import GIF_BYTES, STICKER_BYTES
from ..domain.video import video_candidate, video_mime
from ..domain.voice_copy import MIME as VOICE_COPY_MIME
from ..domain.voice_copy import aac_copy
from ..inference.audio import WHISPER, LocalWhisper
from ..inference.work import PaidSpeech
from ..storage.store import RETENTION
from ..transport.media import (
    MediaValidationError,
    MediaWorkspace,
    image_facts,
)
from ..transport.mtproto import DOWNLOAD, PLAYBACK, PREFETCH, PREPARATION
from .catalog_assets import AssetProofs
from .group_access import native_access
from .media_cache import MediaCache, private_scratch

IMAGE_BYTES = 5 * 1024 * 1024
AUDIO_BYTES = 25_000_000
AUDIO_SECONDS = 600
DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOADABLE = frozenset({"audio", "video_note", "video", "gif", "document"})


def image_mime(path):
    try:
        return image_facts(path).mime_type
    except MediaValidationError:
        raise MediaTooLarge(
            "Image is unsupported or exceeds the media bounds"
        ) from None


def placeholder(message, reason=None):
    """What the model may know about an item it cannot see: kind and safe
    metadata (never file contents, phone numbers, vCards or coordinates)."""
    value = {
        "kind": "other" if message.media == "unsupported" else message.media,
        "not_analysed": True,
    }
    if reason:
        value["reason"] = reason
    info = message.media_info
    if info:
        for field in ("file_name", "size", "duration", "emoji", "title", "question"):
            if getattr(info, field) is not None:
                value[field] = getattr(info, field)
        for field in ("options", "voice", "live", "sticker"):
            if getattr(info, field):
                value[field] = getattr(info, field)
    return value


def voice_note(message):
    """Telegram's voice notes: audio marked as voice, or audio with no details."""
    info = message.media_info
    return message.media == "audio" and (info is None or bool(info.voice))


def sniff(data, message):
    """An inline MIME type only for known media containers; anything else is an
    opaque download so correspondent bytes never render as a page."""
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data[:6] in (b"GIF87a", b"GIF89a") and message.media == "gif":
        return "image/gif"
    if data[4:8] == b"ftyp":
        return "audio/mp4" if message.media == "audio" else "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm" if message.media == "audio" else "video/webm"
    return None


class Media:
    def __init__(self, store, transport, *, transcriber=None):
        self.store, self.transport = store, transport
        self.workspace = MediaWorkspace(store.path.parent / "media")
        self.workspace.prepare()
        self.workspace.cleanup_orphans(older_than_seconds=900)
        self.cache = MediaCache(store, transport)
        self.missing = {}
        # Old cache MIME labels may have come from a header sniff. A process
        # restart verifies retained ordinary-video bytes before any inline read.
        self.video_verified = {}
        self.video_verifications = {}
        self.selection_proofs = AssetProofs()
        self.retained_preview = lambda message: None
        self.transcriber = transcriber or LocalWhisper()
        self.admit = lambda: None
        self.authorized = lambda: True
        self.transcription_lock = asyncio.Lock()
        # Voice copies for iPhones convert at most two at a time.
        self.conversions = asyncio.Semaphore(2)

    async def acquire(self, message, path, limit, *, priority=PREPARATION, guard=None):
        """Link cached source bytes into a lease, downloading them at most once."""
        if not hasattr(self.transport, "download"):
            raise Refused("Original media unavailable")
        found = await self.cache.fetch(
            message,
            limit=limit,
            priority=priority,
            check=self.check(message),
            guard=guard,
        )
        self.cache.link(found, path)

    def check(self, message, variant="file"):
        """Validate before caching: images must decode; other kinds get a MIME
        type only for known containers."""
        if variant == "poster" or message.media == "image":
            return lambda path: image_mime(path)
        if message.media in ("gif", "sticker"):

            async def selected(path):
                facts = await self.selection_type(message, path)
                return None if facts.format == "tgs" else facts.mime

            return selected
        if video_candidate(message):
            return lambda path: self.video_type(message, path)

        def container(path):
            with path.open("rb") as stream:
                return sniff(stream.read(16), message)

        return container

    def kept(self, message):
        """Kinds with retained bytes; ordinary videos need deliberate preparation."""
        return video_candidate(message) or message.media in (
            "image",
            "audio",
            "video_note",
            "gif",
            "sticker",
        )

    def source_access(self, message, *, history=False, variant="file"):
        """Current originals or retained versions, with cache-only access after loss.

        This read gate never enables AI or grants a fresh Telegram transfer.
        Native work uses the captured access revision and exact intake proof.
        """
        if not self.authorized() or message.account_id != self.store.account_id:
            raise Refused("Account authorization changed")
        if message.deleted or message.date < self.store.clock() - RETENTION:
            raise Refused("Media source changed or expired; refresh its original")
        key = f"{message.account_id}/{message.peer}"
        chat = self.store.chat(key)
        if chat.kind in {"private", "saved"}:
            return None
        if chat.kind != "group" or not message.peer.startswith(("chat:", "channel:")):
            raise Refused(
                "Only existing private conversations and groups are supported"
            )
        current = self.store.message(key, message.message_id)
        if not history and (
            current is None
            or current.deleted
            or current.revision != message.revision
            or self._media_identity(current) != self._media_identity(message)
        ):
            raise Refused("Media source changed; refresh its original")
        state = self.store.groups.get(key)
        if not state.supported:
            raise Refused("This group is not supported")
        if (
            history
            or state.access != "available"
            or state.migrated_to is not None
            or not any(row["key"] == key for row in self.store.recent_chats("group"))
        ) and all(
            self.cache.get(message, kept, limit=DOWNLOAD_BYTES) is None
            # A voice copy can still be made from its cached source.
            for kept in ((variant, "file") if variant == "aac" else (variant,))
        ):
            raise Refused("Media is no longer available locally")
        return state.access_revision

    @staticmethod
    def _media_identity(message):
        if message is None:
            return None
        return (
            message.revision,
            message.deleted,
            message.date,
            message.text,
            message.reply_to,
            message.media,
            message.attachment_id,
            message.media_info.content() if message.media_info else None,
        )

    def _source_guard(self, message, *, history=False, variant="file", guard=None):
        revision = self.source_access(message, history=history, variant=variant)
        key = f"{message.account_id}/{message.peer}"
        head = self._media_identity(self.store.message(key, message.message_id))

        def allowed():
            if guard:
                guard()
            fresh = self.source_access(message, history=history, variant=variant)
            if revision != fresh or (
                revision is not None
                and head
                != self._media_identity(self.store.message(key, message.message_id))
            ):
                raise Refused("Group media source or access changed during loading")

        allowed()
        return allowed, revision

    def _native_source(self, message, revision):
        key = f"{message.account_id}/{message.peer}"
        if revision is not None and not any(
            row["key"] == key for row in self.store.recent_chats("group")
        ):
            raise Refused("Inactive group media is available from the cache only")
        native_access(self.store, key, revision)
        if revision is not None and not self.store.groups.current(
            message, admission=False
        ):
            raise Refused("Group media source is not current")

    def video_access(self, message, *, history=False):
        """An exact current source or retained observed version, independent of AI."""
        self.source_access(message, history=history)
        if message.deleted or not video_candidate(message):
            raise Refused("This item has no inline video")
        if message.date < self.store.clock() - RETENTION:
            raise Refused("Media source has expired")
        info = message.media_info
        if info and info.size is not None and info.size > DOWNLOAD_BYTES:
            raise MediaTooLarge("Media exceeds size limit")
        if not history:
            source = self.store.message(
                f"{message.account_id}/{message.peer}", message.message_id
            )
            if not source or source.deleted or source.revision != message.revision:
                raise Refused("Video source changed; refresh its original")

    async def video_type(self, message, path):
        """Reuse actual-container proof for this exact unchanged private file."""
        lock = self.video_verifications.setdefault(
            (message.key, message.revision), asyncio.Lock()
        )
        async with lock:
            try:
                details = path.stat()
                identity = (
                    message.key,
                    message.revision,
                    details.st_dev,
                    details.st_ino,
                    details.st_size,
                    details.st_mtime_ns,
                    details.st_mode,
                )
                if identity not in self.video_verified:
                    mime = await video_mime(path)
                    after = path.stat()
                    if (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_mode,
                    ) != identity[2:]:
                        raise Refused("Video bytes changed during verification")
                    # Disposable evidence, never durable watch history.
                    if len(self.video_verified) >= 1024:
                        self.video_verified.clear()
                    self.video_verified[identity] = mime
                return self.video_verified[identity]
            except OSError:
                raise Refused(
                    "Video is no longer available locally; select Play or Retry loading"
                ) from None

    async def prepare_video(self, message, *, history=False):
        """Play/Retry alone authorize a complete ordinary-video transfer.

        Unsupported complete bytes are retained for Download; they never receive
        an inline MIME type. Observed history always requires existing bytes.
        """
        self.video_access(message, history=history)
        allowed, revision = self._source_guard(message, history=history)
        found = self.cache.get(message, limit=DOWNLOAD_BYTES)
        if found is None:
            if history:
                raise Refused("Historical media is no longer available locally")

            def fresh():
                allowed()
                self._native_source(message, revision)

            fresh()
            found = await self.cache.fetch(
                message,
                limit=DOWNLOAD_BYTES,
                priority=PLAYBACK,
                guard=fresh,
            )
        mime = await self.video_type(message, found["path"])
        allowed()
        self.video_access(message, history=history)
        return mime

    def preview_access(self, message, *, history=False):
        """Source-date/access gates for Non-AI GIF and every sticker format."""
        self.source_access(message, history=history)
        if message.deleted or message.media not in ("gif", "sticker"):
            raise Refused("This item has no GIF or sticker preview")
        key = f"{message.account_id}/{message.peer}"
        if message.date < self.store.clock() - RETENTION:
            raise Refused("Media source has expired")
        limit = GIF_BYTES if message.media == "gif" else STICKER_BYTES
        if (
            message.media_info
            and message.media_info.size is not None
            and message.media_info.size > limit
        ):
            raise MediaTooLarge("Media exceeds size limit")
        if not history:
            source = self.store.message(key, message.message_id)
            if not source or source.deleted or source.revision != message.revision:
                raise Refused("Media source changed; refresh its original")

    async def selection_type(self, message, path):
        """Exact-byte proof, independent of source classification and AI results."""
        if message.media not in ("gif", "sticker"):
            raise Refused("This item has no GIF or sticker preview")
        facts = await self.selection_proofs.prove(path, message.media)
        info = message.media_info
        if (
            message.media == "sticker"
            and info
            and info.sticker in ("static", "animated", "video")
            and info.sticker != facts.sticker
        ):
            raise Refused("Sticker bytes do not match its native format")
        return facts

    async def preview(self, message, *, history=False, guard=None):
        allowed, _ = self._source_guard(message, history=history, guard=guard)
        self.preview_access(message, history=history)
        found = self.cache.get(
            message, limit=GIF_BYTES if message.media == "gif" else STICKER_BYTES
        )
        if found is None:
            return AssetRepresentation(state="missing", kind=message.media)
        try:
            facts = await self.selection_type(message, found["path"])
        except Unavailable:
            raise
        except Refused:
            facts = None
        allowed()
        self.preview_access(message, history=history)
        return representation(facts, kind=message.media)

    async def prepare_preview(self, message, *, history=False, guard=None):
        allowed, _ = self._source_guard(message, history=history, guard=guard)
        self.preview_access(message, history=history)
        await self._preview_file(message, history=history, guard=allowed)
        allowed()
        self.preview_access(message, history=history)
        return await self.preview(message, history=history, guard=allowed)

    async def _preview_file(
        self, message, *, history=False, priority=PLAYBACK, guard=None
    ):
        """Only deliberate current fetches may copy a confirmed retained send."""

        allowed, revision = self._source_guard(message, history=history, guard=guard)
        self.preview_access(message, history=history)
        limit = GIF_BYTES if message.media == "gif" else STICKER_BYTES
        found = self.cache.get(message, limit=limit)
        if found is not None:
            return found
        if history:
            raise Refused("Historical media is no longer available locally")
        owner = (
            self.retained_preview(message)
            if message.outgoing and not message.native_edited
            else None
        )
        if owner is not None:
            if (
                owner.kind != message.media
                or owner.source.account_id != message.account_id
            ):
                raise Refused("Retained media belongs to another source")
            found = await self.cache.retain(
                message,
                owner.path,
                limit=limit,
                expected_sha256=owner.sha256,
                expected_size=owner.size,
                guard=allowed,
            )
        else:
            # Complete original bytes survive a temporary format verifier outage.

            def fresh():
                allowed()
                self._native_source(message, revision)

            fresh()
            found = await self.cache.fetch(
                message, limit=limit, priority=priority, guard=fresh
            )
        allowed()
        return found

    async def serve_preview(self, message, *, history=False, guard=None):
        allowed, _ = self._source_guard(message, history=history, guard=guard)
        self.preview_access(message, history=history)
        found = self.cache.get(
            message, limit=GIF_BYTES if message.media == "gif" else STICKER_BYTES
        )
        if found is None:
            raise Refused("Media preview is not prepared locally")
        facts = await self.selection_type(message, found["path"])
        allowed()
        self.preview_access(message, history=history)
        if (
            facts.format == "tgs"
            or not representation(facts, kind=message.media).renderer_supported
        ):
            raise Refused("Media preview cannot be rendered here")
        path = self.scratch()
        try:
            self.cache.link(found, path)
        except OSError:
            path.unlink(missing_ok=True)
            raise Refused("Media is no longer available locally; retry") from None
        return path, facts.mime, '"' + facts.sha256 + '"'

    async def animation(self, message, *, history=False, guard=None):
        allowed, _ = self._source_guard(message, history=history, guard=guard)
        self.preview_access(message, history=history)
        found = self.cache.get(message, limit=STICKER_BYTES)
        if found is None:
            raise Refused("Media animation is not prepared locally")
        facts = await self.selection_type(message, found["path"])
        allowed()
        self.preview_access(message, history=history)
        if (
            facts.format != "tgs"
            or not representation(facts, kind=message.media).renderer_supported
        ):
            raise Refused("Media animation cannot be rendered here")
        return facts.animation_json

    @staticmethod
    def etag(message, variant="file"):
        """A strong validator derivable without bytes: a revision has fixed bytes."""
        identity = f"{message.key}/{message.revision}/{variant}"
        return '"' + hashlib.sha256(identity.encode()).hexdigest()[:32] + '"'

    async def serve(
        self, message, *, variant="file", priority=PLAYBACK, history=False, inline=True
    ):
        """A private hardlink to cached bytes and its MIME type (None when the
        container is unknown). The caller unlinks the path after responding."""
        allowed, revision = self._source_guard(
            message, history=history, variant=variant
        )
        if (
            variant == "file"
            and not self.kept(message)
            and not (
                revision is not None
                and message.media in DOWNLOADABLE
                and self.cache.get(message, limit=DOWNLOAD_BYTES) is not None
            )
        ):
            raise Refused("This item is not kept locally")
        missing = (message.key, message.revision, variant)
        if self.missing.get(missing, 0) > time.monotonic():
            raise Unavailable("Media unavailable; try again shortly")
        limit = (
            IMAGE_BYTES
            if variant == "poster" or message.media in ("image", "sticker")
            else DOWNLOAD_BYTES
        )
        try:
            selected = variant == "file" and message.media in ("gif", "sticker")
            if selected:
                self.preview_access(message, history=history)
            found = (
                await self.voice_copy(
                    message, priority=priority, guard=allowed, revision=revision
                )
                if variant == "aac"
                else self.cache.get(message, variant, limit=limit)
            )
            if found is None and selected:
                found = await self._preview_file(
                    message, history=history, priority=priority, guard=allowed
                )
            elif found is None and not (
                history or (variant == "file" and video_candidate(message))
            ):

                def fresh():
                    allowed()
                    self._native_source(message, revision)

                fresh()
                found = await self.cache.fetch(
                    message,
                    limit=limit,
                    priority=priority,
                    variant=variant,
                    check=self.check(message, variant),
                    guard=fresh,
                )
            if found is None:
                raise Refused(
                    "Historical media is no longer available locally"
                    if history
                    else "Video is not available locally; select Play or Retry loading"
                )
        except Unavailable:
            # Views re-render often: remember a missing item for a minute.
            self.missing[missing] = time.monotonic() + 60
            raise
        mime = (
            await self.video_type(message, found["path"])
            if inline and variant == "file" and video_candidate(message)
            else found["mime"]
        )
        if inline and selected:
            try:
                facts = await self.selection_type(message, found["path"])
                mime = (
                    facts.mime
                    if facts.format != "tgs"
                    and representation(facts, kind=message.media).renderer_supported
                    else None
                )
            except Unavailable:
                raise
            except Refused:
                mime = None
            self.preview_access(message, history=history)
        allowed()
        path = self.scratch()
        try:
            self.cache.link(found, path)
        except OSError:
            path.unlink(missing_ok=True)
            raise Refused("Media is no longer available locally; retry") from None
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path, mime

    async def voice_copy(self, message, *, priority, guard, revision):
        """A voice note's cached AAC copy, made from its cached source on the
        first request from an iPhone (ADR 46 CU1)."""
        if not voice_note(message):
            raise Refused("Only a current voice note has an AAC copy")
        found = self.cache.get(message, "aac", limit=DOWNLOAD_BYTES)
        if found is not None:
            return found
        source = self.cache.get(message, limit=DOWNLOAD_BYTES)
        if source is None:

            def fresh():
                guard()
                self._native_source(message, revision)

            fresh()
            source = await self.cache.fetch(
                message,
                limit=DOWNLOAD_BYTES,
                priority=priority,
                check=self.check(message),
                guard=fresh,
            )

        async def produce(partial):
            lease = self.scratch()
            try:
                # A hardlink: eviction can't pull the source from under ffmpeg.
                self.cache.link(source, lease)
                async with self.conversions:
                    await aac_copy(lease, partial, source["mime"])
            finally:
                lease.unlink(missing_ok=True)

        return await self.cache.derive(
            message,
            "aac",
            produce,
            mime=VOICE_COPY_MIME,
            limit=DOWNLOAD_BYTES,
            guard=guard,
        )

    async def stream(self, message):
        """Documents and ordinary videos go straight to the download."""
        if message.media not in DOWNLOADABLE:
            raise Refused("This item has no downloadable file")
        allowed, revision = self._source_guard(message)
        self._native_source(message, revision)
        async for chunk in self.transport.stream(
            message.peer,
            message.message_id,
            max_bytes=DOWNLOAD_BYTES,
            expected_revision=message.revision,
            priority=DOWNLOAD,
        ):
            allowed()
            self._native_source(message, revision)
            yield chunk

    async def prefetch(self, message):
        """New voice notes and circles, so the first play reads locally. An
        ordinary read: it continues while Account AI is Off."""
        if message.peer.startswith(("chat:", "channel:")):
            return
        if not (voice_note(message) or message.media == "video_note"):
            return
        try:
            await self.cache.fetch(
                message,
                limit=DOWNLOAD_BYTES,
                priority=PREFETCH,
                check=self.check(message),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return  # Playback or preparation fetches it later on demand.

    def scratch(self):
        self.workspace.prepare()
        return private_scratch(self.workspace.path, suffix=".bin")

    def cached(self, message):
        rows = self.store.rows(
            "SELECT body FROM derivatives WHERE source=? AND revision=?",
            (message.key, message.revision),
        )
        return json.loads(rows[0]["body"]) if rows else {}

    def _current(self, message, valid=None):
        """Material validity allows a started stage to finish while AI is Off."""
        if valid is not None and not valid():
            raise Refused("Media source snapshot changed during preparation")
        key = f"{message.account_id}/{message.peer}"
        if message.account_id != self.store.account_id or not self.authorized():
            raise Refused("Account authorization changed during preparation")
        if self.store.chat(key).kind == "group":
            with self.store.transaction() as db:
                self._group_current(db, message)

    def _group_current(self, db, message):
        source = db.execute(
            "SELECT revision,deleted,date FROM messages WHERE key=?", (message.key,)
        ).fetchone()
        if (
            source is None
            or source["deleted"]
            or source["revision"] != message.revision
            or source["date"] < self.store.clock() - RETENTION
            or message.date < self.store.clock() - RETENTION
            or not self.store.groups.current(message, admission=False, db=db)
        ):
            raise Refused("Group media source changed or is unavailable")

    def _stage(self, message, guard=None, valid=None):
        self._current(message, valid)
        if guard is not None:
            guard()
        elif self.store.chat(f"{message.account_id}/{message.peer}").kind == "group":
            raise Refused("Group media preparation requires stage admission")

    async def image(self, message, *, guard=None, valid=None):
        if (
            guard is not None
            or valid is not None
            or message.peer.startswith(("chat:", "channel:"))
        ):
            self._stage(message, guard, valid)

        def check():
            self._stage(message, guard, valid)

        with self.workspace.lease() as path:
            if guard is not None or valid is not None:
                await self.acquire(message, path, IMAGE_BYTES, guard=check)
                check()
            else:
                await self.acquire(message, path, IMAGE_BYTES)
            try:
                facts = image_facts(path)
            except MediaValidationError:
                raise MediaTooLarge(
                    "Image is unsupported or exceeds the media bounds"
                ) from None
            return path.read_bytes(), facts

    async def transcript(
        self, message, *, retry=False, guard=None, valid=None, on_speech_start=None
    ):
        group = self.store.chat(f"{message.account_id}/{message.peer}").kind == "group"
        guarded = (
            group
            or guard is not None
            or valid is not None
            or on_speech_start is not None
        )
        if guarded:
            self._current(message, valid)
        cached = self.cached(message)
        if cached.get("transcript"):
            return cached["transcript"]

        def admission():
            if guarded:
                self._stage(message, guard, valid)
            if guard is None:
                self.admit()

        admission()
        if group and on_speech_start is None:
            raise Refused("Group speech preparation requires duration admission")
        # Wait before creating a started attempt. An Off during capacity or
        # download waits must leave no failed/unknown transcription behind.
        async with self.transcription_lock:
            admission()
            cached = self.cached(message)
            if cached.get("transcript"):
                return cached["transcript"]
            with self.workspace.lease() as path:
                if guarded:
                    await self.acquire(message, path, AUDIO_BYTES, guard=admission)
                else:
                    await self.acquire(message, path, AUDIO_BYTES)
                admission()
                return await self._transcribe(
                    message,
                    path,
                    retry=retry,
                    guard=admission if guarded else None,
                    valid=valid,
                    on_speech_start=on_speech_start,
                )

    async def _transcribe(
        self,
        message,
        path,
        *,
        retry=False,
        guard=None,
        valid=None,
        on_speech_start=None,
    ):
        # A failed attempt is retried only by the block's deliberate Retry;
        # a completed transcript is never replaced by hand (ADR 26).
        attempts = self.store.rows(
            "SELECT state FROM jobs WHERE scope=? AND role='transcript' AND input_revision=?",
            (message.key, str(message.revision)),
        )
        if attempts and (any(j["state"] == "running" for j in attempts) or not retry):
            raise Refused("Local transcript unavailable; explicitly retry this block")
        identity = uuid.uuid4().hex
        started = False

        def start(duration=None):
            nonlocal started
            if started:
                raise Refused("Local transcription already started")
            if guard is not None:
                guard()
            if on_speech_start is not None:
                on_speech_start(duration)
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,'running',?,?,NULL) ON CONFLICT(id) DO UPDATE SET state='running',error=NULL",
                    (
                        identity,
                        message.key,
                        "transcript",
                        str(message.revision),
                        self.store.clock(),
                        "{}",
                    ),
                )
            started = True

        if guard is None and on_speech_start is None:
            start()
        transcript = None
        error = None
        try:
            # A transcriber with a paid fallback gets this attempt's Daily
            # Budget ledger (ADR 39).
            options = (
                {
                    "ledger": PaidSpeech(
                        self.store, identity, message.key, str(message.revision)
                    )
                }
                if getattr(self.transcriber, "paid", False)
                else {}
            )
            async with asyncio.timeout(650):
                result = (
                    await self.transcriber.transcribe(path, **options)
                    if started
                    else await self.transcriber.transcribe(
                        path, on_start=start, **options
                    )
                )
            if not started:
                raise Refused("Local transcriber did not admit its worker stage")
            if (
                not isinstance(result.get("text"), str)
                or not result["text"].strip()
                or len(result["text"].encode()) > 32000
            ):
                raise Refused("Transcript is empty or exceeds the text bound")
            rows = self.store.rows(
                "SELECT revision,deleted,date FROM messages WHERE key=?", (message.key,)
            )
            if message.date < self.store.clock() - RETENTION:
                raise Refused("Voice source expired during transcription")
            if rows and (rows[0]["deleted"] or rows[0]["revision"] != message.revision):
                raise Refused("Voice source changed during transcription")
            if not self.authorized():
                raise Refused("Account authorization changed during transcription")
            if valid is not None or message.peer.startswith(("chat:", "channel:")):
                self._current(message, valid)
            transcript = {
                k: result.get(k)
                for k in ("text", "language", "duration_seconds", "model_revision")
            }
            transcript.update(
                model=result.get("model", WHISPER), machine_generated=True
            )
        except asyncio.CancelledError:
            error = "Local transcription interrupted; use explicit Retry"
            raise
        except (ProcessingPaused, Refused):
            if not started:
                raise
            error = "Local transcription failed; use explicit Retry"
        except Exception:
            error = "Local transcription failed; use explicit Retry"
        finally:
            if transcript and (
                valid is not None or message.peer.startswith(("chat:", "channel:"))
            ):
                try:
                    self._current(message, valid)
                except Refused:
                    transcript = None
                    error = "Local transcription failed; use explicit Retry"
            if started:
                group = (
                    self.store.chat(f"{message.account_id}/{message.peer}").kind
                    == "group"
                )
                with self.store.transaction() as db:
                    if transcript and group:
                        try:
                            self._group_current(db, message)
                        except Refused:
                            transcript = None
                            error = "Local transcription failed; use explicit Retry"
                    db.execute(
                        "UPDATE jobs SET state=?,error=? WHERE id=?",
                        ("failed" if error else "done", error, identity),
                    )
                    if transcript:
                        db.execute(
                            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                            (
                                message.key,
                                message.revision,
                                json.dumps({"transcript": transcript}),
                            ),
                        )
        if error:
            raise Refused(error)
        return transcript

    async def enrich(
        self,
        packet,
        key,
        *,
        retry=False,
        on_stage=lambda _: None,
        guard=None,
        valid=None,
        on_speech_start=None,
    ):
        group = self.store.chat(key).kind == "group"
        if valid is not None and not valid():
            raise Refused("Media source snapshot changed before preparation")
        images = []
        total = 0
        targets = {m["id"] for m in packet["messages"]}
        unanalysed = []
        for source in packet["messages"] + packet.get("context", []):
            message = self.store.message(key, source["id"]) if group else None
            if group and (source.get("service") or (message and message.service)):
                source.update(text="", media=None)
                continue
            if not source["media"]:
                continue
            target = source["id"] in targets
            if message is None:
                message = self.store.message(key, source["id"])
            if message is None and not group:
                message = await self.transport.exact(
                    self.store.chat(key).peer, source["id"]
                )
            if not message or message.deleted or message.revision != source["revision"]:
                raise Refused("Media source changed or is unavailable")
            if group and message.service:
                source.update(text="", media=None, service=True)
                continue
            if group or valid is not None:
                self._current(message, valid)
            reason = None
            if source["media"] == "image":
                cached = self.cached(message).get("image_context")
                if cached:
                    source["image_context"] = cached
                    continue
                if len(images) >= 4:
                    reason = "more than four images"
                else:
                    if group or guard is not None or valid is not None:
                        self._stage(message, guard, valid)
                    on_stage("reading_images")
                    try:
                        data, facts = (
                            await self.image(message, guard=guard, valid=valid)
                            if group or guard is not None or valid is not None
                            else await self.image(message)
                        )
                    except ProcessingPaused:
                        raise
                    except (MediaTooLarge, Unreadable):
                        reason = "exceeds image limits or cannot be decoded"
                    except Refused:
                        if group or guard is not None or valid is not None:
                            raise
                        if target:
                            raise Refused("Image unavailable; use Retry") from None
                        reason = "unavailable"
                    except Exception:
                        if target:
                            raise Refused("Image unavailable; use Retry") from None
                        reason = "unavailable"
                    else:
                        if group or valid is not None:
                            self._current(message, valid)
                        if total + len(data) > 20_000_000:
                            reason = "combined image limit"
                        else:
                            total += len(data)
                            images.append(
                                dict(
                                    message_id=message.message_id,
                                    sha256=facts.sha256,
                                    data_url=f"data:{facts.mime_type};base64,"
                                    + base64.b64encode(data).decode(),
                                )
                            )
                            continue
            elif source["media"] in SPOKEN_MEDIA:
                info = message.media_info
                cached = self.cached(message).get("transcript")
                if (
                    not (group and cached)
                    and info
                    and (
                        (info.size or 0) > AUDIO_BYTES
                        or (info.duration or 0) > AUDIO_SECONDS
                    )
                ):
                    reason = "exceeds transcription limits"
                else:
                    if not cached:
                        if group or guard is not None or valid is not None:
                            self._stage(message, guard, valid)
                        on_stage("transcribing")
                    try:
                        source["transcript"] = await self.transcript(
                            message,
                            retry=retry,
                            guard=guard,
                            valid=valid,
                            on_speech_start=on_speech_start,
                        )
                        continue
                    except ProcessingPaused:
                        raise
                    except MediaTooLarge:
                        reason = "exceeds transcription limits"
                    except BriefingSpeechLimit:
                        if not group:
                            raise
                        reason = "local speech work limit"
                    except Refused:
                        if group or guard is not None or valid is not None or target:
                            raise
                        reason = "transcript unavailable"
                    except Exception:
                        # Speech is the substance of a target voice note or circle.
                        if target:
                            raise
                        reason = "transcript unavailable"
            source["placeholder"] = placeholder(message, reason)
            if target:
                unanalysed.append(
                    {
                        "id": message.message_id,
                        "kind": source["placeholder"]["kind"],
                        **({"reason": reason} if reason else {}),
                    }
                )
        while (
            len(json.dumps(packet.get("context", []), ensure_ascii=False).encode())
            > 6000
        ):
            removable = next(
                (m for m in packet["context"] if m["id"] != packet.get("direct_reply")),
                None,
            )
            if not removable:
                raise Refused(
                    "Direct reply transcript exceeds the supporting context bound"
                )
            packet["context"].remove(removable)
            packet["inputs"].pop(f"{key}/{removable['id']}", None)
        retained = {m["id"] for m in packet["messages"] + packet.get("context", [])}
        images = [i for i in images if i["message_id"] in retained]
        plain = json.dumps(
            {k: v for k, v in packet.items() if k != "images"}, ensure_ascii=False
        ).encode()
        if len(plain) > 32000:
            raise Refused(
                "Combined source text and transcripts exceed the request bound"
            )
        packet["images"] = images
        packet["transcript_inputs"] = {
            f"{key}/{m['id']}": transcript_fingerprint(m["transcript"])
            for m in packet["messages"] + packet.get("context", [])
            if m.get("transcript")
        }
        packet["unanalysed"] = unanalysed
        packet["nothing"] = not any(
            not m.get("service")
            and (
                m["text"].strip()
                or m.get("transcript")
                or (m["media"] == "image" and "placeholder" not in m)
            )
            for m in packet["messages"]
        )
        if valid is not None and not valid():
            raise Refused("Media source snapshot changed during preparation")
        return packet


def transcript_fingerprint(transcript):
    # Provenance changes alone do not invalidate an unchanged reading.
    content = {k: (transcript or {}).get(k) for k in ("text", "language")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
