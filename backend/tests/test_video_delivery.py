"""VP1-VP2: explicit bounded video preparation and authenticated cached reads.

The MP4 fixture is an authored test video. All source transfers are isolated
fake/in-memory transport; these checks never connect to Telegram or a provider.
"""

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.application.media import DOWNLOAD_BYTES, IMAGE_BYTES
from enloq.application.media_cache import LIMIT, evict
from enloq.config import Settings
from enloq.domain.models import (
    MediaInfo,
    MediaTooLarge,
    Message,
    Refused,
    Unavailable,
)
from enloq.storage.store import RETENTION
from enloq.transport.mtproto import PLAYBACK
from enloq.transport.telethon import PersonalTransport
from fastapi.testclient import TestClient
from PIL import Image
from telethon import types

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message

VIDEO = Path(__file__).with_name("fixtures") / "circle.mp4"
BASE = "/api/accounts/1/chats/user:101/originals/11/media"


def video_source(app, identity=1, **kwargs):
    return message(
        app,
        identity,
        text="Authored video caption.",
        media=kwargs.pop("media", "video"),
        media_info=kwargs.pop(
            "media_info", MediaInfo(file_name="authored.mp4", mime="video/mp4")
        ),
        **kwargs,
    )


@pytest.fixture
def served_video(tmp_path):
    server = create_app(Settings(data=tmp_path / "app"))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        app = server.state.accounts.get(1)
        source = Message(
            account_id=1,
            peer="user:101",
            message_id=11,
            revision=1,
            date=app.store.clock(),
            media="video",
            media_info=MediaInfo(file_name="authored.mp4", mime="video/mp4"),
        )
        app.sync.ingest(source, target=False)
        app.transport.media["user:101", 11] = VIDEO.read_bytes()
        yield server, client, app


def prepare(client, *, revision=1, version=None, base=BASE):
    return client.post(
        base + "/prepare-video", json={"revision": revision, "version": version}
    )


def test_first_play_ranges_replay_and_revalidation_are_cached_only(served_video):
    _, client, app = served_video
    for headers in ({}, {"Range": "bytes=0-100"}, {"If-None-Match": '"invented"'}):
        assert client.get(BASE + "?revision=1", headers=headers).status_code == 400
    assert not app.transport.downloads
    result = prepare(client)
    assert result.status_code == 200 and result.json() == {
        "state": "ready",
        "revision": 1,
        "version": None,
        "mime": "video/mp4",
    }
    assert app.transport.downloads == [("user:101", 11, PLAYBACK, False)]
    assert prepare(client).json()["state"] == "ready"
    complete = client.get(BASE + "?revision=1")
    assert complete.status_code == 200 and complete.content == VIDEO.read_bytes()
    assert complete.headers["content-type"] == "video/mp4"
    assert complete.headers["cache-control"] == "private, no-cache"
    assert complete.headers["accept-ranges"] == "bytes"
    seek = client.get(BASE + "?revision=1", headers={"Range": "bytes=100-199"})
    assert seek.status_code == 206 and seek.content == VIDEO.read_bytes()[100:200]
    assert (
        client.get(
            BASE + "?revision=1", headers={"If-None-Match": complete.headers["etag"]}
        ).status_code
        == 304
    )
    assert len(app.transport.downloads) == 1
    app.media.cache.clear()
    assert (
        client.get(
            BASE + "?revision=1", headers={"If-None-Match": complete.headers["etag"]}
        ).status_code
        == 400
    )
    assert (
        client.get(BASE + "?revision=1", headers={"Range": "bytes=0-9"}).status_code
        == 400
    )
    assert len(app.transport.downloads) == 1
    assert not list(app.media.workspace.path.glob("*.bin"))


def test_unplayed_download_streams_then_prepared_download_reuses_bytes(served_video):
    _, client, app = served_video
    original = client.get(BASE + "?revision=1&download=1")
    assert original.status_code == 200 and original.content == VIDEO.read_bytes()
    assert "attachment" in original.headers["content-disposition"]
    assert "authored.mp4" in original.headers["content-disposition"]
    assert app.media.cache.usage()["files"] == 0
    assert len(app.transport.downloads) == 1
    assert prepare(client).status_code == 200
    again = client.get(BASE + "?revision=1&download=1")
    assert again.status_code == 200 and again.content == original.content
    assert len(app.transport.downloads) == 2


