"""Reading-only observed versions. Never queried by context or chat memory."""

import json
from dataclasses import dataclass

from ..domain.deletion import deletion_fingerprint
from ..domain.models import Message, Refused, provisional, same_content

# Schema 34's repair: Telegram's copy of a sent message follows its receipt
# within the send deadlines.
SETTLE_WINDOW = 300
# Telegram dates in whole seconds, so its date can fall up to a second, plus
# any clock difference, before the moment Send was pressed (schema 36).
SETTLE_SKEW = 5

DDL = """CREATE TABLE IF NOT EXISTS message_versions (
    id INTEGER PRIMARY KEY, source TEXT NOT NULL, date REAL NOT NULL,
    body TEXT NOT NULL, transcript TEXT, observed REAL NOT NULL, reason TEXT NOT NULL
)"""

# Deleted messages the owner hid (ADR 52 Q11). Hiding is final, and a row
# expires with its message.
HIDDEN_DDL = """CREATE TABLE IF NOT EXISTS hidden_messages (
    key TEXT PRIMARY KEY, chat_key TEXT NOT NULL, date REAL NOT NULL
)"""

# Deletions this service asked Telegram for (ADR 52 Q8): a dispatched chunk
# may have reached Telegram before its acknowledgement was saved.
OWN_DELETIONS = (
    "SELECT DISTINCT i.value AS identity FROM deletion_operations d,"
    "json_each(d.body,'$.chunks') c,json_each(c.value,'$.identities') i "
    "WHERE d.chat_key=? AND json_extract(c.value,'$.state') "
    "IN ('dispatching','acknowledged','uncertain')"
)


@dataclass(frozen=True)
class Capture:
    """A deleted message's last observed content: the version saved when its
    deletion was observed."""

    id: int
    message: Message
    transcript: dict | None


def compact(db):
    """Upgrade only proven adjacent duplicates, retaining unknown media."""
    for source in db.execute("SELECT DISTINCT source FROM message_versions").fetchall():
        row = db.execute(
            "SELECT body FROM messages WHERE key=?", (source[0],)
        ).fetchone()
        if not row:
            continue
        current = Message.model_validate_json(row[0])
        versions = db.execute(
            "SELECT * FROM message_versions WHERE source=? ORDER BY id DESC",
            (source[0],),
        ).fetchall()
        if any(v["reason"] == "edited" for v in versions):
            current = current.model_copy(update={"native_edited": True})
            db.execute(
                "UPDATE messages SET body=? WHERE key=?",
                (current.model_dump_json(), source[0]),
            )
        newer = current
        for version in versions:
            prior = Message.model_validate_json(version["body"])
            if same_content(newer, prior):
                db.execute("DELETE FROM message_versions WHERE id=?", (version["id"],))
            else:
                newer = prior


def settle_sent(db):
    """Schema 34 (ADR 19): Telegram's copy of a message
    this service sent was recorded as an edit of the record written from its
    receipt. Remove those versions and the edited flag nothing else shows."""
    sources = set()
    for row in db.execute(
        "SELECT id,source,body FROM message_versions WHERE reason='edited'"
    ).fetchall():
        if provisional(Message.model_validate_json(row["body"])):
            db.execute("DELETE FROM message_versions WHERE id=?", (row["id"],))
            sources.add(row["source"])
    settled = {
        source
        for source in sources
        if not db.execute(
            "SELECT 1 FROM message_versions WHERE source=? AND reason='edited'",
            (source,),
        ).fetchone()
    }

    def unflagged(body, source):
        value = Message.model_validate_json(body)
        native = (
            value.native_revision
            if value.native_revision is not None
            else value.revision
        )
        # The record kept the receipt's date. Telegram dated its copy within
        # minutes of it; a later native date is an edit seen since.
        prompt = native / 1_000_000 - value.date < SETTLE_WINDOW
        # Unchanged sent text keeps the receipt's revision 0 once Telegram's
        # copy supplies its native revision.
        if (
            value.native_edited
            and prompt
            and (
                source in settled
                or (
                    value.outgoing
                    and value.revision == 0
                    and value.native_revision is not None
                )
            )
        ):
            return value.model_copy(update={"native_edited": False})
        return None

    flagged = "json_extract(body,'$.native_edited')=1"
    for row in db.execute(f"SELECT key,body FROM messages WHERE {flagged}").fetchall():
        value = unflagged(row["body"], row["key"])
        if value:
            db.execute(
                "UPDATE messages SET body=? WHERE key=?",
                (value.model_dump_json(), row["key"]),
            )
    for row in db.execute(
        f"SELECT id,source,body FROM message_versions WHERE {flagged}"
    ).fetchall():
        value = unflagged(row["body"], row["source"])
        if value:
            db.execute(
                "UPDATE message_versions SET body=? WHERE id=?",
                (value.model_dump_json(), row["id"]),
            )


