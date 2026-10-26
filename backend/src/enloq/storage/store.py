"""Fresh account content. Shared spending and authorization live outside this store."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from pathlib import Path

from ..config import private_directory
from ..domain.models import (
    REPLY_DETAILS,
    Chat,
    Conflict,
    Message,
    Refused,
    media_class,
    provisional,
    same_content,
)
from . import (
    emotion,
    history,
    memory,
    reactions,
    reminders,
    streams,
    suggestions,
    traits,
    viewing,
)
from .catalogs import DDL as CATALOG_DDL
from .deletion_operations import DDL as DELETION_DDL
from .deletion_operations import DeletionOperations
from .events import commits
from .group_briefings import DDL as GROUP_BRIEFING_DDL
from .group_briefings import GroupBriefings
from .group_filtering import DDL as GROUP_FILTER_DDL
from .group_filtering import GroupFilters
from .group_state import DDL as GROUP_DDL
from .group_state import GroupStates
from .incoming_deferred import DDL as INCOMING_DEFERRED_DDL
from .selected_media import DDL as SELECTED_DDL
from .selected_media import SelectedMedia
from .send_operations import SendOperations

WINDOW = 7 * 86400
RETENTION = 30 * 86400
# Each account's own Saved Messages, shown in the list's third tab (ADR 50).
SAVED_TITLE = "Saved Messages"
METADATA = "metadata"
SCHEMA_VERSION = 40


def forward_merge(prior: Message, message: Message):
    """Forward header details a newer read may fill in but never erase. A
    header read supplies its signature and imported flag with its date."""
    if message.forward_date is None:
        return {
            "forward_date": prior.forward_date,
            "forward_signature": prior.forward_signature,
            "imported": prior.imported,
            "forward_read": prior.forward_read or message.forward_read,
        }
    return {
        "forward_date": message.forward_date,
        "forward_signature": message.forward_signature,
        "imported": message.imported,
        "forward_read": prior.forward_read or message.forward_read,
    }


def reply_merge(prior: Message, message: Message):
    """Reply header details (ADR 41): a capture with them is authoritative; one
    without them never erases what an earlier capture or read supplied."""
    if message.reply_read or not prior.reply_read or message.reply_to != prior.reply_to:
        return {}
    return {field: getattr(prior, field) for field in REPLY_DETAILS}


def blank_draft(key):
    return dict(
        chat_key=key,
        revision=0,
        text="",
        attachments=[],
        reply_to=None,
        undo=None,
        state="idle",
        selected_media=None,
    )


def change_triggers(table, column, events=("INSERT", "UPDATE", "DELETE")):
    """Triggers recording the latest change sequence per scope, so every write
    path, including bulk expiry and recovery, notifies browsers."""
    for event in events:
        row = "OLD" if event == "DELETE" else "NEW"
        scope = column if column.startswith("'") else f"{row}.{column}"
        yield (
            f"CREATE TRIGGER IF NOT EXISTS {table}_{event.lower()}_changes AFTER {event} ON {table} "
            f"BEGIN INSERT INTO changes VALUES ({scope},"
            " (SELECT coalesce(max(seq),0)+1 FROM changes))"
            " ON CONFLICT(scope) DO UPDATE SET seq=excluded.seq; END"
        )


# Statements whose every effect ``total_changes`` counts, triggers included.
_COUNTED = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE", "WITH"})


class _Connection(sqlite3.Connection):
    """Knows whether its transaction wrote anything, and holds that
    transaction's reusable reads."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Schema changes, pragmas and savepoints are invisible to
        # ``total_changes``; a transaction that ran one always commits.
        self.uncounted = False
        # Rows read under this transaction's write lock cannot change beneath
        # it, so they are reusable until this connection's next write.
        self.scratch = {}
        # A writer sets ``muted`` around a write no derived answer reads. The
        # transaction stays ``quiet`` while those are its only writes.
        self.muted = False
        self.quiet = True

    def _note(self, sql):
        verb = sql.lstrip()[:7].split(None, 1)
        verb = verb[0].upper() if verb else ""
        if verb not in _COUNTED:
            self.uncounted = True
        if verb != "SELECT":
            self.scratch.clear()
            if not self.muted:
                self.quiet = False

    def execute(self, sql, *args):
        self._note(sql)
        return super().execute(sql, *args)

    def executemany(self, sql, *args):
        self._note(sql)
        return super().executemany(sql, *args)

    def executescript(self, *args):
        self.uncounted = True
        self.scratch.clear()
        return super().executescript(*args)

    def cursor(self, *args, **kwargs):
        # A caller's own cursor executes without passing through here.
        self.uncounted = True
        self.scratch.clear()
        return super().cursor(*args, **kwargs)


class _Held:
    """A thread's connection, closed when that thread ends or its pool closes."""

    def __init__(self, db, generation):
        self.db, self.generation = db, generation
        weakref.finalize(self, db.close)


