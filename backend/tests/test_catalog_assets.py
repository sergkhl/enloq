"""Authored lazy catalog assets, shared-cache bounds and authenticated routes."""

import asyncio
import gzip
import io
import json
import os
from pathlib import Path

import pytest
from enloq.api.server import create_app
from enloq.application.catalog_assets import CatalogAssets
from enloq.application.media_cache import evict
from enloq.config import Settings
from enloq.domain.catalog import (
    CatalogLibrary,
    CatalogMetadata,
    CatalogOrigin,
    CatalogSearchData,
    NativeCatalogSource,
    NativeThumbnail,
    WebCatalogSource,
)
from enloq.domain.models import Refused, Unavailable
from enloq.domain.selection_formats import GIF_BYTES, STICKER_BYTES
from enloq.storage.store import RETENTION
from enloq.transport.mtproto import PLAYBACK
from fastapi.testclient import TestClient
from PIL import Image

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_catalog import body, load
from backend.tests.test_rebuild import fixture, message
from backend.tests.test_selection_formats import animation, mask

KEY = "1/user:42"
BASE = "/api/accounts/1/chats/user:101/catalog"


def still(*, format="PNG", size=(32, 24)):
    output = io.BytesIO()
    Image.new("RGB", size, "blue").save(output, format=format)
    return output.getvalue()


def source(data, *, kind="gif", format="gif", thumb=None, identity=7):
    return NativeCatalogSource(
        account_id=1,
        document_id=str(identity),
        access_hash="-17",
        file_reference="YQ==",
        dc_id=2,
        metadata=CatalogMetadata(
            kind=kind, format=format, size=len(data), name="Authored catalog media"
        ),
        origins=(CatalogOrigin(kind="saved" if kind == "gif" else "recent"),),
        thumbnail=NativeThumbnail(type="m", size=len(thumb), width=32, height=24)
        if thumb is not None
        else None,
    )


def gif():
    output = io.BytesIO()
    images = [Image.new("RGB", (32, 24), color) for color in ("blue", "green")]
    images[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=100,
        loop=0,
    )
    return output.getvalue()


def tgs(*, supported=True):
    data = animation()
    data["layers"][0]["masksProperties"] = [mask("a" if supported else "d")]
    return gzip.compress(json.dumps(data).encode(), mtime=0)


async def setup(
    app, data, *, kind="gif", format="gif", thumbnail=None, browser="browser-a", key=KEY
):
    native = source(data, kind=kind, format=format, thumb=thumbnail)
    app.transport.catalog_data = CatalogLibrary(
        premium=False,
        gifs=(native,) if kind == "gif" else (),
        recent=(native,) if kind == "sticker" else (),
    )
    app.transport.catalog_media[native.document_id, False] = data
    if thumbnail is not None:
        app.transport.catalog_media[native.document_id, True] = thumbnail
    view = await load(app, key, browser)
    item = (view.gifs if kind == "gif" else view.recent)[0]
    assets = CatalogAssets(app.catalog, app.media.cache, app.transport)
    return assets, item


async def test_catalog_descriptors_do_no_work_then_visible_thumb_and_intent_media_fetch(
    tmp_path,
):
    app = fixture(tmp_path)
    data, thumbnail = gif(), still()
    assets, item = await setup(app, data, thumbnail=thumbnail)
    assert (
        await assets.describe(KEY, "browser-a", item.token, item.revision)
    ).state == "missing"
    assert (
        await assets.describe(
            KEY, "browser-a", item.token, item.revision, variant="media"
        )
    ).state == "missing"
    assert not app.transport.catalog_downloads and not app.transport.downloads
    prepared = await assets.prepare(KEY, "browser-a", item.token, item.revision)
    assert (prepared.state, prepared.kind, prepared.format) == (
        "ready",
        "thumbnail",
        "png",
    )
    assert app.transport.catalog_downloads == [("7", True)]
    path, mime, _ = await assets.serve(KEY, "browser-a", item.token, item.revision)
    assert mime == "image/png" and path.read_bytes() == thumbnail
    path.unlink()
    assert (
        await assets.prepare(
            KEY, "browser-a", item.token, item.revision, variant="media"
        )
    ).format == "gif"
    assert app.transport.catalog_downloads == [("7", True), ("7", False)]
    assert app.media.cache.usage() == {"files": 2, "bytes": len(data) + len(thumbnail)}
    assert not app.transport.downloads and not app.transport.exact_reads
    public = prepared.model_dump_json()
    assert all(
        secret not in public
        for secret in (
            "document_id",
            "access_hash",
            "file_reference",
            "https://",
            "origins",
        )
    )