@pytest.mark.parametrize(
    "payload", [b"%PDF-authored", b"\x00\x00\x00\x18ftypmp42" + bytes(64), b""]
)
def test_unsupported_complete_bytes_remain_available_only_as_download(
    served_video, payload
):
    _, client, app = served_video
    app.transport.media["user:101", 11] = payload
    result = prepare(client)
    assert result.status_code == 200 and result.json()["state"] == "unsupported"
    assert result.json()["mime"] is None
    assert app.media.cache.usage()["files"] == 1
    etag = app.media.etag(app.store.message("1/user:101", 11))
    assert client.get(BASE + "?revision=1").status_code == 400
    assert (
        client.get(BASE + "?revision=1", headers={"If-None-Match": etag}).status_code
        == 400
    )
    download = client.get(BASE + "?revision=1&download=1")
    assert download.status_code == 200 and download.content == payload
    assert download.headers["content-type"] == "application/octet-stream"
    assert len(app.transport.downloads) == 1


def test_video_document_preparation_preserves_source_and_ai_derivatives(served_video):
    _, client, app = served_video
    source = app.store.message("1/user:101", 11).model_copy(
        update={
            "media": "document",
            "media_info": MediaInfo(
                file_name="authored.MOV", mime="application/octet-stream"
            ),
        }
    )
    app.sync.ingest(source, target=False)
    with app.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (source.key, source.revision, '{"authored":"retained"}'),
        )
    before = app.store.rows("SELECT * FROM derivatives")
    assert prepare(client).json()["state"] == "ready"
    assert app.store.message("1/user:101", 11).media == "document"
    assert app.store.message("1/user:101", 11).revision == source.revision
    assert app.store.rows("SELECT * FROM derivatives") == before
    assert not app.engine.calls
    assert not app.store.rows("SELECT * FROM jobs")


def test_source_revision_is_required_and_stale_or_missing_source_never_transfers(
    served_video,
):
    _, client, app = served_video
    for url in (
        BASE,
        BASE + "?revision=2",
        BASE + "?revision=2&download=1",
        BASE + "?revision=2&variant=poster",
    ):
        assert client.get(url).status_code == 400
    assert prepare(client, revision=2).status_code == 400
    assert prepare(client, base=BASE.replace("/11/", "/999/")).status_code == 400
    assert (
        prepare(client, base=BASE.replace("/accounts/1/", "/accounts/2/")).status_code
        == 400
    )
    assert not app.transport.downloads
    chat = app.store.chat("1/user:101")
    app.store.put_chat(chat.model_copy(update={"kind": "group"}))
    assert prepare(client).status_code == 400
    assert client.get(BASE + "?revision=1&download=1").status_code == 400
    assert not app.transport.downloads


