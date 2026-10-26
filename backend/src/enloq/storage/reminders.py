"""Reminder Schedules, their latest occurrences and Kept Messages (ADR 58).

Several schedules per chat, each with its own id and revision; one latest
occurrence per schedule. Kept Messages are the one message text that outlives
the thirty-day cache, for as long as the schedule whose idea named the day.
"""

import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain.models import SPOKEN_MEDIA, Message
from ..domain.reminders import QUIET, ReminderRule, language_name

DDL = (
    "CREATE TABLE IF NOT EXISTS reminder_schedules (id TEXT PRIMARY KEY, chat_key TEXT NOT NULL REFERENCES chats(key), revision INTEGER NOT NULL, body TEXT NOT NULL, next_at REAL, next_date TEXT, paused INTEGER NOT NULL, control TEXT NOT NULL, digest_revision INTEGER NOT NULL, created REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS reminder_schedules_chat ON reminder_schedules(chat_key)",
    "CREATE TABLE IF NOT EXISTS reminder_occurrences (schedule TEXT PRIMARY KEY REFERENCES reminder_schedules(id), id TEXT NOT NULL UNIQUE, chat_key TEXT NOT NULL, schedule_revision INTEGER NOT NULL, due_at REAL NOT NULL, calendar_date TEXT NOT NULL, notify_at REAL NOT NULL, notice TEXT, created REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS reminder_occurrences_chat ON reminder_occurrences(chat_key)",
    "CREATE TABLE IF NOT EXISTS reminder_outbox (chat_key TEXT PRIMARY KEY REFERENCES chats(key), last_sent REAL NOT NULL)",
    # A chat's reminder set is refreshed once new messages go quiet.
    "CREATE TABLE IF NOT EXISTS reminder_refresh (chat_key TEXT PRIMARY KEY, due REAL NOT NULL)",
    # The day a schedule's idea names, and its copied messages (Q11).
    "CREATE TABLE IF NOT EXISTS reminder_kept_days (schedule TEXT PRIMARY KEY REFERENCES reminder_schedules(id), day TEXT NOT NULL, state TEXT NOT NULL, copied REAL)",
    "CREATE TABLE IF NOT EXISTS reminder_kept (schedule TEXT NOT NULL REFERENCES reminder_schedules(id), key TEXT NOT NULL, message_id INTEGER NOT NULL, date REAL NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY (schedule, key))",
)
# A kept day's state reaches its chat's clients, like its schedule's changes.
KEPT_TRIGGERS = tuple(
    f"CREATE TRIGGER IF NOT EXISTS reminder_kept_days_{event.lower()}_changes AFTER {event} "
    "ON reminder_kept_days WHEN EXISTS (SELECT 1 FROM reminder_schedules WHERE id=NEW.schedule) "
    "BEGIN INSERT INTO changes VALUES ((SELECT chat_key FROM reminder_schedules WHERE id=NEW.schedule),"
    " (SELECT coalesce(max(seq),0)+1 FROM changes))"
    " ON CONFLICT(scope) DO UPDATE SET seq=excluded.seq; END"
    for event in ("INSERT", "UPDATE")
)


