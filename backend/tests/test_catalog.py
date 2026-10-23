"""GS1 catalog contracts with authored metadata and isolated owners only."""

import asyncio
import json
from pathlib import Path

import pytest
from enloq.api.server import create_app
from enloq.application.catalog import CATALOG_TTL, TICKET_TTL
from enloq.config import Settings
from enloq.domain.catalog import (
    CatalogLibrary,
    CatalogMetadata,
    CatalogOrigin,
    CatalogPackData,
    CatalogSearchData,
    GifSearch,
    NativeCatalogSource,
    NativePackReference,
    WebCatalogSource,
    pack_revision,
)
from enloq.domain.models import Chat, Conflict, Refused, Unavailable
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture

KEY = "1/user:42"
BASE = "/api/accounts/1/chats/user:101/catalog"
BIG_ID = 2**53 + 123


def native(
    identity=BIG_ID,
    *,
    account=1,
    kind="gif",
    premium=False,
    reference="YQ==",
    origin=None,
):
    return NativeCatalogSource(
        account_id=account,
        document_id=str(identity),
        access_hash="-17",
        file_reference=reference,
        dc_id=2,
        metadata=CatalogMetadata(
            kind=kind,
            format="mp4" if kind == "gif" else "static",
            mime="video/mp4" if kind == "gif" else "image/webp",
            name="Authored media",
            size=100,
            requires_premium=premium,
        ),
        origins=(origin or CatalogOrigin(kind="saved" if kind == "gif" else "recent"),),
    )


def pack(hash=1):
    return NativePackReference(
        id=str(BIG_ID + 1),
        access_hash="-29",
        hash=hash,
        title="Authored stickers",
        short_name="authored_pack",
        count=1,
    )


def set_library(app, *, gifs=None, stickers=(), packs=(), premium=False):
    app.transport.catalog_data = CatalogLibrary(
        premium=premium,
        gifs=tuple(gifs if gifs is not None else [native()]),
        recent=tuple(stickers),
        packs=tuple(packs),
    )


async def load(app, key=KEY, browser="browser-a"):
    return await app.catalog.refresh(
        key, browser, app.catalog.cached(key, browser).revision
    )


def body(epoch, query="owner entered", request=0, offset=""):
    return GifSearch(epoch=epoch, query=query, request=request, offset=offset)


async def test_cached_open_is_immediate_private_and_restart_keeps_only_library(
    tmp_path,
):
    app = fixture(tmp_path)
    before = app.catalog.cached(KEY, "browser-a")
    assert before.stale and before.premium is None and before.gifs == []
    assert not app.transport.catalog_calls and not app.transport.downloads
    set_library(app)
    view = await load(app)
    assert not view.stale and view.premium is False and view.gifs[0].selectable
    public = view.model_dump_json()
    assert all(
        value not in public
        for value in [
            str(BIG_ID),
            "access_hash",
            "file_reference",
            "origins",
            "document_id",
        ]
    )
    selected = app.catalog.resolve(
        KEY, "browser-a", view.gifs[0].token, view.gifs[0].revision
    )
    assert selected.document_id == str(BIG_ID) and selected.access_hash == "-17"
    assert selected is not app.transport.catalog_data.gifs[0]
    with pytest.raises(ValidationError):
        selected.document_id = "7"
    app.transport.catalog_searches["private search text", ""] = CatalogSearchData(
        items=(
            WebCatalogSource(
                account_id=1,
                remote_id="hit",
                url="https://example.org/authored.gif",
                metadata=CatalogMetadata(kind="gif", format="gif", mime="image/gif"),
            ),
        )
    )
    epoch = app.catalog.open_search(KEY, "browser-a").epoch
    searched = await app.catalog.search(
        KEY, "browser-a", body(epoch, "private search text")
    )
    assert "example.org" not in searched.model_dump_json()
    stored = json.dumps(app.store.rows("SELECT * FROM media_catalogs"))
    assert "private search text" not in stored and "example.org" not in stored
    assert app.store.path.stat().st_mode & 0o777 == 0o600
    restarted = fixture(tmp_path)
    assert restarted.catalog.cached(KEY, "browser-a").gifs
    assert not restarted.catalog.searches
    with pytest.raises(Refused):
        restarted.catalog.resolve(
            KEY, "browser-a", searched.items[0].token, searched.items[0].revision
        )
    assert not restarted.transport.catalog_calls and not app.transport.downloads