def test_preparation_requires_authenticated_same_origin_json(served_video):
    _, client, app = served_video
    assert (
        client.post(
            BASE + "/prepare-video",
            json={"revision": 1},
            headers={"Origin": "https://elsewhere.test"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            BASE + "/prepare-video",
            content='{"revision":1}',
            headers={"Content-Type": "text/plain"},
        ).status_code
        == 403
    )
    cookie = dict(client.cookies)
    client.cookies.clear()
    assert prepare(client).status_code == 401
    client.cookies.update(cookie)
    assert not app.transport.downloads


def test_history_requires_exact_retained_version_before_body_and_304(served_video):
    _, client, app = served_video
    assert prepare(client).status_code == 200
    original = app.store.message("1/user:101", 11)

    async def unexpected_exact(*args):
        pytest.fail("Historical requests must not read the native source")

    app.transport.exact = unexpected_exact
    app.sync.ingest(
        original.model_copy(update={"revision": 2, "text": "Authored edit."}),
        target=False,
    )
    version = app.store.history.rows("1/user:101", 11)[0]["id"]
    assert prepare(client, revision=1, version=version).status_code == 200
    url = BASE + f"?revision=1&version={version}"
    retained = client.get(url)
    assert retained.status_code == 200 and retained.content == VIDEO.read_bytes()
    assert (
        client.get(url, headers={"If-None-Match": retained.headers["etag"]}).status_code
        == 304
    )
    assert (
        client.get(
            BASE + "?revision=2", headers={"If-None-Match": retained.headers["etag"]}
        ).status_code
        == 400
    )
    app.media.cache.clear()
    assert prepare(client, revision=1, version=version).status_code == 400
    for suffix in (
        f"?revision=1&version={version}",
        f"?revision=1&version={version}&download=1",
        "?version=999999",
    ):
        assert (
            client.get(
                BASE + suffix, headers={"If-None-Match": retained.headers["etag"]}
            ).status_code
            == 400
        )
    assert len(app.transport.downloads) == 1
    assert not app.transport.exact_reads


def test_unavailable_video_verifier_keeps_download_and_explicit_retry_uses_cache(
    served_video, monkeypatch
):
    from enloq.application import media

    _, client, app = served_video
    verifier = media.video_mime

    async def unavailable(path):
        raise Unavailable("Video verification temporarily unavailable; try again")

    monkeypatch.setattr(media, "video_mime", unavailable)
    result = prepare(client)
    assert (
        result.status_code == 400
        and "temporarily unavailable" in result.json()["detail"]
    )
    assert app.media.cache.usage()["files"] == 1
    assert not app.media.video_verified
    assert client.get(BASE + "?revision=1").status_code == 400
    download = client.get(BASE + "?revision=1&download=1")
    assert download.status_code == 200 and download.content == VIDEO.read_bytes()
    assert len(app.transport.downloads) == 1
    monkeypatch.setattr(media, "video_mime", verifier)
    assert prepare(client).json()["state"] == "ready"
    assert client.get(BASE + "?revision=1").status_code == 200
    assert len(app.transport.downloads) == 1


@pytest.mark.parametrize(
    "change", ["edit", "delete", "expiry", "account", "browser", "eviction"]
)
def test_cached_verification_cannot_return_body_or_304_after_access_changes(
    served_video, monkeypatch, change
):
    from enloq.application import media

    server, client, app = served_video
    assert prepare(client).status_code == 200
    source = app.store.message("1/user:101", 11)
    now = app.store.clock()
    browser = client.get("/api/auth/browsers").json()[0]["id"]
    verifier = media.video_mime
    app.media.video_verified.clear()

    async def changed(path):
        result = await verifier(path)
        if change == "edit":
            app.sync.ingest(
                source.model_copy(update={"revision": 2, "text": "Authored edit."}),
                target=False,
            )
        elif change == "delete":
            app.sync.deleted("user:101", [11])
        elif change == "expiry":
            app.store.clock = lambda: now + RETENTION + 1
        elif change == "account":
            app.media.authorized = lambda: False
        elif change == "browser":
            server.state.browser_auth.revoke(browser)
        else:
            app.media.cache.clear()
        return result

    monkeypatch.setattr(media, "video_mime", changed)
    result = client.get(
        BASE + "?revision=1", headers={"If-None-Match": app.media.etag(source)}
    )
    assert result.status_code == (401 if change == "browser" else 400)
    assert len(app.transport.downloads) == 1
    assert not list(app.media.workspace.path.glob("*.bin"))


def test_failed_transfer_requires_deliberate_retry_and_leaves_no_partial_file(
    served_video,
):
    _, client, app = served_video
    download = app.transport.download
    calls = []

    async def interrupted(peer, identity, path, **kwargs):
        calls.append((peer, identity))
        path.write_bytes(b"authored partial bytes")
        raise Unavailable("Authored interrupted transfer")

    app.transport.download = interrupted
    assert prepare(client).status_code == 400
    assert app.media.cache.usage()["files"] == 0
    assert not list(app.media.cache.directory.iterdir())
    assert (
        client.get(BASE + "?revision=1", headers={"Range": "bytes=0-9"}).status_code
        == 400
    )
    assert calls == [("user:101", 11)]
    app.transport.download = download
    assert prepare(client).json()["state"] == "ready"
    assert len(app.transport.downloads) == 1


@pytest.mark.parametrize("change", ["edit", "delete", "expiry", "account", "browser"])
def test_transfer_completion_rechecks_source_account_expiry_and_browser(
    served_video, change
):
    server, client, app = served_video
    download = app.transport.download
    source = app.store.message("1/user:101", 11)
    now = app.store.clock()
    browser = client.get("/api/auth/browsers").json()[0]["id"]

    async def changed(*args, **kwargs):
        await download(*args, **kwargs)
        if change == "edit":
            app.sync.ingest(
                source.model_copy(update={"revision": 2, "text": "Authored edit."}),
                target=False,
            )
        elif change == "delete":
            app.sync.deleted("user:101", [11])
        elif change == "expiry":
            app.store.clock = lambda: now + RETENTION + 1
        elif change == "account":
            app.media.authorized = lambda: False
        else:
            server.state.browser_auth.revoke(browser)

    app.transport.download = changed
    result = prepare(client)
    assert result.status_code == (401 if change == "browser" else 400)
    etag = app.media.etag(source)
    result = client.get(BASE + "?revision=1", headers={"If-None-Match": etag})
    assert result.status_code in (400, 401)
    if change in ("edit", "delete", "expiry"):
        assert app.media.cache.usage()["files"] == 0
    assert not list(app.media.cache.directory.glob("*.part"))


def test_video_and_poster_bounds_are_separate(served_video):
    _, client, app = served_video
    source = app.store.message("1/user:101", 11).model_copy(
        update={"media_info": MediaInfo(file_name="large.mp4", size=DOWNLOAD_BYTES + 1)}
    )
    app.sync.ingest(source, target=False)
    poster = BytesIO()
    Image.new("RGB", (90, 70), "blue").save(poster, format="JPEG")
    app.transport.media["user:101", 11, "poster"] = poster.getvalue()
    assert prepare(client).status_code == 400
    assert not app.transport.downloads
    frame = client.get(BASE + "?revision=1&variant=poster")
    assert frame.status_code == 200 and frame.content == poster.getvalue()
    assert app.transport.downloads == [("user:101", 11, PLAYBACK, True)]
    assert DOWNLOAD_BYTES == 50 * 1024 * 1024 and IMAGE_BYTES == 5 * 1024 * 1024


def test_transport_ignoring_actual_size_limit_cannot_retain_oversized_video(
    served_video,
):
    _, client, app = served_video

    async def oversized(peer, identity, path, **kwargs):
        with path.open("wb") as output:
            output.write(VIDEO.read_bytes())
            output.truncate(DOWNLOAD_BYTES + 1)

    app.transport.download = oversized
    assert prepare(client).status_code == 400
    assert app.media.cache.usage() == {"files": 0, "bytes": 0}
    assert not list(app.media.cache.directory.iterdir())


def test_retained_actual_size_is_checked_before_revalidation(served_video):
    _, client, app = served_video
    assert prepare(client).status_code == 200
    source = app.store.message("1/user:101", 11)
    cached = app.media.cache.get(source)
    with cached["path"].open("r+b") as output:
        output.truncate(DOWNLOAD_BYTES + 1)
    result = client.get(
        BASE + "?revision=1", headers={"If-None-Match": app.media.etag(source)}
    )
    assert result.status_code == 400
    assert app.media.cache.usage()["files"] == 0
    assert len(app.transport.downloads) == 1


@pytest.mark.parametrize("declared_mime", ["video/mp4", "video/webm"])
def test_old_shallow_cache_mime_never_grants_inline_playback(
    served_video, declared_mime
):
    _, client, app = served_video
    source = app.store.message("1/user:101", 11)
    entry = app.media.cache.entry(source)
    spoofed = b"\x00\x00\x00\x18ftypmp42" + bytes(64)
    app.media.cache.path(entry).write_bytes(spoofed)
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO media_files VALUES (?,?,?,?,?,?,?)",
            (entry, 1, "file", len(spoofed), declared_mime, source.date, source.date),
        )
    assert (
        client.get(
            BASE + "?revision=1", headers={"If-None-Match": app.media.etag(source)}
        ).status_code
        == 400
    )
    assert prepare(client).json()["state"] == "unsupported"
    assert client.get(BASE + "?revision=1&download=1").content == spoofed
    assert not app.transport.downloads


