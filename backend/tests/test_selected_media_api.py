"""GS2/GS3 trusted-browser actions, exact previews and backend draft guards."""

import gzip
import json
import os

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.catalog import (
    CatalogLibrary,
    CatalogMetadata,
    CatalogSearchData,
    WebCatalogSource,
)
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_selected_media import media_bytes, source

BASE = "/api/accounts/1/chats/user:101"
KEY = "1/user:101"


@pytest.fixture
def served(tmp_path):
    server = create_app(Settings(data=tmp_path / "app"))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        yield server, client, server.state.accounts.get(1)


def picker(client, app, kind="gif", *, web=False):
    data = media_bytes(kind)
    native = source(kind, data=data)
    app.transport.catalog_media[native.document_id, False] = data
    app.transport.catalog_data = CatalogLibrary(
        premium=False,
        gifs=(native,) if kind == "gif" else (),
        recent=(native,) if kind == "sticker" else (),
    )
    before = client.get(BASE + "/catalog").json()
    view = client.post(BASE + "/catalog/refresh", json={"revision": before["revision"]})
    assert view.status_code == 200
    if not web:
        return view.json()["gifs" if kind == "gif" else "recent"][0]
    value = WebCatalogSource(
        account_id=1,
        remote_id="authored-hit",
        url="https://example.invalid/authored.gif",
        metadata=CatalogMetadata(kind="gif", format="gif"),
    )
    app.transport.catalog_searches["authored search", ""] = CatalogSearchData(
        items=(value,)
    )
    epoch = client.post(BASE + "/catalog/search/open", json={}).json()["epoch"]
    result = client.post(
        BASE + "/catalog/search",
        json={"epoch": epoch, "query": "authored search", "request": 0},
    )
    assert result.status_code == 200
    return result.json()["items"][0]


def select(client, app, kind="gif", *, value=None):
    value = value or picker(client, app, kind)
    result = client.post(
        BASE + "/selected-media",
        json={
            "revision": app.store.draft(KEY)["revision"],
            "token": value["token"],
            "catalog_revision": value["revision"],
        },
    )
    assert result.status_code == 200
    return result.json()


@pytest.mark.parametrize("kind", ["gif", "sticker"])
def test_safe_selected_preview_and_pending_owner_survive_open_preview_and_send(
    served, kind
):
    server, client, app = served
    selected = select(client, app, kind)
    descriptor = selected["selected_media"]
    content = client.get(descriptor["content_url"])
    assert content.status_code == 200 and content.content == media_bytes(kind)
    assert (
        content.headers["cache-control"] == "no-store"
        and content.headers["x-content-type-options"] == "nosniff"
    )
    assert app.store.selected_media.path(descriptor["id"]).stat().st_nlink == 1
    sent = client.post(BASE + "/send", json={"revision": selected["revision"]})
    assert sent.status_code == 200
    body = sent.json()
    operation = body["operation"]
    assert (
        operation["media"]["id"] == descriptor["id"]
        and body["draft"]["selected_media"] is None
    )
    private = json.dumps(operation)
    for field in (
        "path",
        "directory",
        "source",
        "document_id",
        "file_reference",
        "access_hash",
        "sha256",
        "animation_json",
    ):
        assert '"' + field + '"' not in private
    pending = client.get(operation["media"]["content_url"])
    assert pending.status_code == 200 and pending.content == media_bytes(kind)
    assert client.get(descriptor["content_url"]).status_code == 409
    session = server.state.browser_auth.authenticate(
        client.cookies.get(server.state.browser_auth.cookie)
    )
    server.state.browser_auth.revoke(session)
    revoked = client.get(operation["media"]["content_url"])
    assert revoked.status_code == 401 and "Clear-Site-Data" in revoked.headers
    assert app.store.selected_media.path(descriptor["id"]).exists()


def test_sticker_edit_guard_allows_reply_only_without_clearing_exact_kept_undo_or_state(
    served,
):
    _, client, app = served
    original = app.store.mutate_draft(
        KEY,
        0,
        lambda d: d.update(
            text="Exact kept — wording",
            undo="Previous exact\ntext",
            state="rewritten",
            error="Authored previous status",
            attempt="authored-attempt",
            reply_to=1,
        ),
    )
    selected = select(client, app, "sticker")
    changed = client.post(
        BASE + "/draft",
        json={
            "revision": selected["revision"],
            "text": original["text"],
            "reply_to": 2,
        },
    )
    assert changed.status_code == 200
    after = changed.json()
    assert all(
        after[field] == original[field]
        for field in ("text", "undo", "state", "error", "attempt")
    )
    assert after["reply_to"] == 2
    refused = client.post(
        BASE + "/draft",
        json={"revision": after["revision"], "text": "New words", "reply_to": 2},
    )
    assert refused.status_code == 400 and app.store.draft(KEY) == after
    assert (
        client.post(BASE + "/rewrite", json={"revision": after["revision"]}).status_code
        == 404
    )
    assert (
        client.post(BASE + "/undo", json={"revision": after["revision"]}).status_code
        == 400
    )
    discarded = client.post(
        BASE + "/selected-media/discard",
        json={"revision": after["revision"], "id": after["selected_media"]["id"]},
    )
    assert discarded.status_code == 200
    restored = discarded.json()
    assert all(
        restored[field] == after[field]
        for field in ("text", "undo", "state", "error", "attempt", "reply_to")
    )


