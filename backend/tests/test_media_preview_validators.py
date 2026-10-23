"""Retained GIF/sticker proof must precede a legacy browser validator."""

from io import BytesIO

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import MediaInfo, Message, Unavailable
from enloq.storage.store import RETENTION
from fastapi.testclient import TestClient
from PIL import Image

from backend.tests.auth_helpers import trust_browser

BASE = "/api/accounts/1/chats/user:101/originals/77/media"


@pytest.fixture(params=["gif", "sticker"])
def retained(request, tmp_path):
    kind = request.param
    output = BytesIO()
    image = Image.new("RGBA", (512, 512), (30, 110, 170, 255))
    if kind == "sticker":
        image.save(output, format="WEBP", lossless=True)
    else:
        image.save(
            output,
            format="GIF",
            save_all=True,
            append_images=[Image.new("RGBA", image.size, (170, 110, 30, 255))],
            duration=[100, 100],
            loop=0,
        )
    server = create_app(Settings(data=tmp_path / "app"))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        app = server.state.accounts.get(1)
        source = Message(
            account_id=1,
            peer="user:101",
            message_id=77,
            revision=1,
            date=app.store.clock(),
            media=kind,
            media_info=MediaInfo(sticker="static") if kind == "sticker" else None,
        )
        app.sync.ingest(source, target=False)
        app.transport.media["user:101", 77] = output.getvalue()
        prepared = client.post(BASE + "/prepare-preview", json={"revision": 1})
        assert prepared.status_code == 200 and prepared.json()["state"] == "ready"
        yield server, client, app, source, output.getvalue()


def test_legacy_body_ranges_and_304_reuse_proved_bytes(retained):
    _, client, app, _, payload = retained
    first = client.get(BASE + "?revision=1")
    assert first.status_code == 200 and first.content == payload
    etag = first.headers["etag"]
    again = client.get(BASE + "?revision=1", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "private, no-cache"
    partial = client.get(BASE + "?revision=1", headers={"Range": "bytes=0-9"})
    assert partial.status_code == 206 and partial.content == payload[:10]
    assert len(app.transport.downloads) == 1


@pytest.mark.parametrize("change", ["eviction", "corrupt"])
def test_old_validator_requires_retained_proved_bytes(retained, change):
    _, client, app, source, payload = retained
    etag = app.media.etag(source)
    if change == "eviction":
        app.media.cache.clear()
    else:
        # Preserve the old cache row/MIME/length while replacing actual bytes.
        app.media.cache.get(source)["path"].write_bytes(bytes(len(payload)))
    result = client.get(BASE + "?revision=1", headers={"If-None-Match": etag})
    assert result.status_code == 400
    assert len(app.transport.downloads) == 1
    assert not list(app.media.workspace.path.glob("*.bin"))


def test_verifier_outage_does_not_approve_old_validator(retained, monkeypatch):
    _, client, app, source, payload = retained

    async def unavailable(message, path):
        raise Unavailable("Authored verifier outage")

    monkeypatch.setattr(app.media, "selection_type", unavailable)
    result = client.get(
        BASE + "?revision=1", headers={"If-None-Match": app.media.etag(source)}
    )
    assert result.status_code == 503
    assert app.media.cache.get(source)["path"].read_bytes() == payload
    assert len(app.transport.downloads) == 1


@pytest.mark.parametrize(
    "change", ["edit", "delete", "expiry", "account", "browser", "eviction"]
)
def test_304_rechecks_access_after_actual_verification(retained, monkeypatch, change):
    server, client, app, source, _ = retained
    verifier = app.media.selection_type
    now = app.store.clock()
    browser = client.get("/api/auth/browsers").json()[0]["id"]

    async def changed(message, path):
        facts = await verifier(message, path)
        if change == "edit":
            app.sync.ingest(source.model_copy(update={"revision": 2}), target=False)
        elif change == "delete":
            app.sync.deleted("user:101", [77])
        elif change == "expiry":
            app.store.clock = lambda: now + RETENTION + 1
        elif change == "account":
            app.media.authorized = lambda: False
        elif change == "browser":
            server.state.browser_auth.revoke(browser)
        else:
            app.media.cache.clear()
        return facts

    monkeypatch.setattr(app.media, "selection_type", changed)
    result = client.get(
        BASE + "?revision=1", headers={"If-None-Match": app.media.etag(source)}
    )
    assert result.status_code == (401 if change == "browser" else 400)
    assert len(app.transport.downloads) == 1
    assert not list(app.media.workspace.path.glob("*.bin"))
