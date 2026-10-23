from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from telethon import errors, events, functions, types, utils

from ..config import Settings
from ..domain.deletion import (
    DeleteDescription,
    DeleteEvidence,
    DeleteReceipt,
    DeletionRead,
    deletion_ids,
    private_peer,
)
from ..domain.delivery import ReceiptBatch, ReceiptSink, SendDescription, SendEvidence
from ..domain.models import (
    Chat,
    DeliveryRefused,
    GapTooLong,
    MediaInfo,
    MediaTooLarge,
    Message,
    Reaction,
    ReactionActor,
    ReactionChip,
    ReactionSnapshot,
    Refused,
    ReplyElsewhere,
    Unavailable,
    profile_photo_id,
    same_content,
)
from ..storage.lock import OwnerLock
from ..storage.store import SAVED_TITLE
from . import group_authors, group_chats, group_deletion, group_sending
from .catalog import NativeCatalog
from .media import MediaValidationError, validate_send_image
from .mtproto import (
    DOWNLOAD,
    FILE_PRIORITY,
    MTProtoRecoveryPaused,
    PacedTelegramClient,
    native_request_guard,
    observation_cycle,
    wait_for_connection,
)
from .selected_media import DEFINITE, download_selected, named_refusal, send_selected
from .text_links import text_links

CATALOG_UPDATES = (
    types.UpdateSavedGifs,
    types.UpdateNewStickerSet,
    types.UpdateStickerSets,
    types.UpdateStickerSetsOrder,
    types.UpdateRecentStickers,
    types.UpdateFavedStickers,
)


def _write_state(db, key, state):
    """Store an update state inside the caller's transaction."""
    db.execute(
        "INSERT INTO cursors VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (
            key,
            json.dumps(
                {"pts": state.pts, "qts": state.qts, "date": state.date.timestamp()}
            ),
        ),
    )


def peer_name(peer) -> str:
    for attr, prefix in (
        ("user_id", "user"),
        ("chat_id", "chat"),
        ("channel_id", "channel"),
    ):
        if getattr(peer, attr, None):
            return f"{prefix}:{getattr(peer, attr)}"
    raise Refused("Unsupported Telegram peer")


def forwarded_from(message, entities=None):
    """Use only attribution Telegram already supplied, never resolve a peer."""
    header = getattr(message, "fwd_from", None)
    if header is None:
        return None
    name = getattr(header, "from_name", None)
    if not name:
        forward = getattr(message, "forward", None)
        source = getattr(forward, "sender", None) or getattr(forward, "chat", None)
        if source is None and entities and getattr(header, "from_id", None):
            source = entities.get(peer_name(header.from_id))
        name = utils.get_display_name(source) if source else None
    return name.strip()[:256] or None if name else None


def reply_header(message, peer):
    """The reply header's own details (ADR 41): a quoted fragment, a message in
    another chat, or a story. ``reply_to`` keeps the header's number apart."""
    header = getattr(message, "reply_to", None)
    if isinstance(header, types.MessageReplyStoryHeader):
        return {"reply_story": True}
    if not isinstance(header, types.MessageReplyHeader):
        return {}
    value = {}
    quote = getattr(header, "quote_text", None)
    if isinstance(quote, str) and quote.strip():
        value["reply_quote"] = quote[:4096]
    target = getattr(header, "reply_to_peer_id", None)
    if target is not None and peer_name(target) != peer:
        origin = getattr(header, "reply_from", None)
        sender = getattr(origin, "from_id", None)
        date = getattr(origin, "date", None)
        name = getattr(origin, "from_name", None)
        value["reply_elsewhere"] = {
            "peer": peer_name(target),
            "sender_peer": peer_name(sender) if sender is not None else None,
            "sender": name.strip()[:256] or None if isinstance(name, str) else None,
            "date": date.timestamp() if date else None,
        }
    return value


def forward_details(message):
    """The forward header's own original date, post signature and imported
    flag; None when Telegram supplied no forward header."""
    header = getattr(message, "fwd_from", None)
    if header is None:
        return {}
    date = getattr(header, "date", None)
    signature = getattr(header, "post_author", None)
    return {
        "forward_date": date.timestamp() if date else None,
        "forward_signature": signature.strip()[:256] or None
        if isinstance(signature, str)
        else None,
        "imported": bool(getattr(header, "imported", False)),
    }


def waveform(document):
    """Telegram's captured voice-note waveform as display-only 5-bit levels
    (ADR 27); None when the note carries none. Never computed locally."""
    for attribute in getattr(document, "attributes", None) or ():
        if isinstance(attribute, types.DocumentAttributeAudio):
            packed = getattr(attribute, "waveform", None)
            if isinstance(packed, bytes) and packed:
                # 5-bit levels packed least-significant bit first. Decoded here
                # because Telethon 1.44 misreads the final level.
                bits = int.from_bytes(packed, "little")
                count = min(256, len(packed) * 8 // 5)
                return [(bits >> (5 * index)) & 31 for index in range(count)] or None
    return None


def native_peer(peer: str):
    kind, identity = peer.split(":")
    return {
        "user": types.PeerUser,
        "chat": types.PeerChat,
        "channel": types.PeerChannel,
    }[kind](int(identity))


def _text(value, limit):
    value = getattr(value, "text", value)
    return value[:limit] if isinstance(value, str) and value else None


def poster_location(asset, *, max_bytes=None):
    """The largest stored thumbnail of a video document: a poster frame that
    needs no local video decoding."""
    thumbs = [
        t
        for t in getattr(asset, "thumbs", None) or []
        if isinstance(t, (types.PhotoSize, types.PhotoSizeProgressive))
    ]
    if not isinstance(asset, types.Document) or not thumbs:
        raise Unavailable("No poster frame")
    best = max(thumbs, key=lambda t: t.w * t.h)
    declared = (
        max(best.sizes, default=0)
        if isinstance(best, types.PhotoSizeProgressive)
        else best.size
    )
    if max_bytes is not None and declared > max_bytes:
        raise MediaTooLarge("Poster exceeds size limit")
    return types.InputDocumentFileLocation(
        id=asset.id,
        access_hash=asset.access_hash,
        file_reference=asset.file_reference,
        thumb_size=best.type,
    )


def classify(message):
    """Typed kind and safe metadata. Order matters: GIFs and video stickers
    also carry a video attribute, and circles are round videos."""
    media = getattr(message, "media", None)
    if media is None or isinstance(media, types.MessageMediaEmpty):
        return None, None
    if isinstance(media, types.MessageMediaWebPage):
        # A cached preview photo keeps its established image path; otherwise
        # a link preview is ordinary text and fetches nothing from its URL.
        if getattr(message, "photo", None):
            return "image", None
        page = getattr(media, "webpage", None)
        return "link", MediaInfo(
            site=_text(getattr(page, "site_name", None), 256),
            title=_text(getattr(page, "title", None), 256),
        )
    if getattr(message, "photo", None):
        return "image", None
    document = getattr(message, "document", None)
    if document is not None:
        file = message.file
        info = dict(
            file_name=_text(file.name, 256),
            mime=_text(file.mime_type, 128),
            size=file.size if isinstance(file.size, int) else None,
        )
        duration = file.duration
        if isinstance(duration, (int, float)) and duration >= 0:
            info["duration"] = float(duration)
        for key, value in (("width", file.width), ("height", file.height)):
            if isinstance(value, int) and value >= 0:
                info[key] = value
        if message.sticker:
            variant = {
                "application/x-tgsticker": "animated",
                "video/webm": "video",
            }.get(info["mime"], "static")
            return "sticker", MediaInfo(
                **info, sticker=variant, emoji=_text(file.emoji, 32)
            )
        if message.gif:
            return "gif", MediaInfo(**info)
        if message.video_note:
            return "video_note", MediaInfo(**info)
        if message.voice:
            return "audio", MediaInfo(**info, voice=True, waveform=waveform(document))
        if message.audio:
            return "audio", MediaInfo(
                **info,
                title=_text(file.title, 256),
                performer=_text(file.performer, 256),
            )
        if message.video:
            return "video", MediaInfo(**info)
        return "document", MediaInfo(**info)
    if isinstance(media, types.MessageMediaPoll):
        poll = media.poll
        return "poll", MediaInfo(
            question=_text(poll.question, 512),
            options=[_text(a.text, 256) or "" for a in poll.answers[:12]],
        )
    if isinstance(media, types.MessageMediaVenue):
        return "location", MediaInfo(title=_text(media.title, 256))
    if isinstance(media, (types.MessageMediaGeo, types.MessageMediaGeoLive)):
        return "location", MediaInfo(live=isinstance(media, types.MessageMediaGeoLive))
    if isinstance(media, types.MessageMediaContact):
        name = " ".join(filter(None, (media.first_name, media.last_name)))
        return "contact", MediaInfo(title=name[:256] or None)
    if isinstance(media, types.MessageMediaDice):
        return "other", MediaInfo(title="Dice", emoji=_text(media.emoticon, 32))
    return "other", None


def reaction_value(native):
    """An ordinary emoji, a custom emoji's document ID or a paid reaction."""
    if isinstance(native, types.ReactionEmoji):
        emoticon = native.emoticon
        if isinstance(emoticon, str) and 0 < len(emoticon) <= 32:
            return Reaction(emoji=emoticon)
    elif isinstance(native, types.ReactionCustomEmoji):
        if isinstance(native.document_id, int):
            return Reaction(custom=str(native.document_id))
    elif isinstance(native, types.ReactionPaid):
        return Reaction(paid=True)
    return None


def reaction_snapshot(native, account_id, peer, observed):
    """Reaction metadata of a full native message. A message without reactions
    has none; attribution and own choices stay unknown where Telegram omits
    them, and native addition times are never replaced by observation time."""
    if native is None:
        return ReactionSnapshot(complete=True, observed=observed)
    minimal = bool(getattr(native, "min", False))
    chips, total = [], 0
    for result in (getattr(native, "results", None) or [])[:64]:
        value = reaction_value(result.reaction)
        if value is None or not isinstance(result.count, int) or result.count <= 0:
            continue
        total += result.count
        order = None if minimal else getattr(result, "chosen_order", None)
        chips.append(
            ReactionChip(
                reaction=value,
                count=min(result.count, 1_000_000),
                mine=None if minimal else order is not None,
                order=order,
            )
        )
    if group_sending.group_peer(peer):
        return ReactionSnapshot(
            chips=chips,
            actors=None,
            complete=not chips,
            minimal=minimal,
            observed=observed,
        )
    actors, complete = None, not chips
    recent = getattr(native, "recent_reactions", None)
    if recent is not None:
        actors = []
        for item in recent[:64]:
            value = reaction_value(item.reaction)
            try:
                who = peer_name(item.peer_id)
            except Refused:
                continue
            if value is None:
                continue
            if who == f"user:{account_id}" or getattr(item, "my", False):
                actor = "own"
            elif who == peer:
                actor = "peer"
            else:
                continue
            date = getattr(item, "date", None)
            actors.append(
                ReactionActor(
                    actor=actor,
                    reaction=value,
                    date=date.timestamp() if date else None,
                )
            )
        complete = len(actors) == total
    return ReactionSnapshot(
        chips=chips,
        actors=actors,
        complete=complete,
        minimal=minimal,
        observed=observed,
    )


def app_config_value(config, key):
    """One numeric value from help.getAppConfig's JSON tree, if present."""
    for item in getattr(config, "value", None) or []:
        if getattr(item, "key", None) == key and isinstance(
            item.value, types.JsonNumber
        ):
            return item.value.value
    return None


def attachment_identity(message):
    """Stable asset IDs only; never access hashes or expiring file references."""
    for kind in ("photo", "document"):
        identity = getattr(getattr(message, kind, None), "id", None)
        if isinstance(identity, int):
            return f"{kind}:{identity}"
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaPoll):
        return f"poll:{media.poll.id}"
    if isinstance(media, types.MessageMediaWebPage):
        identity = getattr(media.webpage, "id", None)
        if identity is not None:
            return f"webpage:{identity}"
    # Unknown identity is conservative: metadata alone never proves equality.
    return None


# Still images a host may read back from Saved Messages (ADR 43).
STILL_IMAGES = frozenset({"image/jpeg", "image/png", "image/webp"})


def saved_code(message):
    """A pairing code as the service posts it to Saved Messages (ADR 47 Q2):
    the six digits alone under one code entity. Anything else is None."""
    text = getattr(message, "message", None)
    entities = getattr(message, "entities", None) or []
    if (
        isinstance(text, str)
        and len(text) == 6
        and text.isascii()
        and text.isdigit()
        and getattr(message, "media", None) is None
        and len(entities) == 1
        and isinstance(entities[0], types.MessageEntityCode)
        and (entities[0].offset, entities[0].length) == (0, 6)
    ):
        return text
    return None


