"""GS4 exact Non-AI received previews, history and current retained-send copies."""

import asyncio
import gzip
import hashlib
import json
import threading

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import MediaInfo, Refused, Unavailable
from enloq.domain.selection_formats import GIF_BYTES, STICKER_BYTES
from enloq.storage.store import RETENTION
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_catalog_assets import gif, still, tgs
from backend.tests.test_rebuild import fixture, message
from backend.tests.test_selected_media import media_bytes, stage
from backend.tests.test_selection_formats import (
    animation,
    mask,
)
from backend.tests.test_selection_formats import (
    containers as containers,
)

KEY = "1/user:42"
BASE = "/api/accounts/1/chats/user:101/originals/77/media"


def received(app, data, *, kind="sticker", sticker="static", identity=11, **kwargs):
    value = message(
        app,
        identity,
        text="Authored caption" if kind == "gif" else "",
        media=kind,
        media_info=MediaInfo(
            size=len(data), sticker=sticker if kind == "sticker" else None
        ),
        **kwargs,
    )
    app.transport.media[value.peer, identity] = data
    app.sync.ingest(value, target=False)
    return value


@pytest.mark.parametrize("format", ["webp", "tgs", "gif", "mp4", "webm"])
async def test_received_formats_are_lazy_exact_and_non_ai(tmp_path, containers, format):
    app = fixture(tmp_path)
    data = (
        containers[format].read_bytes()
        if format in ("mp4", "webm")
        else tgs()
        if format == "tgs"
        else gif()
        if format == "gif"
        else still(format="WEBP", size=(512, 128))
    )
    kind = "gif" if format in ("gif", "mp4") else "sticker"
    subtype = {"webp": "static", "tgs": "animated", "webm": "video"}.get(format)
    source = received(app, data, kind=kind, sticker=subtype)
    draft = app.store.draft(KEY)
    assert (await app.media.preview(source)).state == "missing"
    with pytest.raises(Refused):
        await app.media.serve_preview(source)
    await app.media.prefetch(source)
    assert not app.transport.downloads
    prepared = await app.media.prepare_preview(source)
    assert (prepared.state, prepared.format, prepared.renderer_supported) == (
        "ready",
        format,
        True,
    )
    cached = app.media.cache.get(source)
    assert cached["path"].read_bytes() == data
    assert cached["path"].stat().st_mode & 0o777 == 0o600
    facts = await app.media.selection_type(source, cached["path"])
    assert facts.sha256 == hashlib.sha256(data).hexdigest()
    if format == "tgs":
        assert json.loads(await app.media.animation(source))["w"] == 512
        with pytest.raises(Refused):
            await app.media.serve_preview(source)
    else:
        path, mime, etag = await app.media.serve_preview(source)
        assert path.read_bytes() == data and mime == facts.mime
        assert etag == '"' + facts.sha256 + '"'
        path.unlink()
    assert (await app.media.preview(source)).state == "ready"
    assert len(app.transport.downloads) == 1
    assert app.store.draft(KEY) == draft
    assert not app.engine.calls and not app.transport.exact_reads
    assert not app.store.rows("SELECT * FROM derivatives")
    assert app.store.message(KEY, source.message_id).media == kind


@pytest.mark.parametrize("mode", ["d", "f", "l", "tt4"])
async def test_received_unsupported_svg_features_keep_exact_original_fallback(
    tmp_path, mode
):
    app = fixture(tmp_path)
    value = animation()
    if mode == "tt4":
        matte = value["layers"][0].copy()
        matte["ind"] = 2
        value["layers"].append(matte)
        value["layers"][1]["tt"] = 4
    else:
        value["layers"][0]["masksProperties"] = [mask(mode)]
    data = gzip.compress(json.dumps(value).encode(), mtime=0)
    source = received(app, data, sticker="animated")
    prepared = await app.media.prepare_preview(source)
    assert prepared.state == "unsupported" and not prepared.renderer_supported
    assert prepared.format == "tgs"
    with pytest.raises(Refused):
        await app.media.animation(source)
    path, mime = await app.media.serve(source, inline=False)
    assert mime is None and path.read_bytes() == data
    path.unlink()
    assert len(app.transport.downloads) == 1