def native_of(value):
    return (
        value.native_revision if value.native_revision is not None else value.revision
    )


def settle_saved(db, account_id):
    """Schema 35 (ADR 52 Q18): Telegram's update stream reported the owner's
    own notes in Saved Messages as incoming while a fresh read says outgoing,
    and each flip was kept as an edit. Their notes become outgoing again, and
    a version whose content its successor repeats, outgoing flag aside, goes
    with the edited flag nothing else shows."""
    saved, own = f"{account_id}/user:{account_id}", f"user:{account_id}"
    for table, identity in (("messages", "key"), ("message_versions", "id")):
        source = "key" if table == "messages" else "source"
        for row in db.execute(
            f"SELECT {identity},body FROM {table} WHERE {source} LIKE ?",
            (saved + "/%",),
        ).fetchall():
            value = Message.model_validate_json(row["body"])
            if value.author_peer == own and not value.outgoing:
                db.execute(
                    f"UPDATE {table} SET body=? WHERE {identity}=?",
                    (
                        value.model_copy(update={"outgoing": True}).model_dump_json(),
                        row[identity],
                    ),
                )
    for (source,) in db.execute(
        "SELECT DISTINCT source FROM message_versions WHERE reason='edited'"
    ).fetchall():
        row = db.execute("SELECT body FROM messages WHERE key=?", (source,)).fetchone()
        if not row:
            continue
        current = Message.model_validate_json(row[0])
        versions = db.execute(
            "SELECT id,reason,body FROM message_versions WHERE source=? ORDER BY id",
            (source,),
        ).fetchall()
        chain = [Message.model_validate_json(v["body"]) for v in versions] + [current]
        removed = []
        for version, prior, later in zip(versions, chain, chain[1:], strict=False):
            aligned = later.model_copy(update={"outgoing": prior.outgoing})
            if version["reason"] == "edited" and same_content(prior, aligned):
                db.execute("DELETE FROM message_versions WHERE id=?", (version["id"],))
                removed.append(native_of(prior))
        if (
            removed
            and current.native_edited
            and native_of(current) <= max(removed)
            and not db.execute(
                "SELECT 1 FROM message_versions WHERE source=? AND reason='edited'",
                (source,),
            ).fetchone()
        ):
            db.execute(
                "UPDATE messages SET body=? WHERE key=?",
                (
                    current.model_copy(
                        update={"native_edited": False}
                    ).model_dump_json(),
                    source,
                ),
            )


