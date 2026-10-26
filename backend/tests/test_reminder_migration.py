"""Schema 40 moves schema 39's one schedule per chat to per-schedule tables."""

import json

from enloq.domain.reminders import ReminderRule
from enloq.storage.store import SCHEMA_VERSION

from backend.tests.test_rebuild import fixture

KEY = "1/user:42"
OLD = (
    "CREATE TABLE reminder_schedules (chat_key TEXT PRIMARY KEY REFERENCES chats(key), revision INTEGER NOT NULL, body TEXT, next_at REAL, next_date TEXT, paused INTEGER NOT NULL, control TEXT NOT NULL, digest_revision INTEGER NOT NULL)",
    "CREATE TABLE reminder_occurrences (chat_key TEXT PRIMARY KEY REFERENCES reminder_schedules(chat_key), id TEXT NOT NULL UNIQUE, schedule_revision INTEGER NOT NULL, due_at REAL NOT NULL, calendar_date TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0, notify_at REAL NOT NULL, notice TEXT)",
    "CREATE TABLE reminder_outbox (chat_key TEXT PRIMARY KEY REFERENCES reminder_schedules(chat_key), last_sent REAL NOT NULL)",
)


def downgrade(app, schedules, occurrences=(), outbox=()):
    """Rewrite the reminder tables as schema 39 kept them."""
    with app.store.transaction() as db:
        for table in (
            "reminder_kept",
            "reminder_kept_days",
            "reminder_refresh",
            "reminder_occurrences",
            "reminder_outbox",
            "reminder_schedules",
        ):
            db.execute(f"DROP TABLE {table}")
        for statement in OLD:
            db.execute(statement)
        db.executemany(
            "INSERT INTO reminder_schedules VALUES (?,?,?,?,?,?,?,?)", schedules
        )
        db.executemany(
            "INSERT INTO reminder_occurrences VALUES (?,?,?,?,?,?,?,?)", occurrences
        )
        db.executemany("INSERT INTO reminder_outbox VALUES (?,?)", outbox)
        db.execute("PRAGMA user_version=39")


def test_each_schedule_and_its_pending_occurrence_move_keeping_revisions(tmp_path):
    app = fixture(tmp_path)
    from enloq.domain.models import Chat

    for peer in ("user:43", "user:44"):
        app.store.put_chat(
            Chat(account_id=1, peer=peer, title="Authored", kind="private")
        )
    weekly = {
        "frequency": "weekly",
        "weekday": 6,
        "month_day": 1,
        "timezone": "Asia/Tokyo",
        "delivery": "exact",
        "time": "10:30",
        "language": "Japanese",
    }
    monthly = {
        "frequency": "monthly",
        "weekday": 0,
        "month_day": 31,
        "timezone": "UTC",
        "delivery": "digest",
        "time": "09:00",
        "language": "en-US",
    }
    downgrade(
        app,
        [
            (KEY, 3, json.dumps(weekly), 2_000_100_000.0, "2033-05-29", 0, "0:1:1", 0),
            (
                "1/user:43",
                2,
                json.dumps(monthly),
                2_000_200_000.0,
                "2033-05-31",
                1,
                "0:1:0",
                0,
            ),
            # A removed schedule kept only its revision; it does not move.
            ("1/user:44", 4, None, None, None, 0, "0:1:1", 0),
        ],
        [(KEY, "a" * 32, 3, 1_999_990_000.0, "2033-05-22", 1, 1_999_990_000.0, None)],
        [(KEY, 1_999_000_000.0)],
    )
    restarted = fixture(tmp_path)
    assert (
        restarted.store.rows("PRAGMA user_version")[0]["user_version"]
        == (SCHEMA_VERSION)
        == 40
    )
    rows = {
        r["chat_key"]: r
        for r in restarted.store.rows("SELECT * FROM reminder_schedules")
    }
    assert set(rows) == {KEY, "1/user:43"}
    first = ReminderRule.model_validate_json(rows[KEY]["body"])
    assert (first.repeat, first.interval, first.weekday) == ("weeks", 1, 6)
    assert (first.timezone, first.delivery, first.time) == (
        "Asia/Tokyo",
        "exact",
        "10:30",
    )
    assert first.language == "Japanese" and first.idea == ""
    assert rows[KEY]["revision"] == 3 and rows[KEY]["next_at"] == 2_000_100_000.0
    second = ReminderRule.model_validate_json(rows["1/user:43"]["body"])
    assert (second.repeat, second.month_day, second.language) == (
        "monthly",
        31,
        "English",
    )
    assert rows["1/user:43"]["paused"] == 1
    occurrence = restarted.store.rows("SELECT * FROM reminder_occurrences")
    assert [(o["id"], o["schedule"], o["schedule_revision"]) for o in occurrence] == [
        ("a" * 32, rows[KEY]["id"], 3)
    ]
    assert restarted.store.rows("SELECT * FROM reminder_outbox") == [
        {"chat_key": KEY, "last_sent": 1_999_000_000.0}
    ]
    assert not restarted.store.rows(
        "SELECT name FROM sqlite_master WHERE name LIKE '%\\_39' ESCAPE '\\'"
    )
    # The moved schedule reads and edits like any other.
    view = restarted.reminders.get(KEY, rows[KEY]["id"])
    assert view.revision == 3 and view.schedule.weekday == 6


def test_a_fresh_store_creates_no_schedules(tmp_path):
    app = fixture(tmp_path)
    assert app.store.rows("SELECT * FROM reminder_schedules") == []
    assert app.store.rows("PRAGMA user_version")[0]["user_version"] == 40
