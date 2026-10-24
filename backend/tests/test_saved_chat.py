"""ADR 50 SM3: each account's own Saved Messages, its third list.

Saved Messages is admitted at discovery and on live updates, whatever its
activity, and syncs its newest messages within the 30-day content rule. It
reads, sends, replies and deletes like a private chat, but takes no AI work,
unseen messages, notifications or reactions (Q3, Q17). Authored data only.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.storage.store import Store
from enloq.transport.telethon import PersonalTransport
from fastapi.testclient import TestClient
from telethon.tl import types

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture

NOW = 2_000_000_000.0
SAVED = "user:1"
KEY = "1/user:1"


def note(app, identity, *, date=NOW, outgoing=True, **kwargs):
    from enloq.domain.models import Message

    return Message(
        account_id=1,
        peer=SAVED,
        message_id=identity,
        revision=1,
        date=date,
        outgoing=outgoing,
        text=kwargs.pop("text", f"Authored note {identity}"),
        **kwargs,
    )


def saved_app(tmp_path):
    app = fixture(tmp_path, clock=lambda: NOW)
    app.store.ensure_saved()
    return app


def test_the_store_keeps_saved_messages_apart_from_recent_chats(tmp_path):
    store = Store(tmp_path / "db.sqlite3", account_id=1, clock=lambda: NOW)
    store.ensure_saved()
    store.ensure_saved()
    chat = store.chat(KEY)
    assert (chat.kind, chat.title, chat.peer, chat.ai_eligible) == (
        "saved",
        "Saved Messages",
        SAVED,
        False,
    )
    # Never a Recent Chat, always a sync target, whatever its activity.
    assert store.recent_chats() == []
    assert [c["key"] for c in store.sync_targets()] == [KEY]


def transport(tmp_path):
    store = Store(tmp_path / "db.sqlite3", account_id=1, clock=lambda: NOW)
    value = object.__new__(PersonalTransport)
    value.store, value.account_id = store, 1
    value.verified = True
    return value


def dialog(entity, *, top=9, date=NOW - 40 * 86400):
    return SimpleNamespace(
        entity=entity,
        name="Authored owner",
        archived=False,
        date=datetime.fromtimestamp(date, UTC),
        dialog=types.Dialog(
            peer=types.PeerUser(entity.id),
            top_message=top,
            read_inbox_max_id=0,
            read_outbox_max_id=0,
            unread_count=0,
            unread_mentions_count=0,
            unread_poll_votes_count=0,
            unread_reactions_count=0,
            notify_settings=types.PeerNotifySettings(),
        ),
    )


def test_discovery_and_live_updates_admit_the_accounts_own_chat(tmp_path):
    value = transport(tmp_path)
    own = types.User(id=1, is_self=True, first_name="Authored owner", access_hash=2)
    value._discovered(dialog(own))
    chat = value.store.chat(KEY)
    assert (chat.kind, chat.title, chat.latest_message_id) == (
        "saved",
        "Saved Messages",
        9,
    )
    assert value.store.saved_chat()[0]["activity"] == NOW - 40 * 86400
    assert value.store.recent_chats() == []
    # A live message there admits it even before discovery reached it.
    fresh = transport(tmp_path / "fresh")
    assert fresh.admit_peer(SAVED) is True
    assert fresh.store.chat(KEY).kind == "saved"
    # Telegram's service notifications stay out.
    assert fresh.admit_peer("user:777000") is False


async def test_saved_messages_take_no_ai_unseen_or_notification_work(tmp_path):
    app = saved_app(tmp_path)
    # A live message, and one Telegram may report as not outgoing, such as a
    # forward.
    app.sync.ingest(note(app, 1), live=True)
    app.sync.ingest(note(app, 2, outgoing=False, forwarded=True), live=True)
    assert [m.message_id for m in app.store.messages(KEY)] == [1, 2]
    assert not app.store.rows("SELECT 1 FROM blocks WHERE chat_key=?", (KEY,))
    assert not app.store.rows("SELECT 1 FROM jobs")
    assert not app.store.rows("SELECT 1 FROM incoming_notices")
    view = app.conversation(KEY)
    assert view["chat"]["kind"] == "saved"
    assert [m["message_id"] for m in view["messages"]] == [1, 2]
    assert (view["emotion"], view["suggestions"], view["blocks"]) == (None, None, [])
    assert (view["opening_source"], view["unread_sources"]) == (None, [])
    assert view["older"] == {"available": 0, "notice": None}
    assert view["draft"]["text"] == ""


async def test_the_newest_page_syncs_whatever_its_activity_within_30_days(tmp_path):
    app = saved_app(tmp_path)
    app.transport.messages = [
        note(app, 1, date=NOW - 40 * 86400),
        note(app, 2, date=NOW - 20 * 86400),
        note(app, 3, date=NOW - 2 * 86400),
    ]
    progress = await app.sync.scan(KEY)
    assert progress["complete"]
    # Older than the seven-day window but inside the 30-day rule, and never a
    # target for AI.
    assert [m.message_id for m in app.store.messages(KEY)] == [2, 3]
    assert not app.store.rows("SELECT 1 FROM blocks WHERE chat_key=?", (KEY,))
    # Load earlier reads Telegram past the cache, as in a private chat.
    page = await app.history(KEY, 2)
    assert [m["message_id"] for m in page] == [1]


async def test_saved_messages_send_and_reply_but_never_rewrite_or_react(tmp_path):
    app = saved_app(tmp_path)
    app.sync.ingest(note(app, 1))
    draft = app.store.draft(KEY)
    draft = app.store.mutate_draft(
        KEY,
        draft["revision"],
        lambda d: d.update(text="A note to myself", reply_to=1, undo=None),
    )
    with pytest.raises(Refused, match="private chats"):
        app.suggestions.generate(KEY, draft["revision"])
    operation = await app.delivery.submit(KEY, draft["revision"])
    sent = await app.delivery.drain(operation["id"])
    assert sent["state"] == "confirmed"
    delivered = next(iter(app.transport.deliveries.values()))["description"]
    assert (delivered.peer, delivered.text, delivered.reply_to) == (
        SAVED,
        "A note to myself",
        1,
    )
    with pytest.raises(Refused):
        await app.reactions.choose(KEY, 1, "👍", True)
    assert app.transport.reaction_calls == []


async def test_deletion_in_saved_messages_ignores_the_seven_day_window(tmp_path):
    app = saved_app(tmp_path)
    app.sync.ingest(note(app, 1, date=NOW - 10 * 86400))
    source = app.store.message(KEY, 1)
    assert app.deletion.eligible(source)
    operation = await app.deletion.submit(
        KEY,
        uuid.uuid4().hex,
        [{"message_id": 1, "revision": source.revision}],
        browser="authored",
    )
    assert operation["state"] in {"queued", "deleting", "acknowledged"}


async def test_a_note_deleted_elsewhere_reads_as_deleted(tmp_path):
    # Telegram reports a deletion there without its peer, as in a private
    # chat: the Saved Build the owner deleted from their phone stayed shown.
    app = saved_app(tmp_path)
    app.sync.ingest(note(app, 1, media="document"))
    app.sync.ingest(note(app, 2))
    app.sync.deleted(None, [1])
    deleted = app.store.message(KEY, 1)
    assert (deleted.deleted, deleted.text, deleted.media) == (True, "", None)
    assert not app.store.message(KEY, 2).deleted
    assert not app.store.rows("SELECT 1 FROM blocks WHERE chat_key=?", (KEY,))
    assert not app.store.rows("SELECT 1 FROM jobs")


def test_the_saved_list_and_conversation_routes(tmp_path):
    directory = tmp_path / "app"
    server = create_app(Settings(data=directory))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, directory)
        assistant = server.state.accounts.get(1)
        # The third list's one row is there before discovery reaches it.
        rows = client.get("/api/accounts/1/chats?kind=saved")
        assert rows.status_code == 200, rows.text
        assert [
            (r["peer"], r["kind"], r["title"], r["unseen"]) for r in rows.json()
        ] == [(SAVED, "saved", "Saved Messages", False)]
        assistant.sync.ingest(
            note(assistant, 7, date=assistant.store.clock(), text="Pick up the keys")
        )
        preview = client.get("/api/accounts/1/chats?kind=saved").json()[0]["preview"]
        # The account's own notes need no "You:".
        assert preview["original"] == "Pick up the keys"
        private = client.get("/api/accounts/1/chats?kind=private").json()
        assert SAVED not in [r["peer"] for r in private]
        opened = client.get(f"/api/accounts/1/chats/{SAVED}")
        assert opened.status_code == 200, opened.text
        assert opened.json()["chat"]["kind"] == "saved"
        assert [m["message_id"] for m in opened.json()["messages"]] == [7]


def test_a_chat_row_for_another_peer_is_never_saved(tmp_path):
    store = Store(tmp_path / "db.sqlite3", account_id=1, clock=lambda: NOW)
    store.put_chat(Chat(account_id=1, peer="user:42", title="Authored", kind="private"))
    store.ensure_saved()
    assert [c["peer"] for c in store.saved_chat()] == [SAVED]


async def test_gifs_and_stickers_open_in_saved_messages(tmp_path):
    app = saved_app(tmp_path)
    # The picker's library belongs to the account's own Saved Messages too
    # (Q17), never to a "saved" row naming someone else.
    assert app.catalog.cached(KEY, "browser-a") is not None
    app.store.put_chat(
        Chat(account_id=1, peer="user:43", title="Not saved", kind="saved")
    )
    with pytest.raises(Refused):
        app.catalog.cached("1/user:43", "browser-a")