def redate_sent(db):
    """Schemas 35-36 (ADR 52 Q17): a message sent before schema 34 kept the
    moment Send was pressed. While it is unedited, its native revision is
    Telegram's own date, which every record of it takes. Schema 36 also takes
    a Telegram date just before that moment."""
    for (body,) in db.execute(
        "SELECT body FROM operations WHERE state='confirmed'"
    ).fetchall():
        operation = json.loads(body)
        created = operation.get("created")
        for identity in (
            operation.get("message_id"),
            *(operation.get("receipts") or {}).values(),
        ):
            if not identity:
                continue
            key = f"{operation['chat_key']}/{identity}"
            row = db.execute("SELECT body FROM messages WHERE key=?", (key,)).fetchone()
            if not row:
                continue
            value = Message.model_validate_json(row[0])
            native = native_of(value)
            if (
                value.date != created
                or value.native_edited
                or not native
                or not -SETTLE_SKEW <= native / 1_000_000 - created < SETTLE_WINDOW
                or db.execute(
                    "SELECT 1 FROM message_versions WHERE source=? AND reason='edited'",
                    (key,),
                ).fetchone()
            ):
                continue
            date = native / 1_000_000
            db.execute(
                "UPDATE messages SET date=?,body=? WHERE key=?",
                (date, value.model_copy(update={"date": date}).model_dump_json(), key),
            )
            for version in db.execute(
                "SELECT id,body FROM message_versions WHERE source=?", (key,)
            ).fetchall():
                prior = Message.model_validate_json(version["body"])
                db.execute(
                    "UPDATE message_versions SET date=?,body=? WHERE id=?",
                    (
                        date,
                        prior.model_copy(update={"date": date}).model_dump_json(),
                        version["id"],
                    ),
                )
            db.execute("UPDATE message_albums SET date=? WHERE key=?", (date, key))


def capture(db, prior, current, now):
    row = db.execute(
        "SELECT body FROM derivatives WHERE source=? AND revision=?",
        (prior.key, prior.revision),
    ).fetchone()
    transcript = json.loads(row[0]).get("transcript") if row else None
    db.execute(
        "INSERT INTO message_versions(source,date,body,transcript,observed,reason) VALUES (?,?,?,?,?,?)",
        (
            prior.key,
            prior.date,
            prior.model_dump_json(),
            json.dumps(transcript) if transcript else None,
            now,
            "deleted" if current.deleted else "edited",
        ),
    )