class Connections:
    """One connection per thread, reused across transactions. Opening one parses
    the whole schema, triggers included, and attaches the registry; during
    catch-up that cost, paid per transaction, starved the event loop."""

    def __init__(self, path, setup=None):
        self.path, self.setup = path, setup
        self.local = threading.local()
        self.held = weakref.WeakSet()
        self.generation = 0

    def _connect(self):
        db = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
            factory=_Connection,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        if self.setup:
            self.setup(db)
        return db

    @contextmanager
    def transaction(self):
        held = getattr(self.local, "held", None)
        spare = False
        if held is None or held.generation != self.generation:
            held = self.local.held = _Held(self._connect(), self.generation)
            self.held.add(held)
            db = held.db
        elif held.db.in_transaction:
            # Re-entered on this thread: a separate connection, as before reuse.
            db, spare = self._connect(), True
        else:
            db = held.db
        try:
            db.execute("BEGIN IMMEDIATE")
            before, db.uncounted = db.total_changes, False
            db.muted, db.quiet = False, True
            db.scratch.clear()
            yield db
            if db.uncounted or db.total_changes != before:
                db.commit()
            else:
                # Nothing was written. BEGIN IMMEDIATE write-locks the attached
                # registry too, so COMMIT would still create, sync and delete a
                # super-journal file: several hundred a second while idle.
                db.rollback()
            if db.total_changes != before:
                commits.publish(quiet=db.quiet)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.scratch.clear()
            if spare:
                db.close()

    def close(self):
        """Close every thread's connection; later transactions reconnect. Only
        for a store no other thread is using, such as one about to be removed."""
        self.generation += 1
        for held in tuple(self.held):
            held.db.close()


MEMORY = [
    # ADR 15: memory is kept until cleared, outside 30-day content expiry. ADR
    # 55: a chat's Person Memory under its chat key, and the Account Memory
    # under the subject "account".
    "CREATE TABLE IF NOT EXISTS memory_entries (id TEXT PRIMARY KEY, chat_key TEXT NOT NULL, text TEXT NOT NULL, citations TEXT NOT NULL, source TEXT NOT NULL, outdated TEXT, created REAL NOT NULL, guidance INTEGER)",
    "CREATE TABLE IF NOT EXISTS memory_state (chat_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, coverage INTEGER NOT NULL, error TEXT)",
    # Messages admitted as memory sources: sent to a model, or started from
    # the cache (ADR 55 Q20).
    "CREATE TABLE IF NOT EXISTS analyzed (key TEXT PRIMARY KEY, chat_key TEXT NOT NULL, message_id INTEGER NOT NULL, date REAL NOT NULL)",
]

CHANGES = [
    "CREATE TABLE IF NOT EXISTS changes (scope TEXT PRIMARY KEY, seq INTEGER NOT NULL)",
    *MEMORY,
    *change_triggers("memory_entries", "chat_key"),
    *change_triggers("memory_state", "chat_key"),
    *change_triggers("messages", "chat_key"),
    *change_triggers("blocks", "chat_key"),
    *change_triggers("drafts", "chat_key"),
    *change_triggers("operations", "chat_key"),
    *change_triggers("chats", "key", ("INSERT", "UPDATE")),
]


