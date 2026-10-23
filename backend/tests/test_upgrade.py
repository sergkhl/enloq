"""Delivery safety: a store from the deployed schema 11 upgrades in place."""

import json
import sqlite3

from enloq.domain.models import Chat, Message
from enloq.storage.store import SCHEMA_VERSION, Store

from backend.tests.test_rebuild import fixture

# The deployed (pre-E1) schema, verbatim, as authored DDL.
SCHEMA_11 = [
    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE chats (key TEXT PRIMARY KEY, body TEXT NOT NULL, activity REAL NOT NULL DEFAULT 0)",
    "CREATE TABLE messages (key TEXT PRIMARY KEY, chat_key TEXT NOT NULL REFERENCES chats(key), message_id INTEGER NOT NULL, date REAL NOT NULL, revision INTEGER NOT NULL, deleted INTEGER NOT NULL, body TEXT NOT NULL)",
    "CREATE INDEX conversation_order ON messages(chat_key, message_id)",
    "CREATE TABLE cursors (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE drafts (chat_key TEXT PRIMARY KEY REFERENCES chats(key), body TEXT NOT NULL)",
    "CREATE TABLE blocks (id TEXT PRIMARY KEY, chat_key TEXT NOT NULL REFERENCES chats(key), revision TEXT NOT NULL, sources TEXT NOT NULL, rendered TEXT, state TEXT NOT NULL, error TEXT, date REAL NOT NULL)",
    "CREATE TABLE jobs (id TEXT PRIMARY KEY, scope TEXT NOT NULL, role TEXT NOT NULL, input_revision TEXT NOT NULL, state TEXT NOT NULL, due REAL NOT NULL, payload TEXT NOT NULL, error TEXT)",
    "CREATE INDEX due_work ON jobs(state,due)",
    "CREATE TABLE reservations (id TEXT PRIMARY KEY, bucket TEXT NOT NULL, engine TEXT NOT NULL, reserved INTEGER NOT NULL, actual INTEGER, status TEXT NOT NULL, created REAL NOT NULL)",
    "CREATE TABLE receipts (id TEXT PRIMARY KEY, role TEXT NOT NULL, model TEXT NOT NULL, provider TEXT NOT NULL, usage TEXT NOT NULL, error TEXT)",
    "CREATE TABLE derivatives (source TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE attachments (id TEXT PRIMARY KEY, chat_key TEXT NOT NULL REFERENCES chats(key), body TEXT NOT NULL)",
    "CREATE TABLE operations (id TEXT PRIMARY KEY, chat_key TEXT NOT NULL REFERENCES chats(key), state TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE avatars (peer TEXT PRIMARY KEY, photo_id TEXT NOT NULL, mime TEXT, expires REAL NOT NULL)",
]


async def test_schema_11_store_upgrades_to_current_keeping_content(tmp_path):
    path = tmp_path / "1/rewriting.sqlite3"
    path.parent.mkdir(mode=0o700)
    chat = Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    source = Message(
        account_id=1, peer="user:42", message_id=1, revision=1, date=2e9, text="Kept."
    )
    with sqlite3.connect(path) as db:
        for statement in SCHEMA_11:
            db.execute(statement)
        db.execute("PRAGMA user_version=11")
        db.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("account_id", "1"),
                ("mode", "offline"),
                ("timezone", "Asia/Ho_Chi_Minh"),
            ],
        )
        db.execute(
            "INSERT INTO chats VALUES (?,?,?)", (chat.key, chat.model_dump_json(), 2e9)
        )
        db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (source.key, chat.key, 1, 2e9, 1, 0, source.model_dump_json()),
        )
        db.execute(
            "INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?)",
            (
                "1/user:42/1",
                chat.key,
                "rev",
                json.dumps([1]),
                "Rewritten.",
                "ready",
                None,
                2e9,
            ),
        )
        db.execute(
            "INSERT INTO drafts VALUES (?,?)",
            (
                chat.key,
                json.dumps(
                    {
                        "chat_key": chat.key,
                        "revision": 3,
                        "text": "Draft",
                        "attachments": [],
                        "reply_to": None,
                        "undo": None,
                        "state": "idle",
                    }
                ),
            ),
        )
    store = Store(path, account_id=1, clock=lambda: 2e9 + 60)
    assert store.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    block = store.rows("SELECT * FROM blocks")[0]
    assert block["rendered"] == "Rewritten." and block["state"] == "ready"
    assert block["unanalysed"] is None and block["guidance"] is None
    assert store.draft(chat.key)["text"] == "Draft"
    assert store.message(chat.key, 1).media_info is None
    scopes, _ = store.changes()
    store.mutate_draft(chat.key, 3, lambda d: d.update(text="Edited"))
    assert store.changes()[0] == [chat.key]
    reopened = Store(path, account_id=1, clock=lambda: 2e9 + 60)
    assert reopened.draft(chat.key)["text"] == "Edited"


