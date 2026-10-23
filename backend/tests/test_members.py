"""ADR 48 MB1: members and the boundary between them.

Every trusted client belongs to one member and reaches only that member's
accounts, lists, events, preferences and clients; another member's account
answers as an account the server doesn't hold. Only the owner changes the AI
settings, and a burst alerts only its member's phones and tabs.
"""

import re
import sqlite3
from contextlib import closing

import pytest
from enloq.api.server import create_app
from enloq.browser_auth import BrowserAuth, digest
from enloq.config import Settings
from enloq.domain.models import Refused
from enloq.storage.accounts import AccountRegistry
from enloq.storage.members import OWNER, UNAVAILABLE
from fastapi.testclient import TestClient

from backend.tests.login_fixture import SyntheticLoginClient
from backend.tests.test_device_trust import ORIGIN, bearer, remote
from backend.tests.test_push import subscription as authored_subscription


def add_member(app, member="friend", account=2):
    """An approved member with one account and one trusted phone."""
    registry = app.state.accounts.registry
    with registry.transaction() as db:
        db.execute(
            "INSERT INTO members (id,role,state,home_account,name,approved_at) VALUES (?,?,?,?,?,?)",
            (member, "member", "active", account, "Authored friend", 1.0),
        )
    registry.register(
        account, f"authorizations/{member}/telegram.session", member=member
    )
    return app.state.browser_auth._issue("Enloq app on Pixel", "device", member=member)


def phone(app):
    # A phone sends no Origin and keeps no cookies.
    return closing(TestClient(app, base_url=ORIGIN))


def owner_phone(app):
    return app.state.browser_auth._issue("Enloq app on SM-S938B", "device")


def effective_routes(routes):
    """Every served route, inside included routers too."""
    for route in routes:
        if hasattr(route, "effective_candidates"):
            yield from effective_routes(route.effective_candidates())
        elif hasattr(route, "path"):
            yield route


def concrete(path, account_id):
    """A route's path with the account filled in and plausible other parts."""
    values = {
        "account_id": str(account_id),
        "peer": "user:42",
        "day": "2026-10-17",
        "anchor": "a" * 64,
        "attempt": "b" * 32,
        "operation": "send",
        "author_peer": "user:7",
        "token": "c" * 32,
    }
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), "1"), path)


def test_existing_accounts_clients_and_preferences_become_the_owners(tmp_path):
    root = tmp_path
    with closing(sqlite3.connect(root / "browsers.sqlite3")) as db, db:
        db.execute(
            "CREATE TABLE browsers (id TEXT PRIMARY KEY, scope TEXT NOT NULL,"
            " token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL,"
            " approved_at REAL NOT NULL, last_access REAL NOT NULL,"
            " revoked INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'browser')"
        )
        db.execute(
            "INSERT INTO browsers VALUES ('a',?,?,'Safari on a Mac',1,1,0,'browser')",
            ("offline_8765", digest("earlier")),
        )
    (root / "browsers.sqlite3").chmod(0o600)
    registry = AccountRegistry(root)
    registry.register(1, "one.session")
    with registry.transaction() as db:
        for key, value in (
            ("tone", '{"tone": "friendly", "revision": 3}'),
            ("reading_view", "originals"),
            ("hide_bots", "0"),
            ("push_destination", "a"),
        ):
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, value))
        db.execute("DELETE FROM member_settings")
    registry.connections.close()

    reopened = AccountRegistry(root)
    try:
        owner = reopened.member(OWNER)
        assert (owner["role"], owner["state"]) == ("owner", "active")
        assert reopened.member_of(1) == OWNER
        assert reopened.preferences() == {"tone": "friendly", "revision": 3}
        assert reopened.reading_view() == {"reading_view": "originals"}
        assert reopened.hide_bots() == {"hide_bots": False}
        assert reopened.member_setting(OWNER, "push_destination") == "a"
    finally:
        reopened.connections.close()
    auth = BrowserAuth(Settings(data=root))
    assert auth.client("earlier") == {"id": "a", "member": OWNER}


