import asyncio

import pytest

from backend.tests.test_push import enable, ready
from backend.tests.test_push import rig as rig


def presence(
    client,
    *,
    tab="a" * 32,
    account=1,
    peer="user:42",
    focused=True,
    audio=True,
    **extra,
):
    response = client.post(
        "/api/notifications/attention",
        json={
            "tab": tab,
            "account_id": account,
            "peer": peer,
            "focused": focused,
            "audio": audio,
            **extra,
        },
    )
    assert response.status_code == 200
    return response.json()


def claim(client, identity, tab="a" * 32):
    return client.post(
        "/api/notifications/claim", json={"id": identity, "tab": tab}
    ).json()


async def test_matching_active_chat_is_silent_even_without_audio_or_at_history(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    presence(clients[0], audio=False)
    ready(conversation, now)
    presence(clients[0], audio=False)
    await hub.tick()
    assert not hub.transport.sent
    assert not presence(clients[0])["events"]
    assert hub.registry.rows("SELECT channel,state FROM notification_deliveries")[
        0
    ] == {"channel": "silent", "state": "sent"}


async def test_other_active_chat_chimes_once_across_tabs_and_browsers(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[1])
    ready(conversation, now)
    presence(clients[0], peer="user:101")
    presence(clients[1], focused=False, tab="b" * 32)
    await hub.tick()
    identifiers = presence(clients[0], peer="user:101")["events"]
    assert len(identifiers) == 1 and not hub.transport.sent
    assert claim(clients[1], identifiers[0], "b" * 32) is None
    assert claim(clients[0], identifiers[0], "c" * 32) is None
    first = claim(clients[0], identifiers[0])
    assert first["account_id"] == 1 and first["peer"] == "user:42"
    assert claim(clients[0], identifiers[0]) is None
    await hub.tick()
    assert not hub.transport.sent


@pytest.mark.parametrize("sound", [True, False])
async def test_inactive_matching_tab_uses_push_and_sound_preference(rig, sound):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    clients[0].post("/api/notifications/preferences", json={"sound": sound})
    ready(conversation, now)
    presence(clients[0], focused=False)
    await hub.tick()
    assert len(hub.transport.sent) == 1
    assert hub.transport.sent[0][1]["silent"] is (not sound)
    assert not presence(clients[0], focused=False)["events"]


async def test_background_without_push_can_chime_and_disabled_sound_cannot(rig):
    app, clients, hub, conversation, now = rig
    ready(conversation, now)
    presence(clients[0], focused=False)
    await hub.tick()
    events = presence(clients[0], focused=False)["events"]
    assert len(events) == 1
    clients[0].post("/api/notifications/preferences", json={"sound": False})
    assert claim(clients[0], events[0]) is None
    await hub.tick()
    assert not presence(clients[0], focused=False)["events"]
    assert not hub.transport.sent


async def test_chime_to_push_focus_handoff_uses_one_event_and_no_double_audio(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    ready(conversation, now)
    presence(clients[0], peer="user:101")
    await hub.tick()
    event = presence(clients[0], peer="user:101")["events"][0]
    presence(clients[0], focused=False)
    assert claim(clients[0], event) is None
    await hub.tick()
    assert len(hub.transport.sent) == 1 and hub.transport.sent[0][1]["id"] == event
    assert claim(clients[0], event) is None


async def test_queued_chime_becomes_silent_on_focus_matching_chat(rig):
    app, clients, hub, conversation, now = rig
    ready(conversation, now)
    presence(clients[0], peer="user:101")
    await hub.tick()
    event = presence(clients[0], peer="user:101")["events"][0]
    presence(clients[0])
    await hub.tick()
    assert claim(clients[0], event) is None
    assert (
        hub.registry.rows(
            "SELECT channel FROM notification_deliveries WHERE id=?", (event,)
        )[0]["channel"]
        == "silent"
    )


async def test_stale_attention_and_audio_blocking_do_not_suppress_forever(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[1])
    ready(conversation, now)
    presence(clients[0], peer="user:101", audio=False)
    await hub.tick()
    assert not hub.transport.sent
    assert not hub.registry.rows("SELECT * FROM notification_deliveries")
    now[0] += 13
    await hub.tick()
    assert len(hub.transport.sent) == 1


async def test_claim_revalidates_source_and_forgotten_browser(rig):
    app, clients, hub, conversation, now = rig
    ready(conversation, now)
    presence(clients[0], peer="user:101")
    await hub.tick()
    event = presence(clients[0], peer="user:101")["events"][0]
    conversation.sync.deleted("user:42", [1])
    assert claim(clients[0], event) is None
    identity = clients[0].get("/api/notifications").json()["browser"]
    hub.auth.revoke(identity)
    assert (
        clients[0]
        .post(
            "/api/notifications/attention",
            json={"tab": "a" * 32, "focused": True, "audio": True},
        )
        .status_code
        == 401
    )


async def test_permission_loss_retires_destination_and_allows_background_sound(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    ready(conversation, now)
    presence(clients[0], focused=False, push_permission="denied")
    await hub.tick()
    assert hub.active() is None and not hub.transport.sent
    assert len(presence(clients[0], focused=False)["events"]) == 1


async def test_sound_off_active_other_chat_stays_silent(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    clients[0].post("/api/notifications/preferences", json={"sound": False})
    ready(conversation, now)
    presence(clients[0], peer="user:101")
    await asyncio.gather(hub.tick(), hub.tick())
    assert not hub.transport.sent
    assert (
        len(
            hub.registry.rows(
                "SELECT id FROM notification_deliveries WHERE state='sent'"
            )
        )
        == 1
    )
