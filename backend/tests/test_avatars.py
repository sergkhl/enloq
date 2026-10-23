"""Authored profile bytes and RPC construction, never a live native observation."""

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.application.accounts import AccountManager
from enloq.application.avatars import Avatars
from enloq.config import Settings
from enloq.domain.models import Chat, Refused, profile_photo_id
from enloq.storage.store import RETENTION, SCHEMA_VERSION, Store
from enloq.transport.telethon import PersonalTransport
from fastapi.testclient import TestClient
from PIL import Image
from telethon import types

from backend.tests.auth_helpers import trust_browser


def picture(color="green", size=(40, 40)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def photo(app, identity="1001", *, peer="user:42", data=None):
    app.store.put_chat(
        Chat(
            account_id=app.store.account_id,
            peer=peer,
            title="Authored peer",
            kind="private",
            photo_id=identity,
        )
    )
    if identity:
        app.transport.avatars[peer, identity] = data or picture()


@pytest.fixture
def rig(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    manager._create(manager.registry.register(2, "second.session"))
    now = [2_000_000_000.0]
    for identity in (1, 2):
        manager.get(identity).store.clock = lambda: now[0]
    return manager, now


async def test_photo_reads_coalesce_survive_restart_and_never_use_models(rig):
    manager, now = rig
    app = manager.get(1)
    photo(app)
    manager.set_processing(1, False)
    data = await asyncio.gather(*(app.avatars.get("user:42", "1001") for _ in range(4)))
    assert all(value == (picture(), "image/png") for value in data)
    assert app.transport.avatar_reads == [("user:42", "1001")]
    cached = app.avatars.path("user:42", "1001")
    assert cached.stat().st_mode & 0o777 == 0o600
    assert cached.parent.stat().st_mode & 0o777 == 0o700
    restarted = Avatars(app.store, app.transport)
    assert await restarted.get("user:42", "1001") == data[0]
    assert len(app.transport.avatar_reads) == 1
    assert not app.engine.calls and not app.store.rows("SELECT * FROM jobs")
    assert not list((cached.parent / "pending").iterdir())


async def test_changed_removed_expired_and_excess_photos_are_invalidated(
    rig, monkeypatch
):
    from enloq.application import avatars

    manager, now = rig
    app = manager.get(1)
    photo(app)
    await app.avatars.get("user:42", "1001")
    old = app.avatars.path("user:42", "1001")
    photo(app, "1002", data=picture("blue"))
    with pytest.raises(Refused):
        await app.avatars.get("user:42", "1001")
    assert (await app.avatars.get("user:42", "1002"))[0] == picture("blue")
    assert not old.exists()
    now[0] += RETENTION + 1
    await app.avatars.get("user:42", "1002")
    assert len(app.transport.avatar_reads) == 3
    photo(app, None)
    app.avatars.collect()
    assert not list(app.avatars.directory.glob("*.img"))
    assert not app.store.rows("SELECT * FROM avatars")
    monkeypatch.setattr(avatars, "CACHE_ENTRIES", 2)
    for peer in ("user:43", "user:44", "user:45"):
        photo(app, peer=peer)
        await app.avatars.get(peer, "1001")
    assert len(app.store.rows("SELECT * FROM avatars")) == 2
    assert len(list(app.avatars.directory.glob("*.img"))) == 2


@pytest.mark.parametrize(
    "data", [b"authored invalid bytes", b"x" * 270_000, picture(size=(1025, 2))]
)
async def test_photo_failures_are_bounded_coalesced_and_leave_no_private_scratch(
    rig, data
):
    manager, now = rig
    app = manager.get(1)
    photo(app, data=data)
    for _ in range(2):
        with pytest.raises(Refused, match="temporarily unavailable"):
            await app.avatars.get("user:42", "1001")
    assert len(app.transport.avatar_reads) == 1
    assert not list(app.avatars.directory.glob("*.img"))
    assert not list((app.avatars.directory / "pending").iterdir())
    now[0] += 61
    app.transport.avatars["user:42", "1001"] = picture()
    assert (await app.avatars.get("user:42", "1001"))[0] == picture()


async def test_two_account_caches_and_logout_are_isolated(rig):
    manager, now = rig
    a, b = manager.get(1), manager.get(2)
    photo(a, data=picture("red"))
    photo(b, data=picture("blue"))
    assert (await a.avatars.get("user:42", "1001"))[0] == picture("red")
    assert (await b.avatars.get("user:42", "1001"))[0] == picture("blue")
    assert a.avatars.path("user:42", "1001") != b.avatars.path("user:42", "1001")
    await manager.logout(1)
    with pytest.raises(Refused):
        await a.avatars.get("user:42", "1001")
    assert not list(a.avatars.directory.glob("*.img"))
    assert b.avatars.path("user:42", "1001").is_file()
    await manager.remove(1)
    assert not a.avatars.directory.exists()
    assert (await b.avatars.get("user:42", "1001"))[0] == picture("blue")
    await manager.close()


async def test_metadata_change_during_download_cannot_publish_old_photo(rig):
    manager, now = rig
    app = manager.get(1)
    photo(app)
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(peer, identity, path, **bounds):
        entered.set()
        await release.wait()
        path.write_bytes(picture())

    app.transport.download_avatar = download
    task = asyncio.create_task(app.avatars.get("user:42", "1001"))
    await entered.wait()
    photo(app, "1002")
    release.set()
    with pytest.raises(Refused):
        await task
    assert not app.store.rows("SELECT * FROM avatars")
    assert not list(app.avatars.directory.glob("*.img"))


def test_authenticated_photo_api_owns_account_and_native_identity(tmp_path):
    app = create_app(Settings(data=tmp_path))
    a = app.state.accounts.get(1)
    photo(a)
    account_photo = "9223372036854775806"
    app.state.accounts.registry.identity(
        1,
        SimpleNamespace(
            first_name="Authored owner",
            photo=SimpleNamespace(photo_id=int(account_photo)),
        ),
    )
    a.transport.avatars["user:1", account_photo] = picture("blue")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        url = "/api/accounts/1/avatars/user:42?photo_id=1001"
        assert client.get(url).status_code == 401
        trust_browser(client, tmp_path)
        response = client.get(url)
        assert response.status_code == 200 and response.content == picture()
        assert response.headers["cache-control"] == "no-store"
        assert (
            client.get("/api/accounts/2/avatars/user:42?photo_id=1001").status_code
            == 400
        )
        assert (
            client.get("/api/accounts/1/avatars/user:99?photo_id=1001").status_code
            == 400
        )
        assert (
            client.get("/api/accounts/1/avatars/user:42?photo_id=1002").status_code
            == 400
        )
        assert client.get(
            f"/api/accounts/1/avatars/user:1?photo_id={account_photo}"
        ).content == picture("blue")
        assert (
            client.get("/api/accounts").json()["accounts"][0]["photo_id"]
            == account_photo
        )
        client.post("/api/accounts/1/logout", json={})
        assert client.get(url).status_code == 400


async def test_native_photo_download_requests_only_the_small_current_photo(tmp_path):
    requests = []
    entity = types.User(
        id=42, access_hash=101, photo=types.UserProfilePhoto(photo_id=1001, dc_id=2)
    )

    class Client:
        def is_connected(self):
            return True

        async def get_entity(self, peer):
            assert isinstance(peer, types.PeerUser) and peer.user_id == 42
            return entity

        async def iter_download(self, location, **options):
            requests.append((location, options))
            yield picture()

    transport = object.__new__(PersonalTransport)
    transport.account_id, transport.verified, transport.client = 1, True, Client()
    path = tmp_path / "photo"
    path.touch(mode=0o600)
    await transport.download_avatar("user:42", "1001", path, max_bytes=262_144)
    location, options = requests[0]
    assert isinstance(location, types.InputPeerPhotoFileLocation)
    assert location.photo_id == 1001 and location.big is False
    assert location.peer.user_id == 42
    assert options == {"dc_id": 2, "request_size": 65536}
    with pytest.raises(Refused):
        await transport.download_avatar("user:42", "1002", path, max_bytes=262_144)
    assert len(requests) == 1
    with pytest.raises(Refused, match="size bound"):
        await transport.download_avatar("user:42", "1001", path, max_bytes=1)


async def test_discovery_updates_current_photo_and_preserves_exclusions(rig):
    manager, now = rig
    app = manager.get(1)
    app.store.put_chat(
        Chat(
            account_id=1,
            peer="user:42",
            title="Excluded",
            kind="private",
            ai_eligible=False,
            photo_id="1000",
        )
    )
    entity = types.User(id=42, photo=types.UserProfilePhoto(photo_id=1001, dc_id=2))

    class Client:
        async def iter_dialogs(self, **options):
            yield SimpleNamespace(
                entity=entity,
                name="Authored",
                archived=True,
                date=datetime.now(UTC),
                dialog=SimpleNamespace(read_inbox_max_id=0),
                message=None,
            )

    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id, transport.client = app.store, 1, Client()
    await transport.discover()
    assert app.store.chat("1/user:42").photo_id == "1001"
    assert not app.store.chat("1/user:42").ai_eligible
    entity.photo = types.UserProfilePhotoEmpty()
    await transport.discover()
    assert app.store.chat("1/user:42").photo_id is None
    assert profile_photo_id(entity) is None


def test_avatar_schema_upgrade_retains_content_and_account_preferences(rig):
    manager, now = rig
    app = manager.get(1)
    manager.set_processing(1, False)
    app.store.mutate_draft("1/user:101", 0, lambda d: d.update(text="Keep this draft"))
    with app.store.transaction() as db:
        db.execute("DROP TABLE avatars")
        db.execute("PRAGMA user_version=10")
    reopened = Store(app.store.path, account_id=1, shared_path=manager.registry.path)
    assert reopened.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    assert reopened.draft("1/user:101")["text"] == "Keep this draft"
    assert not manager.registry.processing(1)["ai_enabled"]
