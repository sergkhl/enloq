"""Account-owned schedule metadata and one latest occurrence per chat."""

DDL = (
    "CREATE TABLE IF NOT EXISTS reminder_schedules (chat_key TEXT PRIMARY KEY REFERENCES chats(key), revision INTEGER NOT NULL, body TEXT, next_at REAL, next_date TEXT, paused INTEGER NOT NULL, control TEXT NOT NULL, digest_revision INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS reminder_occurrences (chat_key TEXT PRIMARY KEY REFERENCES reminder_schedules(chat_key), id TEXT NOT NULL UNIQUE, schedule_revision INTEGER NOT NULL, due_at REAL NOT NULL, calendar_date TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0, notify_at REAL NOT NULL, notice TEXT)",
    "CREATE TABLE IF NOT EXISTS reminder_outbox (chat_key TEXT PRIMARY KEY REFERENCES reminder_schedules(chat_key), last_sent REAL NOT NULL)",
)


def sent(db, message):
    """Called only for Telegram evidence, including confirmed send receipts.
    Pending/uncertain operations never enter the native message store."""
    if not message.outgoing or message.service:
        return
    key = f"{message.account_id}/{message.peer}"
    db.execute(
        "INSERT INTO reminder_outbox SELECT ?,? WHERE EXISTS "
        "(SELECT 1 FROM reminder_schedules WHERE chat_key=?) "
        "ON CONFLICT(chat_key) DO UPDATE SET last_sent=max(last_sent,excluded.last_sent)",
        (key, message.date, key),
    )
    db.execute(
        "DELETE FROM reminder_occurrences WHERE chat_key=? AND due_at<?",
        (key, message.date),
    )