def test_current_schema_opens_idempotently(tmp_path):
    a = fixture(tmp_path)
    for _ in range(2):
        again = Store(a.store.path, account_id=1, shared_path=a.store.shared_path)
        assert again.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION


def test_schema_28_admission_upgrade_preserves_sources_drafts_and_accounting(tmp_path):
    from backend.tests.test_rebuild import message

    app = fixture(tmp_path)
    app.sync.ingest(message(app))
    app.store.mutate_draft(
        "1/user:42", 0, lambda draft: draft.update(text="Kept draft")
    )
    tables = (
        "metadata",
        "chats",
        "messages",
        "blocks",
        "jobs",
        "drafts",
        "reservations",
        "receipts",
    )
    with app.store.transaction() as db:
        db.execute("DROP TABLE incoming_deferred")
        db.execute("PRAGMA user_version=28")
    prior = {table: app.store.rows(f"SELECT * FROM {table}") for table in tables}
    app.store.close()
    upgraded = Store(app.store.path, account_id=1, shared_path=app.store.shared_path)
    assert upgraded.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    assert {table: upgraded.rows(f"SELECT * FROM {table}") for table in tables} == prior
    assert upgraded.rows("SELECT * FROM incoming_deferred") == []
    upgraded.close()


def test_schema_26_catalog_upgrade_keeps_exact_content_and_receipts(tmp_path):
    a = fixture(tmp_path)
    key = "1/user:42"
    a.store.mutate_draft(
        key,
        0,
        lambda draft: draft.update(
            text="Authored caption\nwith a second line.",
            undo="Earlier authored wording.",
            state="rewritten",
            reply_to=17,
        ),
    )
    with a.store.transaction() as db:
        db.execute(
            "INSERT INTO receipts (id,role,model,provider,usage,error) VALUES (?,?,?,?,?,?)",
            ("authored-receipt", "outgoing_rewrite", "authored", "fake", "{}", None),
        )
        db.execute("DROP TABLE media_catalogs")
        db.execute("PRAGMA user_version=26")
    tables = ("metadata", "chats", "drafts", "receipts", "operations", "blocks")
    prior = {table: a.store.rows(f"SELECT * FROM {table}") for table in tables}
    path, shared = a.store.path, a.store.shared_path
    a.store.close()
    upgraded = Store(path, account_id=1, shared_path=shared)
    assert upgraded.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    assert {table: upgraded.rows(f"SELECT * FROM {table}") for table in tables} == prior
    assert upgraded.rows("SELECT * FROM media_catalogs") == []
    upgraded.close()


def test_schema_27_selected_media_upgrade_preserves_cached_drafts_catalogs_and_receipts(
    tmp_path,
):
    a = fixture(tmp_path)
    key = "1/user:42"
    a.store.mutate_draft(
        key,
        0,
        lambda draft: draft.update(
            text="Kept — authored\ncaption",
            undo="Exact earlier text",
            state="incomplete",
            error="Authored interrupted status",
            reply_to=17,
        ),
    )
    with a.store.transaction() as db:
        db.execute(
            "INSERT INTO receipts (id,role,model,provider,usage,error) VALUES (?,?,?,?,?,?)",
            (
                "authored-kept-receipt",
                "outgoing_rewrite",
                "authored",
                "fake",
                "{}",
                None,
            ),
        )
        db.execute(
            "INSERT INTO media_catalogs VALUES (?,?,?,NULL,1,NULL)",
            ("library", '{"gifs":[]}', "a" * 64),
        )
        db.execute("DROP TABLE selected_media")
        db.execute("PRAGMA user_version=27")
    tables = (
        "metadata",
        "chats",
        "drafts",
        "receipts",
        "operations",
        "blocks",
        "media_catalogs",
    )
    prior = {table: a.store.rows(f"SELECT * FROM {table}") for table in tables}
    path, shared = a.store.path, a.store.shared_path
    a.store.close()
    upgraded = Store(path, account_id=1, shared_path=shared)
    assert upgraded.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    assert {table: upgraded.rows(f"SELECT * FROM {table}") for table in tables} == prior
    assert upgraded.rows("SELECT * FROM selected_media") == []
    assert (
        upgraded.draft(key)["text"] == "Kept — authored\ncaption"
        and upgraded.draft(key)["selected_media"] is None
    )
    upgraded.close()