async def test_cached_view_reads_library_once_for_many_items(tmp_path, monkeypatch):
    app = fixture(tmp_path)
    set_library(
        app,
        gifs=[native(i) for i in range(1, 201)],
        stickers=[native(i + 300, kind="sticker") for i in range(200)],
    )
    await load(app)
    original, reads = app.catalog.library, []

    def library():
        reads.append(True)
        return original()

    monkeypatch.setattr(app.catalog, "library", library)
    view = app.catalog.cached(KEY, "browser-b")
    assert len(view.gifs) == len(view.recent) == 200 and len(reads) == 1


async def test_item_revision_browser_account_chat_and_expiry_bounds(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    set_library(app)
    item = (await load(app)).gifs[0]
    for key, browser, revision in [
        (KEY, "browser-b", item.revision),
        ("1/user:404", "browser-a", item.revision),
        ("2/user:42", "browser-a", item.revision),
        (KEY, "browser-a", "0" * 64),
    ]:
        with pytest.raises(Refused):
            app.catalog.resolve(key, browser, item.token, revision)
    app.store.put_chat(
        Chat(account_id=1, peer="user:43", title="Other authored chat", kind="private")
    )
    with pytest.raises(Refused):
        app.catalog.resolve("1/user:43", "browser-a", item.token, item.revision)
    now[0] += TICKET_TTL
    with pytest.raises(Refused):
        app.catalog.resolve(KEY, "browser-a", item.token, item.revision)
    assert app.catalog.cached(KEY, "browser-a").stale


async def test_catalog_is_independent_of_ai_drafts_and_reply(tmp_path):
    app = fixture(tmp_path)
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"ai_eligible": False}))
    app.admit_ai = lambda: pytest.fail("Catalog must not admit AI")
    app.store.mutate_draft(
        KEY,
        0,
        lambda draft: draft.update(
            text="Keep authored text", undo="Previous authored text", reply_to=77
        ),
    )
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO suggestion_state VALUES (?,?,?)",
            (
                KEY,
                app.store.clock(),
                json.dumps({"state": "ready", "options": ["Authored option"]}),
            ),
        )
    original = app.store.draft(KEY)
    suggestions = app.store.rows("SELECT * FROM suggestion_state")
    set_library(app)
    await load(app)
    epoch = app.catalog.open_search(KEY, "browser-a").epoch
    await app.catalog.search(KEY, "browser-a", body(epoch))
    assert app.store.draft(KEY) == original and not app.store.rows("SELECT * FROM jobs")
    assert app.store.rows("SELECT * FROM suggestion_state") == suggestions
    assert not app.transport.downloads and not app.transport.exact_reads


@pytest.mark.parametrize("kind", ["group", "bot", "saved", "service", "broadcast"])
async def test_catalog_refuses_non_human_private_chat_before_owner_work(tmp_path, kind):
    app = fixture(tmp_path)
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"kind": kind}))
    with pytest.raises(Refused):
        app.catalog.cached(KEY, "browser-a")
    with pytest.raises(Refused):
        await app.catalog.refresh(KEY, "browser-a", "0" * 64)
    assert not app.transport.catalog_calls


