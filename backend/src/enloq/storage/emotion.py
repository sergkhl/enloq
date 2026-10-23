"""One current assessment per chat; no emotion history or migration backfill."""

import json

DDL = "CREATE TABLE IF NOT EXISTS emotion_state (chat_key TEXT PRIMARY KEY REFERENCES chats(key), source_time REAL NOT NULL, body TEXT NOT NULL)"


def expire(db, cutoff):
    db.execute("DELETE FROM emotion_state WHERE source_time<?", (cutoff,))


def recover(db):
    """After job recovery: a dispatched classification is an interrupted
    outcome that is never resampled; undispatched work simply runs again."""
    for row in db.execute(
        "SELECT chat_key,body FROM emotion_state WHERE json_extract(body,'$.state')='pending'"
    ).fetchall():
        body = json.loads(row["body"])
        job = db.execute(
            "SELECT state FROM jobs WHERE id=?", (body["attempt"],)
        ).fetchone()
        if job and job["state"] in ("queued", "claimed", "running"):
            continue
        body.pop("prepared", None)
        if job and job["state"] in ("superseded", "cancelled"):
            body.update(state="superseded", failure=None)
        else:
            body.update(state="failed", failure="interrupted")
        db.execute(
            "UPDATE emotion_state SET body=? WHERE chat_key=?",
            (json.dumps(body), row["chat_key"]),
        )
