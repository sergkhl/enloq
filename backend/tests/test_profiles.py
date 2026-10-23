"""ADR 46 Q11: the Correspondent Profile is read from Telegram on each opening
through the session owner and stored nowhere."""

from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat, Unavailable
from enloq.transport.telethon import PersonalTransport
from fastapi.testclient import TestClient
from telethon import functions

from backend.tests.auth_helpers import trust_browser

URL = "/api/accounts/1/chats/{peer}/profile"
PROFILE = {
    "first_name": "Morgan",
    "last_name": "Lee",
    "username": "authored_morgan",
    "phone": "15550100",
    "bio": "Authored bio.",
}


@pytest.fixture
def served(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(account_id=1, peer="user:42", title="Morgan", kind="private")
        )
        conversation.store.put_chat(
            Chat(account_id=1, peer="chat:7", title="Group", kind="group")
        )
        conversation.transport.profiles["user:42"] = dict(PROFILE)
        yield client, conversation


def snapshot_of(store):
    """Every row the service keeps, to show a profile read adds none."""
    tables = [
        row["name"]
        for row in store.rows(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    return {
        table: store.rows(f"SELECT * FROM {table}")  # noqa: S608 - authored names
        for table in tables
    }


def test_each_opening_reads_the_profile_and_keeps_nothing(served):
    client, conversation = served
    before = snapshot_of(conversation.store)
    first = client.get(URL.format(peer="user:42"))
    assert first.status_code == 200
    assert first.json() == {
        "name": "Morgan Lee",
        "username": "authored_morgan",
        "phone": "15550100",
        "bio": "Authored bio.",
    }
    assert first.headers["cache-control"] == "no-store"
    client.get(URL.format(peer="user:42"))
    # Two openings, two reads: nothing is cached or stored.
    assert conversation.transport.profile_reads == ["user:42", "user:42"]
    assert snapshot_of(conversation.store) == before
    # Without a shared phone or bio, those stay empty and the chat's title
    # stands in for a missing name.
    conversation.transport.profiles["user:42"] = {
        "first_name": "",
        "last_name": "",
        "username": None,
        "phone": None,
        "bio": None,
    }
    assert client.get(URL.format(peer="user:42")).json() == {
        "name": "Morgan",
        "username": None,
        "phone": None,
        "bio": None,
    }


def test_groups_offline_and_failures_are_refused(served):
    client, conversation = served
    group = client.get(URL.format(peer="chat:7"))
    assert group.status_code == 400
    assert group.json()["detail"] == "Only private conversations have a profile"
    conversation.transport.connected = False
    offline = client.get(URL.format(peer="user:42"))
    assert offline.json()["detail"] == (
        "Telegram disconnected; the profile is unavailable"
    )
    conversation.transport.connected = True
    del conversation.transport.profiles["user:42"]
    failed = client.get(URL.format(peer="user:42"))
    assert failed.status_code == 400
    assert failed.json()["detail"] == "Telegram did not supply this profile right now"
    assert conversation.transport.profile_reads == ["user:42"]


async def test_the_live_read_takes_the_exact_user_and_its_bio():
    calls = []

    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            calls.append(("entity", peer.user_id))
            return "input-user"

        async def __call__(self, request):
            calls.append(request)
            other = SimpleNamespace(
                id=7, first_name="Other", last_name=None, username=None, phone=None
            )
            user = SimpleNamespace(
                id=42,
                first_name="Morgan",
                last_name=None,
                username=None,
                usernames=[
                    SimpleNamespace(username="inactive", active=False),
                    SimpleNamespace(username="morgan_live", active=True),
                ],
                phone=None,
            )
            return SimpleNamespace(
                users=[other, user], full_user=SimpleNamespace(about="Live bio")
            )

    transport = object.__new__(PersonalTransport)
    transport.client, transport.verified = Client(), True
    profile = await transport.profile("user:42")
    assert profile == {
        "first_name": "Morgan",
        "last_name": "",
        "username": "morgan_live",
        "phone": None,
        "bio": "Live bio",
    }
    assert calls[0] == ("entity", 42)
    assert isinstance(calls[1], functions.users.GetFullUserRequest)
    assert calls[1].id == "input-user"


async def test_the_live_read_refuses_without_the_user():
    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            return "input-user"

        async def __call__(self, request):
            return SimpleNamespace(users=[], full_user=SimpleNamespace(about=None))

    transport = object.__new__(PersonalTransport)
    transport.client, transport.verified = Client(), True
    with pytest.raises(Unavailable):
        await transport.profile("user:42")
    with pytest.raises(Unavailable):
        await transport.profile("chat:7")


# ADR 47 Q7: the Group Profile reads a group's title, public username, member
# count and description on each opening, and stores none of them.
GROUP_URL = "/api/accounts/1/chats/{peer}/group-profile"


def test_each_group_opening_reads_the_group_and_keeps_nothing(served):
    client, conversation = served
    conversation.store.groups.observe(
        "1/chat:7", access="available", reason="authored_native_evidence"
    )
    conversation.transport.group_profiles["chat:7"] = {
        "title": "Weekend planning",
        "username": None,
        "members": 12,
        "description": "Authored group description.",
    }
    before = snapshot_of(conversation.store)
    first = client.get(GROUP_URL.format(peer="chat:7"))
    assert first.status_code == 200, first.text
    assert first.json() == {
        "title": "Weekend planning",
        "username": None,
        "members": 12,
        "description": "Authored group description.",
    }
    assert first.headers["cache-control"] == "no-store"
    client.get(GROUP_URL.format(peer="chat:7"))
    assert conversation.transport.group_profile_reads == ["chat:7", "chat:7"]
    assert snapshot_of(conversation.store) == before
    # A missing title falls back to the cached chat's; a count that isn't
    # one is left out.
    conversation.transport.group_profiles["chat:7"] = {"title": "", "members": -1}
    assert client.get(GROUP_URL.format(peer="chat:7")).json() == {
        "title": "Group",
        "username": None,
        "members": None,
        "description": None,
    }


def test_a_group_profile_refuses_chats_lost_groups_and_failures(served):
    client, conversation = served
    person = client.get(GROUP_URL.format(peer="user:42"))
    assert person.json()["detail"] == "Only groups have a group profile"
    conversation.store.groups.observe(
        "1/chat:7", access="lost", reason="read_forbidden"
    )
    lost = client.get(GROUP_URL.format(peer="chat:7"))
    assert lost.json()["detail"] == "Group access was lost. Cached details remain."
    conversation.store.groups.observe(
        "1/chat:7", access="available", reason="authored_native_evidence"
    )
    conversation.transport.connected = False
    offline = client.get(GROUP_URL.format(peer="chat:7"))
    assert offline.json()["detail"] == (
        "Telegram disconnected; the profile is unavailable"
    )
    conversation.transport.connected = True
    failed = client.get(GROUP_URL.format(peer="chat:7"))
    assert failed.json()["detail"] == (
        "Telegram did not supply this group's profile right now"
    )
    assert conversation.transport.group_profile_reads == ["chat:7"]


async def test_the_live_group_read_counts_members_of_both_kinds():
    from telethon import types

    calls = []
    channel = types.Channel(
        id=42,
        title="Supergroup discussion",
        photo=types.ChatPhotoEmpty(),
        date=None,
        username=None,
        usernames=[
            types.Username(username="old_name", active=False),
            types.Username(username="weekend_live", active=True),
        ],
        megagroup=True,
    )
    basic = types.Chat(
        id=7,
        title="Basic discussion",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=None,
        version=1,
    )

    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            calls.append(("entity", peer.channel_id))
            return "input-channel"

        async def __call__(self, request):
            calls.append(request)
            if isinstance(request, functions.channels.GetFullChannelRequest):
                return SimpleNamespace(
                    chats=[channel],
                    full_chat=SimpleNamespace(
                        participants_count=1200, about="Live description"
                    ),
                )
            return SimpleNamespace(
                chats=[basic],
                full_chat=SimpleNamespace(
                    participants=SimpleNamespace(participants=[1, 2, 3]), about=""
                ),
            )

    transport = object.__new__(PersonalTransport)
    transport.client, transport.verified = Client(), True
    assert await transport.group_profile("channel:42") == {
        "title": "Supergroup discussion",
        "username": "weekend_live",
        "members": 1200,
        "description": "Live description",
    }
    assert calls[0] == ("entity", 42)
    assert isinstance(calls[1], functions.channels.GetFullChannelRequest)
    assert await transport.group_profile("chat:7") == {
        "title": "Basic discussion",
        "username": None,
        "members": 3,
        "description": None,
    }
    assert isinstance(calls[2], functions.messages.GetFullChatRequest)
    assert calls[2].chat_id == 7
    with pytest.raises(Unavailable):
        await transport.group_profile("user:42")