def test_every_account_route_refuses_another_members_account(tmp_path):
    app = create_app(remote(tmp_path))
    friend = add_member(app)
    owner = owner_phone(app)
    routes = [
        (method, route.path)
        for route in effective_routes(app.routes)
        if "{account_id}" in route.path
        for method in sorted(route.methods - {"HEAD"})
    ]
    assert len(routes) > 80
    with phone(app) as client:
        for token, account in ((friend, 1), (owner, 2)):
            for method, path in routes:
                response = client.request(
                    method, concrete(path, account), headers=bearer(token), json={}
                )
                assert (response.status_code, response.json()) == (
                    400,
                    {"detail": UNAVAILABLE},
                ), (method, path)


def test_an_unknown_account_answers_the_same_as_another_members(tmp_path):
    app = create_app(remote(tmp_path))
    add_member(app)
    with phone(app) as client:
        owner = owner_phone(app)
        unknown = client.get("/api/accounts/9/chats", headers=bearer(owner))
        foreign = client.get("/api/accounts/2/chats", headers=bearer(owner))
        assert unknown.status_code == foreign.status_code == 400
        assert unknown.json() == foreign.json() == {"detail": UNAVAILABLE}
        # The owner's own account still opens.
        assert (
            client.get("/api/accounts/1/chats", headers=bearer(owner)).status_code
            == 200
        )


def test_lists_events_and_preferences_follow_the_member(tmp_path):
    app = create_app(remote(tmp_path))
    friend = add_member(app)
    owner = owner_phone(app)
    with phone(app) as client:
        mine = client.get("/api/accounts", headers=bearer(friend)).json()
        assert [a["account_id"] for a in mine["accounts"]] == [2]
        theirs = client.get("/api/accounts", headers=bearer(owner)).json()
        assert [a["account_id"] for a in theirs["accounts"]] == [1]

        assert client.post(
            "/api/preferences/reading-view",
            json={"reading_view": "originals"},
            headers=bearer(friend),
        ).json() == {"reading_view": "originals"}
        client.post(
            "/api/preferences", json={"tone": "friendly"}, headers=bearer(friend)
        )
        client.post(
            "/api/preferences/hide-bots",
            json={"hide_bots": False},
            headers=bearer(friend),
        )
        assert client.get(
            "/api/preferences/reading-view", headers=bearer(owner)
        ).json() == {"reading_view": "ai"}
        assert (
            client.get("/api/preferences", headers=bearer(owner)).json()["tone"]
            == "neutral"
        )
        assert client.get(
            "/api/preferences/hide-bots", headers=bearer(owner)
        ).json() == {"hide_bots": True}
        assert (
            client.get("/api/preferences", headers=bearer(friend)).json()["tone"]
            == "friendly"
        )


async def test_the_event_stream_names_only_the_members_accounts(tmp_path):
    app = create_app(remote(tmp_path))
    friend = add_member(app)
    auth = app.state.browser_auth
    endpoint = next(
        r.endpoint for r in effective_routes(app.routes) if r.path == "/api/events"
    )
    client = auth.client(friend, kind="device")
    from types import SimpleNamespace

    response = await endpoint(
        SimpleNamespace(
            state=SimpleNamespace(local_session=client["id"], member="friend")
        )
    )
    first = await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert first == 'event: refresh\ndata: {"accounts": [2]}\n\n'


def test_a_members_tone_reaches_only_their_own_rewrites(tmp_path):
    app = create_app(Settings(data=tmp_path))
    registry = app.state.accounts.registry
    registry.set_tone("professional", "friend")
    assert app.state.assistant.rewriting.preferences()["tone"] == "neutral"
    registry.set_tone("friendly")
    assert app.state.assistant.rewriting.preferences()["tone"] == "friendly"


