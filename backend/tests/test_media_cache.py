"""E2: private media cache, revalidation, byte ranges, prefetch and cleanup."""

import asyncio
from pathlib import Path

import pytest
from enloq.api.server import create_app
from enloq.application.media_cache import MediaCache, evict
from enloq.config import Settings
from enloq.domain.models import MediaInfo, Message
from enloq.storage.store import RETENTION
from enloq.transport.mtproto import PLAYBACK, PREFETCH, PREPARATION
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message

FIXTURES = Path(__file__).with_name("fixtures")
BASE = "/api/accounts/1/chats/user:101/originals"


def voice(app, identity=1, **kwargs):
    return message(
        app,
        identity,
        text="",
        media="audio",
        media_info=MediaInfo(voice=True),
        **kwargs,
    )


@pytest.fixture
def served(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        conversation = app.state.accounts.get(1)
        tone = (FIXTURES / "tone.ogg").read_bytes()
        conversation.transport.media["user:101", 7] = tone
        conversation.sync.ingest(
            Message(
                account_id=1,
                peer="user:101",
                message_id=7,
                revision=1,
                date=conversation.store.clock(),
                media="audio",
                media_info=MediaInfo(voice=True),
            ),
            target=False,
        )
        yield app, client, conversation, tone


def test_replay_seek_and_revalidation_read_no_telegram_bytes(served):
    app, client, conversation, tone = served
    first = client.get(f"{BASE}/7/media")
    assert first.status_code == 200 and first.content == tone
    assert first.headers["content-type"] == "audio/ogg"
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.headers["accept-ranges"] == "bytes"
    etag = first.headers["etag"]
    assert not etag.startswith("W/")
    seek = client.get(f"{BASE}/7/media", headers={"Range": "bytes=100-299"})
    assert seek.status_code == 206 and seek.content == tone[100:300]
    assert seek.headers["content-range"] == f"bytes 100-299/{len(tone)}"
    tail = client.get(f"{BASE}/7/media", headers={"Range": "bytes=-50"})
    assert tail.content == tone[-50:]
    again = client.get(f"{BASE}/7/media", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.headers["etag"] == etag
    assert len(conversation.transport.downloads) == 1
    conversation.media.cache.clear()
    # The validator needs no cached bytes, so a browser copy revalidates offline.
    assert (
        client.get(f"{BASE}/7/media", headers={"If-None-Match": etag}).status_code
        == 304
    )
    assert len(conversation.transport.downloads) == 1
    assert not list(conversation.media.workspace.path.glob("*.bin"))


def test_edit_changes_validator_and_preserves_history_bytes(served):
    app, client, conversation, tone = served
    etag = client.get(f"{BASE}/7/media").headers["etag"]
    assert conversation.media.cache.usage()["files"] == 1
    edited = conversation.store.message("1/user:101", 7).model_copy(
        update={"revision": 2}
    )
    conversation.sync.ingest(edited, target=False)
    assert conversation.media.cache.usage()["files"] == 1
    fresh = client.get(f"{BASE}/7/media", headers={"If-None-Match": etag})
    assert fresh.status_code == 200 and fresh.headers["etag"] != etag
    conversation.sync.deleted("user:101", [7])
    assert conversation.media.cache.usage()["files"] == 2
    # ADR 52 Q7: a deleted message keeps its last bytes, from the cache only.
    kept = client.get(f"{BASE}/7/media")
    assert kept.status_code == 200 and kept.headers["etag"] == fresh.headers["etag"]
    assert len(conversation.transport.downloads) == 2


def test_headers_json_documents_assets_and_revoked_browser(served, tmp_path):
    app, client, conversation, tone = served
    assert client.get("/api/accounts").headers["cache-control"] == "no-store"

    built = sorted((Path(__file__).parents[2] / "web/dist/assets").glob("*.js"))
    asset = f"/assets/{built[0].name}" if built else "/assets/probe-abc123.js"
    if not built:  # Without a production build no static mount shadows this.

        @app.get(asset)
        def probe():
            return "bundle"

    assert (
        client.get(asset).headers["cache-control"]
        == "public, max-age=31536000, immutable"
    )
    assert client.get("/assets/missing-0.js").headers["cache-control"] == "no-store"
    conversation.transport.media["user:101", 8] = b"%PDF-authored"
    conversation.sync.ingest(
        Message(
            account_id=1,
            peer="user:101",
            message_id=8,
            revision=1,
            date=conversation.store.clock(),
            media="document",
            media_info=MediaInfo(file_name="plan.pdf"),
        ),
        target=False,
    )
    document = client.get(f"{BASE}/8/media?download=1")
    assert document.headers["cache-control"] == "no-store"
    assert "plan.pdf" in document.headers["content-disposition"]
    assert conversation.media.cache.usage()["files"] == 0  # Streamed, not kept.
    download = client.get(f"{BASE}/7/media?download=1")
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-disposition"].startswith("attachment")
    browsers = client.get("/api/auth/browsers").json()
    app.state.browser_auth.revoke(browsers[0]["id"])
    revoked = client.get(f"{BASE}/7/media")
    assert revoked.status_code == 401
    assert revoked.headers["clear-site-data"] == '"cache"'


def test_logout_and_removal_clear_the_cache_and_browser_copies(served):
    app, client, conversation, tone = served
    client.get(f"{BASE}/7/media")
    directory = conversation.media.cache.directory
    assert list(directory.iterdir())
    response = client.post(
        "/api/accounts/1/logout",
        json={},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.headers["clear-site-data"] == '"cache"'
    assert not list(directory.iterdir())
    removal = client.post(
        "/api/accounts/1/remove-local-data",
        json={"confirm": True},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert removal.headers["clear-site-data"] == '"cache"'
    assert not directory.exists()


async def test_limits_lru_eviction_expiry_and_orphans(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    b = fixture(tmp_path, account=2, clock=lambda: now[0])
    for app in (a, b):
        for identity in (1, 2):
            app.transport.media["user:42", identity] = bytes([identity]) * 1000
            app.sync.ingest(voice(app, identity), target=False)
    caches = [a.media.cache, b.media.cache]
    for cache in caches:
        cache.evict = lambda: evict(caches, 3000)
    for app in (a, b):
        for identity in (1, 2):
            now[0] += 1
            await app.media.cache.fetch(
                app.store.message(f"{app.store.account_id}/user:42", identity),
                limit=5000,
                priority=PREPARATION,
            )
    # One shared limit across accounts: only the least recently used left.
    assert a.media.cache.get(a.store.message("1/user:42", 1)) is None
    assert a.media.cache.get(a.store.message("1/user:42", 2))
    assert b.media.cache.usage() == {"files": 2, "bytes": 2000}
    with pytest.raises(Exception, match="size limit"):
        await b.media.cache.fetch(
            b.store.message("2/user:42", 1), limit=10, priority=PREPARATION
        )
    orphan = b.media.cache.directory / "left-behind.bin"
    orphan.write_bytes(b"x")
    now[0] += RETENTION
    b.media.cache.collect()
    assert b.media.cache.usage()["files"] == 0
    assert not list(b.media.cache.directory.iterdir())


async def test_prefetch_new_arrivals_only_and_whisper_reuses_bytes(tmp_path):
    a = fixture(tmp_path)
    for identity in (1, 2, 3, 4):
        a.transport.media["user:42", identity] = b"OggS" + bytes([identity]) * 64

    class Speech:
        paths = []

        async def transcribe(self, path):
            self.paths.append(path.read_bytes())
            return {"text": "Authored transcript", "language": "en"}

    a.media.transcriber = Speech()
    a.sync.ingest(voice(a, 1))  # A live arrival.
    a.sync.ingest(voice(a, 2), target=False)  # History page: no back-fill.
    a.transport.messages = [voice(a, 3)]
    await a.sync.scan("1/user:42")  # Recent-history scan: not an arrival.
    a.sync.ingest(voice(a, 4, outgoing=True))  # Own voice note.
    await asyncio.gather(*a.tasks)
    # The owner's own note is not prefetched; its transcription reads it
    # (ADR 51 Q8).
    assert a.transport.downloads == [
        ("user:42", 1, PREFETCH, False),
        ("user:42", 4, PREPARATION, False),
    ]
    transcript = await a.media.transcript(a.store.message("1/user:42", 1))
    assert transcript["text"] == "Authored transcript"
    assert Speech.paths == [b"OggS" + bytes([4]) * 64, b"OggS" + bytes([1]) * 64]
    assert len(a.transport.downloads) == 2  # Whisper read the cached bytes.


async def test_prefetch_continues_while_account_ai_is_off(tmp_path):
    a = fixture(tmp_path)
    from enloq.domain.models import ProcessingPaused

    def paused():
        raise ProcessingPaused("AI processing is Off for this account")

    a.admit_ai = a.media.admit = paused
    a.transport.media["user:42", 1] = b"OggS" + bytes(64)
    a.sync.ingest(
        message(a, 1, text="", media="video_note", media_info=MediaInfo(duration=4))
    )
    await asyncio.gather(*a.tasks)
    assert a.media.cache.usage()["files"] == 1


async def test_concurrent_reads_coalesce_and_edit_during_download_is_not_cached(
    tmp_path,
):
    a = fixture(tmp_path)
    a.sync.ingest(voice(a, 1), target=False)
    a.transport.media["user:42", 1] = b"OggS" + bytes(64)
    release = asyncio.Event()
    original = a.transport.download

    async def slow(*args, **kwargs):
        await release.wait()
        return await original(*args, **kwargs)

    a.transport.download = slow
    source = a.store.message("1/user:42", 1)
    reads = [
        asyncio.create_task(a.media.cache.fetch(source, limit=1000, priority=PLAYBACK))
        for _ in range(3)
    ]
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(*reads)
    assert len(a.transport.downloads) == 1
    a.media.cache.clear()
    release.clear()
    pending = asyncio.create_task(
        a.media.cache.fetch(source, limit=1000, priority=PLAYBACK)
    )
    await asyncio.sleep(0.01)
    a.sync.ingest(source.model_copy(update={"revision": 2}), target=False)
    release.set()
    with pytest.raises(Exception, match="changed"):
        await pending
    assert a.media.cache.usage()["files"] == 0


def test_cache_is_private(tmp_path):
    a = fixture(tmp_path)
    cache = MediaCache(a.store, a.transport)
    assert cache.directory.stat().st_mode & 0o777 == 0o700


def test_voice_and_circle_downloads_use_telegram_desktop_names(served):
    """ADR 27: kind plus the message's date and time in the configured zone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from enloq.api.routes import download_name

    app, client, conversation, tone = served
    date = conversation.store.message("1/user:101", 7).date
    stamp = datetime.fromtimestamp(
        date, ZoneInfo(conversation.store.timezone)
    ).strftime("%Y-%m-%d_%H-%M-%S")
    download = client.get(f"{BASE}/7/media?download=1")
    assert download.status_code == 200 and download.content == tone
    assert f"audio_{stamp}.ogg" in download.headers["content-disposition"]
    moment = datetime(2024, 3, 4, 15, 4, 5, tzinfo=ZoneInfo("UTC")).timestamp()
    circle = Message(
        account_id=1,
        peer="user:101",
        message_id=9,
        revision=1,
        date=moment,
        media="video_note",
        media_info=MediaInfo(mime="video/mp4"),
    )
    assert download_name(circle, "Asia/Ho_Chi_Minh") == "video_2024-03-04_22-04-05.mp4"
    named = circle.model_copy(
        update={
            "media": "audio",
            "media_info": MediaInfo(voice=True, mime="audio/mpeg", file_name="x.mp3"),
        }
    )
    assert download_name(named, "UTC") == "audio_2024-03-04_15-04-05.mp3"
    # Audio files and documents keep their own names.
    song = named.model_copy(
        update={"media_info": MediaInfo(file_name="song.mp3", title="Song")}
    )
    assert download_name(song, "UTC") == "song.mp3"


def test_voice_notes_have_one_cached_aac_copy_for_iphones(served, monkeypatch):
    """ADR 46 CU1: made from the cached source on the first request, kept beside
    it, and served like the source with byte ranges and revalidation."""
    from enloq.application import media

    app, client, conversation, tone = served
    conversions = []
    convert = media.aac_copy

    async def counted(*args):
        conversions.append(args[2])
        await convert(*args)

    monkeypatch.setattr(media, "aac_copy", counted)
    first = client.get(f"{BASE}/7/media?variant=aac")
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "audio/mp4"
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.content[4:8] == b"ftyp" and b"mp4a" in first.content
    etag = first.headers["etag"]
    assert etag != client.get(f"{BASE}/7/media").headers["etag"]
    assert conversions == ["audio/ogg"]
    assert len(conversation.transport.downloads) == 1  # One source for both.
    source = conversation.store.message("1/user:101", 7)
    kept = conversation.media.cache.get(source, "aac")
    assert kept["mime"] == "audio/mp4" and kept["date"] == source.date
    again = client.get(f"{BASE}/7/media?variant=aac")
    assert again.content == first.content and conversions == ["audio/ogg"]
    seek = client.get(f"{BASE}/7/media?variant=aac", headers={"Range": "bytes=0-99"})
    assert seek.status_code == 206 and seek.content == first.content[:100]
    revalidated = client.get(
        f"{BASE}/7/media?variant=aac", headers={"If-None-Match": etag}
    )
    assert revalidated.status_code == 304
    assert not list(conversation.media.workspace.path.glob("*.bin"))


def test_only_current_voice_notes_have_an_aac_copy(served):
    app, client, conversation, tone = served
    download = client.get(f"{BASE}/7/media?variant=aac&download=1")
    assert download.status_code == 400
    assert download.json()["detail"] == "Only a current voice note has an AAC copy"
    assert client.get(f"{BASE}/7/media?variant=aac&version=1").status_code == 400
    conversation.transport.media["user:101", 9] = tone
    conversation.sync.ingest(
        Message(
            account_id=1,
            peer="user:101",
            message_id=9,
            revision=1,
            date=conversation.store.clock(),
            media="audio",
            media_info=MediaInfo(file_name="song.ogg", title="Song"),
        ),
        target=False,
    )
    music = client.get(f"{BASE}/9/media?variant=aac")
    assert music.status_code == 400
    assert conversation.media.cache.usage()["files"] == 0


async def test_aac_copy_expires_with_its_source_and_follows_edits(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.transport.media["user:42", 1] = (FIXTURES / "tone.ogg").read_bytes()
    a.sync.ingest(voice(a, 1), target=False)
    source = a.store.message("1/user:42", 1)

    def allowed():
        return None

    kept = await a.media.voice_copy(
        source, priority=PLAYBACK, guard=allowed, revision=None
    )
    assert kept["variant"] == "aac" and a.media.cache.usage()["files"] == 2
    # Another revision is another copy; the old one stays only until collected.
    edited = source.model_copy(update={"revision": 2})
    assert a.media.cache.get(edited, "aac") is None
    now[0] += RETENTION + 1
    assert a.media.cache.get(source, "aac") is None
    a.media.cache.collect()
    assert a.media.cache.usage()["files"] == 0


async def test_a_failed_conversion_leaves_nothing_and_stays_retryable(
    tmp_path, monkeypatch
):
    from enloq.application import media
    from enloq.domain.models import Unavailable

    a = fixture(tmp_path)
    a.transport.media["user:42", 1] = b"OggS" + bytes(64)  # Not decodable.
    a.sync.ingest(voice(a, 1), target=False)
    source = a.store.message("1/user:42", 1)
    with pytest.raises(Unavailable, match="Voice copy unavailable"):
        await a.media.voice_copy(
            source, priority=PLAYBACK, guard=lambda: None, revision=None
        )
    assert a.media.cache.get(source, "aac") is None
    assert a.media.cache.usage()["files"] == 1  # The source stays cached.
    assert not list(a.media.cache.directory.glob("*.part"))
    monkeypatch.setattr(media, "aac_copy", None)  # A retry needs no new source.
    assert len(a.transport.downloads) == 1