# A pairing record as the service posts it to Saved Messages (ADR 47 Q4).
PAIRING_RECORD = re.compile(r"(?s).{1,300} was paired with \S{1,253}\.")


def service_post(message):
    """Whether the service posted this Saved Message itself, as a pairing
    code or a pairing record; the Saved Build is known by its ID instead."""
    text = getattr(message, "message", None)
    return saved_code(message) is not None or (
        isinstance(text, str)
        and getattr(message, "media", None) is None
        and not getattr(message, "entities", None)
        and PAIRING_RECORD.fullmatch(text) is not None
    )


def person(entity, account_id):
    """A person New Message may write to (ADR 51 Q16): a full human user who
    is neither this account, a bot, deleted nor Telegram's service account."""
    if (
        not isinstance(entity, types.User)
        or entity.bot
        or entity.deleted
        or entity.min
        or entity.id in (account_id, 777000)
    ):
        return None
    usernames = [
        item.username
        for item in getattr(entity, "usernames", None) or []
        if getattr(item, "active", True) and item.username
    ]
    return {
        "peer": f"user:{entity.id}",
        "name": (utils.get_display_name(entity) or "Telegram user")[:256],
        "username": entity.username or (usernames[0] if usernames else None),
        "photo_id": profile_photo_id(entity),
    }


USERNAME = re.compile(r"@?([A-Za-z][A-Za-z0-9_]{3,31})")


def saved_image(message):
    """A photo or a still image file: its type and declared size, else None.
    Stickers and animations are not screenshots."""
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaPhoto) and isinstance(
        media.photo, types.Photo
    ):
        sizes = [
            size
            for size in media.photo.sizes
            if isinstance(size, (types.PhotoSize, types.PhotoSizeProgressive))
        ]
        if not sizes:
            return None
        largest = max(sizes, key=lambda size: size.w * size.h)
        if isinstance(largest, types.PhotoSizeProgressive):
            return "image/jpeg", max(largest.sizes, default=0)
        return "image/jpeg", largest.size
    document = getattr(media, "document", None)
    if (
        isinstance(media, types.MessageMediaDocument)
        and isinstance(document, types.Document)
        and document.mime_type in STILL_IMAGES
        and not any(
            isinstance(
                attribute,
                (types.DocumentAttributeSticker, types.DocumentAttributeAnimated),
            )
            for attribute in document.attributes
        )
    ):
        return document.mime_type, document.size
    return None