def initialize(db, now):
    """Schema 40 moves each schedule of schema 39, one per chat, and its
    pending occurrence to per-schedule tables, keeping revisions, and maps its
    free-text fallback language to the list."""
    columns = {r["name"] for r in db.execute("PRAGMA table_info(reminder_schedules)")}
    earlier = bool(columns) and "id" not in columns
    if earlier:
        for table in ("reminder_schedules", "reminder_occurrences", "reminder_outbox"):
            db.execute(f"ALTER TABLE {table} RENAME TO {table}_39")
    for statement in DDL:
        db.execute(statement)
    if not earlier:
        return
    for row in db.execute(
        "SELECT * FROM reminder_schedules_39 WHERE body IS NOT NULL"
    ).fetchall():
        old = json.loads(row["body"])
        today = datetime.fromtimestamp(now, ZoneInfo(old["timezone"])).date()
        weekly = old.get("frequency", "weekly") == "weekly"
        rule = ReminderRule(
            repeat="weeks" if weekly else "monthly",
            start=today.isoformat(),
            weekday=old.get("weekday", 0),
            month_day=old.get("month_day", 1),
            timezone=old["timezone"],
            delivery=old.get("delivery", "digest"),
            time=old.get("time", "09:00"),
            language=language_name(old.get("language")),
        )
        identity = uuid.uuid4().hex
        db.execute(
            "INSERT INTO reminder_schedules VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                row["chat_key"],
                row["revision"],
                rule.model_dump_json(),
                row["next_at"],
                row["next_date"],
                row["paused"],
                row["control"],
                row["digest_revision"],
                now,
            ),
        )
        occurrence = db.execute(
            "SELECT * FROM reminder_occurrences_39 WHERE chat_key=?",
            (row["chat_key"],),
        ).fetchone()
        if occurrence and occurrence["schedule_revision"] == row["revision"]:
            db.execute(
                "INSERT INTO reminder_occurrences VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    occurrence["id"],
                    row["chat_key"],
                    occurrence["schedule_revision"],
                    occurrence["due_at"],
                    occurrence["calendar_date"],
                    occurrence["notify_at"],
                    occurrence["notice"],
                    now,
                ),
            )
    db.execute(
        "INSERT OR IGNORE INTO reminder_outbox SELECT chat_key,last_sent FROM reminder_outbox_39 WHERE chat_key IN (SELECT key FROM chats)"
    )
    for table in ("reminder_occurrences", "reminder_outbox", "reminder_schedules"):
        db.execute(f"DROP TABLE {table}_39")


def forget_kept(db, schedule):
    db.execute("DELETE FROM reminder_kept WHERE schedule=?", (schedule,))
    db.execute("DELETE FROM reminder_kept_days WHERE schedule=?", (schedule,))


def end(db, schedule):
    """A schedule ends, or is removed, with its idea and Kept Messages."""
    db.execute("DELETE FROM reminder_occurrences WHERE schedule=?", (schedule,))
    forget_kept(db, schedule)
    db.execute("DELETE FROM reminder_schedules WHERE id=?", (schedule,))


def complete(db, occurrence):
    """A completed occurrence leaves; a Once schedule ends with it (Q3)."""
    row = db.execute(
        "SELECT o.schedule,s.body FROM reminder_occurrences o JOIN reminder_schedules s ON s.id=o.schedule WHERE o.id=?",
        (occurrence,),
    ).fetchone()
    if not row:
        return False
    db.execute("DELETE FROM reminder_occurrences WHERE id=?", (occurrence,))
    if json.loads(row["body"]).get("repeat") == "once":
        end(db, row["schedule"])
    return True


def sent(db, message):
    """Called only for Telegram evidence, including confirmed send receipts.
    Pending/uncertain operations never enter the native message store. Any
    later outgoing message completes the chat's occurrences without a
    Message Idea; those with one wait for a send from their own set (Q3)."""
    if not message.outgoing or message.service:
        return
    key = f"{message.account_id}/{message.peer}"
    db.execute(
        "INSERT INTO reminder_outbox SELECT ?,? WHERE EXISTS "
        "(SELECT 1 FROM reminder_schedules WHERE chat_key=?) "
        "ON CONFLICT(chat_key) DO UPDATE SET last_sent=max(last_sent,excluded.last_sent)",
        (key, message.date, key),
    )
    for row in db.execute(
        "SELECT o.id FROM reminder_occurrences o JOIN reminder_schedules s ON s.id=o.schedule "
        "WHERE o.chat_key=? AND o.due_at<? AND json_extract(s.body,'$.idea')=''",
        (key, message.date),
    ).fetchall():
        complete(db, row["id"])