@pytest.mark.parametrize("invalid", ["corrupt", "wrong-native", "png"])
async def test_received_bad_or_mismatching_sticker_is_opaque_download(
    tmp_path, invalid
):
    app = fixture(tmp_path)
    data = (
        b"RIFF" + bytes(100)
        if invalid == "corrupt"
        else still()
        if invalid == "png"
        else still(format="WEBP", size=(512, 128))
    )
    source = received(
        app, data, sticker="video" if invalid == "wrong-native" else "static"
    )
    assert (await app.media.prepare_preview(source)).state == "unsupported"
    with pytest.raises(Refused):
        await app.media.serve_preview(source)
    path, mime = await app.media.serve(source)
    assert mime is None and path.read_bytes() == data
    path.unlink()
    assert not app.engine.calls and len(app.transport.downloads) == 1


@pytest.mark.parametrize("kind", ["gif", "sticker"])
async def test_received_declared_and_actual_caps_reject_without_inline(tmp_path, kind):
    app = fixture(tmp_path)
    cap = GIF_BYTES if kind == "gif" else STICKER_BYTES
    source = received(app, b"x", kind=kind)
    oversize = source.model_copy(update={"media_info": MediaInfo(size=cap + 1)})
    app.sync.ingest(oversize, target=False)
    with pytest.raises(Refused):
        await app.media.prepare_preview(oversize)
    assert not app.transport.downloads
    source = oversize.model_copy(update={"media_info": MediaInfo(size=1)})
    app.sync.ingest(source, target=False)
    app.transport.media[source.peer, source.message_id] = b"x" * (cap + 1)
    with pytest.raises(Refused):
        await app.media.prepare_preview(source)
    assert not app.media.cache.usage()["files"]
    assert not list(app.media.cache.directory.glob("*.part"))


async def test_received_verifier_outage_keeps_complete_bytes_and_deliberate_retry(
    tmp_path, containers, monkeypatch
):
    from enloq.domain import selection_formats

    app = fixture(tmp_path)
    source = received(app, containers["mp4"].read_bytes(), kind="gif")
    probe = selection_formats.bounded_process

    async def unavailable(*args, **kwargs):
        raise TimeoutError("private source diagnostics")

    monkeypatch.setattr(selection_formats, "bounded_process", unavailable)
    with pytest.raises(Unavailable, match="^Media verification unavailable$"):
        await app.media.prepare_preview(source)
    cached = app.media.cache.get(source)
    assert cached["path"].read_bytes() == containers["mp4"].read_bytes()
    path, mime = await app.media.serve(source, inline=False)
    assert mime is None and path.read_bytes() == cached["path"].read_bytes()
    path.unlink()
    monkeypatch.setattr(selection_formats, "bounded_process", probe)
    assert (await app.media.prepare_preview(source)).state == "ready"
    assert len(app.transport.downloads) == 1


async def test_history_never_fills_missing_bytes_or_consults_retained_sends(tmp_path):
    app = fixture(tmp_path)
    source = received(app, tgs(), sticker="animated", outgoing=True)
    await app.media.prepare_preview(source)
    app.sync.ingest(
        source.model_copy(update={"revision": 2, "text": "edited"}), target=False
    )
    version = app.store.history.rows(KEY, source.message_id)[0]["id"]
    prior = app.store.history.version(KEY, source.message_id, version)
    assert (await app.media.preview(prior, history=True)).state == "ready"
    assert json.loads(await app.media.animation(prior, history=True))["w"] == 512
    app.media.cache.clear()
    consulted = []
    app.media.retained_preview = lambda value: consulted.append(value)
    assert (await app.media.preview(prior, history=True)).state == "missing"
    for action in (app.media.prepare_preview, app.media.serve_preview, app.media.serve):
        with pytest.raises(Refused):
            await action(prior, history=True)
    assert not consulted and len(app.transport.downloads) == 1
    assert not app.transport.exact_reads


