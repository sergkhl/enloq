"""GB9 retained media uses exact cached bytes and cannot grant native work."""

import base64
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from enloq.domain.models import MediaInfo
from enloq.storage.store import RETENTION, WINDOW
from PIL import Image

from backend.tests.test_retained_group_api import (
    authored_source,
    native_calls,
    save_draft,
)
from backend.tests.test_retained_group_api import retained_rig as retained_rig
from backend.tests.test_selected_media import media_bytes


def picture():
    data = BytesIO()
    Image.new("RGB", (40, 30), (50, 120, 160)).save(data, "PNG")
    return data.getvalue()


def media_source(r, kind):
    data, info = picture(), MediaInfo(mime="image/png")
    if kind == "audio":
        data = b"OggS" + b"authored voice-note bytes"
        info = MediaInfo(mime="audio/ogg", voice=True)
    elif kind in ("video", "video_note"):
        data = (Path(__file__).with_name("fixtures") / "circle.mp4").read_bytes()
        info = MediaInfo(file_name="authored.mp4", mime="video/mp4")
    elif kind in ("gif", "sticker"):
        data = media_bytes(kind)
        info = MediaInfo(
            mime="image/gif" if kind == "gif" else "image/webp",
            sticker="static" if kind == "sticker" else None,
        )
    elif kind == "document":
        data = b"An authored retained document.\n"
        info = MediaInfo(file_name="authored.txt", mime="text/plain")
    source = authored_source(
        r,
        media=kind,
        attachment_id=f"authored-{kind}-attachment",
        media_info=info,
    )
    r.app.transport.media[r.peer, 1] = data
    return source, data


def media_url(r, *, identity=1):
    return r.base + f"/originals/{identity}/media"


def retain_document(r, message, data, tmp_path):
    path = tmp_path / "authored-document.txt"
    path.write_bytes(data)
    r.client.portal.call(
        lambda: r.app.media.cache.retain(
            message,
            path,
            limit=50 * 1024 * 1024,
            expected_sha256=hashlib.sha256(data).hexdigest(),
            expected_size=len(data),
        )
    )


@pytest.mark.parametrize(
    "kind", ["image", "audio", "video_note", "video", "gif", "sticker", "document"]
)
def test_retained_cached_media_serves_but_missing_bytes_never_refetch(
    retained_rig, tmp_path, kind
):
    r = retained_rig
    message, data = media_source(r, kind)
    url = media_url(r)
    if kind == "video":
        prepared = r.client.post(url + "/prepare-video", json={"revision": 1})
        assert prepared.status_code == 200 and prepared.json()["state"] == "ready"
    elif kind in ("gif", "sticker"):
        prepared = r.client.post(url + "/prepare-preview", json={"revision": 1})
        assert prepared.status_code == 200 and prepared.json()["state"] == "ready"
    elif kind == "document":
        retain_document(r, message, data, tmp_path)
    query = "?revision=1" + ("&download=true" if kind == "document" else "")
    first = r.client.get(url + query)
    assert first.status_code == 200 and first.content == data
    saved = save_draft(r)
    r.now[0] += WINDOW + 1
    assert r.app.store.groups.get(r.key).access == "available"
    assert r.app.store.recent_chats("group") == []
    baseline = native_calls(r)
    cached = r.client.get(url + query)
    assert cached.status_code == 200 and cached.content == data
    assert cached.headers["cache-control"] == (
        "no-store" if kind == "document" else "private, no-cache"
    )
    ranged = r.client.get(url + query, headers={"Range": "bytes=0-15"})
    assert ranged.status_code == 206 and ranged.content == data[:16]
    if kind != "document":
        validated = r.client.get(
            url + query, headers={"If-None-Match": first.headers["etag"]}
        )
        assert validated.status_code == 304 and validated.content == b""
    if kind in ("gif", "sticker"):
        representation = r.client.get(url + "/representation?revision=1")
        assert (
            representation.status_code == 200
            and representation.json()["state"] == "ready"
        )
    wrong_revision = r.client.get(url + query.replace("revision=1", "revision=2"))
    assert wrong_revision.status_code == 400
    r.app.media.cache.clear()
    missing = r.client.get(
        url + query, headers={"If-None-Match": first.headers["etag"]}
    )
    assert missing.status_code == 400 and missing.content != data
    if kind == "video":
        assert (
            r.client.post(url + "/prepare-video", json={"revision": 1}).status_code
            == 400
        )
    elif kind in ("gif", "sticker"):
        assert (
            r.client.post(url + "/prepare-preview", json={"revision": 1}).status_code
            == 400
        )
    assert native_calls(r) == baseline
    assert r.app.store.draft(r.key) == saved
    assert (
        not r.app.engine.calls and not r.app.store.groups.get(r.key).processing_enabled
    )
    assert not r.app.store.rows("SELECT * FROM reservations")