class ObservedHistory:
    def __init__(self, store):
        self.store = store

    def rows(self, key, identity, *, before=0):
        self.store.chat(key)
        return self.store.rows(
            "SELECT * FROM message_versions WHERE source=? AND date>=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT 50",
            (f"{key}/{identity}", self.store.clock() - 30 * 86400, before, before),
        )

    def version(self, key, identity, version):
        self.store.chat(key)
        rows = self.store.rows(
            "SELECT * FROM message_versions WHERE source=? AND id=? AND date>=?",
            (f"{key}/{identity}", version, self.store.clock() - 30 * 86400),
        )
        if not rows:
            raise Refused("Observed version unavailable or expired")
        return Message.model_validate_json(rows[0]["body"])

    def deleted_capture(self, key, identity):
        """The content a deleted message last showed (ADR 52 Q7), while kept;
        None for a current message or one deleted before any was seen."""
        rows = self.store.rows(
            "SELECT v.id,v.body,v.transcript FROM message_versions v "
            "JOIN messages m ON m.key=v.source AND m.deleted=1 "
            "WHERE v.source=? AND v.reason='deleted' AND v.date>=? "
            "ORDER BY v.id DESC LIMIT 1",
            (f"{key}/{identity}", self.store.clock() - 30 * 86400),
        )
        if not rows:
            return None
        return Capture(
            rows[0]["id"],
            Message.model_validate_json(rows[0]["body"]),
            json.loads(rows[0]["transcript"]) if rows[0]["transcript"] else None,
        )

    def shown_version(self, key, identity, version=None):
        """The version a media or transcript read of a deleted message uses:
        its capture, read from the cache only like any earlier version."""
        if version is not None:
            return version
        capture = self.deleted_capture(key, identity)
        return capture.id if capture else None

    def shown(self, messages, *, quotes=False):
        """What a conversation shows (ADR 52): current messages, and deleted
        ones with their captured content. A deleted message is left out when
        it was deleted through this service, hidden, in Saved Messages or
        deleted before its content was seen. A Reply Quote still shows any
        captured content, since it is the reply's context. A deleted service
        notice is never shown."""
        messages = [
            m if isinstance(m, Message) else Message.model_validate(m) for m in messages
        ]
        messages = [
            self.store.message(f"{m.account_id}/{m.peer}", m.message_id) or m
            for m in messages
        ]
        result, chats = [], {}
        for m in messages:
            if not m.deleted:
                result.append((m, None))
                continue
            key = f"{m.account_id}/{m.peer}"
            if key not in chats:
                chats[key] = (
                    self.store.chat(key).kind,
                    {
                        r["key"]
                        for r in self.store.rows(
                            "SELECT key FROM hidden_messages WHERE chat_key=?", (key,)
                        )
                    },
                    {r["identity"] for r in self.store.rows(OWN_DELETIONS, (key,))},
                )
            kind, hidden, own = chats[key]
            # A deleted service notice, such as a join, simply leaves.
            if m.service or (
                not quotes
                and (kind == "saved" or m.key in hidden or m.message_id in own)
            ):
                continue
            capture = self.deleted_capture(key, m.message_id)
            if capture:
                result.append((m, capture))
        return result

    def annotate(self, messages, *, quotes=False):
        return self.views(self.shown(messages, quotes=quotes))

    def views(self, shown):
        return [self._view(m, capture) for m, capture in shown]

    def _view(self, m, capture):
        group = m.peer.startswith(("chat:", "channel:"))
        count = self.store.rows(
            "SELECT count(*) AS n FROM message_versions WHERE source=? AND date>=?",
            (m.key, self.store.clock() - 30 * 86400),
        )[0]["n"]
        common = dict(
            **({"deletion_fingerprint": deletion_fingerprint(m)} if group else {}),
            # A group author's photo for their avatar (ADR 51 Q13).
            **(
                {"author_photo_id": self.store.author_photo(m.author_peer)}
                if m.author_peer and group
                else {}
            ),
        )
        if capture is not None:
            content = capture.message
            return dict(
                content.model_dump(),
                **common,
                deleted=True,
                # ADR 52 Q7: the content captured at deletion, its media read
                # from the cache only, like any earlier version.
                deleted_version=capture.id,
                media_available=bool(
                    content.media
                    and self.store.rows(
                        "SELECT 1 FROM media_files WHERE key=? AND revision=?",
                        # MediaCache.entry's key for the file itself.
                        (f"{content.key}@{content.revision}#file", content.revision),
                    )
                ),
                viewed_content=None,
                history_count=max(count - 1, 0),
                reactions=None,
                album=None,
            )
        viewed = self.store.viewing.original(m)
        return dict(
            m.model_dump(),
            **common,
            viewed_content=viewed.model_dump() if viewed else None,
            history_count=count,
            reactions=self.store.reactions.view(m.key),
            # Telegram's album, drawn as one grid (ADR 51 Q12).
            album=self.store.album(m.key),
        )

    def hide(self, key, identities):
        """Hide deleted messages for good (ADR 52 Q11-Q12); a current message
        can't be hidden."""
        if self.store.chat(key).kind not in {"private", "group", "saved"}:
            raise Refused(
                "Only existing private conversations and groups are supported"
            )
        with self.store.transaction() as db:
            hidden = []
            for identity in dict.fromkeys(identities):
                row = db.execute(
                    "SELECT date,deleted FROM messages WHERE chat_key=? AND message_id=?",
                    (key, identity),
                ).fetchone()
                if not row:
                    raise Refused("That message is no longer kept")
                if not row["deleted"]:
                    raise Refused("Only deleted messages can be hidden")
                db.execute(
                    "INSERT OR IGNORE INTO hidden_messages VALUES (?,?,?)",
                    (f"{key}/{identity}", key, row["date"]),
                )
                hidden.append(identity)
        return hidden
