"""GB7 authenticated cached group media, without native or provider traffic."""

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat, MediaInfo, Message
from fastapi.testclient import TestClient
from PIL import Image

from backend.tests.auth_helpers import trust_browser


@pytest.fixture(params=["chat:42", "channel:42"])
def rig(tmp_path, request):
    server = create_app(Settings(data=tmp_path / "app"))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        app = server.state.accounts.get(1)
        peer = request.param
        key = f"1/{peer}"
        app.store.put_chat(
            Chat(account_id=1, peer=peer, title="Authored group", kind="group")
        )
        app.store.groups.observe(key, access="available", reason="authored_native")
        server.state.accounts.set_processing(1, False)
        yield client, app, peer, key


def source(app, peer, *, media="image", info=None):
    value = Message(
        account_id=1,
        peer=peer,
        message_id=11,
        revision=1,
        date=app.store.clock(),
        text="Authored media source.",
        author="Alex",
        author_peer="user:7",
        media=media,
        media_info=info or MediaInfo(mime="image/png"),
    )
    app.sync.ingest(
        value, target=False, group_epoch=app.store.groups.intake(f"1/{peer}").epoch
    )
    app.transport.messages.append(value)
    return app.store.message(f"1/{peer}", 11)


def image_bytes():
    data = BytesIO()
    Image.new("RGB", (40, 30), (50, 120, 160)).save(data, "PNG")
    return data.getvalue()


def url(peer):
    return f"/api/accounts/1/chats/{peer}/originals/11/media"


def test_off_group_image_ranges_and_lost_access_cache_validator_require_exact_bytes(
    rig,
):
    client, app, peer, key = rig
    source(app, peer)
    data = image_bytes()
    app.transport.media[peer, 11] = data
    first = client.get(url(peer) + "?revision=1")
    assert first.status_code == 200 and first.content == data
    assert app.store.groups.get(key).processing_enabled is False
    assert not app.engine.calls
    baseline = list(app.transport.downloads)
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    cached = client.get(url(peer) + "?revision=1")
    assert cached.status_code == 200 and cached.content == data
    ranged = client.get(url(peer) + "?revision=1", headers={"Range": "bytes=0-15"})
    assert ranged.status_code == 206 and ranged.content == data[:16]
    validator = {"If-None-Match": first.headers["etag"]}
    assert client.get(url(peer) + "?revision=1", headers=validator).status_code == 304
    assert (
        client.get(
            url(peer) + "?revision=1&variant=poster", headers=validator
        ).status_code
        == 400
    )
    assert app.transport.downloads == baseline
    app.media.cache.clear()
    assert client.get(url(peer) + "?revision=1", headers=validator).status_code == 400
    assert app.transport.downloads == baseline and not app.engine.calls


def test_cached_group_document_after_loss_uses_retained_file_without_native_stream(
    rig, tmp_path
):
    client, app, peer, key = rig
    value = source(
        app,
        peer,
        media="document",
        info=MediaInfo(file_name="authored.txt", mime="text/plain"),
    )
    data = b"An authored retained document.\n"
    path = tmp_path / "authored.txt"
    path.write_bytes(data)
    client.portal.call(
        lambda: app.media.cache.retain(
            value,
            path,
            limit=50 * 1024 * 1024,
            expected_sha256=hashlib.sha256(data).hexdigest(),
            expected_size=len(data),
        )
    )
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    response = client.get(url(peer) + "?revision=1&download=true")
    assert response.status_code == 200 and response.content == data
    assert "attachment" in response.headers["content-disposition"]
    assert not app.transport.downloads and not app.engine.calls


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_cached_group_original_change_cannot_validate_browser_copy(rig, change):
    client, app, peer, key = rig
    value = source(app, peer)
    app.transport.media[peer, 11] = image_bytes()
    first = client.get(url(peer) + "?revision=1")
    assert first.status_code == 200
    app.sync.ingest(
        value.model_copy(
            update={
                "revision": 2,
                "text": "Changed authored caption.",
                "deleted": change == "delete",
            }
        ),
        target=False,
        group_epoch=app.store.groups.intake(key).epoch,
    )
    response = client.get(
        url(peer) + "?revision=1", headers={"If-None-Match": first.headers["etag"]}
    )
    # ADR 52 Q7: a deleted original keeps showing the bytes it last had.
    assert response.status_code == (304 if change == "delete" else 400)
    assert len(app.transport.downloads) == 1 and not app.engine.calls


@pytest.mark.parametrize("validator", [False, True])
def test_loss_and_restoration_during_cached_video_verification_rejects_late_body_and_304(
    rig, validator
):
    client, app, peer, key = rig
    source(
        app,
        peer,
        media="video",
        info=MediaInfo(file_name="authored.mp4", mime="video/mp4"),
    )
    app.transport.media[peer, 11] = (
        Path(__file__).with_name("fixtures") / "circle.mp4"
    ).read_bytes()
    assert (
        client.post(url(peer) + "/prepare-video", json={"revision": 1}).status_code
        == 200
    )
    initial = client.get(url(peer) + "?revision=1")
    assert initial.status_code == 200
    original = app.media.video_type

    async def verify(message, path):
        mime = await original(message, path)
        app.store.groups.observe(key, access="lost", reason="authored_loss")
        app.store.groups.observe(key, access="available", reason="authored_restoration")
        return mime

    app.media.video_type = verify
    headers = {"If-None-Match": initial.headers["etag"]} if validator else {}
    rejected = client.get(url(peer) + "?revision=1", headers=headers)
    assert rejected.status_code == 400
    assert "access changed" in rejected.json()["detail"]
    assert len(app.transport.downloads) == 1 and not app.engine.calls