def test_a_remembered_adaptive_becomes_neutral(tmp_path):
    """ADR 53 Q2: Adaptive left with no backward compatibility."""
    registry = AccountRegistry(tmp_path)
    registry.set_tone("professional", "friend")
    with registry.transaction() as db:
        db.execute(
            'UPDATE member_settings SET value=\'{"tone": "adaptive", "revision": 3}\''
            " WHERE key='tone'"
        )
    registry = AccountRegistry(tmp_path)
    assert registry.preferences("friend") == {"tone": "neutral", "revision": 4}
    with pytest.raises(Refused, match="Unknown tone"):
        registry.set_tone("adaptive")


def test_only_the_owner_changes_ai_settings(tmp_path):
    app = create_app(remote(tmp_path))
    friend = add_member(app)
    owner = owner_phone(app)
    writes = (
        ("/api/settings/concurrency", {"limit": 2}),
        ("/api/settings/processing-threshold", {"limit": 50}),
        ("/api/settings/memory", {"enabled": False}),
        ("/api/settings/suggestions", {"enabled": False}),
        ("/api/settings/guidance", {"fields": {"incoming_rewrite": "Authored."}}),
        ("/api/settings/guidance/keep", {"role": "incoming_rewrite"}),
        ("/api/settings/budget", {"limit_usd": "2.00"}),
        ("/api/settings/group-budget", {"limit_usd": "3.00"}),
    )
    with phone(app) as client:
        for path, body in writes:
            refused = client.post(path, json=body, headers=bearer(friend))
            assert refused.status_code == 403, path
            assert refused.json() == {"detail": "Only the owner changes this"}
        settings = client.get("/api/settings", headers=bearer(owner)).json()
        assert settings["concurrency"] == 4 and settings["memory"] is True
        assert (
            client.post(
                "/api/settings/concurrency", json={"limit": 2}, headers=bearer(owner)
            ).json()["concurrency"]
            == 2
        )


def test_the_trusted_list_and_forget_are_the_members_own(tmp_path):
    app = create_app(remote(tmp_path))
    friend = add_member(app)
    owner = owner_phone(app)
    auth = app.state.browser_auth
    with phone(app) as client:
        listed = client.get("/api/auth/browsers", headers=bearer(friend)).json()
        assert [row["label"] for row in listed] == ["Enloq app on Pixel"]
        owners = client.get("/api/auth/browsers", headers=bearer(owner)).json()
        assert [row["label"] for row in owners] == ["Enloq app on SM-S938B"]
        forgotten = client.post(
            f"/api/auth/browsers/{owners[0]['id']}/revoke",
            json={},
            headers=bearer(friend),
        )
        assert forgotten.status_code == 400
        assert auth.active(owners[0]["id"])
        assert client.get("/api/accounts", headers=bearer(owner)).status_code == 200


def test_a_phone_paired_from_a_members_client_joins_that_member(tmp_path):
    app = create_app(remote(tmp_path, owner_account=1))
    friend = add_member(app)
    auth = app.state.browser_auth
    with phone(app) as approver, phone(app) as claimant:
        started = approver.post(
            "/api/auth/client-pairings", json={}, headers=bearer(friend)
        )
        identity = started.json()["id"]
        claimed = claimant.post(
            f"/api/auth/pairings/{identity}/device", json={"name": "Moto"}
        )
        proof = claimed.json()["proof"]
        approver.post(
            f"/api/auth/client-pairings/{identity}",
            json={"action": "approve", "code": claimed.json()["code"]},
            headers=bearer(friend),
        )
        token = claimant.post(
            f"/api/auth/pairings/{identity}/device/complete",
            json={},
            headers={"X-Pairing-Proof": proof},
        ).json()["token"]
    assert auth.client(token, kind="device")["member"] == "friend"
    # A member's own pairing leaves the owner's Saved Messages alone (Q12).
    assert app.state.accounts.get(1).transport.saved_posts == []