async def test_simultaneous_first_play_coalesces_and_no_video_prefetch(tmp_path):
    app = fixture(tmp_path)
    source = video_source(app)
    app.sync.ingest(source)
    app.transport.media["user:42", 1] = VIDEO.read_bytes()
    await asyncio.gather(*app.tasks)
    assert not app.transport.downloads
    release, started = asyncio.Event(), asyncio.Event()
    download = app.transport.download

    async def slow(*args, **kwargs):
        started.set()
        await release.wait()
        return await download(*args, **kwargs)

    app.transport.download = slow
    reads = [asyncio.create_task(app.media.prepare_video(source)) for _ in range(3)]
    await started.wait()
    release.set()
    assert await asyncio.gather(*reads) == ["video/mp4"] * 3
    assert app.transport.downloads == [("user:42", 1, PLAYBACK, False)]


async def test_video_bytes_share_cross_account_lru_and_source_age_expiry(tmp_path):
    now = [2_000_000_000.0]
    a, b = (
        fixture(tmp_path, clock=lambda: now[0]),
        fixture(tmp_path, account=2, clock=lambda: now[0]),
    )
    caches = [a.media.cache, b.media.cache]
    for cache in caches:
        cache.evict = lambda: evict(caches, 2 * VIDEO.stat().st_size)
    for app, identity in ((a, 1), (b, 1), (a, 2)):
        now[0] += 1
        source = video_source(app, identity)
        app.sync.ingest(source, target=False)
        app.transport.media["user:42", identity] = VIDEO.read_bytes()
        assert await app.media.prepare_video(source) == "video/mp4"
    assert LIMIT == 2 * 1024**3
    assert a.media.cache.get(a.store.message("1/user:42", 1)) is None
    assert b.media.cache.get(b.store.message("2/user:42", 1)) is not None
    now[0] += RETENTION + 1
    expired = a.store.message("1/user:42", 2)
    with pytest.raises(Refused, match="expired"):
        await a.media.prepare_video(expired)
    for cache in caches:
        cache.collect()
        assert cache.usage()["files"] == 0
    assert len(a.transport.downloads) == 2 and len(b.transport.downloads) == 1