def sent_from_set(db, key, occurrences):
    """A Telegram-confirmed send whose draft took an option of these
    occurrences' set completes the ones still pending (Q3)."""
    for occurrence in occurrences or ():
        row = db.execute(
            "SELECT id FROM reminder_occurrences WHERE id=? AND chat_key=?",
            (occurrence, key),
        ).fetchone()
        if row:
            complete(db, occurrence)


def touched(db, key, now):
    """New content in a chat with occurrences refreshes its reminder set
    after quiet seconds; each change restarts the wait (Q5)."""
    db.execute(
        "INSERT INTO reminder_refresh SELECT ?,? WHERE EXISTS "
        "(SELECT 1 FROM reminder_occurrences WHERE chat_key=?) "
        "ON CONFLICT(chat_key) DO UPDATE SET due=excluded.due",
        (key, now + QUIET, key),
    )


def kept_item(db, message: Message):
    """A message as a reminder set reads it, with its cached transcript and
    image meaning, as the manual context does."""
    from ..application.context import projection

    item = projection(message)
    row = db.execute(
        "SELECT body FROM derivatives WHERE source=? AND revision=?",
        (message.key, message.revision),
    ).fetchone()
    derivative = json.loads(row["body"]) if row else {}
    if item["media"] == "image" and derivative.get("image_context"):
        item["image_context"] = derivative["image_context"]
    if item["media"] in SPOKEN_MEDIA and derivative.get("transcript"):
        item["transcript"] = derivative["transcript"]
    return item


def mirror(db, schedule=None):
    """Kept copies follow edits, deletions and newer transcripts or image
    meanings seen while their originals are cached, as memory citations do
    (ADR 55); an expired original leaves its copy as it was."""
    changed = False
    for row in db.execute(
        "SELECT k.schedule,k.key,k.revision,k.body,m.body AS current,m.deleted FROM reminder_kept k "
        "JOIN messages m ON m.key=k.key WHERE ? IS NULL OR k.schedule=?",
        (schedule, schedule),
    ).fetchall():
        if row["deleted"]:
            db.execute(
                "DELETE FROM reminder_kept WHERE schedule=? AND key=?",
                (row["schedule"], row["key"]),
            )
            changed = True
            continue
        message = Message.model_validate_json(row["current"])
        body = json.dumps(kept_item(db, message), ensure_ascii=False)
        if message.revision != row["revision"] or body != row["body"]:
            db.execute(
                "UPDATE reminder_kept SET revision=?,body=? WHERE schedule=? AND key=?",
                (message.revision, body, row["schedule"], row["key"]),
            )
            changed = True
    return changed


def keep(db, schedule, key, start, end_, limit):
    """Copy up to ``limit`` of a day's cached messages, newest first."""
    db.execute("DELETE FROM reminder_kept WHERE schedule=?", (schedule,))
    rows = db.execute(
        "SELECT body FROM messages WHERE chat_key=? AND deleted=0 AND date>=? AND date<? "
        "ORDER BY date DESC,message_id DESC LIMIT ?",
        (key, start, end_, limit),
    ).fetchall()
    for row in rows:
        message = Message.model_validate_json(row["body"])
        if message.service:
            continue
        db.execute(
            "INSERT OR REPLACE INTO reminder_kept VALUES (?,?,?,?,?,?)",
            (
                schedule,
                message.key,
                message.message_id,
                message.date,
                message.revision,
                json.dumps(kept_item(db, message), ensure_ascii=False),
            ),
        )
    return db.execute(
        "SELECT count(*) FROM reminder_kept WHERE schedule=?", (schedule,)
    ).fetchone()[0]


def kept(db, schedule):
    """A schedule's Kept Messages, oldest first, and their identity."""
    rows = db.execute(
        "SELECT key,revision,body FROM reminder_kept WHERE schedule=? ORDER BY date,message_id",
        (schedule,),
    ).fetchall()
    return [json.loads(r["body"]) for r in rows], [
        [r["key"], r["revision"], r["body"]] for r in rows
    ]