async def test_missing_thumbnail_never_substitutes_a_full_media_fetch(tmp_path):
    app = fixture(tmp_path)
    assets, item = await setup(app, gif())
    with pytest.raises(Refused, match="thumbnail unavailable"):
        await assets.prepare(KEY, "browser-a", item.token, item.revision)
    assert not app.transport.catalog_downloads and app.media.cache.usage()["files"] == 0


@pytest.mark.parametrize(
    "format,data",
    [("static", still(format="WEBP", size=(512, 128))), ("animated", tgs())],
)
async def test_actual_sticker_formats_and_safe_tgs_derivative_keep_native_bytes(
    tmp_path, format, data
):
    app = fixture(tmp_path)
    assets, item = await setup(app, data, kind="sticker", format=format)
    view = await assets.prepare(
        KEY, "browser-a", item.token, item.revision, variant="media"
    )
    assert view.state == "ready" and view.renderer_supported
    native = app.catalog.resolve(KEY, "browser-a", item.token, item.revision)
    found = app.media.cache.get_catalog(
        assets.identity(native, "media"), item.revision, "media", limit=STICKER_BYTES
    )
    assert found["path"].read_bytes() == data
    if format == "animated":
        assert (
            json.loads(
                await assets.animation(KEY, "browser-a", item.token, item.revision)
            )["w"]
            == 512
        )
        with pytest.raises(Refused):
            await assets.serve(
                KEY, "browser-a", item.token, item.revision, variant="media"
            )
    else:
        path, mime, _ = await assets.serve(
            KEY, "browser-a", item.token, item.revision, variant="media"
        )
        assert mime == "image/webp" and path.read_bytes() == data
        path.unlink()


async def test_unsupported_svg_feature_is_precise_fallback_with_exact_bytes_kept(
    tmp_path,
):
    app = fixture(tmp_path)
    assets, item = await setup(
        app, tgs(supported=False), kind="sticker", format="animated"
    )
    view = await assets.prepare(
        KEY, "browser-a", item.token, item.revision, variant="media"
    )
    assert view.state == "unsupported" and view.format == "tgs"
    assert not view.renderer_supported and "unsupported_mask_mode" in view.features
    assert app.media.cache.usage()["files"] == 1
    with pytest.raises(Refused):
        await assets.animation(KEY, "browser-a", item.token, item.revision)


async def test_wrong_thumbnail_mime_and_animation_do_not_enter_still_grid(tmp_path):
    app = fixture(tmp_path)
    assets, item = await setup(app, gif(), thumbnail=gif())
    view = await assets.prepare(KEY, "browser-a", item.token, item.revision)
    assert view.state == "unsupported" and not view.renderer_supported
    assert app.transport.catalog_downloads == [("7", True)]
    with pytest.raises(Refused):
        await assets.serve(KEY, "browser-a", item.token, item.revision)


