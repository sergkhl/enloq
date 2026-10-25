"""Reminders through member/account boundaries and existing push delivery."""

import asyncio

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat
from enloq.domain.reminders import DigestSettings, ReminderRule
from enloq.storage.members import OWNER
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_members import add_member
from backend.tests.test_push import enable
from backend.tests.test_reminders import stamp

PATH = "/api/accounts/1/chats/user:42/reminder"
KEY = "1/user:42"


@pytest.fixture
def rig(tmp_path):
    server = create_app(Settings(data=tmp_path))
    client = TestClient(server, base_url="http://127.0.0.1:8765")
    trust_browser(client, tmp_path)
    now = [stamp("2026-10-25T08:00:00+00:00")]
    manager = server.state.accounts
    manager.reminders.clock = server.state.notifications.clock = lambda: now[0]
    app = manager.get(1)
    app.store.clock = lambda: now[0]
    app.store.put_chat(
        Chat(
            account_id=1,
            peer="user:42",
            title="Authored contact",
            kind="private",
            ai_eligible=True,
        )
    )
    yield server, client, app, now
    client.close()


def save(client, **values):
    rule = ReminderRule(timezone="UTC", language="en", weekday=6, **values)
    response = client.post(PATH, json={**rule.model_dump(), "revision": 0})
    assert response.status_code == 200, response.text
    return response.json()


def test_api_revision_preview_delete_and_no_calls_from_list_or_settings(rig):
    server, client, app, now = rig
    assert client.get(PATH).json()["schedule"] is None
    rule = ReminderRule(timezone="UTC", language="en", weekday=6)
    preview = client.post(PATH + "/preview", json=rule.model_dump())
    assert preview.status_code == 200
    saved = save(client)
    assert saved["next_at"] == preview.json()["next_at"]
    assert (
        client.post(PATH, json={**rule.model_dump(), "revision": 0}).status_code == 409
    )
    assert client.get("/api/reminders").json()["items"][0]["account_id"] == 1
    settings = client.get("/api/reminders/settings").json()
    assert settings["time"] == "09:00" and settings["timezone"] == "UTC"
    settings["time"] = "10:15"
    assert client.post("/api/reminders/settings", json=settings).status_code == 200
    assert client.post("/api/reminders/settings", json=settings).status_code == 409
    assert (
        client.request("DELETE", PATH, json={"revision": saved["revision"]}).status_code
        == 200
    )
    assert client.get("/api/reminders").json() == {"items": []}
    assert not app.engine.calls and not app.decider.calls


def test_daily_digest_claims_once_and_edit_invalidates_queued_target(rig):
    server, client, app, now = rig
    hub = server.state.notifications
    enable(client)
    saved = save(client)
    now[0] = saved["next_at"]
    asyncio.run(hub.tick())
    first = hub.transport.sent[:]
    assert len(first) == 1
    identity = first[0][1]["id"]
    assert client.post("/api/notifications/open", json={"id": identity}).json() == {
        "kind": "reminders"
    }
    asyncio.run(hub.tick())
    now[0] += 86400
    asyncio.run(hub.tick())
    assert hub.transport.sent == first
    assert not app.engine.calls and not app.decider.calls
    client.request("DELETE", PATH, json={"revision": saved["revision"]})
    assert client.post("/api/notifications/open", json={"id": identity}).json() == {
        "kind": "unavailable"
    }


def test_exact_alert_excluded_from_digest_and_occurrence_target_goes_stale(rig):
    server, client, app, now = rig
    hub = server.state.notifications
    enable(client)
    saved = save(client, delivery="exact", time="08:30")
    now[0] = saved["next_at"]
    asyncio.run(hub.tick())
    identity = hub.transport.sent[0][1]["id"]
    occurrence = app.reminders.view(KEY).occurrence.id
    assert client.post("/api/notifications/open", json={"id": identity}).json() == {
        "kind": "reminder",
        "account_id": 1,
        "peer": "user:42",
        "occurrence": occurrence,
    }
    now[0] = stamp("2026-10-25T09:00:00+00:00")
    asyncio.run(hub.tick())
    assert len(hub.transport.sent) == 1
    assert (
        client.post(PATH + "/dismiss", json={"occurrence": occurrence}).status_code
        == 200
    )
    assert client.post("/api/notifications/open", json={"id": identity}).json() == {
        "kind": "unavailable"
    }
    assert (
        client.post(
            PATH + "/review", json={"occurrence": occurrence, "revision": 0}
        ).status_code
        == 409
    )


