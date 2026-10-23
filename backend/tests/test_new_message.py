"""TL7: New Message (ADR 51 Q7, Q16-Q18). Authored people and fake
transports only."""

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.storage.store import WINDOW
from enloq.transport.telethon import person
from fastapi.testclient import TestClient
from telethon import types

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message


def people(app):
    app.transport.people = {
        "contacts": [
            {
                "peer": "user:50",
                "name": "Zoe Contact",
                "username": "zoe_c",
                "photo_id": None,
            },
            {
                "peer": "user:42",
                "name": "Authored conversation",
                "username": "authored",
                "photo_id": None,
            },
            {
                "peer": "user:51",
                "name": "Adam Contact",
                "username": None,
                "photo_id": "9",
            },
        ],
        "search": [
            {
                "peer": "user:60",
                "name": "Zoe Public",
                "username": "zoe_public",
                "photo_id": None,
            },
            {
                "peer": "user:50",
                "name": "Zoe Contact",
                "username": "zoe_c",
                "photo_id": None,
            },
        ],
    }


async def test_it_lists_chats_of_any_age_then_contacts_and_searches(tmp_path):
    app = fixture(tmp_path)
    people(app)
    now = app.store.clock()
    # An older chat outside the seven-day list still comes up.
    app.store.put_chat(
        Chat(account_id=1, peer="user:43", title="Older friend", kind="private"),
        activity=now - WINDOW - 86400,
    )
    app.sync.ingest(message(app, 1))
    app.store.put_chat(
        Chat(account_id=1, peer="user:777", title="A bot", kind="bot"),
        activity=now,
    )
    found = await app.new_message.search("")
    assert [value["peer"] for value in found["chats"]] == ["user:42", "user:43"]
    # A contact with a chat stays among the chats, with its @username.
    assert found["chats"][0]["username"] == "authored"
    assert [value["peer"] for value in found["contacts"]] == ["user:51", "user:50"]
    assert found["search"] == []
    # Typing filters both, and three characters add Telegram's search.
    found = await app.new_message.search("zo")
    assert found["chats"] == [] and [v["peer"] for v in found["contacts"]] == [
        "user:50"
    ]
    assert found["search"] == []
    found = await app.new_message.search("@zoe")
    assert [v["peer"] for v in found["search"]] == ["user:60"]
    found = await app.new_message.search("older")
    assert [v["peer"] for v in found["chats"]] == ["user:43"]


async def test_opening_someone_admits_their_chat_without_making_it_recent(
    tmp_path,
):
    app = fixture(tmp_path)
    people(app)
    opened = await app.new_message.open("user:60")
    assert opened["peer"] == "user:60" and opened["title"] == "Zoe Public"
    assert opened["kind"] == "private"
    assert all(row["peer"] != "user:60" for row in app.store.recent_chats())
    # Its conversation opens, and a message makes it recent.
    assert app.conversation("1/user:60")["messages"] == []
    app.sync.ingest(message(app, 5).model_copy(update={"peer": "user:60"}))
    assert any(row["peer"] == "user:60" for row in app.store.recent_chats())
    # An existing chat opens as it is.
    before = app.store.chat("1/user:42")
    assert (await app.new_message.open("user:42"))["title"] == before.title
    with pytest.raises(Refused, match="Only a person"):
        await app.new_message.open("user:99")
    app.store.put_chat(
        Chat(account_id=1, peer="user:1", title="Saved Messages", kind="saved")
    )
    with pytest.raises(Refused, match="Only a person"):
        await app.new_message.open("user:1")


def test_only_people_are_offered():
    def user(identity, **flags):
        return types.User(id=identity, first_name="Authored", access_hash=1, **flags)

    assert person(user(5, username="five"), 1) == {
        "peer": "user:5",
        "name": "Authored",
        "username": "five",
        "photo_id": None,
    }
    for value in (
        user(6, bot=True),
        user(7, deleted=True),
        user(8, min=True),
        user(1),
        user(777000),
        types.Channel(
            id=9,
            title="A channel",
            photo=types.ChatPhotoEmpty(),
            date=None,
            access_hash=1,
        ),
    ):
        assert person(value, 1) is None


def test_the_routes(tmp_path):
    served = create_app(Settings(data=tmp_path / "app"))
    with TestClient(served, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        conversation = served.state.accounts.get(1)
        people(conversation)
        found = client.get("/api/accounts/1/new-message?query=zoe")
        assert found.status_code == 200, found.text
        assert [v["peer"] for v in found.json()["search"]] == ["user:60"]
        headers = {"Origin": "http://127.0.0.1:8765"}
        opened = client.post(
            "/api/accounts/1/new-message/user:60", json={}, headers=headers
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["peer"] == "user:60"
        refused = client.post(
            "/api/accounts/1/new-message/chat:5", json={}, headers=headers
        )
        assert refused.status_code == 400
        assert client.get("/api/accounts/1/chats/user:60").status_code == 200