class PersonalTransport:
    def __init__(
        self,
        settings: Settings,
        store,
        *,
        client_factory=PacedTelegramClient,
        session_root=None,
    ):
        if settings.mode != "personal":
            raise Refused("Personal sessions cannot be loaded in this mode")
        expected = settings.refs.get("EXPECTED_ACCOUNT_ID")
        if not expected or int(expected) != store.account_id:
            raise Refused("Personal account identity has not been configured")
        session = Path(settings.refs.get("PERSONAL_SESSION_REF", ""))
        owned = settings.data / "personal.session"
        if (
            (
                session.resolve() != owned.resolve()
                if session_root is None
                else not session.resolve().is_relative_to(session_root.resolve())
            )
            or not session.is_file()
            or session.is_symlink()
        ):
            raise Refused(
                "Only this app's existing personal session is accepted; run setup explicitly"
            )
        if session.stat().st_mode & 0o077:
            raise Refused("Personal session must be mode 0600")
        self.account_id, self.store, self.settings = int(expected), store, settings
        self.lock = OwnerLock(Path(str(session) + ".owner.lock"))
        self.client_factory, self.session = client_factory, session
        self.client = None
        self.sync = None
        self.identity = None
        self.requires_login = False
        self.verified = False
        self.catalog_changed = lambda: None

    @property
    def connected(self):
        return bool(self.verified and self.client and self.client.is_connected())

    @property
    def reconnecting(self):
        return bool(self.client and self.client.reconnecting)

    async def connect(self, sync):
        self.lock.acquire()
        self.sync = sync
        try:
            self.client = self.client_factory(
                str(self.session),
                int(self.settings.secret("TELEGRAM_API_ID_REF")),
                self.settings.secret("TELEGRAM_API_HASH_REF"),
                sequential_updates=True,
                catch_up=False,
            )
            self.client.add_event_handler(self._incoming, events.NewMessage())
            self.client.add_event_handler(self._incoming, events.MessageEdited())
            self.client.add_event_handler(self._deleted, events.MessageDeleted())
            self.client.add_event_handler(
                self._receipt, events.Raw(types.UpdateMessageID)
            )
            self.client.add_event_handler(
                self._reactions, events.Raw(types.UpdateMessageReactions)
            )
            self.client.add_event_handler(
                self._read_outbox, events.Raw(types.UpdateReadHistoryOutbox)
            )
            self.client.add_event_handler(
                self._catalog_update, events.Raw(CATALOG_UPDATES)
            )
            self.client.add_event_handler(
                self._group_metadata, events.Raw(group_chats.METADATA_UPDATES)
            )
            self.client.add_event_handler(
                self._group_service,
                events.Raw((types.UpdateNewMessage, types.UpdateNewChannelMessage)),
            )
            self.client.add_event_handler(
                self._channel_gap, events.Raw(types.UpdateChannelTooLong)
            )
            policy = getattr(self.client, "request_policy", None)
            if policy:
                # Recreating a client must not bypass a persisted flood hold.
                await wait_for_connection(policy)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.requires_login = True
                raise Unavailable(
                    "Telegram authorization revoked; explicit setup required"
                )
            self._accept_identity(await self.client.get_me())
        except BaseException:
            await self.close()
            raise

    def ensure_authorized(self):
        """Telethon's update loop disconnects for good, keeping the error, when
        getDifference is refused as not logged in or with a broken authorization
        key; that is a lost authorization, not a connection to retry."""
        error = getattr(getattr(self, "client", None), "_updates_error", None)
        if isinstance(error, (errors.UnauthorizedError, errors.AuthKeyError)):
            self.requires_login, self.verified = True, False
            raise Refused("Telegram authorization revoked; explicit setup required")

    def _accept_identity(self, me):
        """One rule at connect and at every recheck. A missing, different or bot
        identity (Telethon returns None for a revoked session) withdraws the
        account at once, so sends and live intake stop before it is closed."""
        if not me or me.id != self.account_id or me.bot:
            self.requires_login, self.verified = True, False
            raise Refused("Connected Telegram identity mismatch")
        self.identity = me
        self.identity_checked_at = self.store.clock()
        self.verified = True

    async def _catalog_update(self, update):
        if isinstance(update, CATALOG_UPDATES):
            # Library metadata changes never initiate a refresh or media fetch.
            getattr(self, "catalog_changed", lambda: None)()

    async def catalog_entitlement(self):
        return await NativeCatalog(self).entitlement()

    async def catalog_library(self):
        return await NativeCatalog(self).library()

    async def catalog_pack(self, pack):
        return await NativeCatalog(self).pack(pack)

    async def catalog_search(self, query, offset=""):
        return await NativeCatalog(self).search(query, offset)

    async def catalog_refresh_document(self, source):
        return await NativeCatalog(self).refresh_same_document(source)

    async def catalog_download(self, source, path, *, max_bytes, thumb=False):
        return await download_selected(
            self, source, path, max_bytes=max_bytes, thumb=thumb
        )

    async def discover(self):
        # ADR 14 adopts the human-DM default; explicit exclusions survive discovery.
        if (
            getattr(self, "identity", None)
            and self.store.clock() - self.identity_checked_at >= 300
        ):
            # A failed request raises and changes nothing; it is retried.
            self._accept_identity(await self.client.get_me())
        observed = self.observation()
        revisions = {
            row["chat_key"]: self.store.groups.get(row["chat_key"]).access_revision
            for row in self.store.rows("SELECT chat_key FROM group_state")
        }
        saved = self.store.rows("SELECT value FROM cursors WHERE key='dialogs'")
        progress = json.loads(saved[0]["value"]) if saved else {}
        options = {}
        if progress.get("peer") and not progress.get("complete"):
            options = {
                "offset_peer": native_peer(progress["peer"]),
                "offset_id": progress["id"],
                "offset_date": datetime.fromtimestamp(progress["date"], UTC),
                "ignore_pinned": True,
            }
        count, anchor = 0, None
        # Telethon ends a resumed page early when its tail lacks top messages,
        # so the page is capped here rather than by its limit.
        async for dialog in self.client.iter_dialogs(limit=None, **options):
            count += 1
            if getattr(dialog.message, "date", None):
                # A dialog may lack a dated top message; like Telethon, resume
                # from the last one that has it, or the walk would restart.
                anchor = dialog
            self._discovered(dialog, observed=observed, revisions=revisions)
            if count == 100:
                break
        progress = {"complete": count < 100}

        if anchor:
            progress.update(
                peer=peer_name(utils.get_peer(anchor.entity)),
                id=anchor.message.id,
                date=anchor.message.date.timestamp(),
            )
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO cursors VALUES ('dialogs',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(progress),),
            )
        return progress

    def _discovered(self, dialog, *, observed=None, revisions=None):
        """Only existing human DMs and supported native groups are admitted."""
        entity = dialog.entity
        peer = peer_name(utils.get_peer(entity))
        description = group_chats.describe(entity)
        if description is not None:
            state = self._observe_group(
                entity,
                activity=dialog.date.timestamp() if dialog.date else 0,
                dialog=dialog,
                observed=observed,
                expected_revision=revisions.get(f"{self.account_id}/{peer}", 0)
                if revisions is not None
                else None,
            )
            if state and state.supported and state.access == "available":
                self.store.viewing.baseline(
                    state.key,
                    getattr(dialog.dialog, "read_inbox_max_id", 0),
                    getattr(dialog.dialog, "top_message", 0),
                )
                pts = getattr(dialog.dialog, "pts", None)
                intake = self.store.groups.intake(state.key)
                if (
                    state.peer.startswith("channel:")
                    and isinstance(pts, int)
                    and intake.pts is None
                ):
                    self.store.groups.checkpoint(state.key, pts, epoch=intake.epoch)
            return
        kind = (
            "broadcast"
            if getattr(entity, "broadcast", False)
            else "bot"
            if getattr(entity, "bot", False)
            else "saved"
            if entity.id == self.account_id
            else "service"
            if entity.id == 777000
            else "private"
            if isinstance(entity, types.User)
            else "group"
        )
        if kind == "saved":
            # The account's own Saved Messages, its third list (ADR 50): no AI,
            # read position or unseen baseline, whatever its activity.
            self.store.put_chat(
                Chat(
                    account_id=self.account_id,
                    peer=peer,
                    title=SAVED_TITLE,
                    kind="saved",
                    ai_eligible=False,
                    latest_message_id=getattr(dialog.dialog, "top_message", 0) or None,
                ),
                activity=dialog.date.timestamp() if dialog.date else 0,
            )
            return
        if kind != "private":
            return
        old = self.store.rows(
            "SELECT body FROM chats WHERE key=?", (f"{self.account_id}/{peer}",)
        )
        previous = Chat.model_validate_json(old[0]["body"]) if old else None
        mute = getattr(
            getattr(dialog.dialog, "notify_settings", None), "mute_until", None
        )
        self.store.put_chat(
            Chat(
                account_id=self.account_id,
                peer=peer,
                title=dialog.name or "Telegram chat",
                kind=kind,
                muted=bool(mute and mute > datetime.now(UTC)),
                archived=bool(dialog.archived),
                photo_id=profile_photo_id(entity),
                ai_eligible=previous.ai_eligible if previous else True,
                eligibility_source=previous.eligibility_source
                if previous
                else "default",
                read_through=getattr(dialog.dialog, "read_inbox_max_id", 0),
                read_outbox=getattr(dialog.dialog, "read_outbox_max_id", 0) or 0,
                latest_message_id=getattr(dialog.dialog, "top_message", 0) or None,
            ),
            activity=dialog.date.timestamp() if dialog.date else 0,
        )
        self.store.viewing.baseline(
            f"{self.account_id}/{peer}",
            getattr(dialog.dialog, "read_inbox_max_id", 0),
            getattr(dialog.dialog, "top_message", 0),
        )
        self.note_unread_reactions(
            peer, getattr(dialog.dialog, "unread_reactions_count", 0)
        )

    async def refresh_read_positions(self, peers):
        """Fetch the current read position of chats whose seven-day catch-up has
        not started, in batches, and open their catch-up. Pages choose unread
        targets by this position, which discovery refreshes only for the
        dialogs it has reached. A chat Telegram cannot resolve is skipped and
        tried again next cycle, without holding back the others."""
        for start in range(0, len(peers), 100):
            batch = []
            for peer in peers[start : start + 100]:
                try:
                    batch.append(
                        types.InputDialogPeer(
                            await self.client.get_input_entity(native_peer(peer))
                        )
                    )
                except (ValueError, errors.BadRequestError):
                    continue
            answered = []  # (peers sent, response) for each answered request
            if len(batch) > 1:
                try:
                    answered.append(
                        (
                            batch,
                            await self.client(
                                functions.messages.GetPeerDialogsRequest(peers=batch)
                            ),
                        )
                    )
                except errors.BadRequestError:
                    pass  # One refused peer: ask for each on its own below.
            for one in batch if not answered else []:
                try:
                    answered.append(
                        (
                            [one],
                            await self.client(
                                functions.messages.GetPeerDialogsRequest(peers=[one])
                            ),
                        )
                    )
                except errors.BadRequestError:
                    continue
            for sent, result in answered:
                positions = {
                    peer_name(d.peer): d.read_inbox_max_id for d in result.dialogs
                }
                for one in sent:
                    peer = peer_name(one.peer)
                    key = f"{self.account_id}/{peer}"
                    rows = self.store.rows("SELECT body FROM chats WHERE key=?", (key,))
                    if not rows:
                        continue
                    if peer in positions:
                        chat = Chat.model_validate_json(rows[0]["body"])
                        native = next(
                            d for d in result.dialogs if peer_name(d.peer) == peer
                        )
                        # put_chat keeps the higher of the stored and new positions.
                        self.store.put_chat(
                            chat.model_copy(
                                update={
                                    "read_through": positions[peer],
                                    "read_outbox": getattr(
                                        native, "read_outbox_max_id", 0
                                    )
                                    or 0,
                                }
                            )
                        )
                        self.note_unread_reactions(
                            peer, getattr(native, "unread_reactions_count", 0)
                        )
                        if getattr(native, "top_message", 0):
                            self.store.put_chat(
                                self.store.chat(key).model_copy(
                                    update={"latest_message_id": native.top_message}
                                )
                            )
                        self.store.viewing.baseline(
                            key, positions[peer], getattr(native, "top_message", 0)
                        )
                    with self.store.transaction() as db:
                        # Without a dialog there is nothing to catch up.
                        db.execute(
                            "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                            (
                                "recent:" + key,
                                json.dumps(
                                    {"before": 0, "complete": peer not in positions}
                                ),
                            ),
                        )

    async def normalize(self, message, *, observed=None, entities=None) -> Message:
        return (
            await self.normalize_page([message], observed=observed, entities=entities)
        )[0]

    def observation(self):
        store = getattr(self, "store", None)
        return store.clock() if store is not None else datetime.now(UTC).timestamp()

    async def normalize_page(self, values, *, observed=None, entities=None):
        """``observed`` is when the native read started; updates use arrival."""
        observed = self.observation() if observed is None else observed
        # Reply parents come from this page or the local store; intake never
        # fetches them, so an unresolved parent stays "unknown". A reply to
        # another chat's message or to a story has no parent here (ADR 41).
        parents = {(peer_name(m.peer_id), m.id): m for m in values}
        headers = {id(m): reply_header(m, peer_name(m.peer_id)) for m in values}
        for message in values:
            peer = peer_name(message.peer_id)
            reply_id = getattr(
                getattr(message, "reply_to", None), "reply_to_msg_id", None
            )
            if (
                not reply_id
                or "reply_elsewhere" in headers[id(message)]
                or (peer, reply_id) in parents
            ):
                continue
            local = self.store.rows(
                "SELECT body FROM messages WHERE key=? AND deleted=0",
                (f"{self.account_id}/{peer}/{reply_id}",),
            )
            if local:
                parents[peer, reply_id] = Message.model_validate_json(local[0]["body"])
        result = []
        # Group authors' current photos, for their avatars (ADR 51 Q13), and
        # Telegram's albums, drawn as one grid (Q12).
        authors = []
        albums = []
        for message in values:
            peer = peer_name(message.peer_id)
            reply = getattr(message, "reply_to", None)
            reply_id = getattr(reply, "reply_to_msg_id", None)
            header = headers[id(message)]
            if "reply_elsewhere" in header:
                header["reply_elsewhere"] = self._elsewhere(
                    header["reply_elsewhere"], entities
                )
            parent = (
                None if "reply_elsewhere" in header else parents.get((peer, reply_id))
            )
            sender = getattr(message, "sender", None)
            sender_peer = getattr(message, "from_id", None)
            if sender_peer is None and isinstance(message.peer_id, types.PeerUser):
                # Native private history may omit from_id. The dialog peer and
                # outgoing flag still identify the sender; this does not apply
                # to anonymous/group/channel posts.
                sender_peer = (
                    types.PeerUser(self.account_id)
                    if getattr(message, "out", False)
                    else message.peer_id
                )
            author_peer = peer_name(sender_peer) if sender_peer else None
            if sender is None and entities:
                sender = entities.get(author_peer)
            author = (utils.get_display_name(sender) if sender else "") or (
                ("You" if getattr(message, "out", False) else author_peer or peer)
                if isinstance(message.peer_id, types.PeerUser)
                else "Unknown participant"
            )
            if (
                group_sending.group_peer(peer)
                and isinstance(sender, types.User)
                and not sender.min
                and author_peer == f"user:{sender.id}"
            ):
                authors.append(
                    (author_peer, profile_photo_id(sender), message.date.timestamp())
                )
            media, media_info = classify(message)
            grouped = getattr(message, "grouped_id", None)
            if isinstance(grouped, int) and media in ("image", "video"):
                albums.append(
                    (
                        f"{self.account_id}/{peer}/{message.id}",
                        str(grouped),
                        message.date.timestamp(),
                    )
                )
            result.append(
                Message(
                    account_id=self.account_id,
                    peer=peer,
                    message_id=message.id,
                    revision=int(
                        (
                            getattr(message, "edit_date", None) or message.date
                        ).timestamp()
                        * 1_000_000
                    ),
                    date=message.date.timestamp(),
                    text=getattr(message, "raw_text", "") or "",
                    links=text_links(message),
                    # Telegram's update stream reports the owner's own notes
                    # in Saved Messages as incoming, while a fresh read says
                    # outgoing (ADR 52 Q18).
                    outgoing=bool(getattr(message, "out", False))
                    or (peer == f"user:{self.account_id}" and author_peer == peer),
                    service=isinstance(message, types.MessageService),
                    mentioned=bool(getattr(message, "mentioned", False)),
                    reply_to_self=bool(
                        parent
                        and (
                            parent.outgoing
                            if isinstance(parent, Message)
                            else parent.out
                        )
                    ),
                    reply_to=reply_id,
                    **header,
                    reply_read=True,
                    topic_id=getattr(reply, "reply_to_top_id", None),
                    media=media,
                    media_info=media_info,
                    attachment_id=attachment_identity(message),
                    native_edited=bool(getattr(message, "edit_date", None))
                    and not getattr(message, "edit_hide", False),
                    edit_hidden=bool(getattr(message, "edit_hide", False)),
                    forwarded=getattr(message, "fwd_from", None) is not None,
                    forwarded_from=forwarded_from(message, entities),
                    **forward_details(message),
                    author=author[:256],
                    author_peer=author_peer,
                    sender_is_bot=group_authors.bot_flag(sender, author_peer),
                    author_signature=_text(getattr(message, "post_author", None), 256),
                    reply_status="none"
                    if not reply_id or "reply_elsewhere" in header
                    else "available"
                    if parent
                    else "unknown",
                    reactions=None
                    if isinstance(message, types.MessageService)
                    else reaction_snapshot(
                        getattr(message, "reactions", None),
                        self.account_id,
                        peer,
                        observed,
                    ),
                )
            )
        if authors and getattr(self, "store", None) is not None:
            self.store.observe_authors(authors)
        if albums and getattr(self, "store", None) is not None:
            self.store.observe_albums(albums)
        return result

    def _elsewhere(self, value, entities):
        """Name another chat's original sender from what Telegram supplied, a
        known entity or a chat already in the store; never a network read."""
        sender = value.get("sender_peer")
        if not value.get("sender") and sender:
            entity = entities.get(sender) if entities else None
            if entity is not None:
                value["sender"] = (utils.get_display_name(entity) or "")[:256] or None
            elif getattr(self, "store", None) is not None:
                rows = self.store.rows(
                    "SELECT body FROM chats WHERE key=?",
                    (f"{self.account_id}/{sender}",),
                )
                if rows:
                    title = json.loads(rows[0]["body"]).get("title")
                    value["sender"] = title[:256] if title else None
        return ReplyElsewhere(**value)

    def admit_peer(self, peer, entity=None):
        if peer.startswith(("chat:", "channel:")):
            rows = self.store.rows(
                "SELECT body FROM chats WHERE key=?", (f"{self.account_id}/{peer}",)
            )
            description = group_chats.describe(entity)
            if description and description.peer != peer:
                return False
            if rows:
                chat = Chat.model_validate_json(rows[0]["body"])
                if chat.kind != "group":
                    return False
                # Cached event entities cannot restore access or erase a newer
                # protected/forum observation. Negative evidence is conservative.
                if description and (
                    not description.supported or description.access == "lost"
                ):
                    self._observe_group(entity)
                state = self.store.groups.get(chat.key)
                return state.supported and state.access == "available"
            if (
                description
                and description.supported
                and description.access == "available"
                and not getattr(entity, "min", False)
            ):
                return self._observe_group(entity) is not None
            if entity is None or getattr(entity, "min", False):
                raise Unavailable("Group metadata unavailable; discover before retry")
            return False
        if peer == f"user:{self.account_id}":
            # Saved Messages takes live messages too (ADR 50).
            self.store.ensure_saved()
            return True
        if not peer.startswith("user:") or peer == "user:777000":
            return False
        if entity is not None and (
            not isinstance(entity, types.User) or entity.bot or entity.deleted
        ):
            return False
        rows = self.store.rows(
            "SELECT body FROM chats WHERE key=?", (f"{self.account_id}/{peer}",)
        )
        if rows:
            return Chat.model_validate_json(rows[0]["body"]).kind == "private"
        if not isinstance(entity, types.User):
            raise Unavailable(
                "Private dialog metadata unavailable; discover before retry"
            )
        self.store.put_chat(
            Chat(
                account_id=self.account_id,
                peer=peer,
                title=utils.get_display_name(entity) or peer,
                kind="private",
                photo_id=profile_photo_id(entity),
            )
        )
        return True

    def _observe_group(
        self,
        entity,
        *,
        activity=None,
        dialog=None,
        observed=None,
        expected_revision=None,
    ):
        description = group_chats.describe(entity)
        if description is None:
            return None
        group_sending.observe_metadata(self, entity)
        key = f"{self.account_id}/{description.peer}"
        rows = self.store.rows("SELECT body FROM chats WHERE key=?", (key,))
        if not rows and (
            not description.supported
            or description.access != "available"
            or getattr(entity, "min", False)
        ):
            return None
        previous = Chat.model_validate_json(rows[0]["body"]) if rows else None
        if previous and previous.kind != "group":
            return None
        if previous and getattr(entity, "min", False):
            state = self.store.groups.get(key)
            if not description.supported or description.access == "lost":
                return self.store.groups.observe(
                    key,
                    access="lost" if description.access == "lost" else state.access,
                    supported=state.supported and description.supported,
                    reason=description.reason,
                    observed_at=observed,
                    migrated_to=description.migrated_to,
                    expected_revision=expected_revision,
                )
            return state
        if (
            previous
            and expected_revision is not None
            and self.store.groups.get(key).access_revision != expected_revision
        ):
            return self.store.groups.get(key)
        mute = getattr(
            getattr(getattr(dialog, "dialog", None), "notify_settings", None),
            "mute_until",
            None,
        )
        chat = (
            previous
            or Chat(
                account_id=self.account_id,
                peer=description.peer,
                title=description.title,
                kind="group",
            )
        ).model_copy(
            update=dict(
                title=description.title,
                photo_id=profile_photo_id(entity),
                **(
                    dict(
                        muted=bool(mute and mute > datetime.now(UTC)),
                        archived=bool(dialog.archived),
                        read_through=getattr(dialog.dialog, "read_inbox_max_id", 0),
                        latest_message_id=getattr(dialog.dialog, "top_message", 0)
                        or None,
                    )
                    if dialog
                    else {}
                ),
            )
        )
        self.store.put_chat(chat, activity=activity)
        return self.store.groups.observe(
            key,
            access=description.access,
            supported=description.supported,
            reason=description.reason,
            observed_at=observed,
            migrated_to=description.migrated_to,
            expected_revision=expected_revision,
        )

    def _group_read(self, peer, *, expected=None):
        self.ensure_authorized()
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        key = f"{self.account_id}/{peer}"
        chat = self.store.chat(key)
        state, intake = self.store.groups.get(key), self.store.groups.intake(key)
        if chat.kind != "group" or not state.supported or state.access != "available":
            raise Refused("Native group access unavailable")
        if expected is not None and expected != (state.access_revision, intake.epoch):
            raise Refused("Native group changed during read")
        return state.access_revision, intake.epoch

    def _group_request_guard(self, request, peer, expected, *, identities=None):
        request_type = type(request)
        captured_before = getattr(request, "offset_id", None)
        captured_pts = getattr(request, "pts", None)

        def guard(native):
            self._group_read(peer, expected=expected)
            if type(native) is not request_type:
                raise Refused("Native group request changed")
            destination = getattr(native, "peer", None) or getattr(
                native, "channel", None
            )
            if destination is not None and peer_name(destination) != peer:
                raise Refused("Native group request destination changed")
            if identities is not None:
                self._known_group_sources(peer, identities)
                if tuple(
                    getattr(identity, "id", None) for identity in native.id
                ) != tuple(identities):
                    raise Refused("Exact group request sources changed")
            if isinstance(native, functions.messages.GetHistoryRequest):
                intake = self.store.groups.intake(f"{self.account_id}/{peer}")
                if (
                    native.offset_id != captured_before
                    or native.limit != 100
                    or native.add_offset
                    or native.max_id
                    or native.min_id
                    or (
                        captured_before
                        and (intake.complete or intake.before != captured_before)
                    )
                    or not any(
                        chat["peer"] == peer for chat in self.store.sync_targets()
                    )
                ):
                    raise Refused("Bounded recent group request changed")
            if isinstance(native, functions.updates.GetChannelDifferenceRequest):
                intake = self.store.groups.intake(f"{self.account_id}/{peer}")
                if (
                    native.pts != captured_pts
                    or intake.pts != captured_pts
                    or native.limit != 100
                    or not isinstance(native.filter, types.ChannelMessagesFilterEmpty)
                ):
                    raise Refused("Bounded channel recovery request changed")

        request._enloq_final_guard = guard
        guard(request)
        return request

    async def _group_messages(self, peer, expected, *, identities=None, before=0):
        """Group-only raw reads carry policy/wire guards; private SDK reads stay."""
        self._group_read(peer, expected=expected)
        entity = await self.client.get_input_entity(native_peer(peer))
        self._group_read(peer, expected=expected)
        if peer_name(entity) != peer:
            raise Refused("Native group resolution identity mismatch")
        if identities is None:
            request = functions.messages.GetHistoryRequest(
                peer=entity,
                offset_id=before,
                offset_date=None,
                add_offset=0,
                limit=100,
                max_id=0,
                min_id=0,
                hash=0,
            )
        else:
            ids = [types.InputMessageID(identity) for identity in identities]
            request = (
                functions.channels.GetMessagesRequest(
                    utils.get_input_channel(entity), ids
                )
                if peer.startswith("channel:")
                else functions.messages.GetMessagesRequest(ids)
            )
        request = self._group_request_guard(
            request, peer, expected, identities=identities
        )
        observed = self.observation()
        try:
            response = await self.client(request)
        except (errors.ChannelPrivateError, errors.ChannelPublicGroupNaError):
            state = self.store.groups.get(f"{self.account_id}/{peer}")
            self.store.groups.observe(
                state.key,
                access="lost",
                supported=state.supported,
                reason="read_forbidden",
                observed_at=observed,
                expected_revision=expected[0],
            )
            raise Refused("Native group read access was revoked") from None
        self._group_read(peer, expected=expected)
        if (
            not isinstance(
                response,
                (
                    types.messages.Messages,
                    types.messages.MessagesSlice,
                    types.messages.ChannelMessages,
                ),
            )
            or len(response.messages) > 100
        ):
            raise Unavailable("Native group source page is incomplete")
        supplied = group_chats.entity_map(response.users + response.chats)
        if peer in supplied:
            self.admit_peer(peer, supplied[peer])
            self._group_read(peer, expected=expected)
        return response.messages, supplied

    async def refresh_group_authors(self, peer, authors):
        return await group_authors.refresh(self, peer, authors)

    async def refresh_group(self, peer):
        """One exact known-group metadata read, not a participant/history scan."""
        self.ensure_authorized()
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        key = f"{self.account_id}/{peer}"
        previous = self.store.groups.get(key)
        epoch = self.store.groups.intake(key).epoch
        observed = self.observation()
        try:
            if peer.startswith("channel:"):
                entity = await self.client.get_input_entity(native_peer(peer))
                response = await self.client(
                    functions.channels.GetFullChannelRequest(entity)
                )
            else:
                response = await self.client(
                    functions.messages.GetFullChatRequest(int(peer.split(":")[1]))
                )
        except (errors.ChannelPrivateError, errors.ChannelPublicGroupNaError):
            self.store.groups.observe(
                key,
                access="lost",
                supported=previous.supported,
                reason="read_forbidden",
                observed_at=observed,
                expected_revision=previous.access_revision,
            )
            raise Refused("Native group read access was revoked") from None
        supplied = group_chats.entity_map(getattr(response, "chats", []))
        entity = supplied.get(peer)
        if entity is None:
            raise Unavailable("Native group metadata response is incomplete")
        if self.store.groups.intake(key).epoch != epoch:
            raise Unavailable("Group intake changed during metadata read")
        state = self._observe_group(
            entity, observed=observed, expected_revision=previous.access_revision
        )
        if state and state.supported and state.access == "available":
            pts = getattr(getattr(response, "full_chat", None), "pts", None)
            intake = self.store.groups.intake(key)
            if (
                peer.startswith("channel:")
                and isinstance(pts, int)
                and intake.pts is None
            ):
                self.store.groups.checkpoint(key, pts, epoch=intake.epoch)
        return state

    def _group_observation_current(self, peer, revisions):
        if revisions is None or not peer.startswith(("chat:", "channel:")):
            return True
        key = f"{self.account_id}/{peer}"
        revision = (
            self.store.groups.get(key).access_revision
            if self.store.rows("SELECT 1 FROM group_state WHERE chat_key=?", (key,))
            else 0
        )
        return revision == revisions.get(key, 0)

    async def _group_metadata(self, update, *, observed=None, revisions=None):
        if not getattr(self, "verified", False):
            return
        peer = group_chats.update_peer(update)
        key = f"{self.account_id}/{peer}"
        if not peer:
            return
        if not self._group_observation_current(peer, revisions):
            return
        group_sending.invalidate(self, peer)
        if not self.store.rows("SELECT 1 FROM group_state WHERE chat_key=?", (key,)):
            if not group_chats.member_lost(update, self.account_id):
                return
            # Own membership loss is metadata evidence even before discovery;
            # zero activity keeps this stub out of the active Groups list.
            self.store.put_chat(
                Chat(account_id=self.account_id, peer=peer, title=peer, kind="group")
            )
        if group_chats.member_lost(update, self.account_id):
            state = self.store.groups.get(key)
            return self.store.groups.observe(
                key,
                access="lost",
                supported=state.supported,
                reason="membership_removed",
                observed_at=observed,
                expected_revision=revisions.get(key, 0)
                if revisions is not None
                else None,
            )
        try:
            return await self.refresh_group(peer)
        except (Refused, Unavailable, errors.RPCError, OSError):
            pass  # Ordinary bounded discovery/replay can retry metadata alone.

    def _migration(self, native, *, observed=None, revisions=None):
        relation = group_chats.migration(native)
        if relation is not None:
            old_peer, new_peer = relation
            key = f"{self.account_id}/{old_peer}"
            if self.store.rows("SELECT 1 FROM group_state WHERE chat_key=?", (key,)):
                state = self.store.groups.get(key)
                self.store.groups.observe(
                    key,
                    access="lost",
                    supported=state.supported,
                    reason="migrated",
                    migrated_to=new_peer,
                    observed_at=observed,
                    expected_revision=revisions.get(key, 0)
                    if revisions is not None
                    else None,
                )

    async def _group_service(self, update):
        if not getattr(self, "verified", False) or not isinstance(
            update.message, types.MessageService
        ):
            return
        native = update.message
        self._migration(native)
        peer = peer_name(native.peer_id)
        if not self._content_allowed(native):
            return
        if peer.startswith(("chat:", "channel:")):
            try:
                if self.admit_peer(peer):
                    self.sync.ingest(
                        await self.normalize(native), target=False, live=False
                    )
            except Unavailable:
                pass

    async def _channel_gap(self, update):
        if not getattr(self, "verified", False):
            return
        key = f"{self.account_id}/channel:{update.channel_id}"
        if self.store.rows("SELECT 1 FROM group_state WHERE chat_key=?", (key,)):
            # Do not advance a checkpoint from this signal; replay owes effects.
            intake = self.store.groups.intake(key)
            if not intake.gap:
                self.store.groups.mark_gap(key)

    def _content_allowed(self, native):
        peer = peer_name(native.peer_id)
        if peer.startswith(("chat:", "channel:")) and (
            getattr(getattr(native, "reply_to", None), "forum_topic", False)
            or isinstance(
                getattr(native, "action", None), types.MessageActionTopicCreate
            )
        ):
            key = f"{self.account_id}/{peer}"
            if self.store.rows("SELECT 1 FROM group_state WHERE chat_key=?", (key,)):
                state = self.store.groups.get(key)
                self.store.groups.observe(
                    key, access=state.access, supported=False, reason="forum"
                )
            return False
        if peer.startswith(("chat:", "channel:")) and getattr(
            native, "noforwards", False
        ):
            self.store.groups.withdraw_source(f"{self.account_id}/{peer}/{native.id}")
            return False
        return True

    async def _incoming(self, event):
        if not getattr(self, "verified", False):
            # Not yet or no longer verified; the gap cursor still owes this update.
            return
        peer = peer_name(event.message.peer_id)
        try:
            admitted = self.admit_peer(peer, getattr(event, "chat", None))
        except Unavailable:
            return  # Discovery and bounded catch-up will resolve this peer.
        if admitted and self._content_allowed(event.message):
            self.sync.ingest(
                await self.normalize(event.message),
                live=isinstance(event, events.NewMessage.Event)
                and not isinstance(event, events.MessageEdited.Event),
                intake="initial"
                if isinstance(event, events.MessageEdited.Event)
                else None,
            )

    async def _deleted(self, event):
        if not getattr(self, "verified", False):
            return
        peer = peer_name(event.peer) if getattr(event, "peer", None) else None
        self.sync.deleted(peer, event.deleted_ids)

    async def _reactions(self, update):
        if not getattr(self, "verified", False):
            return
        self.reaction_update(update, self.store.clock())

    def reaction_update(self, update, observed):
        try:
            peer = peer_name(update.peer)
        except Refused:
            return
        group = group_sending.group_peer(peer)
        if group:
            try:
                self._group_read(peer)
            except (Refused, Unavailable):
                return
        if (peer.startswith("user:") or group) and type(update.msg_id) is int:
            self.sync.reactions(
                peer,
                update.msg_id,
                reaction_snapshot(update.reactions, self.account_id, peer, observed),
            )

    async def _read_outbox(self, update):
        if not getattr(self, "verified", False):
            return
        self.read_outbox_update(update)

    def read_outbox_update(self, update):
        try:
            peer = peer_name(update.peer)
        except Refused:
            return
        if peer.startswith("user:") and isinstance(update.max_id, int):
            self.sync.read_outbox(peer, update.max_id)

    def note_unread_reactions(self, peer, count):
        """Telegram's count of unread reactions to the owner's messages. A
        changed nonzero count asks the sync cycle for one bounded read."""
        if not isinstance(count, int) or count < 0:
            return
        key = "unread_reactions:" + f"{self.account_id}/{peer}"
        with self.store.transaction() as db:
            row = db.execute("SELECT value FROM cursors WHERE key=?", (key,)).fetchone()
            value = json.loads(row[0]) if row else {"count": 0, "read": 0}
            if value["count"] != count:
                value["count"] = count
                db.execute(
                    "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                    (key, json.dumps(value)),
                )

    async def unread_reactions(self, peer):
        """Reaction snapshots of messages Telegram reports as having unread
        reactions: (message ID, snapshot) pairs. Marks nothing read."""
        observed = self.store.clock()
        entity = await self.client.get_input_entity(native_peer(peer))
        result = await self.client(
            functions.messages.GetUnreadReactionsRequest(
                peer=entity, offset_id=0, add_offset=0, limit=100, max_id=0, min_id=0
            )
        )
        return [
            (
                native.id,
                reaction_snapshot(
                    getattr(native, "reactions", None),
                    self.account_id,
                    peer,
                    observed,
                ),
            )
            for native in getattr(result, "messages", [])
            if isinstance(native, types.Message) and peer_name(native.peer_id) == peer
        ]

    async def post_saved(self, *, path=None, caption="", text="", monospace=False):
        """Post a file with its caption, or a text note, to this account's own
        Saved Messages (ADR 43 Q4). Nothing chooses a peer; Telegram's answer
        is the confirmation, and a failure is reported without sending again.
        A note goes out as written; a monospace one, such as a pairing code
        (ADR 47 Q2), is one code entity, which Telegram's apps copy on a tap.
        """
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        own = types.InputPeerSelf()
        if path is not None:
            message = await self.client.send_file(
                own, str(path), caption=caption, force_document=True
            )
        else:
            message = await self.client.send_message(
                own,
                text,
                link_preview=False,
                parse_mode=None,
                formatting_entities=[
                    types.MessageEntityCode(
                        offset=0, length=len(text.encode("utf-16-le")) // 2
                    )
                ]
                if monospace
                else None,
            )
        identity = getattr(message, "id", None)
        if not isinstance(identity, int):
            raise Unavailable("Telegram did not confirm the Saved Messages post")
        return identity

    async def edit_saved(self, identity, *, path, caption):
        """Replace the file and caption of this account's own Saved Build
        message (ADR 50 Q5). Telegram allows edits there with no time limit.
        False when Telegram says the message is gone, so a new one is posted;
        the old file stays until Telegram confirms the new one."""
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        try:
            message = await self.client.edit_message(
                types.InputPeerSelf(),
                identity,
                caption,
                file=str(path),
                force_document=True,
            )
        except (errors.MessageIdInvalidError, errors.MsgIdInvalidError):
            return False
        except errors.MessageNotModifiedError:
            return True
        if getattr(message, "id", None) != identity:
            raise Unavailable("Telegram did not confirm the Saved Build edit")
        return True

    async def pin_saved(self, identity):
        """Pin this account's own Saved Build message, without a notification
        (ADR 50 Q5)."""
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        await self.client.pin_message(types.InputPeerSelf(), identity, notify=False)

    async def latest_saved_image(self, *, scan, max_bytes):
        """The newest photo or still image file among this account's last
        `scan` Saved Messages, read for a host command (ADR 43). Images only: text, pairing codes included, has its own
        read."""
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        for message in await self.client.get_messages(
            types.InputPeerSelf(), limit=scan
        ):
            found = saved_image(message)
            if found is None:
                continue
            mime, declared = found
            if not isinstance(declared, int) or not 0 < declared <= max_bytes:
                raise MediaTooLarge("The newest image in Saved Messages is too large")
            data = await self.client.download_media(message, file=bytes)
            if not isinstance(data, bytes) or not 0 < len(data) <= max_bytes:
                raise Unavailable("The newest image in Saved Messages did not download")
            return {
                "message_id": message.id,
                "date": message.date,
                "caption": message.message or "",
                "mime": mime,
                "data": data,
            }
        raise Refused(f"No image among the last {scan} Saved Messages")

    async def saved_notes(self, *, count, scan, max_bytes, build=None):
        """The owner's newest `count` own notes among this account's last
        `scan` Saved Messages, newest first, for a host command (ADR 51 Q1):
        each one's text, the kind of any media and a still image's bytes up to
        `max_bytes`. The service's own posts are skipped: pairing codes and
        records, and the Saved Build message `build`."""
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        notes = []
        for message in await self.client.get_messages(
            types.InputPeerSelf(), limit=scan
        ):
            if (
                not isinstance(message, types.Message)
                or message.id == build
                or service_post(message)
            ):
                continue
            note = {
                "message_id": message.id,
                "date": message.date,
                "text": message.message or "",
                "media": classify(message)[0],
                "mime": None,
                "data": None,
            }
            found = saved_image(message)
            if found is not None and isinstance(found[1], int):
                mime, declared = found
                if 0 < declared <= max_bytes:
                    data = await self.client.download_media(message, file=bytes)
                    if isinstance(data, bytes) and 0 < len(data) <= max_bytes:
                        note.update(mime=mime, data=data)
            notes.append(note)
            if len(notes) == count:
                break
        return notes

    async def latest_saved_code(self, *, scan, max_age):
        """The newest pairing code the service posted among this account's last
        `scan` Saved Messages, while it is under `max_age` seconds old, for a
        coding agent's browser on the host (ADR 43). Other text never comes back."""
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        oldest = self.store.clock() - max_age
        for message in await self.client.get_messages(
            types.InputPeerSelf(), limit=scan
        ):
            if message.date.timestamp() < oldest:
                # Newest first: everything after this is older still.
                break
            code = saved_code(message)
            if code is not None:
                return {"message_id": message.id, "date": message.date, "code": code}
        raise Refused(
            f"No pairing code posted to Saved Messages in the last {max_age} seconds"
        )

    async def send_reaction(self, peer, identity, reactions):
        """Set the owner's complete reaction list on one native message.

        Returns Telegram's resulting snapshot when its answer carries one."""
        if group_sending.group_peer(peer):
            raise Refused("Group reactions are display-only")
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        native = []
        for reaction in reactions:
            if reaction.emoji is not None:
                native.append(types.ReactionEmoji(emoticon=reaction.emoji))
            elif reaction.custom is not None:
                native.append(
                    types.ReactionCustomEmoji(document_id=int(reaction.custom))
                )
        observed = self.store.clock()
        entity = await self.client.get_input_entity(native_peer(peer))
        try:
            result = await self.client(
                functions.messages.SendReactionRequest(
                    peer=entity,
                    msg_id=identity,
                    reaction=native,
                    big=False,
                    add_to_recent=bool(native),
                )
            )
        except errors.MessageNotModifiedError:
            return None
        except (
            errors.ReactionInvalidError,
            errors.ReactionEmptyError,
            errors.ReactionsTooManyError,
            errors.PremiumAccountRequiredError,
            errors.MsgIdInvalidError,
            errors.MessageIdInvalidError,
        ) as error:
            raise Refused(
                f"Telegram refused this reaction ({type(error).__name__})"
            ) from None
        for update in getattr(result, "updates", []):
            if (
                isinstance(update, types.UpdateMessageReactions)
                and update.msg_id == identity
            ):
                return reaction_snapshot(
                    update.reactions, self.account_id, peer, observed
                )
            if (
                isinstance(update, types.UpdateEditMessage)
                and getattr(update.message, "id", None) == identity
            ):
                return reaction_snapshot(
                    getattr(update.message, "reactions", None),
                    self.account_id,
                    peer,
                    observed,
                )
        return None

    async def reaction_options(self):
        """The account's selectable ordinary emoji and its reaction limit."""
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        available = await self.client(
            functions.messages.GetAvailableReactionsRequest(hash=0)
        )
        config = await self.client(functions.help.GetAppConfigRequest(hash=0))
        premium = bool(getattr(self.identity, "premium", False))
        limit = app_config_value(
            getattr(config, "config", None),
            "reactions_user_max_premium" if premium else "reactions_user_max_default",
        )
        if not isinstance(limit, (int, float)) or not 1 <= limit <= 100:
            raise Unavailable("Telegram did not supply this account's reaction limit")
        emoji = [
            r.reaction
            for r in getattr(available, "reactions", [])
            if isinstance(r.reaction, str)
            and 0 < len(r.reaction) <= 32
            and not r.inactive
            and (premium or not r.premium)
        ]
        if not emoji:
            raise Unavailable("Telegram supplied no selectable reactions")
        return {"emoji": emoji[:100], "limit": int(limit), "premium": premium}

    async def profile(self, peer):
        """A private correspondent's profile, read on request and kept nowhere
        (ADR 46 Q11): the names, username, a phone Telegram shares and the
        bio."""
        if not self.connected or not peer.startswith("user:"):
            raise Unavailable("Telegram identity is not connected and verified")
        expected = int(peer.split(":")[1])
        entity = await self.client.get_input_entity(native_peer(peer))
        full = await self.client(functions.users.GetFullUserRequest(id=entity))
        user = next(
            (u for u in getattr(full, "users", None) or [] if u.id == expected),
            None,
        )
        if user is None:
            raise Unavailable("Telegram did not supply this profile")
        usernames = [
            item.username
            for item in getattr(user, "usernames", None) or []
            if getattr(item, "active", True) and item.username
        ]
        about = getattr(getattr(full, "full_user", None), "about", None)
        return {
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or (usernames[0] if usernames else None),
            "phone": user.phone or None,
            "bio": about or None,
        }

    async def contacts(self):
        """The account's Telegram contacts, people only (ADR 51 Q16)."""
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        result = await self.client(functions.contacts.GetContactsRequest(hash=0))
        return [
            found
            for user in getattr(result, "users", None) or []
            if (found := person(user, self.account_id))
        ]

    async def search_people(self, query, *, limit=20):
        """Telegram's own people search, which finds public usernames, and an
        exact @username (ADR 51 Q16). People only."""
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        found = []
        exact = USERNAME.fullmatch(query.strip())
        if exact:
            try:
                resolved = await self.client(
                    functions.contacts.ResolveUsernameRequest(exact.group(1))
                )
            except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError):
                resolved = None
            for user in getattr(resolved, "users", None) or []:
                if value := person(user, self.account_id):
                    found.append(value)
        result = await self.client(
            functions.contacts.SearchRequest(q=query.strip().lstrip("@"), limit=limit)
        )
        users = {user.id: user for user in getattr(result, "users", None) or []}
        for peer in [*result.my_results, *result.results]:
            user = users.get(getattr(peer, "user_id", None))
            if (value := person(user, self.account_id)) and value not in found:
                found.append(value)
        return found

    async def resolve_person(self, peer):
        """A person New Message may open (ADR 51 Q17), resolved by ID."""
        if not self.connected or not peer.startswith("user:"):
            raise Unavailable("Telegram identity is not connected and verified")
        entity = await self.client.get_entity(native_peer(peer))
        found = person(entity, self.account_id)
        if found is None or found["peer"] != peer:
            raise Refused("Only a person's private chat opens here")
        return found

    async def group_profile(self, peer):
        """A group's profile, read on request and kept nowhere (ADR 47 Q7):
        its title, public @username, member count and description. The
        group's access state stays refresh_group's to record."""
        if not self.connected or not peer.startswith(("chat:", "channel:")):
            raise Unavailable("Telegram identity is not connected and verified")
        if peer.startswith("channel:"):
            entity = await self.client.get_input_entity(native_peer(peer))
            response = await self.client(
                functions.channels.GetFullChannelRequest(entity)
            )
        else:
            response = await self.client(
                functions.messages.GetFullChatRequest(int(peer.split(":")[1]))
            )
        group = group_chats.entity_map(getattr(response, "chats", None) or []).get(peer)
        if group is None:
            raise Unavailable("Telegram did not supply this group")
        full = getattr(response, "full_chat", None)
        members = getattr(full, "participants_count", None)
        if not isinstance(members, int):
            # A basic group lists its members instead of counting them.
            listed = getattr(getattr(full, "participants", None), "participants", None)
            members = len(listed) if isinstance(listed, list) else None
        usernames = [
            item.username
            for item in getattr(group, "usernames", None) or []
            if getattr(item, "active", True) and item.username
        ]
        return {
            "title": getattr(group, "title", "") or "",
            "username": getattr(group, "username", None)
            or (usernames[0] if usernames else None),
            "members": members,
            "description": getattr(full, "about", None) or None,
        }

    def bind_receipts(self, sink: ReceiptSink):
        self._receipt_sink = sink

    async def _receipt(self, update):
        # Receipt writes are synchronous and complete before replay advances.
        self._receipt_sink(ReceiptBatch(((update.random_id, update.id),)))

    async def exact(self, peer, identity):
        group = peer.startswith(("chat:", "channel:"))
        if group:
            self._group_read(peer)
            self._known_group_sources(peer, [identity])
            sources = await self.exact_many(peer, [identity])
            return next(
                (source for source in sources if source.message_id == identity), None
            )
        observed = self.observation()
        native = await self.client.get_messages(native_peer(peer), ids=identity)
        if not isinstance(native, (types.Message, types.MessageService)):
            return None
        if peer_name(native.peer_id) != peer:
            raise Refused("Exact source identity mismatch")
        if not self._content_allowed(native):
            return None
        return await self.normalize(native, observed=observed)

    async def exact_many(self, peer, identities):
        """One bounded read of known IDs; never a history scan."""
        observed = self.observation()
        group = peer.startswith(("chat:", "channel:"))
        expected = self._group_read(peer) if group else None
        if group:
            self._known_group_sources(peer, list(identities)[:100])
            values, supplied = await self._group_messages(
                peer, expected, identities=list(identities)[:100]
            )
        else:
            values = await self.client.get_messages(
                native_peer(peer), ids=list(identities)[:100]
            )
            supplied = None
        if group:
            self._group_read(peer, expected=expected)
        sources = [
            m for m in values if isinstance(m, (types.Message, types.MessageService))
        ]
        if any(peer_name(m.peer_id) != peer for m in sources):
            raise Refused("Exact source identity mismatch")
        if any(m.id not in identities for m in sources):
            raise Refused("Exact source message identity mismatch")
        eligible = [m for m in sources if self._content_allowed(m)]
        if group:
            self._group_read(peer, expected=expected)
        return await self.normalize_page(
            eligible,
            observed=observed,
            entities=supplied,
        )

    def _known_group_sources(self, peer, identities):
        key = f"{self.account_id}/{peer}"
        if (
            not identities
            or len(identities) > 100
            or any(
                type(identity) is not int or identity <= 0 for identity in identities
            )
        ):
            raise Refused("Invalid exact group sources")
        rows = self.store.rows(
            "SELECT message_id FROM messages WHERE chat_key=? AND date>=? AND message_id IN (SELECT value FROM json_each(?))",
            (key, self.store.clock() - 30 * 86400, json.dumps(identities)),
        )
        if {row["message_id"] for row in rows} != set(identities):
            raise Refused("Exact group reads require retained cached sources")

    async def inspect_deletion(self, peer, identities):
        """Fresh exact reads preserve MessageEmpty and cover every captured ID.

        Telethon's helper drops explicit empty and wrong-peer objects. Raw
        batches distinguish those cases from omitted/truncated responses.
        Groups require known retained IDs, including tombstones; private reads
        preserve their existing cache-independent recovery. Neither mutates.
        """
        group = group_sending.group_peer(peer)
        if not group:
            private_peer(peer)
        identities = deletion_ids(identities)
        account_id = self.account_id
        present, absent, unavailable = [], [], []
        with observation_cycle():
            for offset in range(0, len(identities), 100):
                batch = identities[offset : offset + 100]
                observed = self.observation()
                try:
                    self.ensure_authorized()
                    if not self.connected or self.account_id != account_id:
                        raise Unavailable("Telegram account unavailable for exact read")
                    if group:
                        expected = self._group_read(peer)
                        self._known_group_sources(peer, batch)
                        identity_verified = False

                        def guard(_=None):
                            self._group_read(peer, expected=expected)
                            if self.account_id != account_id:
                                raise Refused(
                                    "Connected deletion check account changed"
                                )
                            if identity_verified:
                                me = getattr(self, "identity", None)
                                age = self.store.clock() - self.identity_checked_at
                                if (
                                    not isinstance(me, types.User)
                                    or me.id != account_id
                                    or me.bot
                                    or me.deleted
                                    or not 0 <= age <= group_sending.PROOF_SECONDS
                                ):
                                    raise Refused(
                                        "Verified deletion check identity changed or expired"
                                    )

                        with native_request_guard(guard):
                            me = await self.client.get_me()
                            guard()
                            if not isinstance(me, types.User) or me.deleted:
                                raise Refused(
                                    "Verified deletion check identity unavailable"
                                )
                            self._accept_identity(me)
                            identity_verified = True
                            guard()
                            values, supplied = await self._group_messages(
                                peer, expected, identities=batch
                            )
                            guard()
                    else:
                        result = await self.client(
                            functions.messages.GetMessagesRequest(
                                [types.InputMessageID(identity) for identity in batch]
                            )
                        )
                        if not isinstance(
                            result,
                            (types.messages.Messages, types.messages.MessagesSlice),
                        ) or not isinstance(result.messages, (list, tuple)):
                            raise Unavailable("Native deletion read is incomplete")
                        values, supplied = result.messages, None
                except Exception:
                    unavailable.extend(batch)
                    continue
                if not isinstance(values, (list, tuple)) or len(values) > 100:
                    unavailable.extend(batch)
                    continue
                by_id = {}
                malformed = False
                for native in values:
                    identity = getattr(native, "id", None)
                    if type(identity) is not int or identity not in batch:
                        malformed = True
                        break
                    by_id.setdefault(identity, []).append(native)
                if malformed or self.account_id != account_id or not self.connected:
                    unavailable.extend(batch)
                    continue
                for identity in batch:
                    values = by_id.get(identity, ())
                    if len(values) != 1:
                        unavailable.append(identity)
                        continue
                    native = values[0]
                    native_target = getattr(native, "peer_id", None)
                    try:
                        if (
                            native_target is not None
                            and peer_name(native_target) != peer
                        ):
                            raise Unavailable(
                                "Native exact result belongs to another peer"
                            )
                        if isinstance(native, types.MessageEmpty):
                            # Only a native explicit empty object proves absence.
                            absent.append(identity)
                        elif isinstance(native, (types.Message, types.MessageService)):
                            if native_target is None:
                                raise Unavailable("Native exact result has no peer")
                            normalized = (
                                (
                                    await self.normalize_page(
                                        [native], observed=observed, entities=supplied
                                    )
                                )[0]
                                if group
                                else await self.normalize(native, observed=observed)
                            )
                            if (
                                normalized.account_id != account_id
                                or normalized.peer != peer
                                or normalized.message_id != identity
                                or normalized.deleted
                            ):
                                raise Unavailable(
                                    "Native exact result has no usable identity"
                                )
                            present.append(normalized)
                        else:
                            unavailable.append(identity)
                    except Exception:
                        unavailable.append(identity)
        return DeletionRead(
            account_id,
            peer,
            identities,
            tuple(present),
            tuple(absent),
            tuple(unavailable),
        )

    async def delete(
        self, description: DeleteDescription, evidence: DeleteEvidence
    ) -> DeleteReceipt:
        """One native for-both chunk through the sole paced session owner."""
        if not isinstance(description, DeleteDescription):
            raise Refused("A captured native deletion description is required")
        self.ensure_authorized()
        if not self.connected:
            raise Unavailable("Telegram disconnected; nothing deleted")
        if description.account_id != self.account_id:
            raise Refused("Wrong connected deletion account")
        if not getattr(self.client, "_guarded_deletion", False):
            raise Refused("Native deletion requires the existing paced session owner")
        group = group_sending.group_peer(description.peer)
        if group:
            request, proof = await group_deletion.prepare(self, description, evidence)
        else:
            proof = None
            request = functions.messages.DeleteMessagesRequest(
                id=list(description.identities), revoke=True
            )

        def final_guard(native):
            self.ensure_authorized()
            if not self.connected or self.account_id != description.account_id:
                raise Refused("Connected deletion account changed")
            if proof is not None:
                proof.guard(native, fresh=True)
            elif (
                not isinstance(native, functions.messages.DeleteMessagesRequest)
                or native.revoke is not True
                or tuple(native.id) != description.identities
            ):
                raise Refused("Captured native deletion membership changed")
            evidence.guard()

        request._enloq_final_guard = final_guard
        try:
            result = await self._dispatch(evidence, request)
        except errors.RPCError as error:
            # Explicit native input/permission/authentication rejections narrow
            # the outcome. Transport/server errors retain possible exposure;
            # flood narrowing belongs to the request policy's wire evidence.
            if group and isinstance(error, group_sending.ACCESS_ERRORS):
                group_deletion.access_lost(self, description.peer, proof.expected)
                evidence.refused(type(error).__name__)
            elif error.code in {400, 401, 403}:
                evidence.refused(type(error).__name__)
            raise
        if not isinstance(result, types.messages.AffectedMessages):
            raise TimeoutError("No native for-both deletion acknowledgement")
        try:
            return DeleteReceipt(result.pts, result.pts_count)
        except Refused:
            raise TimeoutError("Unusable native deletion acknowledgement") from None

    async def history(self, peer, before, limit):
        group = peer.startswith(("chat:", "channel:"))
        expected = self._group_read(peer) if group else None
        if group:
            key = f"{self.account_id}/{peer}"
            intake = self.store.groups.intake(key)
            if not any(chat["key"] == key for chat in self.store.sync_targets()) or (
                before and (intake.complete or before != intake.before)
            ):
                raise Refused("Group history is limited to bounded recent intake")
        observed = self.observation()
        if group:
            values, supplied = await self._group_messages(peer, expected, before=before)
        else:
            values = await self.client.get_messages(
                native_peer(peer), limit=min(limit, 100), offset_id=before
            )
            supplied = None
        if group:
            self._group_read(peer, expected=expected)
        sources = [
            m for m in values if isinstance(m, (types.Message, types.MessageService))
        ]
        if any(peer_name(m.peer_id) != peer for m in sources):
            raise Refused("History source identity mismatch")
        eligible = [m for m in sources if self._content_allowed(m)]
        if group:
            self._group_read(peer, expected=expected)
        normalized = await self.normalize_page(
            eligible,
            observed=observed,
            entities=supplied,
        )
        if group:
            return group_chats.GroupHistoryPage(
                normalized,
                raw_count=len(values),
                before=min((m.id for m in sources), default=0),
                oldest=min((m.date.timestamp() for m in sources), default=None),
            )
        return normalized

    async def _intake_native(
        self, native, entities, observed, *, edit=False, revisions=None
    ):
        if not isinstance(native, (types.Message, types.MessageService)):
            return
        peer = peer_name(native.peer_id)
        if not self._group_observation_current(peer, revisions):
            return
        self._migration(native, observed=observed, revisions=revisions)
        try:
            # A global difference's supplied metadata was observed once at
            # its captured revision. A later exact refresh supplies a separate
            # proof and cannot make that old entity snapshot fresh again.
            supplied_peer = (
                None
                if revisions is not None and peer.startswith(("chat:", "channel:"))
                else entities.get(peer)
            )
            admitted = self.admit_peer(peer, supplied_peer)
        except Unavailable:
            if peer.startswith("user:"):
                raise
            return  # Metadata discovery owes only bounded recent group intake.
        if admitted and self._content_allowed(native):
            self.sync.ingest(
                await self.normalize(native, observed=observed, entities=entities),
                target=peer.startswith("user:"),
                intake="initial" if edit else None,
            )

    async def _replay_update(self, update, entities, observed, *, revisions=None):
        if isinstance(
            update, (types.UpdateEditMessage, types.UpdateEditChannelMessage)
        ):
            await self._intake_native(
                update.message, entities, observed, edit=True, revisions=revisions
            )
        elif isinstance(
            update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)
        ):
            await self._intake_native(
                update.message, entities, observed, revisions=revisions
            )
        elif isinstance(update, types.UpdateDeleteChannelMessages):
            self.sync.deleted(f"channel:{update.channel_id}", update.messages)
        elif isinstance(update, types.UpdateDeleteMessages):
            self.sync.deleted(None, update.messages)
        elif isinstance(update, types.UpdateChannelTooLong):
            if self._group_observation_current(
                f"channel:{update.channel_id}", revisions
            ):
                await self._channel_gap(update)
        elif isinstance(update, group_chats.METADATA_UPDATES):
            await self._group_metadata(update, observed=observed, revisions=revisions)
        elif isinstance(update, types.UpdateMessageID):
            await self._receipt(update)
        elif isinstance(update, types.UpdateMessageReactions):
            self.reaction_update(update, observed)
        elif isinstance(update, types.UpdateReadHistoryOutbox):
            self.read_outbox_update(update)
        elif isinstance(update, CATALOG_UPDATES):
            await self._catalog_update(update)

    async def channel_catch_up(self, peer):
        """Application-owned per-channel watermark, at most five 100-item slices."""
        if not peer.startswith("channel:"):
            return
        key = f"{self.account_id}/{peer}"
        self._group_read(peer)
        if self.store.groups.intake(key).pts is None:
            await self.refresh_group(peer)
        for _ in range(5):
            expected = self._group_read(peer)
            intake = self.store.groups.intake(key)
            if intake.pts is None:
                raise Unavailable("Native channel checkpoint unavailable")
            self.store.groups.begin_replay(key, epoch=intake.epoch)
            observed = self.observation()
            entity = await self.client.get_input_entity(native_peer(peer))
            self._group_read(peer, expected=expected)
            if peer_name(entity) != peer:
                raise Refused("Native channel resolution identity mismatch")
            request = functions.updates.GetChannelDifferenceRequest(
                channel=utils.get_input_channel(entity),
                filter=types.ChannelMessagesFilterEmpty(),
                pts=intake.pts,
                limit=100,
                force=False,
            )
            request = self._group_request_guard(request, peer, expected)
            try:
                difference = await self.client(request)
            except (errors.ChannelPrivateError, errors.ChannelPublicGroupNaError):
                state = self.store.groups.get(key)
                self.store.groups.observe(
                    key,
                    access="lost",
                    supported=state.supported,
                    reason="read_forbidden",
                    observed_at=observed,
                    expected_revision=expected[0],
                )
                raise Refused("Native group read access was revoked") from None
            self._group_read(peer, expected=expected)
            if isinstance(difference, types.updates.ChannelDifferenceEmpty):
                self.store.groups.checkpoint(
                    key,
                    difference.pts,
                    epoch=intake.epoch,
                    final=bool(difference.final),
                )
                if difference.final:
                    return
                continue
            if not isinstance(
                difference,
                (
                    types.updates.ChannelDifference,
                    types.updates.ChannelDifferenceTooLong,
                ),
            ):
                raise Unavailable("Native channel difference is incomplete")
            supplied = group_chats.entity_map(difference.users + difference.chats)
            if peer in supplied:
                self._observe_group(
                    supplied[peer], observed=observed, expected_revision=expected[0]
                )
                self._group_read(peer, expected=expected)
            messages = (
                difference.messages
                if isinstance(difference, types.updates.ChannelDifferenceTooLong)
                else difference.new_messages
            )
            if any(
                peer_name(native.peer_id) != peer
                for native in messages
                if isinstance(native, (types.Message, types.MessageService))
            ):
                raise Refused("Channel difference source identity mismatch")
            if isinstance(difference, types.updates.ChannelDifferenceTooLong):
                if (
                    peer_name(difference.dialog.peer) != peer
                    or type(difference.dialog.pts) is not int
                    or difference.dialog.pts < intake.pts
                ):
                    raise Refused("Channel gap identity mismatch")
                intake = self.store.groups.mark_gap(key)
                expected = (expected[0], intake.epoch)
                for native in messages[:100]:
                    await self._intake_native(native, supplied, observed)
                self._group_read(peer, expected=expected)
                self.store.groups.checkpoint(
                    key,
                    difference.dialog.pts,
                    epoch=intake.epoch,
                    final=bool(difference.final),
                )
                if difference.final:
                    return  # The suffix proves no complete source/day coverage.
                continue
            for update in difference.other_updates:
                if (
                    isinstance(
                        update,
                        (
                            types.UpdateNewMessage,
                            types.UpdateNewChannelMessage,
                            types.UpdateEditMessage,
                            types.UpdateEditChannelMessage,
                        ),
                    )
                    and peer_name(update.message.peer_id) != peer
                ):
                    raise Refused("Channel difference update identity mismatch")
                if isinstance(
                    update, types.UpdateDeleteChannelMessages
                ) and update.channel_id != int(peer.split(":")[1]):
                    raise Refused("Channel deletion identity mismatch")
            for native in messages:
                await self._intake_native(native, supplied, observed)
            for update in difference.other_updates:
                await self._replay_update(update, supplied, observed)
            self._group_read(peer, expected=expected)
            self.store.groups.checkpoint(
                key, difference.pts, epoch=intake.epoch, final=bool(difference.final)
            )
            if difference.final:
                return
        raise Unavailable(
            "Channel catch-up checkpointed; bounded continuation required"
        )

    async def catch_up(self):
        # An independent application watermark, never advanced ahead of durable data.
        key = "global_difference"
        rows = self.store.rows("SELECT value FROM cursors WHERE key=?", (key,))
        if not rows:
            state = await self.client(functions.updates.GetStateRequest())
            await self._save_state(key, state)
            return
        state = json.loads(rows[0]["value"])
        for _ in range(5):
            observed = self.store.clock()
            revisions = {
                row["chat_key"]: self.store.groups.get(row["chat_key"]).access_revision
                for row in self.store.rows("SELECT chat_key FROM group_state")
            }
            difference = await self.client(
                functions.updates.GetDifferenceRequest(
                    pts=state["pts"],
                    date=datetime.fromtimestamp(state["date"], UTC),
                    qts=state["qts"],
                )
            )
            if isinstance(difference, types.updates.DifferenceTooLong):
                raise GapTooLong(
                    "Telegram gap exceeds bounded recovery; full resync required"
                )
            if isinstance(difference, types.updates.DifferenceEmpty):
                return
            supplied = group_chats.entity_map(difference.users + difference.chats)
            for entity in difference.chats:
                description = group_chats.describe(entity)
                if description:
                    current = self._group_observation_current(
                        description.peer, revisions
                    )
                    group_state = self._observe_group(
                        entity,
                        observed=observed,
                        expected_revision=revisions.get(
                            f"{self.account_id}/{description.peer}", 0
                        ),
                    )
                    if current and group_state is not None:
                        revisions[group_state.key] = group_state.access_revision
            for native in difference.new_messages:
                await self._intake_native(
                    native, supplied, observed, revisions=revisions
                )
            for update in difference.other_updates:
                await self._replay_update(
                    update, supplied, observed, revisions=revisions
                )
            next_state = (
                getattr(difference, "intermediate_state", None) or difference.state
            )
            await self._save_state(key, next_state)
            state = {
                "pts": next_state.pts,
                "qts": next_state.qts,
                "date": next_state.date.timestamp(),
            }
            if isinstance(difference, types.updates.Difference):
                return
        raise Unavailable(
            "Catch-up checkpointed; further bounded continuation required"
        )

    async def _save_state(self, key, state):
        with self.store.transaction() as db:
            _write_state(db, key, state)

    async def restart_after_gap(self):
        """After Telegram reports a gap too long to replay, restart from its
        latest update state, as its update guide prescribes. In the same
        transaction every chat loses its coverage floor and seven-day catch-up
        progress, and discovery restarts from the newest dialog, so recent chats
        are read again within seven days; nothing older is scanned. Only the sync
        loop calls this."""
        latest = await self.client(functions.updates.GetStateRequest())
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM cursors WHERE key='dialogs' OR key LIKE 'recent:%' OR key LIKE 'coverage:%'"
            )
            _write_state(db, "global_difference", latest)
            for row in db.execute("SELECT chat_key FROM group_state").fetchall():
                self.store.groups._gap(db, row["chat_key"])

    async def review_group_send(self, description: SendDescription, guard):
        """Read fresh one-peer rights for deliberate retained Resume; send nothing."""
        return await group_sending.review(self, description, guard)

    async def send(
        self, description: SendDescription, evidence: SendEvidence
    ) -> ReceiptBatch:
        if not self.connected:
            raise Unavailable("Telegram disconnected")
        if description.account_id != self.account_id:
            raise Refused("Wrong connected account")
        if description.media is not None:
            return await send_selected(self, description, evidence)
        if description.images:
            return await self.send_images(description, evidence)
        evidence.guard()
        target_proof = await group_sending.prepare(self, description, evidence)
        peer = target_proof.peer
        target = description.reply_to
        reply = (
            types.InputReplyToMessage(
                reply_to_msg_id=target, top_msg_id=description.topic_id
            )
            if target
            else None
        )
        request = functions.messages.SendMessageRequest(
            peer=peer,
            message=description.text,
            random_id=description.random_ids[0],
            reply_to=reply,
            no_webpage=True,
            send_as=target_proof.send_as,
            allow_paid_stars=0,
            allow_paid_floodskip=False,
        )
        request._enloq_group_send_proof = target_proof.proof
        try:
            result = await self._dispatch(evidence, request, description=description)
        except (
            *group_sending.SEND_REFUSALS,
            errors.MessageTooLongError,
        ) as error:
            raise group_sending.refusal(self, description, evidence, error) from None
        except errors.RPCError as error:
            if group_sending.paid_refusal(error):
                raise self._refused(evidence, error) from None
            raise
        if isinstance(result, types.UpdateShortSentMessage):
            return ReceiptBatch(((description.random_ids[0], result.id),))
        for update in getattr(result, "updates", []):
            if (
                isinstance(update, types.UpdateMessageID)
                and update.random_id == description.random_ids[0]
            ):
                await self._receipt(update)
                return ReceiptBatch(((update.random_id, update.id),))
        raise TimeoutError("No native random-ID receipt")

    async def send_images(
        self, description: SendDescription, evidence: SendEvidence
    ) -> ReceiptBatch:

        if description.account_id != self.account_id or not self.connected:
            raise DeliveryRefused("Connected image sender does not match")
        images = description.images
        for image in images:
            validate_send_image(image)
        if not 1 <= len(images) <= 10 or len(set(description.random_ids)) != len(
            images
        ):
            raise DeliveryRefused("Native image count or correlation invalid")
        evidence.guard()
        target_proof = await group_sending.prepare(self, description, evidence)
        peer = target_proof.peer
        reply = (
            types.InputReplyToMessage(
                description.reply_to, top_msg_id=description.topic_id
            )
            if description.reply_to
            else None
        )
        try:
            config = await self.client(functions.help.GetConfigRequest())
            if len(description.text.encode("utf-16-le")) // 2 > min(
                1024, config.caption_length_max
            ):
                raise DeliveryRefused("Caption exceeds the current native limit")
            uploaded = []
            for image in images:
                evidence.guard()
                if target_proof.proof:
                    target_proof.proof.guard()
                validate_send_image(image)
                with native_request_guard(
                    group_sending.preparation_guard(target_proof, evidence)
                ):
                    file = await self.client.upload_file(
                        image.path,
                        file_name="image."
                        + {
                            "image/png": "png",
                            "image/jpeg": "jpg",
                            "image/webp": "webp",
                        }[image.mime],
                        file_size=image.size,
                    )
                evidence.guard()
                if target_proof.proof:
                    target_proof.proof.guard()
                validate_send_image(image)
                media = types.InputMediaUploadedPhoto(file)
                if len(images) > 1:
                    with native_request_guard(
                        group_sending.preparation_guard(target_proof, evidence)
                    ):
                        associated = await self.client(
                            functions.messages.UploadMediaRequest(peer, media)
                        )
                    evidence.guard()
                    if target_proof.proof:
                        target_proof.proof.guard()
                    validate_send_image(image)
                    media = utils.get_input_media(associated.photo)
                uploaded.append(media)
        except (asyncio.CancelledError, MTProtoRecoveryPaused):
            # A hold met while preparing is settled like one met by the final
            # request: nothing was handed off, so the send is refused as not sent.
            raise
        except group_sending.ACCESS_ERRORS as error:
            group_sending.lost(self, description.peer)
            evidence.guard()
            raise DeliveryRefused(type(error).__name__) from None
        except Refused:
            raise
        except DEFINITE as error:
            evidence.guard()
            what = "this image" if len(images) == 1 else "these images"
            raise named_refusal(error, what) from None
        except Exception:
            raise DeliveryRefused(
                "Image preparation or upload failed; nothing sent"
            ) from None
        if target_proof.proof:
            target_proof = await group_sending.prepare(self, description, evidence)
            if bytes(target_proof.peer) != bytes(peer):
                raise DeliveryRefused("Captured native image send peer changed")
            peer = target_proof.peer
        evidence.guard()
        for image in images:
            validate_send_image(image)
        if len(images) == 1:
            request = functions.messages.SendMediaRequest(
                peer=peer,
                media=uploaded[0],
                message=description.text,
                random_id=description.random_ids[0],
                reply_to=reply,
                send_as=target_proof.send_as,
                allow_paid_stars=0,
                allow_paid_floodskip=False,
            )
        else:
            request = functions.messages.SendMultiMediaRequest(
                peer=peer,
                reply_to=reply,
                multi_media=[
                    types.InputSingleMedia(
                        media=media,
                        message=description.text if index == 0 else "",
                        random_id=description.random_ids[index],
                    )
                    for index, media in enumerate(uploaded)
                ],
                send_as=target_proof.send_as,
                allow_paid_stars=0,
                allow_paid_floodskip=False,
            )
        request._enloq_group_send_proof = target_proof.proof

        def exact_images(_):
            try:
                for image in images:
                    validate_send_image(image)
            except (MediaValidationError, OSError):
                raise DeliveryRefused(
                    "Exact send image bytes are unavailable or changed"
                ) from None

        request._enloq_media_guard = exact_images
        try:
            result = await self._dispatch(evidence, request, description=description)
        except (
            *group_sending.SEND_REFUSALS,
            errors.MediaCaptionTooLongError,
            errors.MediaInvalidError,
            errors.PhotoInvalidDimensionsError,
        ) as error:
            raise group_sending.refusal(self, description, evidence, error) from None
        except errors.RPCError as error:
            if group_sending.paid_refusal(error):
                raise self._refused(evidence, error) from None
            raise
        receipts = {}
        if len(images) == 1 and isinstance(result, types.UpdateShortSentMessage):
            receipts[description.random_ids[0]] = result.id
            await self._receipt(
                types.UpdateMessageID(id=result.id, random_id=description.random_ids[0])
            )
        for update in getattr(result, "updates", []):
            if (
                isinstance(update, types.UpdateMessageID)
                and update.random_id in description.random_ids
            ):
                await self._receipt(update)
                receipts[update.random_id] = update.id
        return ReceiptBatch(tuple(receipts.items()))

    async def _dispatch(self, evidence, request, *, description=None):
        """Broad durable evidence precedes the native client call; the request
        policy can narrow it only at its existing observation points."""
        if description is not None:
            evidence.guard()
            captured_request = bytes(request)

            def final_guard(native):
                self.ensure_authorized()
                if not self.connected or self.account_id != description.account_id:
                    raise Refused("Connected send account changed")
                native_randoms = (
                    tuple(item.random_id for item in native.multi_media)
                    if isinstance(native, functions.messages.SendMultiMediaRequest)
                    else (getattr(native, "random_id", None),)
                )
                reply = getattr(
                    getattr(native, "reply_to", None), "reply_to_msg_id", None
                )
                if (
                    peer_name(native.peer) != description.peer
                    or native_randoms != description.random_ids
                    or reply != description.reply_to
                    or getattr(getattr(native, "reply_to", None), "top_msg_id", None)
                    != (description.topic_id if description.reply_to else None)
                    or type(native.allow_paid_stars) is not int
                    or native.allow_paid_stars != 0
                    or native.allow_paid_floodskip is not False
                    or bytes(native) != captured_request
                ):
                    raise Refused("Captured native send identity changed")
                proof = getattr(native, "_enloq_group_send_proof", None)
                if group_sending.group_peer(description.peer):
                    if proof is None:
                        raise Refused("Current native group send rights are required")
                    proof.guard(native, fresh=True)
                media_guard = getattr(native, "_enloq_media_guard", None)
                if media_guard:
                    media_guard(native)
                evidence.guard()

            request._enloq_final_guard = final_guard
        evidence.calling()
        request._enloq_wire = evidence
        if description is not None and not getattr(self.client, "request_policy", None):
            # A plain in-memory client has no packer; preserve the same guarded
            # durable order immediately before its single call.
            evidence.watching()
            request._enloq_final_guard(request)
            evidence.handing_off()
        return await self.client(request)

    def _refused(self, evidence, error):
        reason = type(error).__name__
        evidence.refused(reason)
        return DeliveryRefused(reason)

    def held_until(self, description: SendDescription):
        """When the request policy's account or chat hold ends."""
        kind, identity = description.peer.split(":")
        return self.client.request_policy.due("account", f"peer:{kind}_id:{identity}")

    async def refresh_delivery(self):
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        await self.catch_up()

    async def read(self, peer, through):
        if group_sending.group_peer(peer):
            raise Refused("Group native Read is unavailable")
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        await self.client.send_read_acknowledge(native_peer(peer), max_id=through)

    async def typing(self, peer):
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        entity = await self.client.get_input_entity(native_peer(peer))
        await self.client(
            functions.messages.SetTypingRequest(
                peer=entity, action=types.SendMessageTypingAction()
            )
        )

    def _group_avatar_valid(self, peer, photo_id, revision=None):
        self.ensure_authorized()
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        key = f"{self.account_id}/{peer}"
        chat, state = self.store.chat(key), self.store.groups.get(key)
        if (
            chat.kind != "group"
            or not state.supported
            or state.access != "available"
            or chat.photo_id != photo_id
            or not photo_id
            or (revision is not None and state.access_revision != revision)
        ):
            raise Refused("Current native group profile photo unavailable")
        return state.access_revision

    async def _download_group_avatar(self, peer, photo_id, path, *, max_bytes):
        if type(max_bytes) is not int or not 0 < max_bytes <= 256 * 1024:
            raise Refused("Invalid group profile photo size bound")
        revision = self._group_avatar_valid(peer, photo_id)
        identity = int(peer.split(":")[1])
        asset = {}

        def guard(request):
            self._group_avatar_valid(peer, photo_id, revision)
            while isinstance(
                request,
                (functions.InvokeWithLayerRequest, functions.InitConnectionRequest),
            ):
                request = request.query
            if isinstance(request, functions.messages.GetChatsRequest):
                if peer.startswith("chat:") and request.id == [identity]:
                    return
            elif isinstance(request, functions.channels.GetChannelsRequest):
                if (
                    peer.startswith("channel:")
                    and len(request.id) == 1
                    and peer_name(request.id[0]) == peer
                ):
                    return
            elif isinstance(request, functions.upload.GetFileRequest):
                location = request.location
                if (
                    isinstance(location, types.InputPeerPhotoFileLocation)
                    and peer_name(location.peer) == peer
                    and location.photo_id == int(photo_id)
                    and not location.big
                    and not getattr(location, "video", False)
                    and bytes(location) == asset.get("location")
                    and type(request.offset) is int
                    and 0 <= request.offset <= max_bytes
                    and request.offset % (64 * 1024) == 0
                    and type(request.limit) is int
                    and 0 < request.limit <= 64 * 1024
                ):
                    return
            elif isinstance(request, functions.auth.ExportAuthorizationRequest):
                if request.dc_id == asset.get("dc_id"):
                    return
            elif isinstance(
                request, functions.auth.ImportAuthorizationRequest
            ) and asset.get("dc_id"):
                return
            elif isinstance(request, functions.help.GetConfigRequest):
                return
            raise Refused("Group profile request changed or exceeds its bound")

        observed = self.observation()
        with native_request_guard(guard):
            if peer.startswith("channel:"):
                native = await self.client.get_input_entity(native_peer(peer))
                self._group_avatar_valid(peer, photo_id, revision)
                if peer_name(native) != peer:
                    raise Refused("Group profile resolution identity mismatch")
                request = functions.channels.GetChannelsRequest(
                    [utils.get_input_channel(native)]
                )
            else:
                request = functions.messages.GetChatsRequest([identity])
            guard(request)
            try:
                response = await self.client(request)
            except (errors.ChannelPrivateError, errors.ChannelPublicGroupNaError):
                state = self.store.groups.get(f"{self.account_id}/{peer}")
                self.store.groups.observe(
                    state.key,
                    access="lost",
                    supported=state.supported,
                    reason="read_forbidden",
                    observed_at=observed,
                    expected_revision=revision,
                )
                raise Refused("Native group profile access was revoked") from None
            self._group_avatar_valid(peer, photo_id, revision)
            entity = group_chats.entity_map(getattr(response, "chats", [])).get(peer)
            description = group_chats.describe(entity)
            if description is None:
                raise Refused("Exact group profile metadata unavailable")
            if not description.supported or description.access != "available":
                self._observe_group(
                    entity, observed=observed, expected_revision=revision
                )
                self._group_avatar_valid(peer, photo_id, revision)
            if getattr(entity, "min", False):
                raise Refused("Exact group profile metadata unavailable")
            self._observe_group(entity, observed=observed, expected_revision=revision)
            if profile_photo_id(entity) != photo_id:
                raise Refused("Group profile photo changed or is unavailable")
            asset["dc_id"] = entity.photo.dc_id
            location = types.InputPeerPhotoFileLocation(
                peer=utils.get_input_peer(entity, check_hash=False),
                photo_id=int(photo_id),
                big=False,
            )
            asset["location"] = bytes(location)
            total = 0
            fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            with os.fdopen(fd, "wb") as stream:
                async with asyncio.timeout(30):
                    async for chunk in self.client.iter_download(
                        location, dc_id=asset["dc_id"], request_size=64 * 1024
                    ):
                        self._group_avatar_valid(peer, photo_id, revision)
                        total += len(chunk)
                        if total > max_bytes:
                            raise Refused("Group profile photo exceeds the size bound")
                        stream.write(chunk)
            self._group_avatar_valid(peer, photo_id, revision)

    async def download_avatar(self, peer, photo_id, path: Path, *, max_bytes):
        if peer.startswith(("chat:", "channel:")):
            return await self._download_group_avatar(
                peer, photo_id, path, max_bytes=max_bytes
            )
        if not self.connected or not peer.startswith("user:"):
            raise Unavailable("Telegram identity is not connected and verified")
        entity = await self.client.get_entity(native_peer(peer))
        if (
            not isinstance(entity, types.User)
            or entity.id != int(peer.split(":")[1])
            or profile_photo_id(entity) != photo_id
        ):
            raise Refused("Profile photo changed or is unavailable")
        location = types.InputPeerPhotoFileLocation(
            peer=utils.get_input_peer(entity, check_hash=False),
            photo_id=int(photo_id),
            big=False,
        )
        total = 0
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb") as stream:
            async with asyncio.timeout(30):
                async for chunk in self.client.iter_download(
                    location, dc_id=entity.photo.dc_id, request_size=64 * 1024
                ):
                    total += len(chunk)
                    if total > max_bytes:
                        raise Refused("Profile photo exceeds the size bound")
                    stream.write(chunk)

    async def _media(
        self, peer, identity, expected_revision, max_bytes, *, thumb=False
    ):
        if not self.connected:
            raise Unavailable("Telegram identity is not connected and verified")
        group = group_sending.group_peer(peer)
        group_guard = (
            self._group_media_guard(peer, identity, expected_revision)
            if group
            else None
        )
        observed = self.observation()
        if group:
            expected = self._group_read(peer)
            values, supplied = await self._group_messages(
                peer, expected, identities=[identity]
            )
            group_guard()
            native = next(
                (
                    value
                    for value in values
                    if isinstance(value, types.Message) and value.id == identity
                ),
                None,
            )
            if len(values) != 1 or any(
                isinstance(value, types.Message)
                and (value.id != identity or peer_name(value.peer_id) != peer)
                for value in values
            ):
                raise Refused("Exact group media source identity changed")
            if native is not None and not self._content_allowed(native):
                raise Refused("Native group media source is protected")
        else:
            supplied = None
            native = await self.client.get_messages(native_peer(peer), ids=identity)
        if not native or not native.media:
            raise Unavailable("Source media unavailable")
        if expected_revision is not None:
            normalized = (
                await self.normalize(native, observed=observed, entities=supplied)
                if group
                else await self.normalize(native, observed=observed)
            )
            stored = self.store.message(f"{self.account_id}/{peer}", identity)
            if stored is not None:
                valid = stored.revision == expected_revision and (
                    same_content(stored, normalized)
                    or (
                        stored.attachment_id is None
                        and normalized.attachment_id is None
                        and (stored.native_revision or stored.revision)
                        == normalized.revision
                        and stored.text == normalized.text
                        and stored.media == normalized.media
                    )
                )
            else:
                valid = normalized.revision == expected_revision
            if group and (
                not stored
                or any(
                    getattr(stored, field) != getattr(normalized, field)
                    for field in ("author_peer", "author_signature", "date", "service")
                )
            ):
                valid = False
            if not valid:
                if self.sync:
                    self.sync.ingest(normalized, target=False, arrival=False)
                raise Refused("Native media revision changed; refresh its original")
        if group_guard:
            group_guard()
        # Photo/document properties also unwrap Telegram's cached web preview;
        # no third-party URL is fetched to obtain that native media.
        asset = native.photo or native.document or native.media
        declared = getattr(native.document, "size", None) if native.document else None
        if not thumb and isinstance(declared, int) and declared > max_bytes:
            raise MediaTooLarge("Media exceeds size limit")
        return asset

    def _group_media_guard(self, peer, identity, revision):
        expected = self._group_read(peer)
        key = f"{self.account_id}/{peer}"
        if (
            type(identity) is not int
            or identity <= 0
            or type(revision) is not int
            or revision <= 0
        ):
            raise Refused("Exact cached group media revision is required")

        def guard(_=None):
            self._group_read(peer, expected=expected)
            source = self.store.message(key, identity)
            if (
                not source
                or source.revision != revision
                or not self.store.groups.current(source, admission=False)
            ):
                raise Refused("Exact cached group media source changed")

        guard()
        return guard

    async def stream(
        self,
        peer,
        identity,
        *,
        max_bytes,
        expected_revision=None,
        priority=DOWNLOAD,
        thumb=False,
    ):
        """File parts in order through the account's file lane."""
        guard = (
            self._group_media_guard(peer, identity, expected_revision)
            if group_sending.group_peer(peer)
            else None
        )
        asset = await self._media(
            peer, identity, expected_revision, max_bytes, thumb=thumb
        )
        if thumb:
            asset = poster_location(asset, max_bytes=max_bytes)
        parts = self.client.iter_parts(asset, msg_data=(native_peer(peer), identity))
        total = 0
        try:
            while True:
                # Each part request inherits its priority from this step's context.
                token = FILE_PRIORITY.set(priority)
                try:
                    if guard:
                        guard()
                        with native_request_guard(guard):
                            chunk = await parts.__anext__()
                        guard()
                    else:
                        chunk = await parts.__anext__()
                except StopAsyncIteration:
                    return
                finally:
                    FILE_PRIORITY.reset(token)
                total += len(chunk)
                if total > max_bytes:
                    raise MediaTooLarge("Media exceeds size limit")
                yield chunk
        finally:
            close = getattr(parts, "aclose", None)
            if close:
                await close()

    async def download(
        self,
        peer,
        identity,
        path: Path,
        *,
        max_bytes,
        expected_revision=None,
        timeout=120,
        priority=DOWNLOAD,
        thumb=False,
    ):
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb") as output:
            async with asyncio.timeout(timeout):
                async for chunk in self.stream(
                    peer,
                    identity,
                    max_bytes=max_bytes,
                    expected_revision=expected_revision,
                    priority=priority,
                    thumb=thumb,
                ):
                    output.write(chunk)

    async def close(self):
        self.verified = False
        try:
            if self.client:
                await self.client.disconnect()
                self.client.session.close()
        finally:
            self.lock.close()
