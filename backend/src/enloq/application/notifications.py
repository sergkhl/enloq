"""One durable, content-free identity per new incoming burst. No inference."""

import json
import uuid

from ..domain.models import Refused
from .content_readiness import QUIET, content_kind

LIFETIME = 300
DDL = """CREATE TABLE IF NOT EXISTS incoming_notices (
    chat_key TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, sources TEXT NOT NULL,
    quiet REAL NOT NULL, eligible REAL, state TEXT NOT NULL, kind TEXT,
    inputs TEXT, outputs TEXT
)"""
MEDIA_LABELS = {
    "image": "Image",
    "audio": "Voice message",
    "video_note": "Video message",
    "video": "Video",
    "gif": "GIF",
    "document": "File",
    "sticker": "Sticker",
    "poll": "Poll",
    "location": "Location",
    "contact": "Contact",
    "link": "Link",
}


class IncomingNotifications:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.started = self.store.clock()
        self.unavailable = lambda: False
        with self.store.transaction() as db:
            db.execute(DDL)

    def allowed(self, key):
        if not self.app.authorized():
            return False
        try:
            chat = self.store.chat(key)
            return chat.kind == "private" and chat.ai_eligible and not chat.muted
        except Refused:
            return False

    def admit(self, message):
        now = self.store.clock()
        key = f"{message.account_id}/{message.peer}"
        if (
            message.outgoing
            or message.deleted
            or message.service
            or message.date < self.started
            or not self.allowed(key)
        ):
            return
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT * FROM incoming_notices WHERE chat_key=?", (key,)
            ).fetchone()
            extend = old and old["state"] == "waiting" and now < old["quiet"]
            sources = json.loads(old["sources"]) if extend else []
            if message.message_id in sources:
                return
            sources.append(message.message_id)
            db.execute(
                "INSERT OR REPLACE INTO incoming_notices VALUES (?,?,?,?,NULL,'waiting',NULL,NULL,NULL)",
                (
                    key,
                    old["id"] if extend else uuid.uuid4().hex,
                    json.dumps(sources),
                    now + QUIET,
                ),
            )

    def rows(self):
        return self.store.rows(
            "SELECT * FROM incoming_notices WHERE state IN ('waiting','pending')"
        )

    def cancel_all(self):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE incoming_notices SET state='cancelled' WHERE state IN ('waiting','pending')"
            )

    def finish(self, row, state="delivered"):
        with self.store.transaction() as db:
            return bool(
                db.execute(
                    "UPDATE incoming_notices SET state=? WHERE id=? AND state IN ('waiting','pending')",
                    (state, row["id"]),
                ).rowcount
            )

    def sources(self, row):
        return [
            self.store.message(row["chat_key"], i) for i in json.loads(row["sources"])
        ]

    def advance(self):
        now = self.store.clock()
        for row in self.rows():
            if not self.allowed(row["chat_key"]):
                self.finish(row, "cancelled")
                continue
            if row["state"] == "pending":
                if not self.candidate(row):
                    self.finish(
                        row,
                        "expired" if now >= row["eligible"] + LIFETIME else "cancelled",
                    )
                continue
            if now < row["quiet"]:
                continue
            sources = self.sources(row)
            if not sources or any(m is None or m.deleted for m in sources):
                self.finish(row, "cancelled")
                continue
            blocks = [
                b
                for b in self.app.stages(row["chat_key"])
                if set(b["sources"]) & {m.message_id for m in sources}
            ]
            kind = content_kind(blocks, self.unavailable())
            if not kind:
                continue
            # If the newest applicable block has a validated rewrite, use it;
            # otherwise the adopted fallback consumes the whole burst once.
            inputs = {str(m.message_id): m.revision for m in sources}
            outputs = (
                {b["id"]: b["revision"] for b in blocks} if kind == "rewrite" else {}
            )
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE incoming_notices SET state='pending',eligible=?,kind=?,inputs=?,outputs=? WHERE id=? AND state='waiting'",
                    (now, kind, json.dumps(inputs), json.dumps(outputs), row["id"]),
                )
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM incoming_notices WHERE quiet<? AND state NOT IN ('waiting','pending')",
                (now - 86400,),
            )

    def candidate(self, row, *, delivered=False, settled=False):
        """Revalidate source/account and completed output before every delivery.

        `settled` serves a phone's own copy (ADR 38 Q5): another destination
        ending the burst, delivered or failed, does not withdraw it, while
        every account, source and output check below still applies."""
        states = (
            {"pending", "delivered", "cancelled"}
            if settled
            else {"pending", "delivered"}
            if delivered
            else {"pending"}
        )
        if (
            row["state"] not in states
            or row["eligible"] is None
            or self.store.clock() >= row["eligible"] + LIFETIME
            or not self.allowed(row["chat_key"])
        ):
            return None
        sources = self.sources(row)
        inputs = json.loads(row["inputs"])
        if any(
            m is None or m.deleted or inputs.get(str(m.message_id)) != m.revision
            for m in sources
        ):
            return None
        newest = max(sources, key=lambda m: m.message_id)
        text = newest.text.strip() or MEDIA_LABELS.get(newest.media, "Message")
        if row["kind"] == "rewrite":
            outputs = json.loads(row["outputs"])
            blocks = self.store.rows(
                "SELECT * FROM blocks WHERE chat_key=?", (row["chat_key"],)
            )
            selected = [b for b in blocks if b["id"] in outputs]
            if len(selected) != len(outputs) or any(
                b["revision"] != outputs[b["id"]]
                or b["state"] not in {"ready", "nothing"}
                or (b["state"] == "ready" and b["rendered_revision"] != b["revision"])
                for b in selected
            ):
                return None
            latest = max(selected, key=lambda b: max(json.loads(b["sources"])))
            text = latest["rendered"] or text
        chat = self.store.chat(row["chat_key"])
        return {
            "id": row["id"],
            "account_id": self.store.account_id,
            "peer": chat.peer,
            "sender": chat.title,
            "count": len(sources),
            "text": " ".join(text.split())[:240],
            "expires": row["eligible"] + LIFETIME,
        }
