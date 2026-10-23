"""Private durable draft images, separate from temporary incoming-media leases."""

import base64
import binascii
import json
import os
import uuid
from pathlib import Path

from ..config import private_directory
from ..domain.models import Conflict, Refused
from ..transport.media import MediaValidationError, image_facts


class Attachments:
    def __init__(self, store):
        self.store = store
        self.root = private_directory(store.path.parent / "draft-images")

    def get(self, key, identity, *, db=None):
        if db is None:
            rows = self.store.rows(
                "SELECT body FROM attachments WHERE id=? AND chat_key=?",
                (identity, key),
            )
            row = rows[0]["body"] if rows else None
        else:
            found = db.execute(
                "SELECT body FROM attachments WHERE id=? AND chat_key=?",
                (identity, key),
            ).fetchone()
            row = found[0] if found else None
        if not row:
            raise Refused("Image does not belong to this conversation")
        value = json.loads(row)
        path = self.root / value["file"]
        if path.parent != self.root or path.is_symlink() or not path.is_file():
            raise Refused("Draft image file unavailable")
        return value, path

    def add(self, key, revision, name, encoded):
        self.store.chat(key)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise Refused("Image encoding invalid") from None
        if not 0 < len(data) <= 5 * 1024 * 1024:
            raise Refused("Each image must be at most 5 MB")
        identity = uuid.uuid4().hex
        path = self.root / (identity + ".image")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                facts = image_facts(path)
            except MediaValidationError:
                raise Refused("Image type, dimensions or size unsupported") from None
            if max(facts.width / facts.height, facts.height / facts.width) > 20:
                raise Refused("Image aspect ratio exceeds Telegram photo bounds")
            value = dict(
                id=identity,
                file=path.name,
                name=Path(name).name[:256],
                mime=facts.mime_type,
                sha256=facts.sha256,
                size=facts.size_bytes,
                width=facts.width,
                height=facts.height,
            )
            with self.store.transaction() as db:
                row = db.execute(
                    "SELECT body FROM drafts WHERE chat_key=?", (key,)
                ).fetchone()
                draft = (
                    json.loads(row[0])
                    if row
                    else dict(
                        chat_key=key,
                        revision=0,
                        text="",
                        attachments=[],
                        reply_to=None,
                        undo=None,
                        state="idle",
                    )
                )
                if draft["revision"] != revision:
                    raise Conflict("Draft changed before image attachment")
                if draft.get("selected_media"):
                    raise Refused(
                        "Remove the selected GIF or sticker before adding images"
                    )
                if len(draft["attachments"]) >= 10:
                    raise Refused("A native album supports at most ten images")
                draft["attachments"].append(identity)
                draft.update(revision=revision + 1, undo=None, state="idle")
                db.execute(
                    "INSERT INTO attachments VALUES (?,?,?)",
                    (identity, key, json.dumps(value)),
                )
                db.execute(
                    "INSERT OR REPLACE INTO drafts VALUES (?,?)",
                    (key, json.dumps(draft)),
                )
            return draft
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def remove(self, key, revision, identity):
        def change(draft):
            if identity not in draft["attachments"]:
                raise Refused("Draft image unavailable")
            draft["attachments"].remove(identity)
            draft.update(undo=None, state="idle")

        draft = self.store.mutate_draft(key, revision, change)
        self.collect()
        return draft

    def snapshot(self, key, identities, *, db=None):
        images = []
        for identity in identities:
            value, path = self.get(key, identity, db=db)
            facts = image_facts(path)
            if facts.sha256 != value["sha256"]:
                raise Refused("Draft image bytes changed; attach it again")
            images.append({**value, "path": str(path)})
        return images

    def collect(self):
        retained = set()
        with self.store.transaction() as db:
            for row in db.execute("SELECT body FROM drafts"):
                retained.update(json.loads(row[0])["attachments"])
            retained.update(self.store.send_operations.retained_images(db))
            for row in db.execute("SELECT id,body FROM attachments").fetchall():
                if row[0] not in retained:
                    path = self.root / json.loads(row[1])["file"]
                    if path.parent == self.root:
                        path.unlink(missing_ok=True)
                    db.execute("DELETE FROM attachments WHERE id=?", (row[0],))