@pytest.mark.parametrize(
    "change", ["edit", "delete", "expiry", "account", "chat", "cache"]
)
async def test_received_late_native_completion_cannot_publish_withdrawn_source(
    tmp_path, change
):
    app = fixture(tmp_path)
    source = received(app, gif(), kind="gif")
    download = app.transport.download
    now = app.store.clock()

    async def changed(*args, **kwargs):
        await download(*args, **kwargs)
        if change == "edit":
            app.sync.ingest(source.model_copy(update={"revision": 2}), target=False)
        elif change == "delete":
            app.sync.deleted(source.peer, [source.message_id])
        elif change == "expiry":
            app.store.clock = lambda: now + RETENTION + 1
        elif change == "account":
            app.media.authorized = lambda: False
        elif change == "chat":
            app.store.put_chat(app.store.chat(KEY).model_copy(update={"kind": "bot"}))
        else:
            app.media.cache.clear()

    app.transport.download = changed
    with pytest.raises(Refused):
        await app.media.prepare_preview(source)
    assert not app.media.cache.usage()["files"]
    assert not list(app.media.cache.directory.glob("*.part"))


async def test_current_confirmed_owner_is_copied_only_on_explicit_fetch(tmp_path):
    app = fixture(tmp_path)
    selected = await stage(app, "sticker")
    operation = await app.delivery.send(KEY, selected["revision"])
    assert operation["state"] == "confirmed"
    source = app.store.message(KEY, operation["message_id"])
    owner = app.selected_media.message_file(source)
    assert owner is not None
    assert (await app.media.preview(source)).state == "missing"
    with pytest.raises(Refused):
        await app.media.serve_preview(source)
    assert not app.media.cache.usage()["files"] and not app.transport.downloads
    assert (await app.media.prepare_preview(source)).state == "ready"
    cached = app.media.cache.get(source)
    assert (
        cached["path"].read_bytes() == owner.path.read_bytes() == media_bytes("sticker")
    )
    assert cached["path"].stat().st_ino != owner.path.stat().st_ino
    assert cached["date"] == source.date
    app.media.cache.clear()
    assert owner.path.exists() and owner.path.read_bytes() == media_bytes("sticker")
    path, mime = await app.media.serve(source, inline=False)
    assert path.read_bytes() == media_bytes("sticker") and mime is None
    path.unlink()
    assert not app.transport.downloads
    assert app.media.cache.get(source)["path"].stat().st_ino != owner.path.stat().st_ino


@pytest.mark.parametrize(
    "change", ["history", "expired", "edited", "deleted", "wrong-id"]
)
async def test_retained_owner_lifetime_never_grants_an_unadmitted_source(
    tmp_path, change
):
    app = fixture(tmp_path)
    selected = await stage(app, "gif")
    operation = await app.delivery.send(KEY, selected["revision"])
    source = app.store.message(KEY, operation["message_id"])
    consulted = []
    original = app.media.retained_preview

    def retained(value):
        consulted.append(value)
        return original(value)

    app.media.retained_preview = retained
    if change == "history":
        history = True
    else:
        history = False
        if change == "expired":
            now = app.store.clock()
            app.store.clock = lambda: now + RETENTION + 1
        elif change == "edited":
            source = source.model_copy(
                update={
                    "revision": source.revision + 1,
                    "text": "edited",
                    "native_edited": True,
                }
            )
            app.sync.ingest(source, target=False)
            source = app.store.message(KEY, source.message_id)
        elif change == "deleted":
            app.sync.deleted(source.peer, [source.message_id])
        else:
            source = source.model_copy(update={"message_id": source.message_id + 1})
    if change == "edited":
        # A current edited native source can fetch itself, without an owner fill.
        assert (await app.media.prepare_preview(source)).state == "ready"
        assert len(app.transport.downloads) == 1
    else:
        with pytest.raises((Refused, Unavailable)):
            await app.media.prepare_preview(source, history=history)
        assert not app.media.cache.usage()["files"]
    assert not consulted
    assert app.selected_media.operation_file(
        KEY, operation["id"], operation["media"]["id"]
    )[1].exists()