async def test_catalog_rows_share_lru_without_message_or_history_aliases(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    data = still()
    source_message = message(app, 7, text="", media="image", date=now[0] - 20 * 86400)
    app.sync.ingest(source_message, target=False)
    app.transport.media["user:42", 7] = data
    await app.media.cache.fetch(source_message, limit=10000, priority=PLAYBACK)
    assets, item = await setup(app, gif(), thumbnail=data)
    now[0] += 1
    await assets.prepare(KEY, "browser-a", item.token, item.revision)
    rows = app.store.rows("SELECT * FROM media_files")
    assert len(rows) == 2 and {row["variant"] for row in rows} == {"file", "thumbnail"}
    catalog_row = next(row for row in rows if row["key"].startswith("catalog/"))
    assert catalog_row["date"] == now[0] and catalog_row["revision"] == item.revision
    assert app.store.rows("SELECT count(*) AS n FROM messages")[0]["n"] == 1
    assert not app.store.history.rows(KEY, 7)
    app.media.cache.evict = lambda: evict([app.media.cache], len(data))
    app.media.cache.evict()
    assert app.media.cache.get(source_message) is None
    assert (
        await assets.describe(KEY, "browser-a", item.token, item.revision)
    ).state == "ready"
    now[0] += RETENTION + 1
    app.media.cache.collect()
    assert app.media.cache.usage()["files"] == 0


async def test_concurrent_asset_intent_coalesces_exact_download_and_lease_survives_eviction(
    tmp_path,
):
    app = fixture(tmp_path)
    data = gif()
    assets, item = await setup(app, data)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.transport.catalog_download

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    app.transport.catalog_download = delayed
    calls = [
        asyncio.create_task(
            assets.prepare(KEY, "browser-a", item.token, item.revision, variant="media")
        )
        for _ in range(3)
    ]
    await entered.wait()
    release.set()
    assert all(view.state == "ready" for view in await asyncio.gather(*calls))
    assert app.transport.catalog_downloads == [("7", False)]
    path, _, _ = await assets.serve(
        KEY, "browser-a", item.token, item.revision, variant="media"
    )
    app.media.cache.clear()
    assert path.read_bytes() == data
    path.unlink()
    assert (
        await assets.describe(
            KEY, "browser-a", item.token, item.revision, variant="media"
        )
    ).state == "missing"
    assert not list(app.media.workspace.path.glob("*.bin"))


@pytest.mark.parametrize(
    "withdrawal", ["browser", "ticket", "source", "cache", "account"]
)
async def test_stale_asset_completion_cannot_publish_or_return_after_withdrawal(
    tmp_path, withdrawal
):
    app = fixture(tmp_path)
    assets, item = await setup(app, gif())
    trusted = [True]

    def guard():
        if not trusted[0]:
            raise Refused("Browser trust changed")

    original = app.transport.catalog_download

    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        if withdrawal == "browser":
            trusted[0] = False
        elif withdrawal == "ticket":
            app.catalog.clear_transient("browser-a", KEY)
        elif withdrawal == "source":
            app.catalog.storage.put("library", CatalogLibrary(), "0" * 64)
        elif withdrawal == "cache":
            app.media.cache.clear()
        else:
            app.catalog.authorized = lambda: False
        return result

    app.transport.catalog_download = changed
    with pytest.raises(Refused):
        await assets.prepare(
            KEY, "browser-a", item.token, item.revision, variant="media", guard=guard
        )
    assert app.media.cache.usage()["files"] == 0
    assert not list(app.media.cache.directory.glob("*.part"))


async def test_web_thumbnail_uses_exact_separate_url_and_narrow_safe_fetch_purpose(
    tmp_path,
):
    app = fixture(tmp_path)
    source = WebCatalogSource(
        account_id=1,
        remote_id="authored-hit",
        url="https://example.org/exact.gif?opaque=one",
        thumbnail_url="https://example.org/exact.png?opaque=two",
        metadata=CatalogMetadata(kind="gif", format="gif"),
    )
    app.transport.catalog_searches["authored", ""] = CatalogSearchData(items=(source,))
    epoch = app.catalog.open_search(KEY, "browser-a").epoch
    item = (await app.catalog.search(KEY, "browser-a", body(epoch, "authored"))).items[
        0
    ]
    calls = []

    async def fetch(url, path, **kwargs):
        assert not path.exists() and path.parent.stat().st_mode & 0o777 == 0o700
        calls.append((url, kwargs))
        path.write_bytes(still() if kwargs["purpose"] == "thumbnail" else gif())
        path.chmod(0o600)

    assets = CatalogAssets(
        app.catalog, app.media.cache, app.transport, web_fetcher=fetch
    )
    assert (
        await assets.prepare(KEY, "browser-a", item.token, item.revision)
    ).state == "ready"
    assert calls == [
        (source.thumbnail_url, {"limit": STICKER_BYTES, "purpose": "thumbnail"})
    ]
    assert (
        await assets.prepare(
            KEY, "browser-a", item.token, item.revision, variant="media"
        )
    ).state == "ready"
    assert calls[1] == (source.url, {"limit": GIF_BYTES, "purpose": "media"})
    assert (
        "https://"
        not in (
            await assets.describe(KEY, "browser-a", item.token, item.revision)
        ).model_dump_json()
    )


async def test_disposable_catalog_original_bytes_do_not_gain_correspondence_or_ai_roles(
    tmp_path,
):
    app = fixture(tmp_path)
    app.admit_ai = app.media.admit = lambda: pytest.fail(
        "Media preview must not admit AI"
    )
    assets, item = await setup(app, tgs(), kind="sticker", format="animated")
    before = app.store.draft(KEY)
    await assets.prepare(KEY, "browser-a", item.token, item.revision, variant="media")
    assert app.store.draft(KEY) == before
    assert not app.store.rows("SELECT * FROM messages") and not app.store.rows(
        "SELECT * FROM jobs"
    )
    assert not app.engine.calls and not app.transport.exact_reads


@pytest.fixture
def served_assets(tmp_path):
    server = create_app(Settings(data=tmp_path / "app"))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        app = server.state.accounts.get(1)
        app.catalog_assets = CatalogAssets(app.catalog, app.media.cache, app.transport)
        data, thumbnail = tgs(), still()
        native = source(data, kind="sticker", format="animated", thumb=thumbnail)
        app.transport.catalog_data = CatalogLibrary(premium=False, recent=(native,))
        app.transport.catalog_media["7", False] = data
        app.transport.catalog_media["7", True] = thumbnail
        revision = client.get(BASE).json()["revision"]
        view = client.post(BASE + "/refresh", json={"revision": revision}).json()
        item = view["recent"][0]
        yield server, client, app, item, data, thumbnail


def test_catalog_asset_api_still_headers_animation_no_store_and_no_implicit_fetch(
    served_assets,
):
    _, client, app, item, data, thumbnail = served_assets
    base = BASE + "/items/" + item["token"] + "/assets"
    query = {"revision": item["revision"]}
    assert client.get(base, params=query).json()["state"] == "missing"
    assert client.get(base + "/file", params=query).status_code == 400
    assert not app.transport.catalog_downloads
    ready = client.post(base + "/prepare", json=query)
    assert ready.status_code == 200 and ready.json()["kind"] == "thumbnail"
    response = client.get(base + "/file", params=query)
    assert (
        response.content == thumbnail
        and response.headers["content-type"] == "image/png"
    )
    assert response.headers["cache-control"] == "private, no-cache"
    etag = response.headers["etag"]
    assert (
        client.get(
            base + "/file", params=query, headers={"If-None-Match": etag}
        ).status_code
        == 304
    )
    assert client.get(base + "/animation", params=query).status_code == 400
    assert (
        client.post(base + "/prepare", json={**query, "variant": "media"}).json()[
            "format"
        ]
        == "tgs"
    )
    response = client.get(base + "/animation", params=query)
    assert response.status_code == 200 and response.json()["w"] == 512
    assert response.headers["cache-control"] == "no-store"
    assert app.transport.catalog_downloads == [("7", True), ("7", False)]
    app.media.cache.clear()
    assert (
        client.get(
            base + "/file", params=query, headers={"If-None-Match": etag}
        ).status_code
        == 400
    )
    assert client.get(base + "/animation", params=query).status_code == 400
    assert app.transport.catalog_downloads == [("7", True), ("7", False)]


def test_catalog_assets_require_browser_account_chat_revision_and_same_origin(
    served_assets,
):
    server, client, app, item, _, _ = served_assets
    base = BASE + "/items/" + item["token"] + "/assets"
    query = {"revision": item["revision"]}
    assert (
        client.post(
            base + "/prepare", json=query, headers={"Origin": "https://example.org"}
        ).status_code
        == 403
    )
    assert client.get(base, params={"revision": "0" * 64}).status_code == 400
    assert (
        client.get(base.replace("user:101", "user:404"), params=query).status_code
        == 400
    )
    assert (
        client.get(base.replace("accounts/1", "accounts/2"), params=query).status_code
        == 400
    )
    saved = dict(client.cookies)
    client.cookies.clear()
    assert client.get(base, params=query).status_code == 401
    client.cookies.update(saved)
    browser = client.get("/api/auth/browsers").json()[0]["id"]
    server.state.browser_auth.revoke(browser)
    revoked = client.get(base, params=query)
    assert (
        revoked.status_code == 401 and revoked.headers["clear-site-data"] == '"cache"'
    )
    assert not app.transport.catalog_downloads


@pytest.mark.parametrize("withdrawal", ["browser", "account", "chat"])
def test_catalog_asset_api_rechecks_trust_after_transfer(served_assets, withdrawal):
    server, client, app, item, _, _ = served_assets
    original = app.transport.catalog_download

    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        if withdrawal == "browser":
            with server.state.browser_auth._db() as db:
                db.execute("UPDATE browsers SET revoked=1")
        elif withdrawal == "account":
            server.state.accounts.registry.set_state(1, "requires_login")
        else:
            app.store.put_chat(
                app.store.chat("1/user:101").model_copy(update={"kind": "bot"})
            )
        return result

    app.transport.catalog_download = revoke
    response = client.post(
        BASE + "/items/" + item["token"] + "/assets/prepare",
        json={"revision": item["revision"]},
    )
    assert response.status_code == (401 if withdrawal == "browser" else 400)
    assert app.media.cache.usage()["files"] == 0


async def test_unavailable_format_proof_keeps_complete_catalog_bytes_for_retry(
    tmp_path, monkeypatch
):
    from enloq.domain import selection_formats

    app = fixture(tmp_path)
    data = Path(__file__).with_name("fixtures").joinpath("loop.mp4").read_bytes()
    assets, item = await setup(app, data, format="mp4")
    original = selection_formats.bounded_process

    async def outage(*args, **kwargs):
        raise TimeoutError("private source diagnostic")

    monkeypatch.setattr(selection_formats, "bounded_process", outage)
    with pytest.raises(Unavailable, match="^Media verification unavailable$"):
        await assets.prepare(
            KEY, "browser-a", item.token, item.revision, variant="media"
        )
    assert app.media.cache.usage()["files"] == 1
    monkeypatch.setattr(selection_formats, "bounded_process", original)
    assert (
        await assets.prepare(
            KEY, "browser-a", item.token, item.revision, variant="media"
        )
    ).state == "ready"
    assert app.transport.catalog_downloads == [("7", False)]


@pytest.mark.parametrize("operation", ["prepare", "serve"])
@pytest.mark.parametrize("failure", ["chmod", "close", "unlink"])
async def test_catalog_scratch_setup_fault_closes_descriptor_and_removes_owned_path(
    tmp_path, monkeypatch, operation, failure
):
    from enloq.application import media_cache

    app = fixture(tmp_path)
    assets, item = await setup(app, gif(), thumbnail=still())
    if operation == "serve":
        await assets.prepare(KEY, "browser-a", item.token, item.revision)
    unrelated = app.media.cache.directory / "authored-unrelated.bin"
    unrelated.write_bytes(b"keep")
    scratch = []
    mkstemp = media_cache.tempfile.mkstemp
    chmod, close, unlink = os.fchmod, os.close, Path.unlink
    injected = False

    def created(*args, **kwargs):
        descriptor, raw = mkstemp(*args, **kwargs)
        scratch.append((descriptor, Path(raw)))
        return descriptor, raw

    def failed_chmod(descriptor, mode):
        nonlocal injected
        if failure == "chmod" and not injected:
            injected = True
            raise OSError("authored private setup diagnostic")
        return chmod(descriptor, mode)

    def failed_close(descriptor):
        nonlocal injected
        if (
            failure == "close"
            and not injected
            and any(fd == descriptor for fd, _ in scratch)
        ):
            injected = True
            raise OSError("authored private setup diagnostic")
        return close(descriptor)

    def failed_unlink(path, *args, **kwargs):
        nonlocal injected
        if (
            failure == "unlink"
            and not injected
            and any(owned == path for _, owned in scratch)
        ):
            injected = True
            raise OSError("authored private setup diagnostic")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(media_cache.tempfile, "mkstemp", created)
    monkeypatch.setattr(os, "fchmod", failed_chmod)
    monkeypatch.setattr(os, "close", failed_close)
    monkeypatch.setattr(Path, "unlink", failed_unlink)
    with pytest.raises((Refused, Unavailable)) as error:
        await getattr(assets, operation)(KEY, "browser-a", item.token, item.revision)
    assert "diagnostic" not in str(error.value)
    assert scratch and injected
    for descriptor, path in scratch:
        with pytest.raises(OSError):
            os.fstat(descriptor)
        assert not path.exists()
    assert unrelated.read_bytes() == b"keep"
    assert app.transport.catalog_downloads == (
        [("7", True)] if operation == "serve" else []
    )


def test_private_scratch_cleanup_does_not_unlink_a_replacement_inode(
    tmp_path, monkeypatch
):
    from enloq.application import media_cache

    recorded = []
    mkstemp = media_cache.tempfile.mkstemp

    def created(*args, **kwargs):
        descriptor, raw = mkstemp(*args, **kwargs)
        recorded.append((descriptor, Path(raw)))
        return descriptor, raw

    def replacement(descriptor, mode):
        path = recorded[-1][1]
        path.rename(path.with_suffix(".owned"))
        path.write_bytes(b"authored unrelated replacement")
        raise OSError("authored private setup diagnostic")

    monkeypatch.setattr(media_cache.tempfile, "mkstemp", created)
    monkeypatch.setattr(os, "fchmod", replacement)
    with pytest.raises(Refused, match="^Private media scratch unavailable$"):
        media_cache.private_scratch(tmp_path, suffix=".part")
    descriptor, path = recorded[0]
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert path.read_bytes() == b"authored unrelated replacement"


def test_private_scratch_cleanup_retries_one_transient_unlink_failure(
    tmp_path, monkeypatch
):
    from enloq.application import media_cache

    unlink = Path.unlink
    calls = []

    def setup_error(*args):
        raise OSError("authored private setup diagnostic")

    def transient(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("authored unlink outage")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "fchmod", setup_error)
    monkeypatch.setattr(Path, "unlink", transient)
    with pytest.raises(Refused, match="^Private media scratch unavailable$"):
        media_cache.private_scratch(tmp_path, suffix=".part")
    assert len(calls) == 2 and not list(tmp_path.iterdir())
