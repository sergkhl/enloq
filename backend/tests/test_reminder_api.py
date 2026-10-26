"""Reminders through member/account boundaries and existing push delivery."""

import asyncio

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat
from enloq.domain.reminders import LEAD, DigestSettings, ReminderRule
from enloq.storage.members import OWNER
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_members import add_member
from backend.tests.test_push import enable
from backend.tests.test_reminders import stamp

PATH = "/api/accounts/1/chats/user:42/reminders"
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


def body(**values):
    values.setdefault("start", "2026-10-25")
    values.setdefault("weekday", 6)
    return ReminderRule(timezone="UTC", language="English", **values).model_dump()


def create(client, **values):
    response = client.post(PATH, json=body(**values))
    assert response.status_code == 200, response.text
    return response.json()


def due(server, now, saved):
    now[0] = saved["next_at"]
    asyncio.run(server.state.notifications.tick())


def test_schedules_preview_list_edit_remove_and_no_model_calls(rig):
    server, client, app, now = rig
    assert client.get(PATH).json() == {"items": [], "language": None}
    preview = client.post(PATH + "/preview", json=body(idea="About oct 23"))
    assert preview.json() == {
        "next_at": stamp("2026-10-25T09:00:00+00:00"),
        "kept": {"day": "2026-10-23", "state": "kept", "count": 0},
    }
    ahead = client.post(PATH + "/preview", json=body(idea="About 2026-10-28"))
    assert ahead.json()["kept"] == {"day": "2026-10-28", "state": "waiting", "count": 0}
    first = create(client)
    second = create(client, delivery="exact", time="18:30", idea="Ask [kindly]")
    assert first["next_at"] == preview.json()["next_at"]
    listed = client.get(PATH).json()["items"]
    assert [item["id"] for item in listed] == [first["id"], second["id"]]
    assert client.get(f"{PATH}/{second['id']}").json()["schedule"]["idea"] == (
        "Ask [kindly]"
    )
    edited = client.post(
        f"{PATH}/{first['id']}", json={**body(repeat="monthly"), "revision": 1}
    )
    assert edited.status_code == 200 and edited.json()["revision"] == 2
    stale = client.post(f"{PATH}/{first['id']}", json={**body(), "revision": 1})
    assert stale.status_code == 409
    past = client.post(PATH, json=body(repeat="once", start="2020-01-01"))
    assert past.status_code == 400
    assert client.post(PATH, json={**body(), "language": "Klingon"}).status_code == 422
    rows = client.get("/api/reminders").json()["items"]
    assert {row["id"] for row in rows} == {first["id"], second["id"]}
    assert rows[0]["title"] == "Authored contact" and rows[0]["account_id"] == 1
    removed = client.request(
        "DELETE", f"{PATH}/{first['id']}", json={"revision": 2}
    ).json()
    assert [item["id"] for item in removed["items"]] == [second["id"]]
    assert client.get(f"{PATH}/{first['id']}").status_code == 409
    assert not app.engine.calls and not app.decider.calls


def test_lists_of_timezones_languages_and_the_digest_settings(rig):
    server, client, app, now = rig
    zones = client.get("/api/reminders/timezones").json()["timezones"]
    assert zones[0] == "UTC" and "Asia/Tokyo" in zones
    languages = client.get("/api/reminders/languages").json()["languages"]
    assert {"code": "ru", "name": "Russian", "native": "Русский"} in languages
    settings = client.get("/api/reminders/settings").json()
    assert settings["time"] == "09:00" and settings["timezone"] == "UTC"
    settings["time"] = "10:15"
    assert client.post("/api/reminders/settings", json=settings).status_code == 200
    assert client.post("/api/reminders/settings", json=settings).status_code == 409


def test_an_exact_alert_names_the_person_and_its_idea_and_opens_its_chat(rig):
    server, client, app, now = rig
    hub = server.state.notifications
    enable(client)
    saved = create(
        client, delivery="exact", time="08:30", idea="\nAsk about the exam\nmore"
    )
    now[0] = saved["next_at"] - LEAD
    asyncio.run(hub.tick())
    # Being prepared alerts nobody; alerts never wait for suggestions.
    assert hub.transport.sent == []
    due(server, now, saved)
    assert len(hub.transport.sent) == 1
    identity = hub.transport.sent[0][1]["id"]
    # The push itself stays opaque.
    assert set(hub.transport.sent[0][1]) <= {"id", "expires", "silent"}
    event = client.post("/api/notifications/validate", json={"id": identity}).json()
    assert (event["sender"], event["text"]) == (
        "Authored contact",
        "Ask about the exam",
    )
    assert client.post("/api/notifications/open", json={"id": identity}).json() == {
        "kind": "reminder",
        "account_id": 1,
        "peer": "user:42",
    }
    occurrence = client.get(f"{PATH}/{saved['id']}").json()["occurrence"]["id"]
    now[0] += 60
    asyncio.run(hub.tick())
    assert len(hub.transport.sent) == 1
    dismissed = client.post(
        f"{PATH}/{saved['id']}/dismiss", json={"occurrence": occurrence}
    )
    assert dismissed.status_code == 200
    # A tap whose occurrence is no longer pending opens the list (ADR 36).
    assert client.post("/api/notifications/open", json={"id": identity}).json() == {
        "kind": "reminders"
    }
    assert (
        client.post(
            f"{PATH}/{saved['id']}/dismiss", json={"occurrence": occurrence}
        ).status_code
        == 409
    )