def test_retained_cached_poster_does_not_authorize_missing_video_file(retained_rig):
    r = retained_rig
    media_source(r, "video")
    data = picture()
    r.app.transport.media[r.peer, 1, "poster"] = data
    url = media_url(r)
    cached = r.client.get(url + "?revision=1&variant=poster")
    assert cached.status_code == 200 and cached.content == data
    save_draft(r)
    r.now[0] += WINDOW + 1
    baseline = native_calls(r)
    poster = r.client.get(url + "?revision=1&variant=poster")
    assert poster.status_code == 200 and poster.content == data
    assert r.client.get(url + "?revision=1").status_code == 400
    assert (
        r.client.post(url + "/prepare-video", json={"revision": 1}).status_code == 400
    )
    assert r.client.get(url + "?revision=1&download=true").status_code == 400
    assert native_calls(r) == baseline and not r.app.engine.calls


@pytest.mark.parametrize("kind", ["image", "gif", "sticker"])
@pytest.mark.parametrize("eligibility", ["no_work", "unsupported", "expired"])
def test_cached_body_and_validator_require_retained_eligibility_and_live_source(
    retained_rig, kind, eligibility
):
    r = retained_rig
    _, data = media_source(r, kind)
    url = media_url(r)
    if kind in ("gif", "sticker"):
        assert (
            r.client.post(url + "/prepare-preview", json={"revision": 1}).status_code
            == 200
        )
    first = r.client.get(url + "?revision=1")
    assert first.status_code == 200 and first.content == data
    if eligibility != "no_work":
        save_draft(r)
    r.now[0] += RETENTION + 1 if eligibility == "expired" else WINDOW + 1
    if eligibility == "unsupported":
        r.app.store.groups.observe(
            r.key, supported=False, access="lost", reason="authored_broadcast"
        )
    baseline = native_calls(r)
    for headers in ({}, {"If-None-Match": first.headers["etag"]}):
        result = r.client.get(url + "?revision=1", headers=headers)
        assert result.status_code == 400 and result.content != data
    if kind in ("gif", "sticker"):
        for endpoint in (
            "/representation?revision=1",
            "/preview?revision=1",
            "/animation?revision=1",
        ):
            result = r.client.get(url + endpoint)
            assert result.status_code == 400 and result.content != data
        assert (
            r.client.post(url + "/prepare-preview", json={"revision": 1}).status_code
            == 400
        )
    assert native_calls(r) == baseline and not r.app.engine.calls


@pytest.mark.parametrize(
    "scope", ["account", "typed_peer", "unknown_peer", "invalid_peer"]
)
def test_retained_media_bytes_and_validators_do_not_cross_exact_scope(
    retained_rig, scope
):
    r = retained_rig
    _, data = media_source(r, "image")
    url = media_url(r)
    first = r.client.get(url + "?revision=1")
    assert first.status_code == 200
    save_draft(r)
    r.now[0] += WINDOW + 1
    if scope == "account":
        url = url.replace("accounts/1", "accounts/2")
    elif scope == "typed_peer":
        url = url.replace(r.peer, "channel:42" if r.peer == "chat:42" else "chat:42")
    elif scope == "unknown_peer":
        url = url.replace(r.peer, "chat:84")
    else:
        url = url.replace(r.peer, "42")
    baseline = native_calls(r)
    for headers in ({}, {"If-None-Match": first.headers["etag"]}):
        result = r.client.get(url + "?revision=1", headers=headers)
        assert result.status_code == 400 and result.content != data
    assert native_calls(r) == baseline


