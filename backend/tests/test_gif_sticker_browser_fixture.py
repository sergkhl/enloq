"""Authored GS6 fixture wiring through ASGI, without a listening server."""

from enloq.api.server import create_app
from enloq.application import selected_media
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from scripts.gif_sticker_fixture import register


def fixture_control(client, **body):
    response = client.post("/api/test/gif-sticker", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_fixture_catalog_selection_exact_send_and_two_account_scope(
    tmp_path, monkeypatch
):
    # The disposable fixture dispatches only its synthetic web URL. Restore
    # that module seam when this ASGI-only test completes.
    monkeypatch.setattr(
        selected_media, "fetch_web_media", selected_media.fetch_web_media
    )
    app = create_app(Settings(data=tmp_path, port=8776))
    entry = app.state.accounts.registry.register(
        2, "synthetic2.session", name="Second demo account"
    )
    app.state.accounts._create(entry)
    register(app)
    with TestClient(app, base_url="http://127.0.0.1:8776") as client:
        trust_browser(client, tmp_path)
        for account in (1, 2):
            assert (
                client.post(
                    "/api/test/gif-sticker", json={"account": account}
                ).status_code
                == 200
            )
        base = "/api/accounts/1/chats/user:171"
        catalog = client.get(base + "/catalog").json()
        source = next(
            item
            for item in catalog["recent"]
            if item["metadata"]["name"] == "Pattern static"
        )
        response = client.post(
            base + "/selected-media",
            json={
                "revision": 0,
                "token": source["token"],
                "catalog_revision": source["revision"],
            },
        )
        assert response.status_code == 200
        draft = response.json()
        selected = draft["selected_media"]
        assert selected["status"] == "ready" and selected["format"] == "webp"
        content = client.get(selected["content_url"])
        assert (
            content.status_code == 200
            and content.headers["content-type"] == "image/webp"
        )
        asset = base + f"/catalog/items/{source['token']}/assets"
        prepared = client.post(
            asset + "/prepare",
            json={"revision": source["revision"], "variant": "thumbnail"},
        )
        assert prepared.status_code == 200 and prepared.json()["state"] == "ready"
        client.post("/api/test/gif-sticker", json={"action": "evict"})
        assert (
            client.get(asset, params={"revision": source["revision"]}).json()["state"]
            == "missing"
        )
        # Fixture eviction uses real catalog cache keys while durable selected
        # bytes remain readable outside that disposable cache.
        assert client.get(selected["content_url"]).content == content.content
        # Catalog tickets cannot be moved to another authored account/chat.
        assert (
            client.post(
                "/api/accounts/2/chats/user:171/selected-media",
                json={
                    "revision": 0,
                    "token": source["token"],
                    "catalog_revision": source["revision"],
                },
            ).status_code
            == 400
        )
        sent = client.post(base + "/send", json={"revision": draft["revision"]})
        assert sent.status_code == 200 and sent.json()["operation"]["text"] == ""
        stats = client.post("/api/test/gif-sticker", json={"action": "stats"}).json()
        assert stats["operations"][0]["media"]["format"] == "webp"
        assert not stats["queries_persisted"]
        assert stats["asset_facts"]["static"]["size"] == len(content.content)
        assert not stats["rewrites"]