def test_schema_33_upgrade_settles_sent_messages_recorded_as_edits(tmp_path):
    """Before schema 34, Telegram's copy of a sent message was recorded as an
    edit of the record written from its receipt (ADR 19)."""
    from backend.tests.test_rebuild import message

    app = fixture(tmp_path)
    now = app.store.clock()
    native = int(now + 2) * 1_000_000

    def receipt(identity, **kwargs):
        return message(
            app, identity, revision=0, date=now + 0.5, outgoing=True, **kwargs
        )

    def copy(value, **kwargs):
        return value.model_copy(update={"revision": native, **kwargs})

    flagged = {"native_edited": True}
    # Sent text: Telegram's copy kept the receipt's revision 0.
    text = receipt(1).model_copy(update={"native_revision": native, **flagged})
    # Sent text with a native date long after the receipt: an edit seen since.
    later = receipt(2).model_copy(
        update={"native_revision": native + 36_000 * 1_000_000, **flagged}
    )
    # A sent GIF, one later edited by caption and one deleted.
    gif = receipt(3, media="gif", attachment_id="local-3")
    captioned = receipt(4, media="gif", attachment_id="local-4")
    deleted = receipt(5, media="gif", attachment_id="local-5")
    # An incoming edit and a sent message deleted before Telegram's copy.
    incoming = message(app, 6, revision=native, **flagged)
    unread = receipt(7)
    current = [
        text,
        later,
        copy(gif, attachment_id="document:3", **flagged),
        copy(captioned, attachment_id="document:4", text="Caption", **flagged),
        copy(deleted, revision=native + 1, deleted=True, **flagged),
        incoming,
        unread.model_copy(update={"revision": 1, "deleted": True}),
    ]
    versions = [
        (gif, "edited"),
        (captioned, "edited"),
        (copy(captioned, attachment_id="document:4", **flagged), "edited"),
        (deleted, "edited"),
        (copy(deleted, attachment_id="document:5", **flagged), "deleted"),
        (unread, "deleted"),
    ]
    with app.store.transaction() as db:
        for value in current:
            db.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                (
                    value.key,
                    "1/user:42",
                    value.message_id,
                    value.date,
                    value.revision,
                    value.deleted,
                    value.model_dump_json(),
                ),
            )
        for value, reason in versions:
            db.execute(
                "INSERT INTO message_versions(source,date,body,observed,reason) VALUES (?,?,?,?,?)",
                (value.key, value.date, value.model_dump_json(), now, reason),
            )
        db.execute("PRAGMA user_version=33")
    app.store.close()
    upgraded = Store(app.store.path, account_id=1, shared_path=app.store.shared_path)
    assert upgraded.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    edited = {
        identity: upgraded.message("1/user:42", identity).native_edited
        for identity in range(1, 8)
    }
    assert edited == {1: False, 2: True, 3: False, 4: True, 5: False, 6: True, 7: False}
    kept = [
        (row["source"].rsplit("/", 1)[1], row["reason"], json.loads(row["body"]))
        for row in upgraded.rows("SELECT * FROM message_versions ORDER BY id")
    ]
    assert [(source, reason) for source, reason, _ in kept] == [
        ("4", "edited"),
        ("5", "deleted"),
        ("7", "deleted"),
    ]
    assert kept[0][2]["native_edited"] and not kept[1][2]["native_edited"]
    # Dates stay as recorded; the repair runs once.
    assert upgraded.message("1/user:42", 1).date == now + 0.5
    upgraded.close()
    again = Store(app.store.path, account_id=1, shared_path=app.store.shared_path)
    assert len(again.rows("SELECT * FROM message_versions")) == 3
    again.close()


