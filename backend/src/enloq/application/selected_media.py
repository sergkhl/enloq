"""Deliberate durable selection and exact-byte preparation for one GIF/sticker."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict

from ..domain.catalog import (
    GIF_LIMIT,
    SOURCE,
    STICKER_LIMIT,
    NativeCatalogSource,
    source_identity,
)
from ..domain.models import Conflict, Refused, Unavailable
from ..domain.selection_formats import validate_selection
from ..storage.store import RETENTION
from ..transport.web_media import fetch_web_media

PREPARATION_TIMEOUT = 180


class SelectedMedia:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.storage = app.store.selected_media
        self.tasks = set()

    def allowed(self, key, guard=None):
        revision = self.app.catalog.allowed(key, guard)
        if self.app.transport.account_id != self.store.account_id:
            raise Refused("Selected media belongs to another account")
        return revision

    def read_access(self, key, guard=None):
        """Owned ready bytes remain readable without native preparation rights."""
        if guard:
            guard()
        if (
            not self.app.authorized()
            or self.app.transport.account_id != self.store.account_id
        ):
            raise Refused("Selected media belongs to an unavailable account")
        chat = self.store.chat(key)
        if chat.kind == "group":
            self.app.retained.cache_read(key)
        elif chat.kind not in {"private", "saved"} or not chat.peer.startswith("user:"):
            raise Refused(
                "Only existing private conversations and groups are supported"
            )

    def access_guard(self, key, guard=None):
        current = self.app.catalog.access_guard(key, guard)

        def allowed():
            current()
            if self.app.transport.account_id != self.store.account_id:
                raise Refused("Selected media belongs to another account")

        allowed()
        return allowed

    def connected(self):
        if not self.app.transport.connected:
            raise Refused("Telegram disconnected; your draft is kept")

    def _current(self, key, identity, guard=None, *, revision=None):
        self.allowed(key, guard)
        return self.storage.current(key, identity, revision=revision)

    async def select(
        self, key, revision, session, token, catalog_revision, *, guard=None
    ):
        guard = self.access_guard(key, guard)
        group_revision = self.allowed(key, guard)
        self.connected()
        source = self.app.catalog.resolve(key, session, token, catalog_revision)
        draft = self.store.draft(key)
        if draft["revision"] != revision:
            raise Conflict("Draft changed before media selection")
        if draft["attachments"]:
            raise Refused("Remove images before selecting a GIF or sticker")
        if draft.get("state") == "rewriting":
            raise Refused("Stop or undo this rewrite before selecting media")
        if source.metadata.kind == "sticker":
            premium = await self.app.catalog.entitlement()
            self.allowed(key, guard)
            source = self.app.catalog.resolve(key, session, token, catalog_revision)
            if source.metadata.requires_premium and not premium:
                raise Refused("Telegram Premium is required for this sticker")
        value, _ = self.storage.begin(
            key, revision, source, access_revision=group_revision
        )
        self.storage.collect()
        # Once persisted, the exact selection owns its source independently of
        # transient picker searches and bounded catalog membership.
        return await self._prepare(key, value, guard=guard)

    async def retry(self, key, revision, identity, *, guard=None):
        guard = self.access_guard(key, guard)
        group_revision = self.allowed(key, guard)
        draft = self._current(key, identity, guard, revision=revision)
        if draft.get("state") == "rewriting":
            raise Refused("Stop or undo this rewrite before retrying selected media")
        self.connected()
        value = self.storage.get(key, identity)
        source = SOURCE.validate_python(value["source"])
        if source.metadata.kind == "sticker":
            premium = await self.app.catalog.entitlement()
            self._current(key, identity, guard, revision=revision)
            if source.metadata.requires_premium and not premium:
                raise Refused("Telegram Premium is required for this sticker")
        value = self.storage.preparing(
            key, revision, identity, access_revision=group_revision
        )
        return await self._prepare(key, value, guard=guard)

    async def _prepare(self, key, value, *, guard=None):
        identity = value["id"]
        source = SOURCE.validate_python(value["source"])
        path = self.storage.path(identity)
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with asyncio.timeout(PREPARATION_TIMEOUT):
                self._current(key, identity, guard)
                self.connected()
                if value.get("facts"):
                    facts = await validate_selection(
                        path,
                        source.metadata.kind,
                        expected_sha256=value["facts"]["sha256"],
                    )
                    self._current(key, identity, guard)
                else:
                    # A previous interrupted partial has no exact-byte proof.
                    path.unlink(missing_ok=True)
                    if isinstance(source, NativeCatalogSource):
                        fresh = await self.app.transport.catalog_download(
                            source,
                            path,
                            max_bytes=GIF_LIMIT
                            if source.metadata.kind == "gif"
                            else STICKER_LIMIT,
                        )
                        self._current(key, identity, guard)
                        if (
                            source_identity(fresh) != source_identity(source)
                            or fresh.metadata.kind != source.metadata.kind
                        ):
                            raise Refused("Exact selected media changed")
                        source = fresh
                    else:
                        if source.metadata.kind != "gif":
                            raise Refused(
                                "Stickers require an exact native catalog source"
                            )
                        await fetch_web_media(source.url, path, limit=GIF_LIMIT)
                        self._current(key, identity, guard)
                    facts = await validate_selection(path, source.metadata.kind)
                    self._current(key, identity, guard)
                if source.metadata.kind == "sticker":
                    premium = await self.app.catalog.entitlement()
                    self._current(key, identity, guard)
                    if source.metadata.requires_premium and not premium:
                        raise Refused("Telegram Premium is required for this sticker")
                # Both source bytes and their directory entry precede publishing ready.
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                descriptor = os.open(self.storage.root, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self.storage.verified_file(
                    {**value, "status": "ready", "facts": asdict(facts)}
                )
                return self.storage.finish(key, identity, source=source, facts=facts)
        except asyncio.CancelledError:
            self._failed(
                key,
                identity,
                "Media preparation interrupted; use Retry",
                retryable=True,
            )
            raise
        except Conflict:
            self._failed(
                key,
                identity,
                "Selection changed; select the exact media again",
                retryable=False,
            )
            raise
        except Exception as error:
            retryable = isinstance(error, (Unavailable, TimeoutError, ConnectionError))
            self._failed(
                key,
                identity,
                "Media preparation unavailable; use Retry"
                if retryable
                else "Exact media is unavailable or unsupported; select it again",
                retryable=retryable,
            )
            # A browser/account guard refusal is never a successful draft read.
            self.allowed(key, guard)
            return self.store.draft(key)
        finally:
            self.tasks.discard(task)
            try:
                current = self.storage.get(key, identity)
                if not current.get("facts"):
                    path.unlink(missing_ok=True)
            except Refused:
                path.unlink(missing_ok=True)

    def _failed(self, key, identity, error, *, retryable):
        try:
            self.storage.finish(key, identity, error=error, retryable=retryable)
        except (Conflict, Refused):
            pass

    def discard(self, key, revision, identity, *, guard=None):
        self._current(key, identity, guard, revision=revision)
        return self.storage.discard(key, revision, identity)

    async def validate_send(self, key, revision, *, guard=None):
        """Recheck fresh access and exact facts before the atomic queue transfer."""
        draft = self.store.draft(key)
        selected = draft.get("selected_media")
        if not selected:
            return
        guard = self.access_guard(key, guard)
        identity = selected["id"]
        self._current(key, identity, guard, revision=revision)
        value = self.storage.get(key, identity)
        source = SOURCE.validate_python(value["source"])
        path = self.storage.verified_file(value)
        if source.metadata.kind == "sticker":
            premium = await self.app.catalog.entitlement()
            self._current(key, identity, guard, revision=revision)
            if source.metadata.requires_premium and not premium:
                raise Refused("Telegram Premium is required for this sticker")
        facts = await validate_selection(
            path, source.metadata.kind, expected_sha256=value["facts"]["sha256"]
        )
        self._current(key, identity, guard, revision=revision)
        current = self.storage.get(key, identity)
        if (
            asdict(facts) != current["facts"]
            and {**asdict(facts), "features": list(facts.features)} != current["facts"]
        ):
            raise Refused("Exact selected media format changed; select it again")

    def selected_file(self, key, identity):
        self.read_access(key)
        self.storage.current(key, identity)
        value = self.storage.get(key, identity)
        return value, self.storage.verified_file(value)

    def operation_file(self, key, operation, identity):
        self.read_access(key)
        value = self.store.send_operations.get(operation)
        if value["chat_key"] != key or value["account_id"] != self.store.account_id:
            raise Refused("Send operation belongs to another conversation")
        return self.storage.operation_file(value, identity)

    def message_file(self, message):
        """Optional exact retained bytes for an admitted current native source.

        Received-media owners call this only on current preparation, after their
        normal source/access checks. Historical cache misses never consult it.
        An edited or expired message cannot gain access from operation lifetime.
        """
        if (
            message.account_id != self.store.account_id
            or message.media not in {"gif", "sticker"}
            or not message.outgoing
            or message.deleted
            or message.native_edited
            or message.date < self.store.clock() - RETENTION
        ):
            return None
        key = f"{message.account_id}/{message.peer}"
        current = self.store.message(key, message.message_id)
        if current is None or current.model_dump() != message.model_dump():
            return None
        for row in self.store.rows(
            "SELECT body FROM operations WHERE chat_key=? AND state='confirmed' AND json_extract(body,'$.message_id')=?",
            (key, message.message_id),
        ):
            operation = json.loads(row["body"])
            media = operation.get("media")
            if (
                media
                and media["kind"] == message.media
                and operation["text"] == message.text
                and operation["reply_to"] == message.reply_to
            ):
                if message.media_info and any(
                    getattr(message.media_info, name) is not None
                    and getattr(message.media_info, name) != media.get(name)
                    for name in ("mime", "size", "width", "height", "sticker")
                ):
                    continue
                # Telegram reports whole or rounded seconds of the length the
                # service measured: 1.0 for 1.333333, 6.934 for 6.933244.
                duration = message.media_info and message.media_info.duration
                if duration is not None and (
                    media.get("duration") is None
                    or abs(duration - media["duration"]) >= 1
                ):
                    continue
                return self.storage.description(operation)
        return None

    async def close(self):
        tasks = tuple(task for task in self.tasks if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