async def test_an_account_another_member_holds_cannot_be_added(tmp_path):
    app = create_app(Settings(data=tmp_path), login_factory=SyntheticLoginClient)
    registry = app.state.accounts.registry
    with registry.transaction() as db:
        db.execute(
            "INSERT INTO members (id,role,state,home_account,approved_at) VALUES ('friend','member','active',4,1)"
        )
    registry.register(4, "authorizations/friend/telegram.session", member="friend")
    logins = app.state.logins
    auth = app.state.browser_auth
    client = auth.client(auth._issue("Safari on this Mac", "browser"))["id"]
    started = await logins.begin(client, "phone", "+12025550004", member=OWNER)
    assert started["stage"] == "code"
    done = await logins.submit(started["id"], client, "code", "12345")
    assert done["stage"] == "error"
    assert done["message"] == "This account belongs to another person on this server."
    assert registry.member_of(4) == "friend"
    assert registry.get(4)["session"] == "authorizations/friend/telegram.session"


def test_register_keeps_an_accounts_member(tmp_path):
    registry = AccountRegistry(tmp_path)
    try:
        registry.register(5, "five.session", member="friend")
        with pytest.raises(Refused):
            registry.register(5, "again.session")
        assert (
            registry.register(5, "again.session", member="friend")["member"] == "friend"
        )
    finally:
        registry.connections.close()


def test_a_burst_alerts_only_its_members_phones_and_tabs(tmp_path):
    app = create_app(remote(tmp_path, fcm_project="enloq-test"))
    friend = add_member(app)
    owner = owner_phone(app)
    auth = app.state.browser_auth
    delivery = app.state.notifications
    friend_id = auth.client(friend, kind="device")["id"]
    owner_id = auth.client(owner, kind="device")["id"]
    for device in (friend_id, owner_id):
        delivery.devices.enable(device, f"eAuthoredFcm{device[:8]}:APA91b" + "Q" * 120)
    event = {"id": "e" * 32, "account_id": 1, "peer": "user:42", "expires": 9e9}
    delivery.devices.fan_out(event, delivery.event_member(event))
    rows = delivery.registry.rows("SELECT device FROM device_deliveries")
    assert [row["device"] for row in rows] == [owner_id]

    # The friend's focused tab elsewhere never takes the owner's chime.
    friend_tab = auth._issue("Chromium browser on Linux", "browser", member="friend")
    tab = auth.client(friend_tab)["id"]
    delivery.attention.update(tab, "t1", None, None, True, True)
    assert delivery.attention.choose(event) is None
    assert delivery.attention.choose({**event, "member": "friend"}) == (
        "chime",
        tab,
        "t1",
    )


def test_presence_in_another_members_account_is_refused(tmp_path):
    app = create_app(remote(tmp_path))
    add_member(app)
    auth = app.state.browser_auth
    tab = auth.client(auth._issue("Safari on an iPhone", "browser", member="friend"))[
        "id"
    ]
    with pytest.raises(Refused):
        app.state.notifications.attention.update(tab, "t1", 1, "user:42", True, True)


def test_each_member_has_their_own_browser_push_destination(tmp_path):
    app = create_app(remote(tmp_path))
    add_member(app)
    auth = app.state.browser_auth
    delivery = app.state.notifications
    mine = auth.client(auth._issue("Safari on a Mac", "browser"))["id"]
    theirs = auth.client(auth._issue("Firefox on Linux", "browser", member="friend"))[
        "id"
    ]
    delivery.enable(mine, authored_subscription()[0])
    delivery.enable(theirs, authored_subscription()[0])
    assert delivery.active(OWNER) == mine
    assert delivery.active("friend") == theirs
    assert delivery.status(mine)["push"] and delivery.status(theirs)["push"]
    delivery.preferences(theirs, push=False)
    assert delivery.active(OWNER) == mine and delivery.active("friend") is None


def test_only_the_owner_reads_processing_guidance(tmp_path):
    app = create_app(remote(tmp_path))
    friend = add_member(app)
    owner = owner_phone(app)
    with phone(app) as client:
        theirs = client.get("/api/settings", headers=bearer(friend)).json()
        assert theirs["guidance"] is None and theirs["decision_model"] is None
        assert theirs["budget"]["limit"] == 500_000_000
        mine = client.get("/api/settings", headers=bearer(owner)).json()
        assert mine["guidance"]["version"] == 0
        assert mine["budget"]["limit"] == 1_000_000_000
