"""Durable, source-specific local viewing. Never writes Telegram read state."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from ..domain.models import ChatKey, Conflict, Message, Model, Refused
from ..domain.source_revision import fingerprint

WINDOW = 7 * 86400
RETENTION = 30 * 86400
# ``outgoing`` is not an authorship proof in groups: a participant can post as
# a channel, and the connected user's own original may arrive with out=False.
# Account IDs are the verified native self-user IDs. Keep the SQL projection
# equal to ``incoming`` below, including canonical typed sender identity.
_AUTHOR = "json_extract(m.body,'$.author_peer')"
_AUTHOR_ID = f"substr({_AUTHOR},instr({_AUTHOR},':')+1)"
GROUP_INCOMING_SQL = (
    f"({_AUTHOR}<>('user:' || json_extract(m.body,'$.account_id')) "
    f"AND substr({_AUTHOR},1,instr({_AUTHOR},':')-1) IN ('user','channel') "
    f"AND {_AUTHOR_ID}=CAST(CAST({_AUTHOR_ID} AS INTEGER) AS TEXT) "
    f"AND CAST({_AUTHOR_ID} AS INTEGER)>0 "
    "AND json_extract(m.body,'$.topic_id') IS NULL)"
)
INCOMING_SQL = (
    "(json_extract(m.body,'$.service')=0 AND "
    "((json_extract(c.body,'$.kind')='private' "
    "AND json_extract(m.body,'$.outgoing')=0) OR "
    f"(json_extract(c.body,'$.kind')='group' AND {GROUP_INCOMING_SQL})))"
)
# Cached group viewing is deliberately independent of access/epoch admission.
# A lost or migrated peer still keeps the exact last observed native original;
# a withdrawn source or mismatched material revision is not such evidence.
CACHED_SOURCE_SQL = (
    "(json_extract(c.body,'$.kind')<>'group' OR EXISTS "
    "(SELECT 1 FROM group_sources g WHERE g.source=m.key "
    "AND g.chat_key=m.chat_key AND g.revision=m.revision))"
)
SOURCE_IDENTITY_SQL = (
    "(m.key=v.chat_key || '/' || v.message_id "
    "AND m.chat_key=json_extract(m.body,'$.account_id') || '/' || "
    "json_extract(m.body,'$.peer') "
    "AND m.message_id=json_extract(m.body,'$.message_id') "
    "AND m.revision=json_extract(m.body,'$.revision') "
    "AND m.date=json_extract(m.body,'$.date') "
    "AND m.deleted=json_extract(m.body,'$.deleted'))"
)
READY_SOURCE_FILTER_SQL = (
    "FROM source_views v INDEXED BY unseen_sources JOIN messages m ON m.key=v.source "
    "JOIN chats c ON c.key=v.chat_key "
    "WHERE v.chat_key=? AND m.chat_key=? AND v.candidate=1 "
    "AND v.ready IS NOT NULL AND v.viewed IS NULL AND v.message_id>? "
    "AND m.message_id=v.message_id AND m.deleted=0 AND m.date>=? "
    f"AND {SOURCE_IDENTITY_SQL} AND {INCOMING_SQL} AND {CACHED_SOURCE_SQL} "
    "ORDER BY v.message_id LIMIT ?"
)
READY_SOURCES_SQL = "SELECT m.body,v.message_id " + READY_SOURCE_FILTER_SQL
READY_SOURCE_IDS_SQL = "SELECT v.message_id " + READY_SOURCE_FILTER_SQL


# A ready, unseen correspondent reaction that can still show its dot (ADR 26):
# active, on a current cached target within its window and cache retention.
READY_REACTIONS_SQL = (
    "SELECT e.id,e.emoji,e.added,e.message_id FROM reaction_events e "
    "JOIN messages m ON m.key=e.source WHERE e.chat_key=? AND e.baseline=0 "
    "AND e.removed IS NULL AND e.ready IS NOT NULL AND e.viewed IS NULL "
    "AND m.deleted=0 AND m.date>=? AND m.date>=e.added-? "
    "ORDER BY e.message_id,e.added,e.id LIMIT 100"
)


class ViewedContent(Model):
    kind: Literal["original", "rewrite", "reaction"]
    identity: str = Field(min_length=1, max_length=128)
    token: str = Field(pattern=r"^[a-f0-9]{64}$")


class ViewedSources(Model):
    content: list[ViewedContent] = Field(min_length=1, max_length=100)


def initialize(db, now):
    db.execute("""CREATE TABLE IF NOT EXISTS viewing_baselines (
        chat_key TEXT PRIMARY KEY REFERENCES chats(key), read_through INTEGER NOT NULL,
        top INTEGER NOT NULL, cutoff REAL NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS source_views (
        source TEXT PRIMARY KEY REFERENCES messages(key) ON DELETE CASCADE,
        chat_key TEXT NOT NULL, message_id INTEGER NOT NULL, intake TEXT NOT NULL,
        candidate INTEGER, ready REAL, viewed REAL, arrived REAL NOT NULL
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS unseen_sources
        ON source_views(chat_key,candidate,viewed,ready,message_id)""")
    # Existing cache awaits real dialog evidence. Opening a store is not evidence
    # that its last mirrored read position is still current.
    db.execute(
        f"""INSERT OR IGNORE INTO source_views
        SELECT m.key,m.chat_key,m.message_id,'initial',NULL,NULL,NULL,?
        FROM messages m JOIN chats c ON c.key=m.chat_key
        WHERE m.deleted=0 AND {INCOMING_SQL}""",
        (now,),
    )
    # Adopt authorship for already cached GB1-GB8 group sources. Their original
    # native baseline stays frozen; reopening never treats native read changes
    # or previously viewed messages as a new burst.
    db.execute(
        f"DELETE FROM source_views WHERE chat_key IN "
        "(SELECT key FROM chats WHERE json_extract(body,'$.kind')='group') "
        "AND source NOT IN (SELECT m.key FROM messages m JOIN chats c "
        f"ON c.key=m.chat_key WHERE m.deleted=0 AND {INCOMING_SQL})"
    )
    db.execute(
        "UPDATE source_views SET "
        "candidate=CASE WHEN message_id>(SELECT read_through FROM viewing_baselines "
        "WHERE chat_key=source_views.chat_key) AND "
        "(SELECT date FROM messages WHERE key=source)>=(SELECT cutoff "
        "FROM viewing_baselines WHERE chat_key=source_views.chat_key) THEN 1 ELSE 0 END, "
        "viewed=CASE WHEN viewed IS NOT NULL THEN viewed WHEN message_id<="
        "(SELECT read_through FROM viewing_baselines WHERE chat_key=source_views.chat_key) "
        "OR (SELECT date FROM messages WHERE key=source)<(SELECT cutoff "
        "FROM viewing_baselines WHERE chat_key=source_views.chat_key) THEN ? "
        "ELSE NULL END WHERE candidate IS NULL AND intake='initial' AND "
        "chat_key IN (SELECT key FROM chats WHERE json_extract(body,'$.kind')='group') "
        "AND EXISTS (SELECT 1 FROM viewing_baselines WHERE chat_key=source_views.chat_key)",
        (now,),
    )


def incoming(message, *, group=False):
    if message.deleted or message.service:
        return False
    if not group:
        return not message.outgoing
    if message.topic_id is not None or not message.author_peer:
        return False
    try:
        ChatKey.typed_peer(message.author_peer)
    except ValueError:
        return False
    return (
        message.author_peer.startswith(("user:", "channel:"))
        and message.author_peer != f"user:{message.account_id}"
    )


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class LocalViewing:
    def __init__(self, store):
        self.store = store

    def baseline(self, key, read_through, top):
        """Called only by ordinary native read-position discovery, once per chat.

        The top ID and cutoff freeze the initial paging boundary across restart,
        later native reads and too-long-gap recovery. No history is requested.
        """
        self.store.chat(key)
        with self.store.transaction() as db:
            created = db.execute(
                "INSERT OR IGNORE INTO viewing_baselines VALUES (?,?,?,?)",
                (key, read_through, top, self.store.clock() - WINDOW),
            ).rowcount
            if not created:
                return
            db.execute(
                """UPDATE source_views SET
                candidate=CASE WHEN message_id>? AND
                  (SELECT date FROM messages WHERE key=source)>=? THEN 1 ELSE 0 END,
                viewed=CASE WHEN viewed IS NOT NULL THEN viewed WHEN message_id<=? OR
                  (SELECT date FROM messages WHERE key=source)<? THEN ? ELSE NULL END
                WHERE chat_key=? AND intake='initial' AND candidate IS NULL""",
                (
                    read_through,
                    self.store.clock() - WINDOW,
                    read_through,
                    self.store.clock() - WINDOW,
                    self.store.clock(),
                    key,
                ),
            )

    def record(self, db, message, intake):
        """Part of message intake's transaction; repeats never reflag a source."""
        key = f"{message.account_id}/{message.peer}"
        row = db.execute("SELECT body FROM chats WHERE key=?", (key,)).fetchone()
        group = row and json.loads(row[0])["kind"] == "group"
        if not incoming(message, group=group):
            db.execute("DELETE FROM source_views WHERE source=?", (message.key,))
            return
        now = self.store.clock()
        prior = db.execute(
            "SELECT source FROM source_views WHERE source=?", (message.key,)
        ).fetchone()
        if prior:
            db.execute(
                "UPDATE source_views SET arrived=? WHERE source=? AND ready IS NULL",
                (now, message.key),
            )
            return
        candidate = None
        if intake == "history" or message.date < now - WINDOW:
            candidate = 0
        elif intake == "arrival":
            candidate = 1
        else:
            base = db.execute(
                "SELECT * FROM viewing_baselines WHERE chat_key=?", (key,)
            ).fetchone()
            if base:
                candidate = int(
                    message.message_id > base["top"]
                    or (
                        message.message_id > base["read_through"]
                        and message.date >= base["cutoff"]
                    )
                )
        db.execute(
            "INSERT INTO source_views VALUES (?,?,?,?,?,NULL,?,?)",
            (
                message.key,
                key,
                message.message_id,
                intake,
                candidate,
                now if candidate == 0 else None,
                now,
            ),
        )

    def original(self, message):
        if message.date < self.store.clock() - RETENTION:
            return None
        if message.account_id != self.store.account_id or not incoming(
            message, group=message.peer.startswith(("chat:", "channel:"))
        ):
            return None
        return ViewedContent(
            kind="original",
            identity=str(message.message_id),
            token=digest(message.model_dump()),
        )

    def reaction(self, row):
        return ViewedContent(
            kind="reaction",
            identity=row["id"],
            token=digest([row["id"], row["emoji"], row["added"]]),
        )

    def ready_reactions(self, db, key):
        row = db.execute(
            "SELECT json_extract(body,'$.kind') FROM chats WHERE key=?", (key,)
        ).fetchone()
        if not row or row[0] != "private":
            return []
        return db.execute(
            READY_REACTIONS_SQL, (key, self.store.clock() - RETENTION, WINDOW)
        ).fetchall()

    def ready_sources(self, key, *, after=0, limit=100):
        """A bounded, read-only snapshot for focused conversation viewing.

        Readiness is already latched independently of alerts. Returning exact
        original tokens keeps later arrivals and source edits outside the
        eventual acknowledgement, including when their content is offscreen.
        """
        chat = self.store.chat(key)
        if chat.kind == "saved":
            # The account's own notes are never unseen (ADR 50).
            return {"content": [], "next_cursor": None}
        if chat.kind not in {"private", "group"}:
            raise Refused("Only private conversations and groups are supported")
        if not 0 <= after < 2**63 or not 1 <= limit <= 100:
            raise Refused("Invalid ready-source page")
        with self.store.transaction() as db:
            rows = db.execute(
                READY_SOURCES_SQL,
                (key, key, after, self.store.clock() - RETENTION, limit),
            ).fetchall()
            content = []
            for row in rows:
                message = Message.model_validate_json(row["body"])
                if message.key != f"{key}/{row['message_id']}":
                    raise Refused("Ready source identity mismatch")
                viewed = self.original(message)
                if viewed:
                    content.append(viewed.model_dump())
            if not after and chat.kind == "private":
                # Ready reactions need their own viewing: an earlier view of
                # their target never acknowledges a later reaction (ADR 26).
                content.extend(
                    self.reaction(r).model_dump() for r in self.ready_reactions(db, key)
                )
            return {
                "content": content,
                "next_cursor": rows[-1]["message_id"] if len(rows) == limit else None,
            }

    def rewrite(self, db, block):
        if (
            block["state"] != "ready"
            or not block["rendered"]
            or block["rendered_revision"] != block["revision"]
        ):
            return None
        ids = json.loads(block["sources"])
        if not ids:
            return None
        sources = db.execute(
            "SELECT body FROM messages WHERE chat_key=? AND deleted=0 "
            "AND date>=? AND message_id IN (SELECT value FROM json_each(?)) "
            "AND json_extract(body,'$.outgoing')=0 ORDER BY message_id",
            (block["chat_key"], self.store.clock() - RETENTION, block["sources"]),
        ).fetchall()
        if len(sources) != len(ids):
            return None
        messages = [Message.model_validate_json(r[0]) for r in sources]
        if fingerprint(messages) != block["revision"]:
            return None
        return ViewedContent(
            kind="rewrite",
            identity=block["id"],
            token=digest([block["revision"], block["rendered"]]),
        )

    def acknowledge(self, key, body: ViewedSources):
        chat = self.store.chat(key)
        if chat.kind == "saved":
            return {"viewed": 0}
        if chat.kind not in {"private", "group"}:
            raise Refused("Only private conversations and groups are supported")
        with self.store.transaction() as db:
            sources = set()
            reactions = set()
            for content in body.content:
                if chat.kind == "group" and content.kind != "original":
                    raise Refused("Group viewing requires ready native originals")
                expected = None
                if content.kind == "reaction":
                    row = db.execute(
                        "SELECT id,emoji,added FROM reaction_events "
                        "WHERE chat_key=? AND id=? AND removed IS NULL",
                        (key, content.identity),
                    ).fetchone()
                    if row:
                        expected = self.reaction(row)
                        reactions.add(row["id"])
                elif content.kind == "original":
                    if not content.identity.isdigit() or len(content.identity) > 19:
                        raise Refused("Invalid original identity")
                    if not 0 < int(content.identity) < 2**63:
                        raise Refused("Invalid original identity")
                    row = db.execute(
                        "SELECT body FROM messages WHERE chat_key=? AND message_id=?",
                        (key, int(content.identity)),
                    ).fetchone()
                    if row:
                        message = Message.model_validate_json(row[0])
                        if message.key != f"{key}/{content.identity}":
                            raise Refused("Viewed source identity mismatch")
                        current = (
                            chat.kind != "group"
                            or db.execute(
                                "SELECT 1 FROM source_views v JOIN messages m ON m.key=v.source "
                                "JOIN chats c ON c.key=v.chat_key WHERE v.source=? "
                                "AND v.chat_key=? AND m.chat_key=v.chat_key "
                                "AND m.message_id=v.message_id AND v.candidate=1 "
                                "AND v.ready IS NOT NULL AND m.deleted=0 AND m.date>=? "
                                f"AND {SOURCE_IDENTITY_SQL} AND {CACHED_SOURCE_SQL}",
                                (message.key, key, self.store.clock() - RETENTION),
                            ).fetchone()
                        )
                        expected = self.original(message) if current else None
                        sources.add(message.message_id)
                else:
                    row = db.execute(
                        "SELECT * FROM blocks WHERE chat_key=? AND id=?",
                        (key, content.identity),
                    ).fetchone()
                    if row:
                        expected = self.rewrite(db, row)
                        sources.update(json.loads(row["sources"]))
                if expected != content:
                    raise Conflict(
                        "Displayed content changed; observe its current version"
                    )
            changed = db.execute(
                "UPDATE source_views SET viewed=? WHERE chat_key=? AND viewed IS NULL "
                "AND message_id IN (SELECT value FROM json_each(?))",
                (self.store.clock(), key, json.dumps(sorted(sources))),
            ).rowcount
            changed += db.execute(
                "UPDATE reaction_events SET viewed=? WHERE chat_key=? AND viewed IS NULL "
                "AND id IN (SELECT value FROM json_each(?))",
                (self.store.clock(), key, json.dumps(sorted(reactions))),
            ).rowcount
        return {"viewed": changed}