@pytest.mark.parametrize("change", ["digest", "edit", "clear", "cancel", "symlink"])
async def test_retained_copy_checks_integrity_and_post_await_access(
    tmp_path, monkeypatch, change
):
    from enloq.application import media_cache

    app = fixture(tmp_path)
    selected = await stage(app, "sticker")
    operation = await app.delivery.send(KEY, selected["revision"])
    source = app.store.message(KEY, operation["message_id"])
    owner = app.selected_media.message_file(source)
    copying = media_cache._copy_retained

    def changed(*args, **kwargs):
        size = copying(*args, **kwargs)
        if change == "edit":
            app.sync.ingest(
                source.model_copy(
                    update={
                        "revision": source.revision + 1,
                        "text": "edited",
                        "native_edited": True,
                    }
                ),
                target=False,
            )
        elif change == "clear":
            app.media.cache.clear()
        return size

    if change == "digest":
        data = owner.path.read_bytes()
        owner.path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    elif change == "symlink":
        replacement = owner.path.with_suffix(".authored")
        owner.path.rename(replacement)
        owner.path.symlink_to(replacement)
    elif change == "cancel":
        entered, release = asyncio.Event(), asyncio.Event()
        original_thread = asyncio.to_thread

        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_thread(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", held)
        task = asyncio.create_task(app.media.prepare_preview(source))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        monkeypatch.setattr(media_cache, "_copy_retained", changed)
    if change != "cancel":
        with pytest.raises(Refused):
            await app.media.prepare_preview(source)
    assert not app.media.cache.usage()["files"]
    assert not list(app.media.cache.directory.glob("*.part"))
    assert owner.path.exists() and not app.transport.downloads


async def test_concurrent_current_retained_copies_coalesce_and_survive_lru_eviction(
    tmp_path, monkeypatch
):
    from enloq.application import media_cache

    app = fixture(tmp_path)
    selected = await stage(app, "gif")
    operation = await app.delivery.send(KEY, selected["revision"])
    source = app.store.message(KEY, operation["message_id"])
    owner = app.selected_media.message_file(source)
    copying, copies = media_cache._copy_retained, []

    def counted(*args, **kwargs):
        copies.append(1)
        return copying(*args, **kwargs)

    monkeypatch.setattr(media_cache, "_copy_retained", counted)
    results = await asyncio.gather(
        app.media.prepare_preview(source), app.media.prepare_preview(source)
    )
    assert all(view.state == "ready" for view in results) and copies == [1]
    app.media.cache.evict = lambda: media_cache.evict([app.media.cache], 0)
    app.media.cache.evict()
    assert not app.media.cache.usage()["files"]
    assert owner.path.read_bytes() == media_bytes("gif")


async def test_cancelling_started_copy_cannot_recreate_removed_scratch_or_publish(
    tmp_path, monkeypatch
):
    from enloq.application import media_cache

    app = fixture(tmp_path)
    selected = await stage(app, "gif")
    operation = await app.delivery.send(KEY, selected["revision"])
    source = app.store.message(KEY, operation["message_id"])
    owner = app.selected_media.message_file(source)
    copying = media_cache._copy_retained
    entered, release, completed = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def blocked(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(5)
            return copying(*args, **kwargs)
        finally:
            completed.set()

    monkeypatch.setattr(media_cache, "_copy_retained", blocked)
    task = asyncio.create_task(app.media.prepare_preview(source))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(app.media.cache.directory.glob("*.part"))
    release.set()
    assert await asyncio.to_thread(completed.wait, 5)
    assert not list(app.media.cache.directory.glob("*.part"))
    assert not app.media.cache.usage()["files"]
    assert owner.path.read_bytes() == media_bytes("gif")


@pytest.fixture(params=["gif", "sticker"])
def served_binary(request, tmp_path):
    server = create_app(Settings(data=tmp_path / "app"))
    kind = request.param
    data = gif() if kind == "gif" else still(format="WEBP", size=(512, 128))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        app = server.state.accounts.get(1)
        source = message(
            app,
            77,
            media=kind,
            media_info=MediaInfo(sticker="static") if kind == "sticker" else None,
        ).model_copy(update={"peer": "user:101"})
        app.sync.ingest(source, target=False)
        app.transport.media["user:101", 77] = data
        yield client, app, source, data


def test_received_binary_preview_ranges_and_304_require_prepared_proven_bytes(
    served_binary,
):
    client, app, _, data = served_binary
    assert client.get(BASE + "/preview?revision=1").status_code == 400
    assert not app.transport.downloads
    assert (
        client.post(BASE + "/prepare-preview", json={"revision": 1}).json()["state"]
        == "ready"
    )
    first = client.get(BASE + "/preview?revision=1")
    assert first.status_code == 200 and first.content == data
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.headers["x-content-type-options"] == "nosniff"
    etag = first.headers["etag"]
    partial = client.get(BASE + "/preview?revision=1", headers={"Range": "bytes=0-9"})
    assert partial.status_code == 206 and partial.content == data[:10]
    assert (
        client.get(
            BASE + "/preview?revision=1", headers={"If-None-Match": etag}
        ).status_code
        == 304
    )
    app.media.cache.clear()
    assert (
        client.get(
            BASE + "/preview?revision=1", headers={"If-None-Match": etag}
        ).status_code
        == 400
    )
    assert len(app.transport.downloads) == 1
    assert not list(app.media.workspace.path.glob("*.bin"))


def test_received_binary_old_proof_rejects_corruption_and_verifier_outage(
    served_binary, monkeypatch
):
    client, app, source, data = served_binary
    client.post(BASE + "/prepare-preview", json={"revision": 1})
    etag = client.get(BASE + "/preview?revision=1").headers["etag"]
    verifier = app.media.selection_type

    async def unavailable(*args, **kwargs):
        raise Unavailable("authored private diagnostic")

    monkeypatch.setattr(app.media, "selection_type", unavailable)
    response = client.get(BASE + "/preview?revision=1", headers={"If-None-Match": etag})
    assert response.status_code == 503 and "diagnostic" not in response.text
    assert app.media.cache.get(source)["path"].read_bytes() == data
    monkeypatch.setattr(app.media, "selection_type", verifier)
    app.media.cache.get(source)["path"].write_bytes(bytes(len(data)))
    assert (
        client.get(
            BASE + "/preview?revision=1", headers={"If-None-Match": etag}
        ).status_code
        == 400
    )
    assert len(app.transport.downloads) == 1


@pytest.fixture
def served_received(tmp_path):
    server = create_app(Settings(data=tmp_path / "app"))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        app = server.state.accounts.get(1)
        data = tgs()
        value = message(
            app,
            77,
            media="sticker",
            media_info=MediaInfo(sticker="animated", size=len(data)),
        ).model_copy(update={"peer": "user:101", "text": ""})
        app.sync.ingest(value, target=False)
        app.transport.media["user:101", 77] = data
        yield server, client, app, value, data


def test_received_private_json_and_exact_revision_without_implicit_fetch(
    served_received,
):
    _, client, app, _, data = served_received
    assert client.get(BASE + "/representation?revision=1").json()["state"] == "missing"
    assert client.get(BASE + "/animation?revision=1").status_code == 400
    assert client.get(BASE + "/preview?revision=1").status_code == 400
    assert not app.transport.downloads
    prepared = client.post(BASE + "/prepare-preview", json={"revision": 1})
    assert prepared.status_code == 200 and prepared.json()["format"] == "tgs"
    assert client.get(BASE + "/preview?revision=1").status_code == 400
    response = client.get(BASE + "/animation?revision=1")
    assert response.status_code == 200 and response.json()["w"] == 512
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"
    original = client.get(BASE + "?revision=1&download=1")
    assert original.content == data
    assert original.headers["content-disposition"].startswith("attachment")
    assert client.get(BASE + "/animation?revision=2").status_code == 400
    assert len(app.transport.downloads) == 1


def test_received_versions_are_read_only_cache_only_and_source_date_bound(
    served_received,
):
    _, client, app, source, _ = served_received
    client.post(BASE + "/prepare-preview", json={"revision": 1})
    app.sync.ingest(
        source.model_copy(update={"revision": 2, "text": "edited"}), target=False
    )
    version = app.store.history.rows("1/user:101", 77)[0]["id"]
    query = {"revision": 1, "version": version}
    assert client.get(BASE + "/animation", params=query).status_code == 200
    assert client.get(BASE + "/animation?revision=1").status_code == 400
    app.sync.deleted("user:101", [77])
    assert client.get(BASE + "/animation?revision=2").status_code == 400
    assert client.get(BASE + "/animation", params=query).status_code == 200
    app.media.cache.clear()
    assert (
        client.get(BASE + "/representation", params=query).json()["state"] == "missing"
    )
    assert client.post(BASE + "/prepare-preview", json=query).status_code == 400
    assert client.get(BASE + "/animation", params=query).status_code == 400
    assert len(app.transport.downloads) == 1 and not app.transport.exact_reads
    now = app.store.clock()
    app.store.clock = lambda: now + RETENTION + 1
    assert client.get(BASE + "/representation", params=query).status_code == 400


@pytest.mark.parametrize("change", ["browser", "account", "chat", "edit", "delete"])
def test_received_api_rechecks_access_after_native_await(served_received, change):
    server, client, app, source, _ = served_received
    download = app.transport.download

    async def changed(*args, **kwargs):
        await download(*args, **kwargs)
        if change == "browser":
            with server.state.browser_auth._db() as db:
                db.execute("UPDATE browsers SET revoked=1")
        elif change == "account":
            server.state.accounts.registry.set_state(1, "requires_login")
        elif change == "chat":
            app.store.put_chat(
                app.store.chat("1/user:101").model_copy(update={"kind": "bot"})
            )
        elif change == "edit":
            app.sync.ingest(source.model_copy(update={"revision": 2}), target=False)
        else:
            app.sync.deleted("user:101", [77])

    app.transport.download = changed
    response = client.post(BASE + "/prepare-preview", json={"revision": 1})
    assert response.status_code == (401 if change == "browser" else 400)
    assert not app.media.cache.usage()["files"]
    assert not list(app.media.cache.directory.glob("*.part"))


def test_received_api_requires_trust_account_source_and_same_origin(served_received):
    server, client, app, _, _ = served_received
    assert (
        client.post(
            BASE + "/prepare-preview",
            json={"revision": 1},
            headers={"Origin": "https://example.org"},
        ).status_code
        == 403
    )
    assert client.get(BASE + "/representation?revision=2").status_code == 400
    assert (
        client.get(
            BASE.replace("accounts/1", "accounts/2") + "/representation?revision=1"
        ).status_code
        == 400
    )
    cookies = dict(client.cookies)
    client.cookies.clear()
    assert client.get(BASE + "/representation?revision=1").status_code == 401
    client.cookies.update(cookies)
    browser = client.get("/api/auth/browsers").json()[0]["id"]
    server.state.browser_auth.revoke(browser)
    assert client.get(BASE + "/representation?revision=1").status_code == 401
    assert not app.transport.downloads