async def test_refresh_stale_update_failure_and_known_pack_rows_are_bounded(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    set_library(app, packs=[pack()])
    view = await load(app)
    assert view.packs and not app.transport.downloads
    app.transport.catalog_changed()
    assert app.catalog.cached(KEY, "browser-a").stale
    assert app.transport.catalog_calls == [("library",)]
    app.transport.catalog_failure = RuntimeError("private diagnostics")
    failed = await load(app)
    assert (
        failed.gifs
        and failed.stale
        and failed.error == "Library refresh unavailable; try again"
    )
    assert "private diagnostics" not in failed.model_dump_json()
    app.transport.catalog_failure = None
    app.catalog.storage.put(
        "pack:" + view.packs[0].id,
        CatalogPackData(pack=pack(), items=()),
        pack_revision(pack()),
    )
    set_library(app, packs=[])
    await load(app)
    assert not app.store.rows(
        "SELECT scope FROM media_catalogs WHERE scope LIKE 'pack:%'"
    )
    now[0] += CATALOG_TTL + 1
    assert app.catalog.cached(KEY, "browser-a").stale


async def test_native_reference_renewal_preserves_item_but_metadata_change_revokes_it(
    tmp_path,
):
    app = fixture(tmp_path)
    set_library(app)
    item = (await load(app)).gifs[0]
    set_library(app, gifs=[native(reference="Yg==")])
    await load(app)
    assert (
        app.catalog.resolve(KEY, "browser-a", item.token, item.revision).file_reference
        == "Yg=="
    )
    changed = native().model_copy(
        update={"metadata": native().metadata.model_copy(update={"size": 101})}
    )
    set_library(app, gifs=[changed])
    await load(app)
    with pytest.raises(Refused):
        app.catalog.resolve(KEY, "browser-a", item.token, item.revision)


async def test_pack_contents_are_lazy_and_mismatched_revision_cannot_select_old_items(
    tmp_path,
):
    app = fixture(tmp_path)
    current = pack()
    set_library(app, packs=[current])
    view = await load(app)
    descriptor = view.packs[0]
    assert not app.catalog.pack(
        KEY, "browser-a", descriptor.id, descriptor.revision
    ).items
    assert app.transport.catalog_calls == [("library",)]
    sticker = native(kind="sticker", origin=CatalogOrigin(kind="pack", pack=current))
    app.transport.catalog_packs[current.id] = CatalogPackData(
        pack=current, items=(sticker,)
    )
    detailed = await app.catalog.refresh_pack(
        KEY, "browser-a", descriptor.id, descriptor.revision
    )
    item = detailed.items[0]
    assert (
        app.catalog.resolve(KEY, "browser-a", item.token, item.revision).document_id
        == sticker.document_id
    )
    set_library(app, packs=[pack(hash=2)])
    changed = (await load(app)).packs[0]
    stale = app.catalog.pack(KEY, "browser-a", changed.id, changed.revision)
    assert not stale.items and stale.pack.stale and stale.error
    with pytest.raises(Refused):
        app.catalog.resolve(KEY, "browser-a", item.token, item.revision)
    app.transport.catalog_packs[current.id] = CatalogPackData(
        pack=pack(hash=3), items=()
    )
    refreshed = await app.catalog.refresh_pack(
        KEY, "browser-a", changed.id, changed.revision
    )
    assert (
        refreshed.pack.revision == pack_revision(pack(hash=3))
        and not refreshed.pack.stale
    )
    assert not refreshed.items and not app.transport.downloads


async def test_premium_is_metadata_for_picker_and_fresh_entitlement_is_available(
    tmp_path,
):
    app = fixture(tmp_path)
    set_library(app, stickers=[native(kind="sticker", premium=True)], premium=False)
    view = await load(app)
    item = view.recent[0]
    assert not item.selectable and item.unavailable == "Telegram Premium is required"
    assert app.catalog.preview(KEY, "browser-a", item.token, item.revision)
    with pytest.raises(Refused):
        app.catalog.resolve(KEY, "browser-a", item.token, item.revision)
    set_library(app, stickers=[native(kind="sticker", premium=True)], premium=True)
    item = (await load(app)).recent[0]
    assert app.catalog.resolve(KEY, "browser-a", item.token, item.revision)
    set_library(app, premium=False)
    assert await app.catalog.entitlement() is False


async def test_search_requires_server_scope_and_retired_epoch_never_reactivates(
    tmp_path,
):
    app = fixture(tmp_path)
    with pytest.raises(Refused):
        await app.catalog.search(KEY, "browser-a", body("a" * 32))
    a = app.catalog.open_search(KEY, "browser-a").epoch
    await app.catalog.search(KEY, "browser-a", body(a, "first"))
    b = app.catalog.open_search(KEY, "browser-a").epoch
    with pytest.raises(Refused):
        await app.catalog.search(KEY, "browser-a", body(a, "late", request=99))
    await app.catalog.search(KEY, "browser-a", body(b, "new"))
    with pytest.raises(Conflict):
        app.catalog.close_search(KEY, "browser-a", a)
    app.catalog.close_search(KEY, "browser-a", b)
    with pytest.raises(Refused):
        await app.catalog.search(KEY, "browser-a", body(b, "late", request=99))
    assert app.transport.catalog_calls == [
        ("search", "first", ""),
        ("search", "new", ""),
    ]


@pytest.mark.parametrize("replacement", ["empty", "open", "close"])
async def test_empty_query_supersedes_search_without_bot_and_late_results_cannot_publish(
    tmp_path,
    replacement,
):
    app = fixture(tmp_path)
    set_library(app)
    await load(app)
    epoch = app.catalog.open_search(KEY, "browser-a").epoch
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(query, offset):
        entered.set()
        await release.wait()
        return CatalogSearchData(items=(native(17),))

    app.transport.catalog_search = delayed
    late = asyncio.create_task(app.catalog.search(KEY, "browser-a", body(epoch)))
    await entered.wait()
    if replacement == "empty":
        saved = await app.catalog.search(KEY, "browser-a", body(epoch, "", request=1))
        assert saved.items
    elif replacement == "open":
        app.catalog.open_search(KEY, "browser-a")
    else:
        app.catalog.close_search(KEY, "browser-a", epoch)
    assert app.transport.catalog_calls == [("library",)]
    release.set()
    with pytest.raises(Conflict):
        await late
    assert all(ticket.generation is None for ticket in app.catalog.tickets.values())


async def test_search_pagination_exact_identity_and_superseding_query(tmp_path):
    app = fixture(tmp_path)
    epoch = app.catalog.open_search(KEY, "browser-a").epoch
    app.transport.catalog_searches["authored", ""] = CatalogSearchData(
        items=(native(11),), next_offset="page2"
    )
    app.transport.catalog_searches["authored", "page2"] = CatalogSearchData(
        items=(native(12),)
    )
    first = await app.catalog.search(KEY, "browser-a", body(epoch, "authored"))
    with pytest.raises(Conflict):
        await app.catalog.search(KEY, "browser-a", body(epoch, "authored"))
    with pytest.raises(Refused):
        await app.catalog.search(
            KEY, "browser-a", body(epoch, "authored", request=1, offset="invented")
        )
    second = await app.catalog.search(
        KEY, "browser-a", body(epoch, "authored", request=1, offset="page2")
    )
    assert len(second.items) == 1
    old = first.items[0]
    assert (
        app.catalog.resolve(KEY, "browser-a", old.token, old.revision).document_id
        == "11"
    )
    await app.catalog.search(KEY, "browser-a", body(epoch, "changed", request=2))
    with pytest.raises(Refused):
        app.catalog.resolve(KEY, "browser-a", old.token, old.revision)


@pytest.mark.parametrize("action", ["library", "pack", "search"])
async def test_post_await_account_and_browser_guard_prevents_ticket_publication(
    tmp_path, action
):
    app = fixture(tmp_path)
    set_library(app, packs=[pack()])
    view = await load(app)
    app.catalog.clear_transient()
    trusted = [True]

    def guard():
        if not trusted[0]:
            raise Refused("Browser trust changed")

    async def revoke(*args):
        trusted[0] = False
        if action == "library":
            return app.transport.catalog_data
        if action == "pack":
            return CatalogPackData(pack=pack(), items=(native(kind="sticker"),))
        return CatalogSearchData(items=(native(),))

    if action == "library":
        app.transport.catalog_library = revoke
        work = app.catalog.refresh(KEY, "browser-a", view.revision, guard=guard)
    elif action == "pack":
        app.transport.catalog_pack = revoke
        work = app.catalog.refresh_pack(
            KEY, "browser-a", view.packs[0].id, view.packs[0].revision, guard=guard
        )
    else:
        app.transport.catalog_search = revoke
        epoch = app.catalog.open_search(KEY, "browser-a").epoch
        work = app.catalog.search(KEY, "browser-a", body(epoch), guard=guard)
    with pytest.raises(Refused):
        await work
    assert not app.catalog.tickets
    app.catalog.authorized = lambda: False
    with pytest.raises(Refused):
        app.catalog.cached(KEY, "browser-a")
    app.catalog.clear()
    assert not app.store.rows("SELECT * FROM media_catalogs")


async def test_same_document_refresh_is_detached_and_refuses_substitution(tmp_path):
    app = fixture(tmp_path)
    original = native(reference="YQ==")
    set_library(app, gifs=[native(reference="Yg==")])
    fresh = await app.catalog.refresh_same_document(original)
    assert fresh.document_id == original.document_id and fresh.file_reference == "Yg=="

    async def substitute(source):
        return native(123)

    app.transport.catalog_refresh_document = substitute
    with pytest.raises(Refused):
        await app.catalog.refresh_same_document(original)


@pytest.fixture
def served_catalog(tmp_path):
    server = create_app(Settings(data=tmp_path / "app"))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        app = server.state.accounts.get(1)
        set_library(app, packs=[pack()])
        yield server, client, app


def test_catalog_api_contract_cached_refresh_pack_preview_and_search(served_catalog):
    _, client, app = served_catalog
    cached = client.get(BASE)
    assert cached.status_code == 200 and cached.headers["cache-control"] == "no-store"
    assert not app.transport.catalog_calls
    refreshed = client.post(
        BASE + "/refresh", json={"revision": cached.json()["revision"]}
    )
    assert refreshed.status_code == 200 and refreshed.json()["gifs"]
    item = refreshed.json()["gifs"][0]
    preview = client.get(
        BASE + "/items/" + item["token"] + "/preview",
        params={"revision": item["revision"]},
    )
    assert preview.status_code == 200 and preview.json() == item["preview"]
    descriptor = refreshed.json()["packs"][0]
    assert (
        client.get(
            BASE + "/packs/" + descriptor["id"],
            params={"revision": descriptor["revision"]},
        ).json()["items"]
        == []
    )
    scope = client.post(BASE + "/search/open", json={}).json()["epoch"]
    assert client.post(BASE + "/search", json=body(scope, "").model_dump()).json()[
        "items"
    ]
    assert client.post(BASE + "/search/close", json={"epoch": scope}).status_code == 204
    assert (
        client.post(
            BASE + "/search", json=body(scope, "late", request=1).model_dump()
        ).status_code
        == 400
    )
    serialized = json.dumps(refreshed.json())
    assert all(
        value not in serialized
        for value in [
            str(BIG_ID),
            "file_reference",
            "access_hash",
            "document_id",
            "https://",
        ]
    )
    assert app.transport.catalog_calls == [("library",)] and not app.transport.downloads


def test_catalog_api_requires_auth_same_origin_json_and_current_revision(
    served_catalog,
):
    _, client, app = served_catalog
    saved = dict(client.cookies)
    client.cookies.clear()
    assert client.get(BASE).status_code == 401
    client.cookies.update(saved)
    revision = client.get(BASE).json()["revision"]
    assert (
        client.post(
            BASE + "/refresh",
            json={"revision": revision},
            headers={"Origin": "https://example.org"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            BASE + "/refresh", content="{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 403
    )
    assert (
        client.post(BASE + "/refresh", json={"revision": "0" * 64}).status_code == 409
    )
    assert (
        client.post(
            BASE + "/search", json={"epoch": "x", "query": "secret", "request": 0}
        ).status_code
        == 422
    )
    assert client.get(BASE.replace("user:101", "user:404")).status_code == 400
    assert not app.transport.catalog_calls


@pytest.mark.parametrize("revocation", ["browser", "account", "chat"])
def test_catalog_api_rechecks_revocation_after_refresh(served_catalog, revocation):
    server, client, app = served_catalog
    cached = client.get(BASE).json()

    async def withdrawn():
        if revocation == "browser":
            with server.state.browser_auth._db() as db:
                db.execute("UPDATE browsers SET revoked=1")
        elif revocation == "account":
            server.state.accounts.registry.set_state(1, "requires_login")
        else:
            app.store.put_chat(
                app.store.chat("1/user:101").model_copy(update={"kind": "bot"})
            )
        return app.transport.catalog_data

    app.transport.catalog_library = withdrawn
    result = client.post(BASE + "/refresh", json={"revision": cached["revision"]})
    assert result.status_code == (401 if revocation == "browser" else 400)
    assert not app.catalog.tickets and not app.store.rows(
        "SELECT * FROM media_catalogs"
    )
    if revocation == "browser":
        assert result.headers["clear-site-data"] == '"cache"'


def test_generated_public_schema_contains_only_picker_projection_contracts():
    schema = json.loads(Path("config/api-schema.json").read_text())["components"][
        "schemas"
    ]
    for name in [
        "CatalogView",
        "CatalogPackView",
        "CatalogItem",
        "CatalogPreview",
        "CatalogRefresh",
        "CatalogOpen",
        "GifSearch",
        "GifSearchScope",
        "GifSearchView",
    ]:
        assert name in schema
    for name in [
        "NativeCatalogSource",
        "WebCatalogSource",
        "NativePackReference",
        "CatalogOrigin",
    ]:
        assert name not in schema
    assert schema["CatalogPack"]["properties"]["id"]["type"] == "string"
    assert schema["CatalogItem"]["properties"]["token"]["type"] == "string"


async def test_search_failure_never_exposes_private_query_diagnostics(tmp_path):
    app = fixture(tmp_path)
    epoch = app.catalog.open_search(KEY, "browser-a").epoch
    app.transport.catalog_failure = RuntimeError(
        "private query and URL https://example.org/secret"
    )
    with pytest.raises(Unavailable) as error:
        await app.catalog.search(KEY, "browser-a", body(epoch, "private query"))
    assert str(error.value) == "GIF search is unavailable; try again"
    assert not app.catalog.tickets and not app.store.rows(
        "SELECT * FROM media_catalogs"
    )


async def test_standalone_service_entitlement_timeout_and_authorization_recheck(
    tmp_path, monkeypatch
):
    from enloq.application import catalog

    app = fixture(tmp_path)

    async def stalled():
        await asyncio.Future()

    app.transport.catalog_entitlement = stalled
    monkeypatch.setattr(catalog, "ENTITLEMENT_TIMEOUT", 0.01)
    with pytest.raises(Unavailable):
        await app.catalog.entitlement()

    async def withdrawn():
        app.catalog.authorized = lambda: False
        return True

    app.transport.catalog_entitlement = withdrawn
    with pytest.raises(Refused):
        await app.catalog.entitlement()