def test_schema_34_upgrade_settles_saved_flips_and_dates_sent_messages(tmp_path):
    """ADR 52 Q17-Q18: Saved notes whose outgoing flag flipped kept each flip
    as an edit, and messages sent before schema 34 kept the moment Send was
    pressed."""
    from backend.tests.test_rebuild import message

    app = fixture(tmp_path)
    app.store.ensure_saved()
    now = app.store.clock()
    native = int(now + 2) * 1_000_000

    def note(identity, outgoing, **kwargs):
        return message(app, identity, **kwargs).model_copy(
            update={"peer": "user:1", "author_peer": "user:1", "outgoing": outgoing}
        )

    def sent(identity, **kwargs):
        return message(
            app, identity, revision=0, date=now + 0.5, outgoing=True, **kwargs
        ).model_copy(update={"native_revision": native})

    # Each flip raised the local revision; the native one stayed Telegram's.
    flipped = note(10, False, revision=native + 2, native_edited=True).model_copy(
        update={"native_revision": native}
    )
    rewritten = note(11, True, revision=native + 1, native_edited=True, text="New")
    current = [
        flipped,
        rewritten,
        sent(20),
        sent(21, native_edited=True),
        sent(22).model_copy(update={"revision": 1, "deleted": True, "text": ""}),
        # Telegram's whole-second date just before Send was pressed.
        sent(23).model_copy(update={"native_revision": int(now) * 1_000_000}),
    ]
    versions = [
        (note(10, True, revision=native), "edited"),
        (
            note(10, False, revision=native + 1).model_copy(
                update={"native_revision": native}
            ),
            "edited",
        ),
        (note(11, False, revision=native), "edited"),
        (sent(22), "deleted"),
    ]
    with app.store.transaction() as db:
        for value in current:
            db.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                (
                    value.key,
                    value.key.rsplit("/", 1)[0],
                    value.message_id,
                    value.date,
                    value.revision,
                    value.deleted,
                    value.model_dump_json(),
                ),
            )
        for value, reason in versions:
            db.execute(
                "INSERT INTO message_versions(source,date,body,observed,reason) VALUES (?,?,?,?,?)",
                (value.key, value.date, value.model_dump_json(), now, reason),
            )
        for identity in (20, 21, 22, 23):
            db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (
                    f"op-{identity}",
                    "1/user:42",
                    "confirmed",
                    json.dumps(
                        {
                            "chat_key": "1/user:42",
                            "state": "confirmed",
                            "created": now + 0.5,
                            "message_id": identity,
                        }
                    ),
                ),
            )
        db.execute("PRAGMA user_version=34")
    app.store.close()
    upgraded = Store(app.store.path, account_id=1, shared_path=app.store.shared_path)
    assert upgraded.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    saved = "1/user:1"
    # Only flips: gone, with their pencil; the note is the owner's again.
    settled = upgraded.message(saved, 10)
    assert settled.outgoing and not settled.native_edited
    # A real change keeps its version and pencil; its body is outgoing too.
    kept = upgraded.rows("SELECT source,body FROM message_versions ORDER BY id")
    assert [row["source"] for row in kept] == [f"{saved}/11", "1/user:42/22"]
    assert json.loads(kept[0]["body"])["outgoing"]
    assert upgraded.message(saved, 11).native_edited
    # Unedited sends take Telegram's date, their captured versions too.
    assert upgraded.message("1/user:42", 20).date == native / 1_000_000
    assert upgraded.message("1/user:42", 21).date == now + 0.5
    assert upgraded.message("1/user:42", 22).date == native / 1_000_000
    assert upgraded.message("1/user:42", 23).date == int(now)
    assert json.loads(kept[1]["body"])["date"] == native / 1_000_000
    assert (
        upgraded.rows("SELECT date FROM messages WHERE key='1/user:42/20'")[0]["date"]
        == native / 1_000_000
    )
    upgraded.close()