class Store:
    # ADR 54: None follows the registry's text_rewriting; a finite check's own
    # store may set it, so its text cases are rewritten without changing the
    # owner's setting.
    rewrites_text = None

    def __init__(
        self,
        path: Path,
        *,
        account_id: int,
        mode="offline",
        clock=time.time,
        timezone="Asia/Ho_Chi_Minh",
        shared_path: Path | None = None,
    ):
        self.path, self.account_id, self.clock = path, account_id, clock
        self.timezone, self.shared_path = timezone, shared_path
        self.send_operations = SendOperations(self)
        self.selected_media = SelectedMedia(self)
        self.deletion_operations = DeletionOperations(self)
        self.history = history.ObservedHistory(self)
        self.streams = streams.RewriteStreams(self)
        self.viewing = viewing.LocalViewing(self)
        self.reactions = reactions.Reactions(self)
        self.groups = GroupStates(self)
        self.group_briefings = GroupBriefings(self)
        self.group_filters = GroupFilters(self)
        self.connections = Connections(path, self._attach)
        private_directory(path.parent)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (
                0,
                10,
                11,
                12,
                13,
                14,
                15,
                16,
                17,
                18,
                19,
                20,
                21,
                22,
                23,
                24,
                25,
                26,
                27,
                28,
                29,
                30,
                31,
                32,
                33,
                34,
                35,
                36,
                37,
                38,
                39,
                40,
            ):
                raise Refused(
                    "Use the fresh rewriting store; legacy content is retired"
                )
            if not version:
                for statement in (
                    Path(__file__).with_name("schema.sql").read_text().split(";")
                ):
                    if statement.strip():
                        db.execute(statement)
            if version == 10:
                db.execute(
                    "CREATE TABLE avatars (peer TEXT PRIMARY KEY, photo_id TEXT NOT NULL, mime TEXT, expires REAL NOT NULL)"
                )
            if version in (10, 11):
                columns = {r["name"] for r in db.execute("PRAGMA table_info(blocks)")}
                if "unanalysed" not in columns:
                    db.execute("ALTER TABLE blocks ADD COLUMN unanalysed TEXT")
                db.execute("PRAGMA user_version=12")
            if version in (10, 11, 12):
                db.execute(
                    "CREATE TABLE IF NOT EXISTS media_files (key TEXT PRIMARY KEY, revision INTEGER NOT NULL, variant TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT, date REAL NOT NULL, used REAL NOT NULL)"
                )
                db.execute("PRAGMA user_version=13")
            for statement in CHANGES:
                db.execute(statement)
            if version in (10, 11, 12, 13):
                columns = {r["name"] for r in db.execute("PRAGMA table_info(blocks)")}
                if "stage" not in columns:
                    db.execute("ALTER TABLE blocks ADD COLUMN stage TEXT")
                db.execute("PRAGMA user_version=14")
            if version in (10, 11, 12, 13, 14):
                db.execute("PRAGMA user_version=15")
            if version in (10, 11, 12, 13, 14, 15):
                # Each result records the Processing Guidance version behind it.
                for table in ("blocks", "memory_entries"):
                    columns = {
                        r["name"] for r in db.execute(f"PRAGMA table_info({table})")
                    }
                    if "guidance" not in columns:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN guidance INTEGER")
                db.execute("PRAGMA user_version=16")
            db.execute(history.DDL)
            db.execute(
                "CREATE INDEX IF NOT EXISTS versions_source ON message_versions(source,id)"
            )
            db.execute("PRAGMA user_version=17")
            db.execute(streams.DDL)
            for trigger in change_triggers("rewrite_streams", "chat_key"):
                db.execute(trigger)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(blocks)")}
            if "rendered_revision" not in columns:
                db.execute("ALTER TABLE blocks ADD COLUMN rendered_revision TEXT")
                db.execute(
                    "UPDATE blocks SET rendered_revision=revision WHERE state='ready'"
                )
            if "source_revisions" not in columns:
                db.execute("ALTER TABLE blocks ADD COLUMN source_revisions TEXT")
            if version < 19:
                history.compact(db)
            if version and version < 34:
                history.settle_sent(db)
            memory.initialize(db)
            for trigger in change_triggers("remembering", "chat_key"):
                db.execute(trigger)
            # ADR 55: traits of each chat's person and of the account's holder.
            for statement in traits.DDL:
                db.execute(statement)
            for table in traits.TABLES:
                for trigger in change_triggers(table, "subject"):
                    db.execute(trigger)
            db.execute(emotion.DDL)
            for trigger in change_triggers("emotion_state", "chat_key"):
                db.execute(trigger)
            viewing.initialize(db, self.clock())
            db.execute(
                "CREATE INDEX IF NOT EXISTS blocks_chat ON blocks(chat_key,date,id)"
            )
            for trigger in change_triggers("source_views", "chat_key"):
                db.execute(trigger)
            # ADR 25: one current suggestion outcome per chat, no backfill.
            db.execute(suggestions.DDL)
            for trigger in change_triggers("suggestion_state", "chat_key"):
                db.execute(trigger)
            # ADR 56: each private chat's Suggestions choice.
            suggestions.migrate_settings(db)
            for trigger in change_triggers("suggestion_settings", "chat_key"):
                db.execute(trigger)
            # ADR 58: several schedules per chat since schema 40.
            reminders.initialize(db, self.clock())
            for table in ("reminder_schedules", "reminder_occurrences"):
                for trigger in change_triggers(table, "chat_key"):
                    db.execute(trigger)
            for statement in reminders.KEPT_TRIGGERS:
                db.execute(statement)
            # ADR 26: reaction metadata and dated correspondent evidence.
            reactions.initialize(db, self.clock())
            # ADR 29: results also record the shipped-defaults revision; earlier
            # results keep no value, which means revision 1.
            for table in ("blocks", "memory_entries"):
                columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if "defaults" not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN defaults INTEGER")
            db.execute(DELETION_DDL)
            for trigger in change_triggers("deletion_operations", "chat_key"):
                db.execute(trigger)
            db.execute(CATALOG_DDL)
            db.execute(SELECTED_DDL)
            db.execute(INCOMING_DEFERRED_DDL)
            db.execute(
                "CREATE TABLE IF NOT EXISTS budget_changes (at REAL NOT NULL,old INTEGER NOT NULL,new INTEGER NOT NULL)"
            )
            # ADR 34: old amounts, receipts and changes retain their original
            # daily family. Group purpose accounting is an additive extension.
            for table in ("reservations", "receipts", "budget_changes"):
                columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if "family" not in columns:
                    db.execute(
                        f"ALTER TABLE {table} ADD COLUMN family TEXT NOT NULL DEFAULT 'daily'"
                    )
            for statement in GROUP_DDL:
                db.execute(statement)
            # Group authors' current profile photos, for their avatars beside
            # their messages (ADR 51 Q13): seen at intake, never content.
            db.execute(
                "CREATE TABLE IF NOT EXISTS group_authors (peer TEXT PRIMARY KEY, photo_id TEXT, seen REAL NOT NULL)"
            )
            # Telegram albums (`grouped_id`), drawn as one grid (ADR 51 Q12).
            db.execute(
                "CREATE TABLE IF NOT EXISTS message_albums (key TEXT PRIMARY KEY, album TEXT NOT NULL, date REAL NOT NULL)"
            )
            for table in ("group_state", "group_intake", "group_sources"):
                for trigger in change_triggers(table, "chat_key"):
                    db.execute(trigger)
            for statement in GROUP_BRIEFING_DDL:
                db.execute(statement)
            for statement in GROUP_FILTER_DDL:
                db.execute(statement)
            for table in ("group_filter_decisions", "group_bot_decisions"):
                for trigger in change_triggers(table, "chat_key"):
                    db.execute(trigger)
            for table in ("days", "runs", "batches", "attempts", "chunks", "results"):
                for trigger in change_triggers("group_briefing_" + table, "chat_key"):
                    db.execute(trigger)
            for trigger in change_triggers("selected_media", "chat_key"):
                db.execute(trigger)
            # ADR 52: hidden deleted messages, and repairs of Saved Messages'
            # outgoing flips (Q18) and of sent messages' dates (Q17).
            db.execute(history.HIDDEN_DDL)
            for trigger in change_triggers("hidden_messages", "chat_key"):
                db.execute(trigger)
            # ADR 53: a private chat's own calming choice, kept once made
            # (without one the default applies), and the Calmed mark of a
            # block's published rewrite.
            db.execute(
                "CREATE TABLE IF NOT EXISTS calming (chat_key TEXT PRIMARY KEY, "
                "enabled INTEGER NOT NULL, changed REAL NOT NULL)"
            )
            for trigger in change_triggers("calming", "chat_key"):
                db.execute(trigger)
            # Mood-based releases from the previous design are not hostility
            # decisions. Reconsider undispatched work under the new contract;
            # completed renderings and all spending evidence stay intact.
            db.execute(
                "UPDATE incoming_deferred SET payload=json_remove(payload,'$.decided') WHERE scope IN "
                "(SELECT scope FROM jobs WHERE role='incoming_rewrite' AND state IN ('queued','claimed') "
                "AND json_type(payload,'$.calm')='text')"
            )
            db.execute(
                "UPDATE jobs SET payload=json_remove(payload,'$.calm','$.sarcastic','$.decided') "
                "WHERE role='incoming_rewrite' AND state IN ('queued','claimed') "
                "AND json_type(payload,'$.calm')='text'"
            )
            columns = {r["name"] for r in db.execute("PRAGMA table_info(blocks)")}
            if "calmed" not in columns:
                db.execute(
                    "ALTER TABLE blocks ADD COLUMN calmed INTEGER NOT NULL DEFAULT 0"
                )
            if version and version < 35:
                history.settle_saved(db, account_id)
            if version and version < 36:
                history.redate_sent(db)
            # ADR 55 Q20: today's takeaways leave, and every memory starts
            # once from the cache as it stands now.
            if version and version < 37:
                memory.clear_takeaways(db)
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('memory_start',?)",
                (str(self.clock()),),
            )
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            for key, value in {
                "account_id": str(account_id),
                "mode": mode,
                "timezone": timezone,
            }.items():
                current = db.execute(
                    "SELECT value FROM metadata WHERE key=?", (key,)
                ).fetchone()
                if current and current[0] != value:
                    raise Refused("Storage identity or accounting timezone mismatch")
                db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
            # Before ADR 14 native discovery wrote an unmarked blanket false.
            # Adopt the new default only for those records; retain explicit policy.
            for row in db.execute("SELECT key,body FROM chats").fetchall():
                body = json.loads(row["body"])
                if "eligibility_source" not in body:
                    body.update(
                        eligibility_source="default",
                        ai_eligible=body["kind"] == "private",
                    )
                    db.execute(
                        "UPDATE chats SET body=? WHERE key=?",
                        (json.dumps(body), row["key"]),
                    )
                if body["kind"] == "group":
                    self.groups.ensure(db, row["key"])

    def _attach(self, db):
        db.execute("PRAGMA foreign_keys=ON")
        if self.shared_path:
            db.execute("ATTACH DATABASE ? AS shared", (str(self.shared_path),))

    def transaction(self):
        return self.connections.transaction()

    def close(self):
        self.connections.close()

    def rows(self, sql, args=()):
        with self.transaction() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def changes(self, after=0):
        """Chat keys changed after a sequence, and the latest sequence."""
        # Read scopes and cursor together so a concurrent write cannot advance
        # the cursor past a scope the reader has not seen.
        with self.transaction() as db:
            rows = db.execute(
                "SELECT scope,seq FROM changes WHERE seq>?", (after,)
            ).fetchall()
            latest = db.execute("SELECT coalesce(max(seq),0) FROM changes").fetchone()[
                0
            ]
        return [r["scope"] for r in rows], latest

    def chat(self, key):
        rows = self.rows("SELECT body FROM chats WHERE key=?", (key,))
        if not rows or not key.startswith(f"{self.account_id}/"):
            raise Refused("Conversation unavailable for this account")
        return Chat.model_validate_json(rows[0]["body"])

    def put_chat(self, chat: Chat, *, activity=None):
        if chat.account_id != self.account_id:
            raise Refused("Wrong account")
        if not chat.ai_eligible:
            chat = chat.model_copy(update={"eligibility_source": "explicit"})
        with self.transaction() as db:
            old = db.execute(
                "SELECT body,activity FROM chats WHERE key=?", (chat.key,)
            ).fetchone()
            if old:
                previous = Chat.model_validate_json(old[0])
                chat = chat.model_copy(
                    update={
                        "read_through": max(chat.read_through, previous.read_through),
                        "read_outbox": max(chat.read_outbox, previous.read_outbox),
                        "latest_message_id": max(
                            chat.latest_message_id or 0, previous.latest_message_id or 0
                        )
                        or None,
                        **(
                            {
                                "ai_eligible": previous.ai_eligible,
                                "eligibility_source": "explicit",
                            }
                            if previous.eligibility_source == "explicit"
                            and chat.eligibility_source == "default"
                            else {}
                        ),
                    }
                )
            body = chat.model_dump_json()
            # Every sync cycle reports each dialog again. Rewriting an identical
            # row would commit, wake every browser and discard planning answers.
            if not old or old[0] != body or (activity or 0) > old[1]:
                db.execute(
                    "INSERT INTO chats VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body,activity=max(chats.activity,excluded.activity)",
                    (chat.key, body, activity or 0),
                )
            if chat.kind == "group":
                self.groups.ensure(db, chat.key)

    def observe_authors(self, authors):
        """Group authors' current photos as intake saw them: (peer, photo ID
        or None, when seen). A later sighting replaces an earlier one."""
        if not authors:
            return
        with self.transaction() as db:
            db.executemany(
                "INSERT INTO group_authors VALUES (?,?,?) ON CONFLICT(peer) DO UPDATE SET photo_id=excluded.photo_id, seen=excluded.seen WHERE excluded.seen >= group_authors.seen",
                authors,
            )

    def observe_albums(self, albums):
        """Messages' Telegram albums as intake saw them: (message key, album,
        date). Albums of messages past the content window are forgotten."""
        if not albums:
            return
        with self.transaction() as db:
            db.executemany(
                "INSERT OR REPLACE INTO message_albums VALUES (?,?,?)", albums
            )
            db.execute(
                "DELETE FROM message_albums WHERE date<?",
                (self.clock() - RETENTION,),
            )

    def album(self, key):
        rows = self.rows("SELECT album FROM message_albums WHERE key=?", (key,))
        return rows[0]["album"] if rows else None

    def author_photo(self, peer):
        """A group author's current photo ID, if intake saw them within the
        content window. An anonymous admin posts as the group itself, whose
        photo is its chat's (ADR 51 Q13)."""
        if peer.startswith(("chat:", "channel:")):
            rows = self.rows(
                "SELECT body FROM chats WHERE key=?", (f"{self.account_id}/{peer}",)
            )
            if rows:
                chat = Chat.model_validate_json(rows[0]["body"])
                return chat.photo_id if chat.kind == "group" else None
            return None
        rows = self.rows(
            "SELECT photo_id FROM group_authors WHERE peer=? AND seen>=?",
            (peer, self.clock() - RETENTION),
        )
        return rows[0]["photo_id"] if rows else None

    def recent_chats(self, kind="private", *, cached_access=False):
        """Seven-day native activity, optionally keeping readable cached groups."""
        if kind not in {"private", "group"}:
            raise Refused("Unsupported recent conversation kind")
        return [
            {
                **json.loads(r["body"]),
                "key": r["key"],
                "activity": r["activity"],
                "processing": r["processing"],
            }
            for r in self.rows(
                "SELECT chats.*,(SELECT count(*) FROM blocks WHERE blocks.chat_key=chats.key AND blocks.state IN ('queued','working')) AS processing FROM chats WHERE activity>=? ORDER BY activity DESC,key",
                (self.clock() - WINDOW,),
            )
            if json.loads(r["body"])["kind"] == kind
            and (
                kind != "group"
                or self.groups.visible_in_list(r["key"], cached_access=cached_access)
            )
        ]

    @property
    def saved_key(self):
        """The account's own Saved Messages (ADR 50): its chat with itself."""
        return f"{self.account_id}/user:{self.account_id}"

    def ensure_saved(self):
        """Saved Messages is always the account's third list, whatever its
        activity. It never takes AI work (ADR 50 Q3)."""
        if self.rows("SELECT 1 FROM chats WHERE key=?", (self.saved_key,)):
            return
        self.put_chat(
            Chat(
                account_id=self.account_id,
                peer=f"user:{self.account_id}",
                title=SAVED_TITLE,
                kind="saved",
                ai_eligible=False,
            )
        )

    def saved_chat(self):
        """The Saved Messages row as `recent_chats` gives rows, if discovered."""
        return [
            {**json.loads(r["body"]), "key": r["key"], "activity": r["activity"]}
            for r in self.rows(
                "SELECT body,key,activity FROM chats WHERE key=?", (self.saved_key,)
            )
            if json.loads(r["body"])["kind"] == "saved"
        ]

    def sync_targets(self):
        """Native group intake is independent of its initially-Off AI opt-in.
        Saved Messages is always one, outside the seven-day window (ADR 50)."""
        return sorted(
            self.recent_chats() + self.recent_chats("group"),
            key=lambda chat: (-chat["activity"], chat["key"]),
        ) + [{**chat, "processing": 0} for chat in self.saved_chat()]

    def read_outbox(self, key, through):
        """Raise the recipient's read cursor; it never moves backwards."""
        with self.transaction() as db:
            row = db.execute("SELECT body FROM chats WHERE key=?", (key,)).fetchone()
            if not row or not key.startswith(f"{self.account_id}/"):
                return False
            body = json.loads(row[0])
            if through <= body.get("read_outbox", 0):
                return False
            body["read_outbox"] = through
            db.execute("UPDATE chats SET body=? WHERE key=?", (json.dumps(body), key))
        return True

    def apply_message(
        self, message: Message, *, cursor=None, intake="initial", group_epoch=None
    ):
        """True for a content change, METADATA for a refreshed kind or safe
        metadata of unchanged content, otherwise False."""
        if message.account_id != self.account_id:
            raise Refused("Wrong account")
        # Telegram's edit_hide marks an edit date that is not an edit (ADR 19);
        # like reactions, it is never stored or compared.
        hidden = message.edit_hidden
        if message.reactions is not None or hidden:
            # Reaction metadata has its own owner and never compares as content.
            message = message.model_copy(
                update={"reactions": None, "edit_hidden": False}
            )
        key = f"{self.account_id}/{message.peer}"
        chat = self.chat(key)
        with self.transaction() as db:
            group = chat.kind == "group"
            if group and not self.groups.permit_source(db, chat, message, group_epoch):
                return False
            if chat.kind == "private":
                reminders.sent(db, message)
            old = db.execute(
                "SELECT body FROM messages WHERE key=?", (message.key,)
            ).fetchone()
            if old:
                prior = Message.model_validate_json(old[0])
                native = (
                    message.native_revision
                    if message.native_revision is not None
                    else message.revision
                )
                prior_native = (
                    prior.native_revision
                    if prior.native_revision is not None
                    else prior.revision
                )
                if prior.deleted or native < prior_native:
                    return False
                # Telegram's copy of a message this service sent replaces the
                # record written from its receipt: never an observed version
                # or an edit, and dated by Telegram (ADR 19).
                first = (
                    provisional(prior)
                    and not provisional(message)
                    and not message.deleted
                )
                message = message.model_copy(
                    update={
                        "date": message.date if first else prior.date,
                        "native_revision": message.native_revision,
                        "native_edited": prior.native_edited
                        or message.native_edited
                        or (
                            native > prior_native
                            and not message.deleted
                            and not hidden
                            and not first
                        ),
                        "forwarded": prior.forwarded
                        if message.forwarded is None
                        else message.forwarded,
                        "forwarded_from": None
                        if message.forwarded is False
                        else message.forwarded_from or prior.forwarded_from,
                        "sender_is_bot": (
                            prior.sender_is_bot
                            if message.sender_is_bot is None
                            and message.author_peer == prior.author_peer
                            else message.sender_is_bot
                        ),
                        **forward_merge(prior, message),
                        **reply_merge(prior, message),
                    }
                )
                fields = (
                    "text",
                    "links",
                    "deleted",
                    "reply_to",
                    "outgoing",
                    "attachment_id",
                )
                # An outgoing flag alone is never an edit (ADR 52 Q18).
                unchanged = same_content(
                    prior, message.model_copy(update={"outgoing": prior.outgoing})
                )
                if group:
                    fields += ("author_peer", "author_signature", "service")
                    unchanged = unchanged and all(
                        getattr(prior, field) == getattr(message, field)
                        for field in ("author_peer", "author_signature", "service")
                    )
                metadata = (
                    native == prior_native
                    and all(getattr(prior, f) == getattr(message, f) for f in fields)
                    and media_class(prior.media) == media_class(message.media)
                )
                if unchanged or metadata:
                    message = message.model_copy(
                        update={
                            "revision": prior.revision,
                            "native_revision": native
                            if native != prior.revision
                            else prior.native_revision,
                        }
                    )
                    if (
                        group
                        and message.sender_is_bot is not None
                        and message.author_peer
                    ):
                        self.group_filters.native_bot(
                            key, message.author_peer, message.sender_is_bot, db=db
                        )
                    if message == prior:
                        if group:
                            self.groups.record_source(db, message, group_epoch)
                        return False
                    # Metadata only: keep derivatives, blocks and activity.
                    db.execute(
                        "UPDATE messages SET date=?,body=? WHERE key=?",
                        (message.date, message.model_dump_json(), message.key),
                    )
                    if cursor:
                        db.execute(
                            "INSERT OR REPLACE INTO cursors VALUES (?,?)", cursor
                        )
                    if group:
                        self.groups.record_source(db, message, group_epoch)
                    return METADATA
                # Preserve the previous observed native content before removing
                # derivatives. Every version keeps the original message's expiry.
                message = message.model_copy(
                    update={
                        "revision": max(message.revision, prior.revision + 1),
                        "native_revision": native
                        if native <= prior.revision
                        else message.native_revision,
                        "native_edited": message.native_edited
                        or not (message.deleted or hidden or first),
                    }
                )
                # An edit Telegram hides keeps an earlier version only for new
                # wording or link targets (ADR 52 Q6).
                untracked = first or (
                    hidden
                    and prior.text == message.text
                    and prior.links == message.links
                )
                if not untracked and prior.date >= self.clock() - RETENTION:
                    history.capture(db, prior, message, self.clock())
            if group and message.sender_is_bot is not None and message.author_peer:
                self.group_filters.native_bot(
                    key, message.author_peer, message.sender_is_bot, db=db
                )
            db.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET date=excluded.date,revision=excluded.revision,deleted=excluded.deleted,body=excluded.body",
                (
                    message.key,
                    key,
                    message.message_id,
                    message.date,
                    message.revision,
                    message.deleted,
                    message.model_dump_json(),
                ),
            )
            self.viewing.record(db, message, intake)
            if group:
                self.groups.record_source(db, message, group_epoch)
                db.execute(
                    "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                    ("group-effects:" + message.key, str(message.revision)),
                )
            if not message.service:
                row = db.execute(
                    "SELECT body,activity FROM chats WHERE key=?", (key,)
                ).fetchone()
                body = json.loads(row["body"])
                latest = body.get("latest_message_id")
                # An unknown legacy top stays unknown while old cache is read.
                # Real dialog discovery supplies its identity without a scan.
                if (latest and message.message_id > latest) or (
                    latest is None and message.date > row["activity"]
                ):
                    body["latest_message_id"] = message.message_id
                    db.execute(
                        "UPDATE chats SET body=? WHERE key=?", (json.dumps(body), key)
                    )
            if not message.service and not message.deleted:
                db.execute(
                    "UPDATE chats SET activity=max(activity,?) WHERE key=?",
                    (message.date, key),
                )
            if old:
                db.execute("DELETE FROM derivatives WHERE source=?", (message.key,))
            if chat.kind == "private":
                # New content refreshes a pending reminder set (ADR 58 Q5).
                reminders.touched(db, key, self.clock())
            if cursor:
                db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?)", cursor)
        return True

    def learn_reply(self, message: Message):
        """Learn a reply header's details from a successful exact read of a
        reply captured before they existed (ADR 41). The header number, content,
        history and derived work stay as they are."""
        if message.account_id != self.account_id:
            raise Refused("Wrong account")
        with self.transaction() as db:
            row = db.execute(
                "SELECT body FROM messages WHERE key=?", (message.key,)
            ).fetchone()
            if not row:
                return
            current = Message.model_validate_json(row[0])
            if current.reply_read or current.reply_to != message.reply_to:
                return
            learned = current.model_copy(
                update={
                    **{field: getattr(message, field) for field in REPLY_DETAILS},
                    "reply_read": True,
                }
            )
            db.execute(
                "UPDATE messages SET body=? WHERE key=?",
                (learned.model_dump_json(), learned.key),
            )

    def learn_forwarding(self, message: Message):
        """Fill in missing attribution from a successful exact read, without
        replacing current content, history or any known value (ADR 27)."""
        if message.account_id != self.account_id:
            raise Refused("Wrong account")
        if message.forwarded is None:
            return
        with self.transaction() as db:
            row = db.execute(
                "SELECT body FROM messages WHERE key=?", (message.key,)
            ).fetchone()
            if not row:
                return
            current = Message.model_validate_json(row[0])
            if current.forwarded is False or (
                current.forwarded and current.forwarded != message.forwarded
            ):
                return
            if not message.forwarded:
                update = {"forwarded": False, "forwarded_from": None}
            else:
                update = {
                    "forwarded": True,
                    "forwarded_from": current.forwarded_from or message.forwarded_from,
                    **forward_merge(current, message),
                    "forward_read": True,
                }
            learned = current.model_copy(update=update)
            if learned != current:
                db.execute(
                    "UPDATE messages SET body=? WHERE key=?",
                    (learned.model_dump_json(), learned.key),
                )

    def messages(self, chat_key, limit=100):
        self.chat(chat_key)
        return [
            Message.model_validate_json(r["body"])
            for r in reversed(
                self.rows(
                    "SELECT body FROM messages WHERE chat_key=? ORDER BY message_id DESC LIMIT ?",
                    (chat_key, limit),
                )
            )
        ]

    def message(self, chat_key, identity):
        self.chat(chat_key)
        rows = self.rows(
            "SELECT body FROM messages WHERE chat_key=? AND message_id=?",
            (chat_key, identity),
        )
        return Message.model_validate_json(rows[0]["body"]) if rows else None

    def draft(self, key):
        self.chat(key)
        rows = self.rows("SELECT body FROM drafts WHERE chat_key=?", (key,))
        draft = json.loads(rows[0]["body"]) if rows else blank_draft(key)
        draft.setdefault("selected_media", None)
        return draft

    def mutate_draft(self, key, revision, change):
        self.chat(key)
        with self.transaction() as db:
            row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (key,)
            ).fetchone()
            draft = json.loads(row[0]) if row else blank_draft(key)
            if draft["revision"] != revision:
                raise Conflict("Draft changed; keep your edits and reload")
            draft.setdefault("selected_media", None)
            change(draft)
            draft["revision"] += 1
            db.execute(
                "INSERT OR REPLACE INTO drafts VALUES (?,?)", (key, json.dumps(draft))
            )
        return draft

    def recover(self):
        with self.transaction() as db:
            self.streams.recover(db)
            db.execute(
                "UPDATE jobs SET state='failed',error='Local transcription interrupted; use Retry' WHERE role='transcript' AND state IN ('queued','claimed')"
            )
            # Claimed work never reached a provider: it is simply queued again.
            db.execute(
                "UPDATE blocks SET state='queued',stage=NULL WHERE state='working' AND id IN (SELECT scope FROM jobs WHERE state='claimed')"
            )
            db.execute("UPDATE jobs SET state='queued' WHERE state='claimed'")
            db.execute(
                "UPDATE jobs SET state='unknown',error='Interrupted attempt; use Retry' WHERE state='running'"
            )
            db.execute(
                "UPDATE blocks SET state='unknown',error='Interrupted attempt; use Retry',rendered=CASE WHEN rendered_revision=revision THEN rendered ELSE NULL END WHERE state='working'"
            )
            self.send_operations.recover(db)
            self.selected_media.recover(db)
            self.deletion_operations.recover(db)
            memory.recover(db)
            emotion.recover(db)
            suggestions.recover(db)
            reactions.recover(db)
            self.group_briefings.recover(db)

    def expire(self):
        with self.transaction() as db:
            cutoff = self.clock() - RETENTION
            db.execute("DELETE FROM message_versions WHERE date<?", (cutoff,))
            db.execute("DELETE FROM hidden_messages WHERE date<?", (cutoff,))
            db.execute(
                "DELETE FROM derivatives WHERE source NOT IN (SELECT key FROM messages) OR source IN (SELECT key FROM messages WHERE date<?)",
                (self.clock() - RETENTION,),
            )
            db.execute("DELETE FROM blocks WHERE date<?", (self.clock() - RETENTION,))
            db.execute("DELETE FROM messages WHERE date<?", (self.clock() - RETENTION,))
            memory.expire(db)
            emotion.expire(db, cutoff)
            suggestions.expire(db, self.clock(), cutoff)
            reactions.expire(db, cutoff)
            db.execute(
                "DELETE FROM jobs WHERE due<? AND state NOT IN ('running','unknown')",
                (cutoff,),
            )
            self.send_operations.expire(db, cutoff)
            self.deletion_operations.expire(db, cutoff)
            self.group_briefings.expire(db, cutoff)
            # Drafts, cost receipts/reservations and unresolved sends survive.