def test_retained_owner_draft_image_survives_source_expiry_with_exact_ownership(
    retained_rig,
):
    r = retained_rig
    authored_source(r)
    data = picture()
    before = save_draft(r, "Authored draft with retained image", reply_to=1)
    added = r.client.post(
        r.base + "/images",
        json={
            "revision": before["revision"],
            "name": "authored.png",
            "data": base64.b64encode(data).decode(),
        },
    )
    assert added.status_code == 200, added.text
    saved = added.json()
    identity = saved["attachments"][0]
    url = r.base + f"/images/{identity}"
    r.now[0] += WINDOW + 1
    cached = r.client.get(url)
    assert cached.status_code == 200 and cached.content == data
    assert r.client.get(r.retained).json()[0]["reasons"] == ["draft"]
    r.now[0] += RETENTION + 1
    r.app.store.expire()
    r.app.attachments.collect()
    preserved = r.client.get(url)
    assert preserved.status_code == 200 and preserved.content == data
    assert r.client.get(r.base).json()["messages"] == []
    for foreign in (
        url.replace("accounts/1", "accounts/2"),
        url.replace(r.peer, "channel:42" if r.peer == "chat:42" else "chat:42"),
        r.base + "/images/" + "a" * 32,
    ):
        refused = r.client.get(foreign)
        assert refused.status_code == 400 and refused.content != data
    assert r.app.store.draft(r.key) == saved
    assert not any(native_calls(r).values()) and not r.app.engine.calls


def test_inactive_group_avatar_can_reuse_cache_without_native_refresh(retained_rig):
    r = retained_rig
    r.app.store.put_chat(
        r.app.store.chat(r.key).model_copy(update={"photo_id": "123"}),
        activity=r.now[0],
    )
    data = picture()
    r.app.transport.avatars[r.peer, "123"] = data
    url = f"/api/accounts/1/avatars/{r.peer}?photo_id=123"
    first = r.client.get(url)
    assert first.status_code == 200 and first.content == data
    save_draft(r)
    r.now[0] += WINDOW + 1
    baseline = native_calls(r)
    cached = r.client.get(url)
    assert cached.status_code == 200 and cached.content == data
    r.app.avatars.clear()
    missing = r.client.get(url)
    assert missing.status_code == 400 and missing.content != data
    assert native_calls(r) == baseline and not r.app.engine.calls


@pytest.mark.parametrize("stage", ["original", "avatar", "catalog"])
def test_native_preparation_rechecks_activity_after_wait_before_publication(
    retained_rig, monkeypatch, stage
):
    r = retained_rig
    saved = save_draft(r)
    if stage == "original":
        _, data = media_source(r, "image")
        method = "download"
        url = media_url(r) + "?revision=1"
    elif stage == "avatar":
        data = picture()
        r.app.store.put_chat(
            r.app.store.chat(r.key).model_copy(update={"photo_id": "123"}),
            activity=r.now[0],
        )
        r.app.transport.avatars[r.peer, "123"] = data
        method = "download_avatar"
        url = f"/api/accounts/1/avatars/{r.peer}?photo_id=123"
    else:
        before = r.client.get(r.base + "/catalog")
        assert before.status_code == 200
        method = "catalog_library"
    native = getattr(r.app.transport, method)

    async def became_inactive(*args, **kwargs):
        result = await native(*args, **kwargs)
        r.now[0] += WINDOW + 1
        return result

    monkeypatch.setattr(r.app.transport, method, became_inactive)
    response = (
        r.client.post(
            r.base + "/catalog/refresh", json={"revision": before.json()["revision"]}
        )
        if stage == "catalog"
        else r.client.get(url)
    )
    assert response.status_code == 400, response.text
    if stage != "catalog":
        assert response.content != data
    assert r.app.store.draft(r.key) == saved
    assert r.client.get(r.retained).json()[0]["reasons"] == ["draft"]
    assert r.app.media.cache.usage()["files"] == 0
    assert not r.app.store.rows("SELECT * FROM avatars")
    assert r.app.catalog.storage.get("library") is None
    assert not r.app.engine.calls and not r.app.store.rows("SELECT * FROM reservations")