def test_member_settings_list_and_claims_do_not_cross_accounts(rig):
    server, client, app, now = rig
    manager = server.state.accounts
    add_member(server)
    friend = manager._create(manager.registry.get(2)).assistant
    friend.store.clock = lambda: now[0]
    friend.store.put_chat(
        Chat(
            account_id=2,
            peer="user:42",
            title="Other authored contact",
            kind="private",
            ai_eligible=True,
        )
    )
    manager.reminders.save_settings("friend", DigestSettings(timezone="Asia/Tokyo"))
    friend.reminders.edit(
        "2/user:42", 0, ReminderRule(timezone="UTC", language="en", weekday=6)
    )
    assert client.get("/api/reminders").json() == {"items": []}
    assert client.get("/api/reminders/settings").json()["timezone"] == "UTC"
    assert client.get("/api/accounts/2/chats/user:42/reminder").status_code == 400
    assert manager.reminders.items("friend")[0]["account_id"] == 2
    assert manager.reminders.items(OWNER) == []


def test_global_off_on_between_ticks_resets_future_without_backfill(rig):
    server, client, app, now = rig
    saved = save(client)
    now[0] = saved["next_at"]
    app.reminders.tick()
    assert app.reminders.view(KEY).occurrence
    registry = server.state.accounts.registry
    registry.set_suggestions_enabled(False)
    now[0] += 21 * 86400
    registry.set_suggestions_enabled(True)
    app.reminders.tick()
    assert app.reminders.view(KEY).occurrence is None
    assert app.reminders.view(KEY).next_at > now[0]


def test_combined_digest_across_two_member_accounts(rig):
    server, client, app, now = rig
    manager, hub = server.state.accounts, server.state.notifications
    enable(client)
    manager.registry.register(3, "authored-three.session")
    other = manager._create(manager.registry.get(3)).assistant
    other.store.clock = lambda: now[0]
    other.store.put_chat(
        Chat(
            account_id=3,
            peer="user:42",
            title="Authored other account",
            kind="private",
            ai_eligible=True,
        )
    )
    saved = save(client)
    other.reminders.edit(
        "3/user:42", 0, ReminderRule(timezone="UTC", language="en", weekday=6)
    )
    now[0] = saved["next_at"]
    asyncio.run(hub.tick())
    assert len(hub.transport.sent) == 1
    identity = hub.transport.sent[0][1]["id"]
    event = client.post("/api/notifications/validate", json={"id": identity}).json()
    assert event["count"] == 2 and event["account"] == "All accounts"
    assert len(client.get("/api/reminders").json()["items"]) == 2


def test_restart_retains_notification_claims_and_account_removal_purges_targets(rig):
    server, client, app, now = rig
    enable(client)
    saved = save(client)
    now[0] = saved["next_at"]
    asyncio.run(server.state.notifications.tick())
    restarted = create_app(server.state.settings)
    hub = restarted.state.notifications
    hub.clock = restarted.state.accounts.reminders.clock = lambda: now[0]
    restarted.state.accounts.get(1).store.clock = lambda: now[0]
    asyncio.run(hub.tick())
    assert hub.transport.sent == []
    assert restarted.state.accounts.get(1).reminders.view(KEY).occurrence
    restarted.state.accounts.reminders.remove_account(1)
    assert (
        restarted.state.accounts.registry.rows("SELECT * FROM reminder_notices") == []
    )
