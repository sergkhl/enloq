"""One current Response Suggestions outcome per chat; no history or backfill.

A pending record keeps the prepared, bounded provider input so queued work can
run after a restart without another Telegram read or media preparation. Every
outcome drops that copy; a record past the 12-hour lifetime cannot keep it.
"""

import json

from ..domain.suggestions import EXPIRY

DDL = "CREATE TABLE IF NOT EXISTS suggestion_state (chat_key TEXT PRIMARY KEY REFERENCES chats(key), source_time REAL NOT NULL, body TEXT NOT NULL)"
SETTINGS_DDL = (
    "CREATE TABLE IF NOT EXISTS suggestion_settings (chat_key TEXT PRIMARY KEY, "
    "automatic INTEGER NOT NULL, style TEXT NOT NULL, changed REAL NOT NULL)"
)


def migrate_settings(db):
    db.execute(SETTINGS_DDL)
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='suggestion_choice'"
    ).fetchone():
        db.execute(
            "INSERT OR IGNORE INTO suggestion_settings SELECT chat_key, choice!='off', "
            "CASE WHEN choice='idioms' THEN 'idioms' ELSE 'friendly' END,changed "
            "FROM suggestion_choice"
        )
        db.execute("DROP TABLE suggestion_choice")


def settle(db, chat_key, body, state, reason=None):
    body = {k: v for k, v in body.items() if k != "packet"}
    body.update(state=state, failure=reason)
    db.execute(
        "UPDATE suggestion_state SET body=? WHERE chat_key=?",
        (json.dumps(body), chat_key),
    )


def recover(db):
    """After the store's job recovery: claimed work is queued again, while a
    dispatched attempt is an unknown outcome that is never resampled. A label
    request (ADR 49 Q19) belongs to the call that started it, so an
    interrupted one is never queued again; its attempt fails below."""
    db.execute(
        "UPDATE jobs SET state='cancelled',error='Labelling interrupted' WHERE role='suggestion_labels' AND state IN ('queued','claimed')"
    )
    for row in db.execute(
        "SELECT chat_key,body FROM suggestion_state WHERE json_extract(body,'$.state')='pending'"
    ).fetchall():
        body = json.loads(row["body"])
        job = db.execute(
            "SELECT state FROM jobs WHERE id=?", (body["attempt"],)
        ).fetchone()
        if job and job["state"] in ("queued", "claimed", "running"):
            continue
        if job and job["state"] in ("unknown", "failed", "done"):
            settle(db, row["chat_key"], body, "failed", "interrupted")
        else:
            settle(db, row["chat_key"], body, "cancelled")


def expire(db, now, cutoff):
    db.execute("DELETE FROM suggestion_state WHERE source_time<?", (cutoff,))
    for row in db.execute(
        "SELECT chat_key,body FROM suggestion_state WHERE source_time<=? AND json_extract(body,'$.packet') IS NOT NULL",
        (now - EXPIRY,),
    ).fetchall():
        settle(db, row["chat_key"], json.loads(row["body"]), "cancelled", "expired")
