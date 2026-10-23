"""Deleted messages keep their last content, hidden on request (ADR 52)."""

import time
import uuid

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import MediaInfo, Refused
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"
CHAT = "/api/accounts/1/chats/user:101"


def shown(app, key=KEY):
    return {m["message_id"]: m for m in app.conversation(key)["messages"]}


def test_deleted_message_shows_its_last_content_until_hidden(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, text="First words"), target=False)
    app.sync.ingest(
        message(app, 1, revision=2, text="Edited words", native_edited=True),
        target=False,
    )
    app.sync.ingest(message(app, 2, text="Still here"), target=False)
    app.sync.deleted("user:42", [1])
    deleted = shown(app)[1]
    # What it last said, with the edit before it as its only earlier version.
    assert deleted["deleted"] and deleted["text"] == "Edited words"
    assert deleted["history_count"] == 1 and deleted["deleted_version"]
    assert deleted["reactions"] is None and deleted["viewed_content"] is None
    assert not deleted["media_available"]
    assert shown(app)[2]["text"] == "Still here"
    # Only a deleted message can be hidden, and hiding is final.
    with pytest.raises(Refused, match="Only deleted"):
        app.store.history.hide(KEY, [2])
    assert app.store.history.hide(KEY, [1]) == [1]
    assert list(shown(app)) == [2]
    # A Reply Quote still shows a hidden target's captured content.
    quoted = app.store.history.annotate([app.store.message(KEY, 1)], quotes=True)
    assert [(m["deleted"], m["text"]) for m in quoted] == [(True, "Edited words")]
    # The hidden row expires with its message.
    app.store.clock = lambda: 2_000_000_000 + 31 * 86400
    app.store.expire()
    assert not app.store.rows("SELECT 1 FROM hidden_messages")


def test_deletion_seen_before_any_content_and_saved_notes_are_left_out(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1), target=False)
    with app.store.transaction() as db:
        db.execute("DELETE FROM message_versions")
        db.execute(
            "UPDATE messages SET deleted=1,body=json_set(body,'$.deleted',json('true'))"
        )
    assert shown(app) == {}
    app.store.ensure_saved()
    saved = app.store.saved_key
    note = message(app, 5, text="A note").model_copy(
        update={"peer": "user:1", "outgoing": True, "author_peer": "user:1"}
    )
    app.sync.ingest(note, target=False)
    app.sync.deleted("user:1", [5])
    assert app.conversation(saved)["messages"] == []
    assert app.previews.saved()[0].preview.original == ""


def test_reply_quote_of_a_deleted_target_shows_its_capture(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, text="The question"), target=False)
    app.sync.ingest(message(app, 2, text="The answer", reply_to=1), target=False)
    app.sync.deleted("user:42", [1])
    app.store.history.hide(KEY, [1])
    import asyncio

    resolved = asyncio.run(app.replies.resolve(KEY, [2]))
    assert [
        (t["message_id"], t["deleted"], t["text"]) for t in resolved["targets"]
    ] == [(1, True, "The question")]
    with app.store.transaction() as db:
        db.execute("DELETE FROM message_versions")
    resolved = asyncio.run(app.replies.resolve(KEY, [2]))
    assert resolved["targets"] == [] and resolved["missing"] == [1]


def test_group_deleted_original_keeps_its_filter_flags(tmp_path):
    from backend.tests.test_group_deletion import group

    app, key = group(tmp_path, count=2, author_peer="user:9")
    app.sync.deleted("chat:42", [2])
    values = app.briefings.annotate(app.store.messages(key))
    assert [(v["message_id"], v["deleted"], v["text"]) for v in values] == [
        (1, False, "Authored original 1"),
        (2, True, "Authored original 2"),
    ]
    assert all("group_filter" in v for v in values)