@pytest.mark.parametrize(
    "change", ["revision", "id", "peer", "account", "invalid_peer"]
)
def test_selection_actions_and_preview_refuse_stale_or_cross_scope_requests(
    served, change
):
    _, client, app = served
    selected = select(client, app)
    identity = selected["selected_media"]["id"]
    if change in {"revision", "id"}:
        body = {
            "revision": selected["revision"] - (1 if change == "revision" else 0),
            "id": identity if change == "revision" else "a" * 32,
        }
        result = client.post(BASE + "/selected-media/discard", json=body)
        assert result.status_code == 409
    else:
        url = BASE + "/selected-media/" + identity + "/content"
        url = (
            url.replace("user:101", "user:102" if change == "peer" else "invalid")
            if change != "account"
            else url.replace("accounts/1", "accounts/2")
        )
        result = client.get(url)
        assert (
            result.status_code == (409 if change == "peer" else 400)
            and "Authored" not in result.text
            and "ValidationError" not in result.text
        )
    assert app.store.draft(KEY) == selected


@pytest.mark.parametrize("boundary", ["download", "validation", "entitlement", "web"])
def test_revocation_during_preparation_returns401_and_never_private_draft(
    served, monkeypatch, boundary
):
    from enloq.application import selected_media as module

    server, client, app = served
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Private authored draft"))
    value = picker(
        client,
        app,
        "sticker" if boundary == "entitlement" else "gif",
        web=boundary == "web",
    )
    session = server.state.browser_auth.authenticate(
        client.cookies.get(server.state.browser_auth.cookie)
    )

    def revoke():
        server.state.browser_auth.revoke(session)

    if boundary == "download":
        original = app.transport.catalog_download

        async def changed(*args, **kwargs):
            result = await original(*args, **kwargs)
            revoke()
            return result

        app.transport.catalog_download = changed
    elif boundary == "validation":
        original = module.validate_selection

        async def changed(*args, **kwargs):
            result = await original(*args, **kwargs)
            revoke()
            return result

        monkeypatch.setattr(module, "validate_selection", changed)
    elif boundary == "entitlement":
        original = app.catalog.entitlement
        calls = [0]

        async def changed():
            result = await original()
            calls[0] += 1
            if calls[0] == 2:
                revoke()
            return result

        app.catalog.entitlement = changed
    else:

        async def changed(url, path, *, limit):
            assert (
                url == "https://example.invalid/authored.gif" and limit == 50 * 1024**2
            )
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(media_bytes())
            revoke()

        monkeypatch.setattr(module, "fetch_web_media", changed)
    result = client.post(
        BASE + "/selected-media",
        json={
            "revision": app.store.draft(KEY)["revision"],
            "token": value["token"],
            "catalog_revision": value["revision"],
        },
    )
    assert result.status_code == 401 and "Clear-Site-Data" in result.headers
    assert "Private authored draft" not in result.text
    draft = app.store.draft(KEY)
    assert (
        draft["text"] == "Private authored draft"
        and draft["selected_media"]["status"] == "failed"
    )
    assert not app.store.rows("SELECT * FROM operations")


def test_web_gif_selection_uses_its_exact_url_and_bytes_without_native_search_replay(
    served, monkeypatch
):
    from enloq.application import selected_media as module

    _, client, app = served
    value = picker(client, app, web=True)
    calls = []

    async def fetch(url, path, *, limit):
        calls.append(url)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(media_bytes())

    monkeypatch.setattr(module, "fetch_web_media", fetch)
    selected = select(client, app, value=value)
    assert selected["selected_media"]["status"] == "ready" and calls == [
        "https://example.invalid/authored.gif"
    ]
    assert not app.transport.catalog_downloads
    selected_body = json.dumps(selected)
    assert (
        "example.invalid" not in selected_body and "authored-hit" not in selected_body
    )
    content = client.get(selected["selected_media"]["content_url"])
    assert content.content == media_bytes()


@pytest.mark.parametrize("mode,supported", [("i", True), ("d", False)])
def test_safe_tgs_animation_route_reports_precise_renderer_support(
    served, mode, supported
):
    from backend.tests.test_selection_formats import animation, mask

    _, client, app = served
    authored = animation()
    authored["layers"][0]["masksProperties"] = [mask(mode)]
    data = gzip.compress(json.dumps(authored).encode())
    native = source("sticker", data=data)
    app.transport.catalog_data = CatalogLibrary(premium=False, recent=(native,))
    app.transport.catalog_media[native.document_id, False] = data
    before = client.get(BASE + "/catalog").json()
    view = client.post(BASE + "/catalog/refresh", json={"revision": before["revision"]})
    selected = select(client, app, "sticker", value=view.json()["recent"][0])
    descriptor = selected["selected_media"]
    assert descriptor["renderer_supported"] is supported
    url = BASE + "/selected-media/" + descriptor["id"] + "/animation"
    rendered = client.get(url)
    assert rendered.status_code == (200 if supported else 400)
    if supported:
        assert rendered.json() == authored and rendered.headers[
            "content-type"
        ].startswith("application/json")
    else:
        assert (
            descriptor["animation_url"] is None
            and "Safe sticker animation" in rendered.text
        )
    assert client.get(descriptor["content_url"]).content == data