def test_a_check_in_alert_says_check_in(rig):
    server, client, app, now = rig
    hub = server.state.notifications
    enable(client)
    saved = create(client, delivery="exact", time="08:30")
    due(server, now, saved)
    identity = hub.transport.sent[0][1]["id"]
    event = client.post("/api/notifications/validate", json={"id": identity}).json()
    assert (event["sender"], event["text"]) == ("Authored contact", "Check in")


def test_a_digest_names_up_to_three_people_and_one_reminder_opens_its_chat(rig):
    server, client, app, now = rig
    hub = server.state.notifications
    enable(client)
    for index in range(43, 48):
        app.store.put_chat(
            Chat(
                account_id=1,
                peer=f"user:{index}",
                title=f"Authored person {index}",
                kind="private",
            )
        )
    saved = create(client)
    asyncio.run(hub.tick())
    due(server, now, saved)
    identity = hub.transport.sent[0][1]["id"]
    event = client.post("/api/notifications/validate", json={"id": identity}).json()
    assert (event["sender"], event["text"]) == ("Reminders", "Authored contact")
    assert event["account"] == "All accounts"
    assert client.post("/api/notifications/open", json={"id": identity}).json() == {
        "kind": "reminder",
        "account_id": 1,
        "peer": "user:42",
    }
    # Next week's digest names five people: three and "+2".
    for index in range(43, 47):
        response = client.post(
            f"/api/accounts/1/chats/user:{index}/reminders", json=body()
        )
        assert response.status_code == 200
    now[0] = stamp("2026-11-01T09:00:00+00:00")
    asyncio.run(hub.tick())
    later = hub.transport.sent[-1][1]["id"]
    event = client.post("/api/notifications/validate", json={"id": later}).json()
    assert event["sender"] == "Reminders" and event["count"] == 5
    assert event["text"].endswith(" +2") and event["text"].count(",") == 2
    assert client.post("/api/notifications/open", json={"id": later}).json() == {
        "kind": "reminders"
    }
    assert not app.engine.calls or all(
        role in ("response_suggestions",) for role, _ in app.engine.calls
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
    friend.reminders.create("2/user:42", ReminderRule(**body()))
    assert client.get("/api/reminders").json() == {"items": []}
    assert client.get("/api/reminders/settings").json()["timezone"] == "UTC"
    assert client.get("/api/accounts/2/chats/user:42/reminders").status_code == 400
    assert manager.reminders.items("friend")[0]["account_id"] == 2
    assert manager.reminders.items(OWNER) == []


def test_global_off_on_between_ticks_resets_future_without_backfill(rig):
    server, client, app, now = rig
    saved = create(client)
    now[0] = saved["next_at"]
    app.reminders.tick()
    assert app.reminders.get(KEY, saved["id"]).occurrence
    registry = server.state.accounts.registry
    registry.set_suggestions_enabled(False)
    now[0] += 21 * 86400
    registry.set_suggestions_enabled(True)
    app.reminders.tick()
    assert app.reminders.get(KEY, saved["id"]).occurrence is None
    assert app.reminders.get(KEY, saved["id"]).next_at > now[0]


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
    saved = create(client)
    other.reminders.create("3/user:42", ReminderRule(**body()))
    due(server, now, saved)
    assert len(hub.transport.sent) == 1
    identity = hub.transport.sent[0][1]["id"]
    event = client.post("/api/notifications/validate", json={"id": identity}).json()
    assert event["count"] == 2 and event["account"] == "All accounts"
    assert event["text"] == "Authored contact, Authored other account"
    items = client.get("/api/reminders").json()["items"]
    assert len(items) == 2 and all(item["occurrence"] for item in items)


def test_restart_retains_notification_claims_and_account_removal_purges_targets(rig):
    server, client, app, now = rig
    enable(client)
    saved = create(client)
    due(server, now, saved)
    restarted = create_app(server.state.settings)
    hub = restarted.state.notifications
    hub.clock = restarted.state.accounts.reminders.clock = lambda: now[0]
    restarted.state.accounts.get(1).store.clock = lambda: now[0]
    asyncio.run(hub.tick())
    assert hub.transport.sent == []
    assert restarted.state.accounts.get(1).reminders.get(KEY, saved["id"]).occurrence
    restarted.state.accounts.reminders.remove_account(1)
    assert (
        restarted.state.accounts.registry.rows("SELECT * FROM reminder_notices") == []
    )


def test_a_draft_keeps_its_reminder_mark_through_edits_until_cleared(rig):
    server, client, app, now = rig
    saved = create(client, idea="Ask about the exam")
    due(server, now, saved)
    opened = client.post(PATH + "/open", json={})
    assert opened.status_code == 200 and opened.json()["state"] == "preparing"
    for _ in range(4):
        asyncio.run(app.process_due())
    shown = client.get("/api/accounts/1/chats/user:42").json()
    assert [r["schedule"] for r in shown["reminders"]] == [saved["id"]]
    suggestions = shown["suggestions"]
    chosen = client.post(
        "/api/accounts/1/chats/user:42/suggestions/insert",
        json={"revision": 0, "attempt": suggestions["attempt"], "index": 0},
    ).json()["draft"]
    occurrence = shown["reminders"][0]["occurrence"]
    assert chosen["reminders"] == [occurrence]
    edited = client.post(
        "/api/accounts/1/chats/user:42/draft",
        json={"revision": chosen["revision"], "text": chosen["text"] + " Edited."},
    ).json()
    assert edited["reminders"] == [occurrence]
    cleared = client.post(
        "/api/accounts/1/chats/user:42/draft",
        json={"revision": edited["revision"], "text": ""},
    ).json()
    assert "reminders" not in cleared