def test_hidden_edit_keeps_a_version_only_for_new_wording(tmp_path):
    """ADR 52 Q6: Telegram's edit_hide marks a server-side change."""
    app = fixture(tmp_path)
    photo = MediaInfo(mime="image/jpeg", width=10, height=10)
    app.store.apply_message(
        message(app, 1, media="image", attachment_id="photo:1", media_info=photo)
    )
    # Only media details change: replaced quietly.
    app.store.apply_message(
        message(
            app,
            1,
            revision=2,
            media="image",
            attachment_id="photo:2",
            media_info=photo,
            edit_hidden=True,
        )
    )
    current = app.store.message(KEY, 1)
    assert current.attachment_id == "photo:2" and not current.native_edited
    assert not app.store.rows("SELECT 1 FROM message_versions")
    # New wording keeps its earlier version, still without the pencil.
    app.store.apply_message(
        message(
            app,
            1,
            revision=3,
            text="New wording",
            media="image",
            attachment_id="photo:2",
            media_info=photo,
            edit_hidden=True,
        )
    )
    assert not app.store.message(KEY, 1).native_edited
    assert len(app.store.rows("SELECT 1 FROM message_versions")) == 1


def test_an_outgoing_flip_alone_is_never_an_edit(tmp_path):
    """ADR 52 Q18."""
    app = fixture(tmp_path)
    app.store.apply_message(message(app, 1, outgoing=True))
    app.store.apply_message(message(app, 1, outgoing=False))
    current = app.store.message(KEY, 1)
    assert not current.outgoing and not current.native_edited
    assert current.revision == 1
    assert not app.store.rows("SELECT 1 FROM message_versions")


@pytest.fixture
def served(tmp_path):
    directory = tmp_path / "app"
    server = create_app(Settings(data=directory))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        assistant = server.state.accounts.get(1)
        assistant.transport.messages = assistant.store.messages("1/user:101")
        trust_browser(client, directory)
        yield assistant, client


def settle(client, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = client.get(f"{CHAT}/deletions/{identity}").json()
        if value["state"] not in {"queued", "deleting"}:
            return value
        time.sleep(0.01)
    pytest.fail("The deletion did not settle")


def test_own_deletion_leaves_and_another_shows_until_hidden(served):
    assistant, client = served
    key = "1/user:101"
    source = assistant.store.message(key, 2)
    created = client.post(
        f"{CHAT}/deletions",
        json={
            "id": uuid.uuid4().hex,
            "targets": [{"message_id": 2, "revision": source.revision}],
        },
    )
    assert created.status_code == 200, created.text
    assert settle(client, created.json()["id"])["state"] == "completed"
    assistant.sync.deleted("user:101", [2])
    assert assistant.store.message(key, 2).deleted
    # Deleted through Enloq: gone at once (Q8).
    view = client.get(CHAT).json()["messages"]
    assert 2 not in {m["message_id"] for m in view}
    # Deleted elsewhere: its last content, until hidden (Q7, Q11).
    text = assistant.store.message(key, 1).text
    assistant.sync.deleted("user:101", [1])
    view = {m["message_id"]: m for m in client.get(CHAT).json()["messages"]}
    assert view[1]["deleted"] and view[1]["text"] == text
    assistant.sync.ingest(
        message(assistant, 9, text="Live").model_copy(update={"peer": "user:101"}),
        target=False,
    )
    refused = client.post(f"{CHAT}/hidden", json={"identities": [9]})
    assert refused.status_code == 400
    hidden = client.post(f"{CHAT}/hidden", json={"identities": [1]})
    assert hidden.json() == {"hidden": [1]}
    view = client.get(CHAT).json()["messages"]
    assert 1 not in {m["message_id"] for m in view}
    assert client.post(f"{CHAT}/hidden", json={"identities": []}).status_code == 422


async def test_saved_notes_read_from_the_update_stream_are_outgoing(tmp_path):
    """ADR 52 Q18: getDifference reports the owner's own Saved notes with
    out=False while a fresh history read says out=True."""
    from datetime import UTC, datetime

    from enloq.transport.telethon import PersonalTransport
    from telethon import types

    app = fixture(tmp_path)
    transport = object.__new__(PersonalTransport)
    transport.account_id, transport.store = 1, app.store
    date = datetime.fromtimestamp(app.store.clock(), UTC)
    note = types.Message(id=5, peer_id=types.PeerUser(1), date=date, message="Note")
    assert (await transport.normalize(note)).outgoing
    incoming = types.Message(id=6, peer_id=types.PeerUser(42), date=date, message="Hi")
    assert not (await transport.normalize(incoming)).outgoing
