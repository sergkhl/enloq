"""GB7 authored group cache/catalog/selection reads; no native or provider work."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from enloq.application import selected_media as selection_module
from enloq.application.accounts import AccountManager
from enloq.application.media import DOWNLOAD_BYTES
from enloq.config import Settings
from enloq.domain.catalog import CatalogLibrary, CatalogSearchData
from enloq.domain.models import Chat, MediaInfo, Message, Refused
from enloq.storage.store import RETENTION
from enloq.transport.mtproto import PLAYBACK

from backend.tests.test_avatars import picture
from backend.tests.test_catalog import body, load
from backend.tests.test_selected_media import media_bytes
from backend.tests.test_selected_media import source as catalog_source

VIDEO = Path(__file__).with_name("fixtures") / "circle.mp4"
BROWSER = "authored-group-browser"


def rig(tmp_path, *, peer="channel:42"):
    manager = AccountManager(Settings(data=tmp_path))
    app = manager.get(1)
    now = [2_000_000_000.0]
    app.store.clock = lambda: now[0]
    key = f"1/{peer}"
    app.store.put_chat(
        Chat(account_id=1, peer=peer, title="Authored group", kind="group"),
        activity=now[0],
    )
    app.store.groups.observe(key, access="available", reason="authored_native_metadata")
    manager.set_processing(1, False)
    return SimpleNamespace(manager=manager, app=app, key=key, peer=peer, now=now)


def original(r, identity=1, *, kind="audio", **values):
    info = MediaInfo(
        voice=kind == "audio",
        mime="video/mp4" if kind in {"video", "video_note"} else None,
        sticker="static" if kind == "sticker" else None,
    )
    message = Message(
        account_id=1,
        peer=r.peer,
        message_id=identity,
        revision=1,
        date=r.now[0],
        author="Authored participant",
        author_peer="user:7",
        text="Authored media caption",
        media=kind,
        media_info=info,
        attachment_id=f"authored-{identity}-{kind}",
    ).model_copy(update=values)
    r.app.sync.ingest(message, target=False)
    data = (
        picture()
        if kind == "image"
        else media_bytes(kind)
        if kind in {"gif", "sticker"}
        else VIDEO.read_bytes()
        if kind in {"video", "video_note"}
        else b"OggS-authored-local-media"
    )
    r.app.transport.media[r.peer, identity] = data
    return r.app.store.message(r.key, identity), data


def change_access(r, *, restore=False, migrate=False):
    r.app.store.groups.observe(
        r.key,
        access="lost",
        reason="authored_membership_loss",
        migrated_to="channel:84" if migrate else None,
    )
    if restore:
        r.app.store.groups.observe(
            r.key, access="available", reason="authored_restoration"
        )


async def prepared(r, message):
    if message.media == "video":
        assert await r.app.media.prepare_video(message) == "video/mp4"
    elif message.media in {"gif", "sticker"}:
        assert (await r.app.media.prepare_preview(message)).state == "ready"
    path, _ = await r.app.media.serve(message)
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
@pytest.mark.parametrize(
    "kind", ["image", "audio", "video_note", "video", "gif", "sticker"]
)
async def test_deliberate_media_with_both_ai_switches_off_reuses_unchanged_cache_after_loss(
    tmp_path, peer, kind
):
    r = rig(tmp_path, peer=peer)
    message, data = original(r, kind=kind)
    assert await prepared(r, message) == data
    assert len(r.app.transport.downloads) == 1
    change_access(r)
    assert not r.app.store.groups.current(message, admission=False)
    assert await prepared(r, message) == data
    assert len(r.app.transport.downloads) == 1
    assert not r.app.store.groups.get(r.key).processing_enabled
    assert not r.manager.registry.processing(1)["ai_enabled"]
    assert not r.app.engine.calls and not r.app.store.rows("SELECT * FROM derivatives")
    assert not r.app.store.rows("SELECT * FROM reservations")
    assert not r.app.transport.reads and not r.app.transport.exact_reads
    await r.manager.close()


@pytest.mark.parametrize("access", ["lost", "unknown", "migration"])
async def test_inaccessible_uncached_source_cannot_play_download_or_validate_poster(
    tmp_path, access
):
    r = rig(tmp_path)
    message, _ = original(r, kind="video")
    if access == "unknown":
        r.app.store.groups.observe(r.key, access="unknown", reason="authored_unknown")
    else:
        change_access(r, migrate=access == "migration")
    with pytest.raises(Refused, match="locally"):
        await r.app.media.prepare_video(message)
    with pytest.raises(Refused, match="locally"):
        r.app.media.source_access(message, variant="poster")
    with pytest.raises(Refused):
        await anext(r.app.media.stream(message))
    assert not r.app.transport.downloads
    await r.manager.close()


async def test_cached_poster_does_not_authorize_lost_access_file_and_expiry_never_refetches(
    tmp_path,
):
    r = rig(tmp_path)
    message, _ = original(r, kind="video")
    r.app.transport.media[r.peer, 1, "poster"] = picture()
    path, mime = await r.app.media.serve(message, variant="poster")
    assert mime == "image/png"
    path.unlink()
    change_access(r)
    assert r.app.media.source_access(message, variant="poster") is not None
    with pytest.raises(Refused, match="locally"):
        r.app.media.source_access(message)
    r.now[0] += RETENTION + 1
    with pytest.raises(Refused, match="expired"):
        await r.app.media.serve(message, variant="poster")
    assert len(r.app.transport.downloads) == 1
    await r.manager.close()


@pytest.mark.parametrize("kind", ["audio", "video_note", "video", "gif", "sticker"])
async def test_group_media_is_never_background_prefetched(tmp_path, kind):
    r = rig(tmp_path)
    message, _ = original(r, kind=kind)
    await r.app.media.prefetch(message)
    assert not r.app.transport.downloads and r.app.media.cache.usage()["files"] == 0
    await r.manager.close()


@pytest.mark.parametrize(
    "change", ["loss", "restoration", "migration", "edit", "deletion", "expired"]
)
async def test_late_native_download_never_publishes_stale_group_bytes(tmp_path, change):
    r = rig(tmp_path)
    message, data = original(r, kind="image")
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(peer, identity, path, **options):
        entered.set()
        await release.wait()
        path.write_bytes(data)

    r.app.transport.download = download
    task = asyncio.create_task(r.app.media.serve(message))
    await entered.wait()
    if change in {"edit", "deletion"}:
        r.app.sync.ingest(
            message.model_copy(
                update={
                    "revision": 2,
                    "text": "Changed authored caption",
                    "deleted": change == "deletion",
                }
            ),
            target=False,
        )
    elif change == "expired":
        r.now[0] += RETENTION + 1
    else:
        change_access(r, restore=change == "restoration", migrate=change == "migration")
    release.set()
    with pytest.raises(Refused):
        await task
    assert r.app.media.cache.usage()["files"] == 0
    assert not list(r.app.media.cache.directory.glob("*.part"))
    assert not list(r.app.media.workspace.path.glob("*.bin"))
    await r.manager.close()


async def test_observed_group_version_is_cache_only_and_late_verification_rechecks_access(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    old, data = original(r, kind="gif")
    assert await prepared(r, old) == data
    r.app.sync.ingest(
        old.model_copy(update={"revision": 2, "text": "Edited caption"}), target=False
    )
    with pytest.raises(Refused, match="changed"):
        await r.app.media.serve(old)
    assert await prepared(r, r.app.store.message(r.key, 1)) == data
    change_access(r)
    path, _ = await r.app.media.serve(old, history=True)
    assert path.read_bytes() == data
    path.unlink()
    verify = r.app.media.selection_type

    async def changed(*args):
        value = await verify(*args)
        change_access(r, restore=True)
        return value

    monkeypatch.setattr(r.app.media, "selection_type", changed)
    with pytest.raises(Refused, match="changed"):
        await r.app.media.serve(old, history=True)
    assert len(r.app.transport.downloads) == 2
    await r.manager.close()


async def test_direct_group_download_rechecks_access_between_parts_without_caching(
    tmp_path,
):
    r = rig(tmp_path)
    message, _ = original(r, kind="document")

    async def stream(*args, **kwargs):
        yield b"authored-first-part"
        change_access(r, restore=True)
        yield b"authored-late-part"

    r.app.transport.stream = stream
    chunks = r.app.media.stream(message)
    assert await anext(chunks) == b"authored-first-part"
    with pytest.raises(Refused, match="changed"):
        await anext(chunks)
    assert r.app.media.cache.usage()["files"] == 0
    await r.manager.close()


async def test_lost_access_download_reads_an_existing_document_cache_without_native_work(
    tmp_path,
):
    r = rig(tmp_path)
    message, data = original(r, kind="document")
    await r.app.media.cache.fetch(message, limit=DOWNLOAD_BYTES, priority=PLAYBACK)
    change_access(r)
    path, mime = await r.app.media.serve(message, inline=False)
    assert path.read_bytes() == data and mime is None
    path.unlink()
    assert len(r.app.transport.downloads) == 1
    r.app.media.cache.clear()
    with pytest.raises(Refused, match="locally"):
        await r.app.media.serve(message, inline=False)
    assert len(r.app.transport.downloads) == 1
    await r.manager.close()


async def catalog_item(r, kind="gif", *, premium=False):
    value = catalog_source(kind, premium=premium)
    r.app.transport.catalog_data = CatalogLibrary(
        premium=premium,
        gifs=(value,) if kind == "gif" else (),
        recent=(value,) if kind == "sticker" else (),
    )
    r.app.transport.catalog_media[value.document_id, False] = media_bytes(kind)
    view = await load(r.app, key=r.key, browser=BROWSER)
    return (view.gifs if kind == "gif" else view.recent)[0]


@pytest.mark.parametrize("kind", ["gif", "sticker"])
async def test_group_selection_off_keeps_exact_draft_reply_undo_and_pinned_bytes(
    tmp_path, kind
):
    r = rig(tmp_path)
    before = r.app.store.mutate_draft(
        r.key,
        r.app.store.draft(r.key)["revision"],
        lambda value: value.update(
            text="Authored kept wording",
            undo="Authored undo",
            reply_to=7,
            state="rewritten",
        ),
    )
    item = await catalog_item(r, kind)
    draft = await r.app.selected_media.select(
        r.key, before["revision"], BROWSER, item.token, item.revision
    )
    assert all(
        draft[name] == before[name] for name in ("text", "undo", "reply_to", "state")
    )
    identity = draft["selected_media"]["id"]
    value, path = r.app.selected_media.selected_file(r.key, identity)
    assert value["status"] == "ready" and path.read_bytes() == media_bytes(kind)
    r.app.media.cache.clear()
    change_access(r)
    assert r.app.selected_media.selected_file(r.key, identity)[
        1
    ].read_bytes() == media_bytes(kind)
    with pytest.raises(Refused):
        await r.app.selected_media.retry(r.key, draft["revision"], identity)
    assert not r.app.engine.calls and not r.app.store.rows("SELECT * FROM reservations")
    assert not r.app.store.rows("SELECT * FROM media_files")
    await r.manager.close()


async def test_catalog_tickets_and_search_scopes_cannot_cross_loss_and_restoration(
    tmp_path,
):
    r = rig(tmp_path)
    item = await catalog_item(r)
    epoch = r.app.catalog.open_search(r.key, BROWSER).epoch
    change_access(r)
    with pytest.raises(Refused):
        r.app.catalog.cached(r.key, BROWSER)
    with pytest.raises(Refused):
        r.app.catalog.resolve(r.key, BROWSER, item.token, item.revision)
    change_access(r, restore=True)
    with pytest.raises(Refused, match="expired"):
        r.app.catalog.resolve(r.key, BROWSER, item.token, item.revision)
    with pytest.raises(Refused, match="expired"):
        await r.app.catalog.search(r.key, BROWSER, body(epoch))
    fresh = r.app.catalog.cached(r.key, BROWSER).gifs[0]
    assert fresh.token != item.token
    assert (
        r.app.catalog.resolve(r.key, BROWSER, fresh.token, fresh.revision).metadata.kind
        == "gif"
    )
    assert r.app.transport.catalog_calls == [("library",)]
    await r.manager.close()


@pytest.mark.parametrize(
    "boundary",
    ["library", "search", "selection-download", "selection-validation", "entitlement"],
)
async def test_catalog_and_selection_completion_rejects_access_loss_even_when_restored(
    tmp_path, monkeypatch, boundary
):
    r = rig(tmp_path)
    item = await catalog_item(r, "sticker" if boundary == "entitlement" else "gif")
    before = r.app.store.draft(r.key)
    if boundary in {"library", "search"}:
        method = "catalog_library" if boundary == "library" else "catalog_search"
        original_method = getattr(r.app.transport, method)

        async def changed(*args):
            value = await original_method(*args)
            change_access(r, restore=True)
            return value

        monkeypatch.setattr(r.app.transport, method, changed)
        if boundary == "library":
            action = r.app.catalog.refresh(
                r.key, BROWSER, r.app.catalog.cached(r.key, BROWSER).revision
            )
        else:
            r.app.transport.catalog_searches["owner entered", ""] = CatalogSearchData(
                items=(catalog_source(),)
            )
            epoch = r.app.catalog.open_search(r.key, BROWSER).epoch
            action = r.app.catalog.search(r.key, BROWSER, body(epoch))
    else:
        owner, method = (
            (r.app.transport, "catalog_download")
            if boundary == "selection-download"
            else (selection_module, "validate_selection")
            if boundary == "selection-validation"
            else (r.app.catalog, "entitlement")
        )
        original_method = getattr(owner, method)

        async def changed(*args, **kwargs):
            value = await original_method(*args, **kwargs)
            change_access(r, restore=True)
            return value

        monkeypatch.setattr(owner, method, changed)
        action = r.app.selected_media.select(
            r.key, before["revision"], BROWSER, item.token, item.revision
        )
    with pytest.raises(Refused, match="access changed"):
        await action
    current = r.app.store.draft(r.key)
    assert (
        current["text"] == before["text"] and current["reply_to"] == before["reply_to"]
    )
    assert (
        not current["selected_media"] or current["selected_media"]["status"] != "ready"
    )
    assert not r.app.engine.calls
    await r.manager.close()


@pytest.mark.parametrize("boundary", ["begin", "finish"])
async def test_selection_atomic_publication_rejects_an_access_change_after_application_check(
    tmp_path, monkeypatch, boundary
):
    r = rig(tmp_path)
    item = await catalog_item(r)
    before = r.app.store.draft(r.key)
    storage = r.app.store.selected_media
    original_method = getattr(storage, boundary)

    def changed(*args, **kwargs):
        if boundary == "begin" or kwargs.get("facts") is not None:
            change_access(r, restore=True)
        return original_method(*args, **kwargs)

    monkeypatch.setattr(storage, boundary, changed)
    with pytest.raises(Refused, match="access changed"):
        await r.app.selected_media.select(
            r.key, before["revision"], BROWSER, item.token, item.revision
        )
    current = r.app.store.draft(r.key)
    assert current["text"] == before["text"] and current["undo"] == before["undo"]
    if boundary == "begin":
        assert current == before and not r.app.transport.catalog_downloads
    else:
        assert current["selected_media"]["status"] == "failed"
        assert not list(storage.root.glob("*.bin"))
    assert not r.app.store.rows("SELECT * FROM media_files")
    await r.manager.close()


async def test_a_voice_copy_is_made_from_the_cached_source_after_access_loss(
    tmp_path,
):
    """ADR 46 CU1: an iPhone's AAC copy needs no Telegram transfer once the
    source is cached, and an uncached source stays refused after loss."""
    r = rig(tmp_path)
    tone = Path(__file__).with_name("fixtures") / "tone.ogg"
    message, _ = original(r)
    uncached, _ = original(r, 2)
    r.app.transport.media[r.peer, 1] = tone.read_bytes()
    path, mime = await r.app.media.serve(message)  # The source is played once.
    path.unlink()
    change_access(r)
    path, mime = await r.app.media.serve(message, variant="aac")
    try:
        assert mime == "audio/mp4" and path.read_bytes()[4:8] == b"ftyp"
    finally:
        path.unlink(missing_ok=True)
    assert len(r.app.transport.downloads) == 1
    with pytest.raises(Refused, match="locally"):
        await r.app.media.serve(uncached, variant="aac")
    assert len(r.app.transport.downloads) == 1
    await r.manager.close()
