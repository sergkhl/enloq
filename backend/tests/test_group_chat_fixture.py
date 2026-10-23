"""The disposable group-chat fixture preserves typed speakers and isolation."""

import base64
import time

from enloq.api.server import create_app
from enloq.application import selected_media
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_avatars import picture
from scripts.gif_sticker_fixture import register as register_media
from scripts.group_chat_fixture import register


def test_group_fixture_exposes_default_off_typed_groups_and_truthful_native_authors(
    tmp_path,
):
    settings = Settings(data=tmp_path)
    server = create_app(settings)
    server.state.accounts._create(
        server.state.accounts.registry.register(2, "second.synthetic.session")
    )
    register(server)
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(server, base_url=origin["Origin"]) as client:
        trust_browser(client, settings.data)
        for account in (1, 2):
            setup = client.post(
                "/api/test/group-chats", json={"account_id": account}, headers=origin
            )
            assert setup.status_code == 200
            rows = client.get(f"/api/accounts/{account}/chats?kind=group").json()
            assert {row["peer"] for row in rows} == {"chat:42", "channel:42"}
            value = client.get(f"/api/accounts/{account}/chats/chat:42").json()
            assert not value["group_processing"]["processing_enabled"]
            authors = [
                (m["author_peer"], m["outgoing"], m["author_signature"])
                for m in value["messages"]
            ]
            assert authors[3:] == [
                (f"user:{account}", True, None),
                ("channel:42", True, "Moderator"),
                (None, True, None),
            ]
            assert value["blocks"] == []
            # Reset only this authored account's group evidence between journeys.
            assert (
                client.post(
                    "/api/test/group-chats",
                    json={"account_id": account},
                    headers=origin,
                ).status_code
                == 200
            )
        client.post(
            "/api/test/group-chats",
            json={"action": "access", "access": "lost"},
            headers=origin,
        )
        assert len(client.get("/api/accounts/1/chats?kind=group").json()) == 2
        retained = client.get("/api/accounts/1/chats/chat:42").json()
        assert retained["group_processing"]["access"] == "lost"
        assert not retained["group_processing"]["effective"]
        assert len(retained["messages"]) == 6
        assert len(client.get("/api/accounts/2/chats?kind=group").json()) == 2


def test_group_fixture_stats_keep_confirmed_sends_and_reset_retained_media_children(
    tmp_path, monkeypatch
):
    # The catalog fixture dispatches only authored bytes. Restore its optional
    # web-media seam once this isolated ASGI test completes.
    monkeypatch.setattr(
        selected_media, "fetch_web_media", selected_media.fetch_web_media
    )
    settings = Settings(data=tmp_path)
    server = create_app(settings)
    register(server)
    register_media(server)
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(server, base_url=origin["Origin"]) as client:
        trust_browser(client, tmp_path)
        setup = client.post(
            "/api/test/group-chats", json={"master": False}, headers=origin
        )
        assert setup.status_code == 200
        app = server.state.accounts.get(1)
        key, base = "1/chat:42", "/api/accounts/1/chats/chat:42"
        draft = app.attachments.add(
            key, 0, "authored.png", base64.b64encode(picture()).decode()
        )
        sent = client.post(
            base + "/send", json={"revision": draft["revision"]}, headers=origin
        )
        assert sent.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stats = client.post(
                "/api/test/group-chats", json={"action": "stats"}, headers=origin
            )
            assert stats.status_code == 200
            if stats.json()["operations"][0]["state"] == "confirmed":
                break
            time.sleep(0.01)
        assert stats.json()["operations"][0]["state"] == "confirmed"
        assert stats.json()["sends"][0]["images"] == 1
        assert not app.store.send_operations.recent(key)
        attachment_key = "1/channel:42"
        app.attachments.add(
            attachment_key, 0, "kept.png", base64.b64encode(picture()).decode()
        )
        assert app.store.rows(
            "SELECT 1 FROM attachments WHERE chat_key=?", (attachment_key,)
        )

        assert (
            client.post("/api/test/gif-sticker", json={}, headers=origin).status_code
            == 200
        )
        catalog = client.get(base + "/catalog").json()
        source = next(
            item
            for item in catalog["recent"]
            if item["metadata"]["name"] == "Pattern static"
        )
        selected = client.post(
            base + "/selected-media",
            json={
                "revision": app.store.draft(key)["revision"],
                "token": source["token"],
                "catalog_revision": source["revision"],
            },
            headers=origin,
        )
        assert selected.status_code == 200
        assert selected.json()["selected_media"]["status"] == "ready"
        assert app.store.rows("SELECT 1 FROM selected_media WHERE chat_key=?", (key,))
        reset = client.post("/api/test/group-chats", json={}, headers=origin)
        assert reset.status_code == 200
        assert not app.store.rows(
            "SELECT 1 FROM attachments WHERE chat_key=?", (attachment_key,)
        )
        assert not app.store.rows(
            "SELECT 1 FROM selected_media WHERE chat_key=?", (key,)
        )
        assert not app.store.rows("SELECT 1 FROM operations WHERE chat_key=?", (key,))
        assert len(client.get(base).json()["messages"]) == 6