@pytest.mark.parametrize("thumbnail", ["ordinary", "progressive"])
async def test_native_small_thumbnail_of_large_parent_and_selected_poster_limit(
    tmp_path, thumbnail
):
    """The real transport checks the selected thumbnail, then actual parts."""
    calls = []
    thumb = (
        types.PhotoSize(type="m", w=90, h=90, size=512)
        if thumbnail == "ordinary"
        else types.PhotoSizeProgressive(type="m", w=90, h=90, sizes=[200, 512])
    )
    document = types.Document(
        id=11,
        access_hash=1,
        file_reference=b"authored",
        date=datetime.now(UTC),
        mime_type="video/mp4",
        size=DOWNLOAD_BYTES + 1,
        dc_id=1,
        attributes=[],
        thumbs=[thumb],
    )

    class Client:
        def is_connected(self):
            return True

        async def get_messages(self, peer, ids):
            return SimpleNamespace(media=object(), document=document, photo=None)

        async def iter_parts(self, asset, **kwargs):
            calls.append(asset)
            yield b"authored-thumbnail"

    transport = object.__new__(PersonalTransport)
    transport.client, transport.verified = Client(), True
    transport.observation = lambda: 0
    data = b"".join(
        [
            chunk
            async for chunk in transport.stream(
                "user:42", 11, max_bytes=IMAGE_BYTES, thumb=True
            )
        ]
    )
    assert data == b"authored-thumbnail" and calls[0].thumb_size == "m"
    if thumbnail == "ordinary":
        thumb.size = IMAGE_BYTES + 1
    else:
        thumb.sizes = [200, IMAGE_BYTES + 1]
    with pytest.raises(MediaTooLarge, match="Poster"):
        await anext(transport.stream("user:42", 11, max_bytes=IMAGE_BYTES, thumb=True))
    assert len(calls) == 1
    with pytest.raises(MediaTooLarge):
        await anext(transport.stream("user:42", 11, max_bytes=DOWNLOAD_BYTES))
