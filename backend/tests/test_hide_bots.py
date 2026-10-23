"""The reading preference is shared, durable and separate from AI work."""

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.storage.accounts import AccountRegistry
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser


def test_hide_bots_is_default_on_durable_and_has_one_shared_change_scope(tmp_path):
    registry = AccountRegistry(tmp_path)
    try:
        registry.register(1, "one.session")
        registry.register(2, "two.session")
        registry.set_reading_view("originals")
        registry.set_processing(1, False)
        registry.set_tone("friendly")
        before = registry.changes()[1]
        assert registry.hide_bots() == {"hide_bots": True}
        assert registry.changes(before)[0] == []  # Reading starts no writes.
        assert registry.set_hide_bots(False) == {"hide_bots": False}
        assert registry.changes(before)[0] == ["hide_bots"]
        saved = registry.changes()[1]
        registry.set_hide_bots(False)
        assert registry.changes(saved) == ([], saved)
        assert registry.reading_view() == {"reading_view": "originals"}
        assert registry.preferences()["tone"] == "friendly"
        assert not registry.processing(1)["ai_enabled"]
        assert registry.processing(2)["ai_enabled"]
    finally:
        registry.connections.close()
    reopened = AccountRegistry(tmp_path)
    try:
        assert reopened.hide_bots() == {"hide_bots": False}
        assert reopened.set_hide_bots(True) == {"hide_bots": True}
    finally:
        reopened.connections.close()


def test_hide_bots_http_requires_trust_and_shares_only_the_reading_choice(tmp_path):
    app = create_app(Settings(data=tmp_path))
    manager = app.state.accounts
    manager._create(manager.registry.register(2, "two.session"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/preferences/hide-bots").status_code == 401
        trust_browser(client, tmp_path)
        assert client.get("/api/preferences/hide-bots").json() == {"hide_bots": True}
        before = [entry["ai_enabled"] for entry in manager.summaries()]
        response = client.post("/api/preferences/hide-bots", json={"hide_bots": False})
        assert response.status_code == 200
        assert response.json() == {"hide_bots": False}
        assert [entry["ai_enabled"] for entry in manager.summaries()] == before
        assert client.get("/api/preferences/reading-view").json() == {
            "reading_view": "ai"
        }
        # A newly paired browser sees the same value without choosing an account.
        client.cookies.clear()
        assert client.get("/api/preferences/hide-bots").status_code == 401
        trust_browser(client, tmp_path)
        assert client.get("/api/preferences/hide-bots").json() == {"hide_bots": False}
        response = client.post(
            "/api/preferences/hide-bots",
            json={"hide_bots": True},
            headers={"Origin": "https://untrusted.example"},
        )
        assert response.status_code == 403
        assert client.get("/api/preferences/hide-bots").json() == {"hide_bots": False}


def test_hide_bots_refuses_ambiguous_values_without_changing_the_preference(tmp_path):
    app = create_app(Settings(data=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path)
        for value in (0, 1, "false", None):
            assert (
                client.post(
                    "/api/preferences/hide-bots", json={"hide_bots": value}
                ).status_code
                == 422
            )
            with pytest.raises(Refused):
                app.state.accounts.registry.set_hide_bots(value)
        assert client.get("/api/preferences/hide-bots").json() == {"hide_bots": True}


def test_owner_correction_is_account_group_bound_durable_and_starts_no_work(tmp_path):
    app = create_app(Settings(data=tmp_path))
    manager = app.state.accounts
    manager._create(manager.registry.register(2, "two.session"))
    for account in (1, 2):
        assistant = manager.get(account)
        manager.set_processing(account, False)
        for peer in ("chat:42", "channel:42"):
            key = f"{account}/{peer}"
            assistant.store.put_chat(
                Chat(
                    account_id=account, peer=peer, title="Authored group", kind="group"
                )
            )
            assistant.store.groups.observe(
                key, access="available", reason="authored_native"
            )
            with assistant.store.transaction() as db:
                assert assistant.store.group_filters.ignore_bot(
                    key,
                    "user:7",
                    day="2026-10-13",
                    fingerprint="a" * 64,
                    probability=0.95,
                    db=db,
                )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path)
        one, two = manager.get(1), manager.get(2)
        usage = manager.registry.usage()
        calls = [len(one.engine.calls), len(two.engine.calls)]
        response = client.post(
            "/api/accounts/1/chats/chat%3A42/bots/user%3A7/restore", json={}
        )
        assert response.status_code == 200
        assert response.json() == {"author_peer": "user:7", "ignored": False}
        assert one.store.group_filters.bot("1/chat:42", "user:7")["status"] == "allowed"
        assert (
            one.store.group_filters.bot("1/channel:42", "user:7")["status"] == "ignored"
        )
        assert two.store.group_filters.bot("2/chat:42", "user:7")["status"] == "ignored"
        assert (
            two.store.group_filters.bot("2/channel:42", "user:7")["status"] == "ignored"
        )
        # A repeated click is safe, and a later model judgment cannot undo the owner.
        assert (
            client.post(
                "/api/accounts/1/chats/chat%3A42/bots/user%3A7/restore", json={}
            ).status_code
            == 200
        )
        with one.store.transaction() as db:
            assert not one.store.group_filters.ignore_bot(
                "1/chat:42",
                "user:7",
                day="2026-10-14",
                fingerprint="b" * 64,
                probability=1.0,
                db=db,
            )
        assert (
            client.post(
                "/api/accounts/1/chats/chat%3A42/bots/user%3A8/restore", json={}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/accounts/99/chats/chat%3A42/bots/user%3A7/restore", json={}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/accounts/1/chats/chat%3A42/bots/channel%3A7/restore", json={}
            ).status_code
            == 422
        )
        assert manager.registry.hide_bots() == {"hide_bots": True}
        assert manager.registry.usage() == usage
        assert [len(one.engine.calls), len(two.engine.calls)] == calls
        assert not manager.registry.processing(1)["ai_enabled"]
    # The owner override remains after reopening the actual account store.
    reopened = create_app(Settings(data=tmp_path))
    with TestClient(reopened, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path)
        assert (
            reopened.state.accounts.get(1).store.group_filters.bot(
                "1/chat:42", "user:7"
            )["status"]
            == "allowed"
        )
