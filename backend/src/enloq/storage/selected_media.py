"""Private durable exact selected/send bytes, outside disposable media LRU."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import asdict

from ..config import private_directory
from ..domain.catalog import GIF_LIMIT, SOURCE, STICKER_LIMIT, source_identity
from ..domain.delivery import SendMedia
from ..domain.models import Chat, Conflict, Refused
from ..domain.selected_media import public_media

DDL = "CREATE TABLE IF NOT EXISTS selected_media (id TEXT PRIMARY KEY, chat_key TEXT NOT NULL REFERENCES chats(key), body TEXT NOT NULL)"


class SelectedMedia:
    def __init__(self, store):
        self.store = store
        self.root = private_directory(store.path.parent / "selected-media")

    def path(self, identity):
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise Refused("Selected media identity is invalid")
        try:
            status = self.root.lstat()
            if (
                not stat.S_ISDIR(status.st_mode)
                or status.st_mode & 0o077
                or self.root.resolve() != self.root
            ):
                raise OSError("private directory")
        except OSError:
            raise Refused("Private selected media directory is unavailable") from None
        return self.root / (identity + ".media")

    def get(self, key, identity, *, db=None):
        self.path(identity)
        if db is None:
            rows = self.store.rows(
                "SELECT body FROM selected_media WHERE id=? AND chat_key=?",
                (identity, key),
            )
            body = rows[0]["body"] if rows else None
        else:
            row = db.execute(
                "SELECT body FROM selected_media WHERE id=? AND chat_key=?",
                (identity, key),
            ).fetchone()
            body = row[0] if row else None
        if body is None:
            raise Refused("Selected media does not belong to this conversation")
        value = json.loads(body)
        source = SOURCE.validate_python(value["source"])
        if source.account_id != self.store.account_id or value["id"] != identity:
            raise Refused("Selected media belongs to another account")
        return value

    def public(self, key, value):
        account, _, peer = key.partition("/")
        return public_media(
            value,
            base=f"/api/accounts/{account}/chats/{peer}/selected-media/{value['id']}",
        )

    @staticmethod
    def _save(db, value):
        db.execute(
            "INSERT OR REPLACE INTO selected_media VALUES (?,?,?)",
            (value["id"], value["chat_key"], json.dumps(value)),
        )

    def _native_access(self, db, key, access_revision):
        row = db.execute("SELECT body FROM chats WHERE key=?", (key,)).fetchone()
        if row is None:
            raise Refused("Conversation unavailable for this account")
        chat = Chat.model_validate_json(row[0])
        if chat.key != key or chat.account_id != self.store.account_id:
            raise Refused("Selected media belongs to another account")
        if chat.kind == "private" and chat.peer.startswith("user:"):
            if access_revision is not None:
                raise Refused("Conversation access changed")
            return
        if chat.kind != "group" or not chat.peer.startswith(("chat:", "channel:")):
            raise Refused(
                "Selected media requires an existing private conversation or group"
            )
        state = self.store.groups.get(key, db=db)
        if (
            type(access_revision) is not int
            or not state.supported
            or state.access != "available"
            or state.migrated_to is not None
            or state.access_revision != access_revision
        ):
            raise Refused("Native group access changed or is unavailable")

    def begin(self, key, revision, source, *, identity=None, access_revision=None):
        """Persist intent before bytes are fetched; never put a source in drafts."""
        chat = self.store.chat(key)
        if source.account_id != self.store.account_id or chat.kind not in {
            "private",
            "group",
        }:
            raise Refused("Selected media belongs to another account")
        value = dict(
            id=identity or uuid.uuid4().hex,
            chat_key=key,
            source=source.model_dump(mode="json"),
            status="preparing",
            facts=None,
            created=self.store.clock(),
            operation=None,
        )
        with self.store.transaction() as db:
            from .store import blank_draft

            self._native_access(db, key, access_revision)
            if access_revision is not None:
                value["group_access_revision"] = access_revision
            row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (key,)
            ).fetchone()
            draft = json.loads(row[0]) if row else blank_draft(key)
            if draft["revision"] != revision:
                raise Conflict("Draft changed before media selection")
            if draft.get("state") == "rewriting":
                raise Refused("Stop or undo this rewrite before selecting media")
            if draft["attachments"]:
                raise Refused("Remove images before selecting a GIF or sticker")
            self._save(db, value)
            draft.update(selected_media=self.public(key, value), revision=revision + 1)
            db.execute(
                "INSERT OR REPLACE INTO drafts VALUES (?,?)", (key, json.dumps(draft))
            )
        return value, draft

    def current(self, key, identity, *, revision=None, db=None):
        if db is None:
            draft = self.store.draft(key)
        else:
            row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (key,)
            ).fetchone()
            draft = json.loads(row[0]) if row else {}
        if (draft.get("selected_media") or {}).get("id") != identity or (
            revision is not None and draft.get("revision") != revision
        ):
            raise Conflict("Selected media or draft changed; review the current draft")
        return draft

    def preparing(self, key, revision, identity, *, access_revision=None):
        with self.store.transaction() as db:
            self._native_access(db, key, access_revision)
            draft = self.current(key, identity, revision=revision, db=db)
            if draft.get("state") == "rewriting":
                raise Refused(
                    "Stop or undo this rewrite before retrying selected media"
                )
            value = self.get(key, identity, db=db)
            if value["status"] != "failed" or not value.get("retryable", True):
                raise Refused("This selection cannot be retried; select media again")
            value.update(status="preparing", error=None)
            if access_revision is not None:
                value["group_access_revision"] = access_revision
            self._save(db, value)
            draft.update(selected_media=self.public(key, value), revision=revision + 1)
            db.execute(
                "UPDATE drafts SET body=? WHERE chat_key=?", (json.dumps(draft), key)
            )
        return value

    def finish(
        self, key, identity, *, source=None, facts=None, error=None, retryable=True
    ):
        with self.store.transaction() as db:
            draft = self.current(key, identity, db=db)
            value = self.get(key, identity, db=db)
            if value["status"] != "preparing":
                raise Conflict("Media preparation is no longer current")
            if facts is not None:
                self._native_access(db, key, value.get("group_access_revision"))
            if source is not None:
                if source_identity(source) != source_identity(
                    SOURCE.validate_python(value["source"])
                ):
                    raise Refused("Exact selected media changed")
                value["source"] = source.model_dump(mode="json")
            value.update(
                status="ready" if facts else "failed", error=error, retryable=retryable
            )
            if facts is not None:
                value["facts"] = asdict(facts)
            self._save(db, value)
            draft.update(
                selected_media=self.public(key, value), revision=draft["revision"] + 1
            )
            db.execute(
                "UPDATE drafts SET body=? WHERE chat_key=?", (json.dumps(draft), key)
            )
        return draft

    def discard(self, key, revision, identity):
        with self.store.transaction() as db:
            draft = self.current(key, identity, revision=revision, db=db)
            if draft.get("state") == "rewriting":
                raise Refused(
                    "Stop or undo this rewrite before removing selected media"
                )
            # The exact text, Undo, rewrite state/error and reply never left it.
            draft.update(selected_media=None, revision=revision + 1)
            db.execute(
                "UPDATE drafts SET body=? WHERE chat_key=?", (json.dumps(draft), key)
            )
        self.collect()
        return draft

    def verified_file(self, value):
        path, facts = self.path(value["id"]), value.get("facts")
        if value.get("status") != "ready" or not facts:
            raise Refused("Selected media is not ready; use Retry or select it again")
        source = SOURCE.validate_python(value["source"])
        ceiling = GIF_LIMIT if source.metadata.kind == "gif" else STICKER_LIMIT
        if (
            facts.get("kind") != source.metadata.kind
            or type(facts.get("size")) is not int
            or not 0 < facts["size"] <= ceiling
        ):
            raise Refused("Exact selected media exceeds its format or file limit")
        try:
            root = self.root.lstat()
            if (
                not stat.S_ISDIR(root.st_mode)
                or root.st_mode & 0o077
                or self.root.resolve() != self.root
            ):
                raise OSError("private directory")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_mode & 0o077
                    or before.st_size != facts["size"]
                ):
                    raise OSError("private file")
                digest = hashlib.sha256()
                total = 0
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > ceiling:
                        raise OSError("file bound")
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
                current = path.lstat()

                def signature(s):
                    return (
                        s.st_dev,
                        s.st_ino,
                        s.st_size,
                        s.st_mtime_ns,
                        s.st_ctime_ns,
                        s.st_mode,
                        s.st_nlink,
                    )

                if (
                    signature(before) != signature(after)
                    or signature(after) != signature(current)
                    or digest.hexdigest() != facts["sha256"]
                ):
                    raise OSError("changed bytes")
        except OSError:
            raise Refused(
                "Exact selected media bytes are unavailable or changed; select it again"
            ) from None
        return path

    def snapshot(self, key, draft, *, db):
        selected = draft.get("selected_media")
        if not selected:
            return None
        value = self.get(key, selected["id"], db=db)
        self.current(key, value["id"], revision=draft["revision"], db=db)
        if value.get("operation") is not None:
            raise Refused("Selected media already belongs to a send operation")
        path = self.verified_file(value)
        facts = {k: v for k, v in value["facts"].items() if k != "animation_json"}
        return {
            "id": value["id"],
            **facts,
            "source": value["source"],
            "path": str(path),
            "emoji": value["source"]["metadata"].get("emoji"),
            "requires_premium": value["source"]["metadata"].get(
                "requires_premium", False
            ),
        }

    def transfer(self, key, identity, operation, *, db):
        value = self.get(key, identity, db=db)
        if value.get("operation") is not None:
            raise Refused("Selected media was already submitted")
        value["operation"] = operation
        self._save(db, value)

    def operation_file(self, operation, identity):
        media = operation.get("media")
        if not media or media["id"] != identity:
            raise Refused("Media does not belong to this send operation")
        value = self.get(operation["chat_key"], identity)
        if (
            value.get("operation") != operation["id"]
            or value["facts"]["sha256"] != media["sha256"]
        ):
            raise Refused("Retained send media ownership changed")
        return value, self.verified_file(value)

    def description(self, operation):
        media = operation.get("media")
        if media is None:
            return None
        _, path = self.operation_file(operation, media["id"])
        return SendMedia(
            **{
                k: media[k]
                for k in (
                    "id",
                    "sha256",
                    "mime",
                    "size",
                    "width",
                    "height",
                    "kind",
                    "format",
                )
            },
            path=path,
            directory=self.root,
            source=SOURCE.validate_python(media["source"]),
            duration=media.get("duration"),
            sticker=media.get("sticker"),
            emoji=media.get("emoji"),
            requires_premium=media.get("requires_premium", False),
        )

    def recover(self, db):
        for row in db.execute("SELECT id,chat_key,body FROM selected_media").fetchall():
            value = json.loads(row["body"])
            if value["status"] != "preparing":
                continue
            value.update(
                status="failed",
                error="Media preparation interrupted; use Retry",
                retryable=True,
            )
            if not value.get("facts"):
                self.path(value["id"]).unlink(missing_ok=True)
            self._save(db, value)
            draft_row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (row["chat_key"],)
            ).fetchone()
            if draft_row:
                draft = json.loads(draft_row[0])
                if (draft.get("selected_media") or {}).get("id") == row["id"]:
                    draft.update(
                        selected_media=self.public(row["chat_key"], value),
                        revision=draft["revision"] + 1,
                    )
                    db.execute(
                        "UPDATE drafts SET body=? WHERE chat_key=?",
                        (json.dumps(draft), row["chat_key"]),
                    )

    def collect(self):
        with self.store.transaction() as db:
            retained = {
                (json.loads(row[0]).get("selected_media") or {}).get("id")
                for row in db.execute("SELECT body FROM drafts")
            }
            retained.update(self.store.send_operations.retained_media(db))
            for row in db.execute("SELECT id FROM selected_media").fetchall():
                if row[0] not in retained:
                    self.path(row[0]).unlink(missing_ok=True)
                    db.execute("DELETE FROM selected_media WHERE id=?", (row[0],))
