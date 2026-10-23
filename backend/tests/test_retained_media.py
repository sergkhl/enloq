"""Trusted-browser previews read pinned bytes after access loss without fetching."""

import gzip
import json

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.catalog import CatalogLibrary
from enloq.domain.models import Chat, Refused
from enloq.storage.store import RETENTION, WINDOW
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_selected_media import media_bytes, source
from backend.tests.test_selection_formats import animation


@pytest.fixture
def served(tmp_path):
    server = create_app(Settings(data=tmp_path / "app"))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        yield server, client, server.state.accounts.get(1)


def stage_group(client, app, peer="chat:42", kind="gif", *, data=None):
    key, base = f"1/{peer}", f"/api/accounts/1/chats/{peer}"
    app.store.put_chat(
        Chat(account_id=1, peer=peer, title="Authored group", kind="group"),
        activity=app.store.clock(),
    )
    app.store.groups.observe(key, access="available", reason="authored_native")
    app.store.mutate_draft(key, 0, lambda d: d.update(text="Exact kept owner text"))
    data = data or media_bytes(kind)
    native = source(kind, data=data)
    app.transport.catalog_media[native.document_id, False] = data
    app.transport.catalog_data = CatalogLibrary(
        premium=False,
        gifs=(native,) if kind == "gif" else (),
        recent=(native,) if kind == "sticker" else (),
    )
    before = client.get(base + "/catalog").json()
    refreshed = client.post(
        base + "/catalog/refresh", json={"revision": before["revision"]}
    )
    assert refreshed.status_code == 200
    item = refreshed.json()["gifs" if kind == "gif" else "recent"][0]
    selected = client.post(
        base + "/selected-media",
        json={
            "revision": app.store.draft(key)["revision"],
            "token": item["token"],
            "catalog_revision": item["revision"],
        },
    )
    assert selected.status_code == 200
    return key, base, selected.json(), data


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
@pytest.mark.parametrize("kind", ["gif", "sticker"])
def test_ready_selection_stays_viewable_from_inactive_lost_group(served, peer, kind):
    _, client, app = served
    key, base, selected, data = stage_group(client, app, peer, kind)
    native_activity = app.store.rows("SELECT activity FROM chats WHERE key=?", (key,))
    now = app.store.clock() + WINDOW + 1
    app.store.clock = lambda: now
    app.store.groups.observe(key, access="lost", reason="authored_native_loss")
    app.transport.connected = False

    def no_catalog(*args, **kwargs):
        raise AssertionError("Pinned preview must not consult the native catalog")

    app.catalog.allowed = no_catalog
    content = client.get(selected["selected_media"]["content_url"])
    assert content.status_code == 200 and content.content == data
    assert content.headers["cache-control"] == "no-store"
    assert content.headers["x-content-type-options"] == "nosniff"
    value, path = app.selected_media.selected_file(
        key, selected["selected_media"]["id"]
    )
    assert path.read_bytes() == data and value["status"] == "ready"
    assert app.store.draft(key) == selected
    assert app.retained.entries()[0]["reasons"] == ["draft", "selected_media"]
    assert (
        app.store.rows("SELECT activity FROM chats WHERE key=?", (key,))
        == native_activity
    )
    assert app.transport.reads == app.transport.downloads == []
    assert not app.engine.calls
    # A different native group cannot borrow this draft's selected-byte owner.
    app.store.put_chat(
        Chat(account_id=1, peer="chat:99", title="Other group", kind="group"),
        activity=now,
    )
    app.store.groups.observe("1/chat:99", access="available", reason="authored_native")
    wrong = client.get(
        base.replace(peer, "chat:99") + "/selected-media/" + value["id"] + "/content"
    )
    assert wrong.status_code == 409 and data not in wrong.content


def test_safe_animation_is_cache_only_after_group_migration(served):
    _, client, app = served
    authored = animation()
    data = gzip.compress(json.dumps(authored).encode())
    key, _, selected, _ = stage_group(client, app, kind="sticker", data=data)
    app.store.groups.observe(
        key, access="lost", reason="migrated", migrated_to="channel:99"
    )
    app.transport.connected = False
    descriptor = selected["selected_media"]
    result = client.get(descriptor["animation_url"])
    assert result.status_code == 200 and result.json() == authored
    assert client.get(descriptor["content_url"]).content == data
    assert app.retained.cache_read(key).peer == "chat:42"
    assert app.store.draft(key) == selected
    assert not app.transport.reads and not app.engine.calls


def test_unresolved_send_owner_bytes_outlive_disposable_history_retention(served):
    server, client, app = served
    key, _, selected, data = stage_group(client, app)
    operation = app.store.send_operations.prepare(
        key,
        selected["revision"],
        snapshot=app.attachments.snapshot,
        media_snapshot=app.store.selected_media.snapshot,
        expected_group_revision=app.store.groups.get(key).access_revision,
    )
    now = app.store.clock() + RETENTION + 1
    app.store.clock = lambda: now
    app.store.groups.observe(key, access="lost", reason="authored_native_loss")
    app.transport.connected = False
    app.store.expire()
    media = selected["selected_media"]
    url = (
        f"/api/accounts/1/chats/chat:42/selected-media/operations/"
        f"{operation['id']}/{media['id']}/content"
    )
    result = client.get(url)
    assert result.status_code == 200 and result.content == data
    assert app.retained.entries()[0]["send_count"] == 1
    assert client.get(media["content_url"]).status_code == 409
    # Ownership, exact bytes and trusted-browser access still apply to kept work.
    assert client.get(url.replace(operation["id"], "b" * 32)).status_code == 400
    session = server.state.browser_auth.authenticate(
        client.cookies.get(server.state.browser_auth.cookie)
    )
    server.state.browser_auth.revoke(session)
    revoked = client.get(url)
    assert revoked.status_code == 401 and "Clear-Site-Data" in revoked.headers
    assert app.store.selected_media.path(media["id"]).read_bytes() == data


@pytest.mark.parametrize("change", ["unsupported", "stale", "bytes", "account"])
def test_local_read_gate_never_bypasses_owner_or_byte_guards(served, change):
    _, client, app = served
    key, _, selected, data = stage_group(client, app)
    media = selected["selected_media"]
    app.store.groups.observe(key, access="lost", reason="authored_native_loss")
    if change == "unsupported":
        app.store.groups.observe(
            key, access="lost", supported=False, reason="protected"
        )
    elif change == "stale":
        app.store.mutate_draft(
            key, selected["revision"], lambda d: d.update(selected_media=None)
        )
    elif change == "bytes":
        app.store.selected_media.path(media["id"]).write_bytes(
            b"changed authored bytes"
        )
    else:
        app.transport.account_id = 2
    result = client.get(media["content_url"])
    assert result.status_code in (400, 409) and data not in result.content
    assert not app.transport.reads and not app.engine.calls


def test_inactive_native_selection_is_refused_while_owned_preview_stays_readable(
    served,
):
    _, client, app = served
    key, base, selected, data = stage_group(client, app)
    now = app.store.clock() + WINDOW + 1
    app.store.clock = lambda: now
    assert client.get(selected["selected_media"]["content_url"]).content == data
    with pytest.raises(Refused):
        app.selected_media.allowed(key)
    retry = client.post(
        base + "/selected-media/retry",
        json={"revision": selected["revision"], "id": selected["selected_media"]["id"]},
    )
    assert retry.status_code == 400 and app.store.draft(key) == selected
