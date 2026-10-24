"""Arrival notifications with bounded hostile-wording screening (ADR 20)."""

import asyncio
import hashlib
import json
import uuid

from ..domain.forwarding import attribution
from ..domain.models import Refused, reply_target
from . import context, decision_state
from .content_readiness import QUIET

LIFETIME = 300
SCREEN_SECONDS = 5
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
            columns = {
                r["name"] for r in db.execute("PRAGMA table_info(incoming_notices)")
            }
            for name, definition in (
                ("burst", "TEXT"),
                ("alerted", "INTEGER NOT NULL DEFAULT 0"),
                ("silent", "INTEGER NOT NULL DEFAULT 0"),
                ("signature", "TEXT"),
            ):
                if name not in columns:
                    db.execute(
                        f"ALTER TABLE incoming_notices ADD COLUMN {name} {definition}"
                    )
            db.execute("""CREATE TABLE IF NOT EXISTS notice_sources (
                chat_key TEXT NOT NULL, message_id INTEGER NOT NULL, revision INTEGER NOT NULL,
                deadline REAL NOT NULL, state TEXT NOT NULL, attempt TEXT,
                PRIMARY KEY(chat_key,message_id))""")
            # Old readiness candidates have no arrival-screen identity.
            db.execute(
                "UPDATE incoming_notices SET state='cancelled' WHERE burst IS NULL"
            )

    def allowed(self, key):
        if not self.app.authorized():
            return False
        try:
            chat = self.store.chat(key)
            return chat.kind == "private" and chat.ai_eligible and not chat.muted
        except Refused:
            return False

    def bypass(self, key):
        if not self.app.calming.on(key) or self.unavailable():
            return True
        try:
            self.app.admit_ai()
            return False
        except Refused:
            return True

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
            extend = old and now < old["quiet"] and old["state"] != "cancelled"
            sources = json.loads(old["sources"]) if extend else []
            if message.message_id in sources:
                return
            sources.append(message.message_id)
            if not extend:
                db.execute("DELETE FROM notice_sources WHERE chat_key=?", (key,))
                db.execute(
                    "INSERT OR REPLACE INTO incoming_notices(chat_key,id,sources,quiet,state,burst) VALUES (?,?,?,?,'waiting',?)",
                    (
                        key,
                        uuid.uuid4().hex,
                        json.dumps(sources),
                        now + QUIET,
                        uuid.uuid4().hex,
                    ),
                )
            else:
                db.execute(
                    "UPDATE incoming_notices SET sources=?,quiet=? WHERE chat_key=?",
                    (json.dumps(sources), now + QUIET, key),
                )
        bypass = not message.text.strip() or self.bypass(key)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT burst FROM incoming_notices WHERE chat_key=?", (key,)
            ).fetchone()
            attempt = None if bypass else uuid.uuid4().hex
            db.execute(
                "INSERT OR REPLACE INTO notice_sources VALUES (?,?,?,?,?,?)",
                (
                    key,
                    message.message_id,
                    message.revision,
                    now + SCREEN_SECONDS,
                    "fallback" if bypass else "waiting",
                    attempt,
                ),
            )
            if attempt:
                db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
                    (
                        attempt,
                        key,
                        "hostility_screen",
                        str(message.revision),
                        now,
                        json.dumps(
                            {
                                "class": "new",
                                "notice_source": message.message_id,
                                "notice_burst": row["burst"],
                            }
                        ),
                    ),
                )
        self.advance()

    def rows(self):
        return self.store.rows(
            "SELECT * FROM incoming_notices WHERE state IN ('waiting','pending')"
        )

    def cancel_all(self):
        with self.store.transaction() as db:
            db.execute("UPDATE incoming_notices SET state='cancelled'")
            db.execute(
                "UPDATE jobs SET state='cancelled' WHERE role='hostility_screen' AND state IN ('queued','claimed')"
            )

    def finish(self, row, state="delivered"):
        with self.store.transaction() as db:
            return bool(
                db.execute(
                    "UPDATE incoming_notices SET state=?,alerted=max(alerted,?) WHERE id=? AND state IN ('waiting','pending')",
                    (state, int(state == "delivered"), row["id"]),
                ).rowcount
            )

    def sources(self, row):
        return [
            self.store.message(row["chat_key"], i) for i in json.loads(row["sources"])
        ]

    def _screen_current(self, job):
        payload = json.loads(job["payload"])
        rows = self.store.rows(
            "SELECT n.burst,s.* FROM incoming_notices n JOIN notice_sources s ON s.chat_key=n.chat_key WHERE s.attempt=? AND n.state!='cancelled'",
            (job["id"],),
        )
        if (
            not rows
            or rows[0]["burst"] != payload["notice_burst"]
            or rows[0]["state"] != "waiting"
        ):
            return None
        row = rows[0]
        source = self.store.message(job["scope"], row["message_id"])
        if (
            self.store.clock() >= row["deadline"]
            or not source
            or source.deleted
            or source.revision != row["revision"]
            or not self.allowed(job["scope"])
            or self.bypass(job["scope"])
        ):
            return None
        return row, source

    async def screen(self, job):
        current = self._screen_current(job)
        if not current:
            self._screen_end(job, "fallback")
            return
        row, source = current
        captured = context.cached_recent(
            self.store,
            job["scope"],
            before=source.message_id,
            reply_id=reply_target(source),
        )
        supporting = captured.pop("messages")
        captured["inputs"][source.key] = source.revision
        captured["attribution_inputs"][source.key] = attribution(source)
        packet = {
            "state": decision_state.incoming([context.projection(source)], supporting)
        }
        if len(json.dumps(packet).encode()) > context.PACKET_BYTES:
            self._screen_end(job, "fallback")
            return

        def valid():
            return bool(self._screen_current(job)) and context.current(
                self.store, captured
            )

        try:
            async with asyncio.timeout(max(0, row["deadline"] - self.store.clock())):
                hostile = await self.app.rewriting.work.run(
                    self.app.decider,
                    job,
                    packet,
                    valid,
                )
            if valid():
                self._screen_end(job, "hostile" if hostile else "clean")
            else:
                self._screen_end(job, "fallback")
        except asyncio.CancelledError:
            raise
        except (Refused, TimeoutError):
            self._screen_end(job, "fallback")
        finally:
            self.advance()

    def _screen_end(self, job, state):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE notice_sources SET state=? WHERE attempt=? AND state='waiting'",
                (state, job["id"]),
            )
            db.execute(
                "UPDATE jobs SET state='cancelled',error='Notification screen ended' WHERE id=? AND state IN ('queued','claimed')",
                (job["id"],),
            )

    def advance(self):
        now = self.store.clock()
        for row in self.store.rows(
            "SELECT * FROM incoming_notices WHERE state IN ('waiting','pending','delivered')"
        ):
            key = row["chat_key"]
            if row["eligible"] is not None and now >= row["eligible"] + LIFETIME:
                self.finish(row, "expired")
                continue
            if not self.allowed(key):
                self.finish(row, "cancelled")
                continue
            bypass = self.bypass(key)
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE notice_sources SET state='fallback' WHERE chat_key=? AND state='waiting' AND (deadline<=? OR ?)",
                    (key, now, int(bypass)),
                )
                db.execute(
                    "UPDATE jobs SET state='cancelled',error='Notification screen expired' WHERE role='hostility_screen' AND scope=? AND state IN ('queued','claimed') AND id IN (SELECT attempt FROM notice_sources WHERE chat_key=? AND state='fallback')",
                    (key, key),
                )
            eligible = []
            blocks = None
            for item in self.store.rows(
                "SELECT * FROM notice_sources WHERE chat_key=? ORDER BY message_id",
                (key,),
            ):
                source = self.store.message(key, item["message_id"])
                if not source or source.deleted:
                    continue
                if source.revision != item["revision"]:
                    # A changed input invalidates screening. Before the first
                    # alert use the adopted original fallback, without treating
                    # the edit as a new arrival or extending its quiet period.
                    if row["alerted"]:
                        continue
                    with self.store.transaction() as db:
                        db.execute(
                            "UPDATE notice_sources SET revision=?,state='fallback' WHERE chat_key=? AND message_id=?",
                            (source.revision, key, source.message_id),
                        )
                    item = {**item, "revision": source.revision, "state": "fallback"}
                if item["state"] == "waiting":
                    continue
                text = source.text.strip() or MEDIA_LABELS.get(source.media, "Message")
                output = {}
                if item["state"] == "hostile" and not bypass:
                    blocks = blocks if blocks is not None else self.app.stages(key)
                    applicable = [
                        b for b in blocks if source.message_id in b["sources"]
                    ]
                    completed = [
                        b
                        for b in applicable
                        if b["state"] == "ready"
                        and b.get("calmed")
                        and b.get("rendered_revision") == b["revision"]
                    ]
                    if completed:
                        b = completed[-1]
                        text = b["rendered"]
                        output = {b["id"]: b["revision"]}
                    elif not any(
                        b["state"] in ("failed", "unknown")
                        or (
                            b["state"] == "ready"
                            and b.get("rendered_revision") == b["revision"]
                        )
                        or (
                            b["state"] == "original"
                            and self.store.rows(
                                "SELECT 1 FROM incoming_deferred WHERE scope=? AND input_revision=? AND json_extract(payload,'$.decided')=1",
                                (b["id"], b["revision"]),
                            )
                        )
                        or b.get("stage")
                        in ("budget", "paused", "format_paused", "retrying")
                        for b in applicable
                    ):
                        continue
                eligible.append((source, text, output))
            if not eligible:
                if row["state"] == "pending":
                    with self.store.transaction() as db:
                        db.execute(
                            "UPDATE incoming_notices SET state='waiting' WHERE id=?",
                            (row["id"],),
                        )
                continue
            source, text, outputs = eligible[-1]
            inputs = {str(source.message_id): source.revision}
            signature = hashlib.sha256(
                json.dumps(
                    [inputs, text, outputs, len(eligible)], sort_keys=True
                ).encode()
            ).hexdigest()
            if signature == row["signature"]:
                continue
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE incoming_notices SET id=?,eligible=?,state='pending',kind=?,inputs=?,outputs=?,silent=?,signature=? WHERE chat_key=?",
                    (
                        uuid.uuid4().hex,
                        now,
                        "rewrite" if outputs else "original",
                        json.dumps(inputs),
                        json.dumps(outputs),
                        row["alerted"],
                        signature,
                        key,
                    ),
                )
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM notice_sources WHERE chat_key IN (SELECT chat_key FROM incoming_notices WHERE quiet<?)",
                (now - 86400,),
            )
            db.execute("DELETE FROM incoming_notices WHERE quiet<?", (now - 86400,))

    def candidate(self, row, *, delivered=False, settled=False):
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
        inputs = json.loads(row["inputs"])
        selected = [self.store.message(row["chat_key"], int(i)) for i in inputs]
        if not selected or any(
            m is None or m.deleted or m.revision != inputs[str(m.message_id)]
            for m in selected
        ):
            return None
        newest = selected[-1]
        text = newest.text.strip() or MEDIA_LABELS.get(newest.media, "Message")
        outputs = json.loads(row["outputs"])
        if outputs:
            blocks = [b for b in self.app.stages(row["chat_key"]) if b["id"] in outputs]
            if len(blocks) != len(outputs) or any(
                b["revision"] != outputs[b["id"]]
                or b["state"] != "ready"
                or b.get("rendered_revision") != b["revision"]
                for b in blocks
            ):
                return None
            text = blocks[-1]["rendered"]
        return {
            "id": row["id"],
            "group": row["burst"],
            "silent": bool(row["silent"]),
            "account_id": self.store.account_id,
            "peer": newest.peer,
            "sender": self.store.chat(row["chat_key"]).title,
            "count": len(json.loads(row["sources"])),
            "text": " ".join(text.split())[:240],
            "expires": row["eligible"] + LIFETIME,
        }
