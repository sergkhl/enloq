"""Durable memory admission metadata, separate from source content retention."""


def initialize(db):
    columns = {r["name"] for r in db.execute("PRAGMA table_info(analyzed)")}
    if "revision" not in columns:
        db.execute("ALTER TABLE analyzed ADD COLUMN revision INTEGER")
        db.execute("ALTER TABLE analyzed ADD COLUMN digest TEXT")
        db.execute("ALTER TABLE analyzed ADD COLUMN consumed TEXT")
        # Legacy rows already represent disclosed material. Bind only the
        # current native version; normal edits previously removed these rows.
        db.execute(
            "UPDATE analyzed SET revision=(SELECT revision FROM messages WHERE key=analyzed.key)"
        )
    if "origin" not in columns:
        # ADR 55 Q20: "sent" to a model, or admitted by the cache "start".
        db.execute("ALTER TABLE analyzed ADD COLUMN origin TEXT")
    db.execute(
        "CREATE INDEX IF NOT EXISTS analyzed_chat ON analyzed(chat_key,message_id)"
    )
    # ADR 55 Q7, Q18: a private chat's own Remember this chat choice, kept once
    # made (without one, it is on).
    db.execute(
        "CREATE TABLE IF NOT EXISTS remembering (chat_key TEXT PRIMARY KEY, "
        "enabled INTEGER NOT NULL, changed REAL NOT NULL)"
    )
    # A chat switched off never starts from the cache again (Q20).
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_starts (chat_key TEXT PRIMARY KEY, closed REAL)"
    )
    # ADR 21's one-time consolidation ended with the takeaways it converted.
    db.execute("DROP TABLE IF EXISTS memory_consolidation")


def clear_takeaways(db):
    """ADR 55 Q20: Chat Memory's takeaways leave rather than convert; the
    start relearns from the cache what each person and the owner care about."""
    db.execute("DELETE FROM memory_entries")
    db.execute("DELETE FROM memory_state")
    db.execute("UPDATE analyzed SET consumed=NULL")


def recover(db):
    # A receipt can commit just before publication. Never silently redisclose
    # that batch following a crash in the gap; leave the previous memory/Retry.
    for job in db.execute(
        "SELECT * FROM jobs j WHERE role='chat_memory' AND rowid=(SELECT max(rowid) FROM jobs WHERE role='chat_memory' AND scope=j.scope)"
    ).fetchall():
        if job["state"] in ("failed", "unknown") or (
            job["state"] == "done"
            and db.execute(
                "SELECT json_extract(?, '$.publication_pending')", (job["payload"],)
            ).fetchone()[0]
        ):
            db.execute(
                "INSERT INTO memory_state VALUES (?,0,0,?) ON CONFLICT(chat_key) DO UPDATE SET error=excluded.error",
                (
                    job["scope"],
                    "Memory update interrupted or failed; previous memory kept. Use Retry",
                ),
            )


def expire(db):
    db.execute("DELETE FROM analyzed WHERE key NOT IN (SELECT key FROM messages)")
