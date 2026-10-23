"""Authored photos through the installed SDK, fake wire and controlled policy."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from enloq.application.accounts import AccountManager
from enloq.application.avatars import Avatars
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.storage.store import RETENTION, Store
from enloq.transport import mtproto
from enloq.transport.mtproto import (
    DOWNLOAD,
    PacedTelegramClient,
    _BoundedPacker,
    native_request_guard,
    observation_cycle,
)
from enloq.transport.telethon import PersonalTransport, peer_name
from telethon import errors, functions, types, utils

from backend.tests.test_avatars import picture
from backend.tests.test_group_intake import entity
from backend.tests.test_transport import Clock


def put_group(
    store, peer="channel:42", *, photo_id="1001", access="available", supported=True
):
    chat = Chat(
        account_id=store.account_id,
        peer=peer,
        title="Authored group",
        kind="group",
        photo_id=photo_id,
    )
    store.put_chat(chat, activity=store.clock())
    store.groups.observe(
        chat.key, access=access, supported=supported, reason="authored_metadata"
    )
    return chat.key


def lose(store, key, *, restore=False):
    store.groups.observe(key, access="lost", reason="confirmed_membership_loss")
    if restore:
        store.groups.observe(key, access="available", reason="fresh_restoration")


async def test_group_photo_cache_is_account_and_typed_peer_scoped_and_cache_only_after_loss(
    tmp_path,
):
    manager = AccountManager(Settings(data=tmp_path))
    manager._create(manager.registry.register(2, "authored-second.session"))
    for identity in (1, 2):
        app = manager.get(identity)
        app.store.clock = lambda: 2_000_000_000
        manager.set_processing(identity, False)
        for peer, color in (("chat:42", "red"), ("channel:42", "blue")):
            put_group(app.store, peer)
            app.transport.avatars[peer, "1001"] = picture(
                color if identity == 1 else "green"
            )
            results = await asyncio.gather(
                *(app.avatars.get(peer, "1001") for _ in range(3))
            )
            assert all(value == results[0] for value in results)
            assert len(app.transport.avatar_reads) == (1 if peer == "chat:42" else 2)
            assert not app.store.groups.get(f"{identity}/{peer}").processing_enabled
            assert not app.engine.calls and not app.store.rows("SELECT * FROM jobs")
    a, b = manager.get(1), manager.get(2)
    assert a.avatars.path("chat:42", "1001") != a.avatars.path("channel:42", "1001")
    assert a.avatars.path("channel:42", "1001") != b.avatars.path("channel:42", "1001")
    lose(a.store, "1/channel:42")
    restarted = Avatars(a.store, a.transport)
    assert restarted.current("channel:42") == "1001"
    assert await restarted.get("channel:42", "1001") == (picture("blue"), "image/png")
    assert len(a.transport.avatar_reads) == 2
    await manager.logout(1)
    with pytest.raises(Refused):
        await a.avatars.get("channel:42", "1001")
    assert await b.avatars.get("channel:42", "1001") == (picture("green"), "image/png")
    await manager.close()


@pytest.mark.parametrize(
    "access,supported,photo_id",
    [
        ("unknown", True, "1001"),
        ("lost", True, "1001"),
        ("available", False, "1001"),
        ("available", True, None),
    ],
)
async def test_unavailable_uncached_group_photo_has_initials_fallback_without_native_work(
    tmp_path, access, supported, photo_id
):
    manager = AccountManager(Settings(data=tmp_path))
    app = manager.get(1)
    put_group(app.store, access=access, supported=supported, photo_id=photo_id)
    assert app.avatars.current("channel:42") == photo_id
    with pytest.raises(Refused):
        await app.avatars.get("channel:42", photo_id or "1001")
    assert not app.transport.avatar_reads and not app.store.rows(
        "SELECT * FROM avatars"
    )
    assert app.avatars.current("channel:999") is None


async def test_expired_group_photo_on_lost_access_does_not_refetch(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    app = manager.get(1)
    now = [2_000_000_000]
    app.store.clock = lambda: now[0]
    key = put_group(app.store)
    app.transport.avatars["channel:42", "1001"] = picture()
    await app.avatars.get("channel:42", "1001")
    lose(app.store, key)
    now[0] += RETENTION + 1
    with pytest.raises(Refused):
        await app.avatars.get("channel:42", "1001")
    assert len(app.transport.avatar_reads) == 1
    assert not app.store.rows("SELECT * FROM avatars")
    assert not list(app.avatars.directory.glob("*.img"))


@pytest.mark.parametrize("change", ["loss", "restoration", "photo", "protected"])
async def test_group_avatar_completion_requires_unchanged_access_and_photo_evidence(
    tmp_path, change
):
    manager = AccountManager(Settings(data=tmp_path))
    app = manager.get(1)
    key = put_group(app.store)
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(peer, identity, path, **options):
        entered.set()
        await release.wait()
        path.write_bytes(picture())

    app.transport.download_avatar = download
    task = asyncio.create_task(app.avatars.get("channel:42", "1001"))
    await entered.wait()
    if change == "photo":
        app.store.put_chat(app.store.chat(key).model_copy(update={"photo_id": "1002"}))
    elif change == "protected":
        app.store.groups.observe(
            key, access="available", supported=False, reason="protected"
        )
    else:
        lose(app.store, key, restore=change == "restoration")
    release.set()
    with pytest.raises(Refused):
        await task
    assert not app.store.rows("SELECT * FROM avatars")
    assert not list(app.avatars.directory.glob("*.img"))
    assert not list((app.avatars.directory / "pending").iterdir())


class FakeSender:
    """The real SDK/policy append to a real bounded packer, answered locally."""

    def __init__(self, harness, *, exported=False):
        self.harness, self.exported = harness, exported
        self.wires = []
        self._send_queue = SimpleNamespace(append=self.wires.append)
        self.disconnected = False

    def is_connected(self):
        return True

    async def connect(self, connection):
        if self.harness.connect_hook:
            self.harness.connect_hook()

    async def disconnect(self):
        self.disconnected = True

    def send(self, request, ordered=False):
        future = asyncio.get_running_loop().create_future()
        for index in range(self.harness.repeats):
            if self.harness.before_wire:
                self.harness.before_wire(request, index, self.exported)
            state = SimpleNamespace(request=request, future=future)
            self._send_queue.append(state)
            if future.done():
                return future
            # Record values before Telethon mutates the same GetFile offset.
            self.harness.requests.append(copy.deepcopy(request))

        async def respond():
            value = request
            while isinstance(
                value,
                (functions.InvokeWithLayerRequest, functions.InitConnectionRequest),
            ):
                value = value.query
            if self.harness.response_hook:
                await self.harness.response_hook(value)
            if isinstance(
                value,
                (
                    functions.messages.GetChatsRequest,
                    functions.channels.GetChannelsRequest,
                ),
            ):
                if self.harness.metadata_error:
                    raise self.harness.metadata_error
                result = types.messages.Chats(chats=[self.harness.entity])
            elif isinstance(value, functions.upload.GetFileRequest):
                result = types.upload.File(
                    type=types.storage.FilePng(),
                    mtime=0,
                    bytes=self.harness.data[value.offset : value.offset + value.limit],
                )
            elif isinstance(value, functions.auth.ExportAuthorizationRequest):
                result = types.auth.ExportedAuthorization(
                    id=71, bytes=b"authored-export"
                )
            elif isinstance(value, functions.auth.ImportAuthorizationRequest):
                result = True
            else:
                raise AssertionError(type(value).__name__)
            future.set_result(result)

        task = asyncio.create_task(respond())

        def done(task):
            if not task.cancelled() and task.exception() and not future.done():
                future.set_exception(task.exception())

        task.add_done_callback(done)
        return future


class Harness:
    def __init__(self, tmp_path, peer):
        self.clock = Clock()
        self.store = Store(
            tmp_path / "store.sqlite3", account_id=1, clock=lambda: self.clock.now
        )
        self.key = put_group(self.store, peer)
        self.peer = peer
        self.entity = entity(peer, self.clock.now)
        self.entity.photo = types.ChatPhoto(photo_id=1001, dc_id=2)
        self.data = picture()
        self.requests, self.exported = [], []
        self.before_wire = self.response_hook = self.lookup_hook = self.connect_hook = (
            None
        )
        self.metadata_error, self.repeats = None, 1
        self.client = PacedTelegramClient(
            str(tmp_path / "authored.session"), 1, "authored"
        )
        self.client.session.set_dc(2, "127.0.0.1", 443)
        self.policy = self.clock.policy(tmp_path / "authored.policy.sqlite3")
        self.client.request_policy = self.policy
        self.sender = FakeSender(self)
        self.client._sender = self.sender

        async def lookup(peer):
            if self.lookup_hook:
                await self.lookup_hook(peer)
            if isinstance(peer, types.PeerChannel):
                return types.InputPeerChannel(peer.channel_id, 100)
            return utils.get_input_peer(peer, check_hash=False)

        self.client.get_input_entity = lookup
        self.transport = object.__new__(PersonalTransport)
        self.transport.store, self.transport.account_id, self.transport.client = (
            self.store,
            1,
            self.client,
        )
        self.transport.verified, self.transport.requires_login = True, False
        self.avatars = Avatars(self.store, self.transport)

    def file_requests(self):
        return [
            value
            for value in self.requests
            if isinstance(value, functions.upload.GetFileRequest)
        ]

    async def download(self, tmp_path):
        path = tmp_path / "authored-photo"
        path.touch(mode=0o600)
        await self.transport.download_avatar(self.peer, "1001", path, max_bytes=262_144)
        return path.read_bytes()


@pytest.fixture
def sdk(tmp_path):
    harnesses = []

    def create(peer="channel:42"):
        directory = tmp_path / str(len(harnesses))
        directory.mkdir()
        value = Harness(directory, peer)
        harnesses.append(value)
        return value

    yield create
    for value in harnesses:
        value.client.session.close()


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_group_native_profile_uses_exact_metadata_and_small_bounded_sdk_file_chunks(
    sdk, peer
):
    value = sdk(peer)
    assert await value.avatars.get(peer, "1001") == (picture(), "image/png")
    request, part = value.requests
    if peer.startswith("chat:"):
        assert isinstance(
            request, functions.messages.GetChatsRequest
        ) and request.id == [42]
    else:
        assert isinstance(request, functions.channels.GetChannelsRequest)
        assert [(item.channel_id, item.access_hash) for item in request.id] == [
            (42, 100)
        ]
    assert isinstance(part, functions.upload.GetFileRequest)
    assert isinstance(part.location, types.InputPeerPhotoFileLocation)
    assert peer_name(part.location.peer) == peer and part.location.photo_id == 1001
    assert not part.location.big and not getattr(part.location, "video", False)
    assert (part.offset, part.limit) == (0, 65536)
    assert all(getattr(native, "_enloq_final_guard", None) for native in value.requests)
    assert await value.avatars.get(peer, "1001") == (picture(), "image/png")
    assert len(value.requests) == 2


@pytest.mark.parametrize(
    "race", ["lookup", "pacing", "metadata_response", "file_lane", "wire", "correction"]
)
async def test_group_avatar_access_rechecked_after_lookup_pacing_and_at_sdk_wire(
    sdk, race
):
    value = sdk()
    if race == "lookup":

        async def lookup(peer):
            lose(value.store, value.key)

        value.lookup_hook = lookup
    elif race == "pacing":
        value.policy.hold("reads", value.clock.now + 1)

        async def sleep(seconds):
            value.clock.now += seconds
            lose(value.store, value.key)

        value.policy.sleep = sleep
    elif race == "metadata_response":

        async def response(request):
            lose(value.store, value.key, restore=True)

        value.response_hook = response
    elif race in {"wire", "correction"}:
        value.repeats = 2 if race == "correction" else 1

        def before_wire(request, index, exported):
            if isinstance(request, functions.upload.GetFileRequest) and index == (
                1 if race == "correction" else 0
            ):
                lose(value.store, value.key)

        value.before_wire = before_wire
    if race == "file_lane":
        queued = asyncio.Event()
        original = value.policy.execute

        async def execute(request, *args, **kwargs):
            if isinstance(request, functions.upload.GetFileRequest):
                queued.set()
            return await original(request, *args, **kwargs)

        value.policy.execute = execute
        async with value.policy.files.slot(DOWNLOAD):
            task = asyncio.create_task(value.avatars.get(value.peer, "1001"))
            await queued.wait()
            lose(value.store, value.key)
        with pytest.raises(Refused):
            await task
    else:
        with pytest.raises(Refused):
            await value.avatars.get(value.peer, "1001")
    assert len(value.file_requests()) == (1 if race == "correction" else 0)
    if race in {"lookup", "pacing"}:
        assert not value.requests
    assert not value.store.rows("SELECT * FROM avatars")
    assert not list(value.avatars.directory.glob("*.img"))
    assert not list((value.avatars.directory / "pending").iterdir())


@pytest.mark.parametrize(
    "change",
    ["left", "protected_min", "forum", "migrated", "photo", "wrong_peer", "forbidden"],
)
async def test_exact_group_photo_metadata_refuses_changed_or_excluded_group_before_file_reads(
    sdk, change
):
    value = sdk("chat:42" if change == "migrated" else "channel:42")
    if change == "left":
        value.entity.left = True
    elif change == "protected_min":
        value.entity.min, value.entity.noforwards = True, True
    elif change == "forum":
        value.entity.forum = True
    elif change == "migrated":
        value.entity.migrated_to = types.InputChannel(42, 100)
    elif change == "photo":
        value.entity.photo.photo_id = 1002
    elif change == "wrong_peer":
        value.entity.id = 43
    else:
        value.metadata_error = errors.ChannelPrivateError(request=None)
    with pytest.raises(Refused):
        await value.avatars.get(value.peer, "1001")
    assert not value.file_requests()
    state = value.store.groups.get(value.key)
    if change in {"left", "migrated", "forbidden"}:
        assert state.access == "lost"
    if change in {"protected_min", "forum"}:
        assert not state.supported
    if change == "photo":
        assert value.avatars.current(value.peer) == "1002"
    if change not in {"wrong_peer"}:
        assert not value.store.rows("SELECT * FROM avatars")


@pytest.mark.parametrize(
    "mutation", ["peer", "hash", "photo", "big", "video", "offset", "limit"]
)
async def test_sdk_file_request_guard_keeps_exact_photo_location_and_chunk_bound(
    sdk, mutation
):
    value = sdk()

    def before_wire(request, index, exported):
        if not isinstance(request, functions.upload.GetFileRequest):
            return
        if mutation == "peer":
            request.location.peer = types.InputPeerChannel(43, 100)
        elif mutation == "hash":
            request.location.peer.access_hash = 101
        elif mutation == "photo":
            request.location.photo_id = 1002
        elif mutation in {"big", "video"}:
            setattr(request.location, mutation, True)
        elif mutation == "offset":
            request.offset = 262145
        else:
            request.limit = 262144

    value.before_wire = before_wire
    with pytest.raises(Refused):
        await value.avatars.get(value.peer, "1001")
    assert not value.file_requests()


async def test_sdk_chunks_stop_after_bound_and_after_confirmed_loss_between_parts(sdk):
    bounded = sdk()
    bounded.data = b"x" * (262_144 + 1)
    with pytest.raises(Refused, match="size bound"):
        await bounded.download(bounded.store.path.parent)
    assert [request.offset for request in bounded.file_requests()] == [
        0,
        65536,
        131072,
        196608,
        262144,
    ]
    assert all(request.limit == 65536 for request in bounded.file_requests())

    lost = sdk()
    lost.data = picture() + b"\0" * 65536

    def before_wire(request, index, exported):
        if (
            isinstance(request, functions.upload.GetFileRequest)
            and request.offset == 65536
        ):
            lose(lost.store, lost.key)

    lost.before_wire = before_wire
    with pytest.raises(Refused):
        await lost.avatars.get(lost.peer, "1001")
    assert [request.offset for request in lost.file_requests()] == [0]
    assert not lost.store.rows("SELECT * FROM avatars")


@pytest.mark.parametrize("race", [None, "connected", "export_response", "import_wire"])
async def test_guarded_exported_file_sender_retains_intent_through_sdk_initialization(
    sdk, monkeypatch, race
):
    value = sdk()
    value.entity.photo.dc_id = 3

    async def dc(identity, cdn=False):
        return SimpleNamespace(id=identity, ip_address="127.0.0.1", port=443)

    value.client._get_dc = dc

    def sender(*args, **kwargs):
        result = FakeSender(value, exported=True)
        value.exported.append(result)
        return result

    monkeypatch.setattr(mtproto, "MTProtoSender", sender)
    if race == "connected":
        value.connect_hook = lambda: lose(value.store, value.key)
    elif race == "export_response":

        async def response(request):
            if isinstance(request, functions.auth.ExportAuthorizationRequest):
                lose(value.store, value.key)

        value.response_hook = response
    elif race == "import_wire":

        def before_wire(request, index, exported):
            if exported:
                lose(value.store, value.key)

        value.before_wire = before_wire
    if race:
        with pytest.raises(Refused):
            await value.avatars.get(value.peer, "1001")
        assert not value.file_requests() and not value.exported[0].wires
        assert value.exported[0].disconnected
    else:
        assert await value.avatars.get(value.peer, "1001") == (picture(), "image/png")
        assert len(value.file_requests()) == 1
        assert isinstance(value.requests[1], functions.auth.ExportAuthorizationRequest)
        assert isinstance(value.requests[2], functions.InvokeWithLayerRequest)
        assert isinstance(
            value.requests[2].query.query, functions.auth.ImportAuthorizationRequest
        )
        assert isinstance(value.exported[0]._send_queue, _BoundedPacker)


async def test_task_guard_revalidates_queued_cached_reads_and_does_not_leak_into_other_tasks(
    tmp_path,
):
    clock = Clock()
    policy = clock.policy(tmp_path / "policy.sqlite3")
    active = [True]
    entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    def guard(request):
        if not active[0]:
            raise Refused("Authored intent expired")

    async def read():
        calls.append("native")
        entered.set()
        await release.wait()
        return "authored cached metadata"

    async def guarded_read():
        with native_request_guard(guard):
            queued.set()
            return await policy.execute(functions.messages.GetChatsRequest([42]), read)

    with observation_cycle():
        first = asyncio.create_task(
            policy.execute(functions.messages.GetChatsRequest([42]), read)
        )
        await entered.wait()
        second = asyncio.create_task(guarded_read())
        await queued.wait()
        await asyncio.sleep(0)
        active[0] = False
        release.set()
        assert await first == "authored cached metadata"
        with pytest.raises(Refused, match="intent expired"):
            await second
        assert (
            await policy.execute(functions.messages.GetChatsRequest([42]), read)
            == "authored cached metadata"
        )
    assert calls == ["native"]


@pytest.mark.parametrize("restored", [False, True])
async def test_confirmed_avatar_read_loss_respects_revision_order_and_clock_rollback(
    sdk, restored
):
    value = sdk()
    value.store.groups.set_processing_enabled(value.key, True)
    original = value.store.groups.get(value.key)
    value.clock.now -= 1
    value.metadata_error = errors.ChannelPrivateError(request=None)

    async def response(request):
        if restored:
            lose(value.store, value.key, restore=True)

    value.response_hook = response
    with pytest.raises(Refused):
        await value.avatars.get(value.peer, "1001")
    state = value.store.groups.get(value.key)
    assert state.access == ("available" if restored else "lost")
    assert state.access_revision == original.access_revision + (2 if restored else 1)
    assert state.processing_enabled and value.avatars.current(value.peer) == "1001"
    assert not value.file_requests() and not value.store.rows("SELECT * FROM avatars")


async def test_avatar_sending_ban_is_not_confirmed_read_access_loss(sdk):
    value = sdk()
    original = value.store.groups.get(value.key)
    value.metadata_error = errors.ChatWriteForbiddenError(request=None)
    with pytest.raises(Refused):
        await value.avatars.get(value.peer, "1001")
    assert value.store.groups.get(value.key) == original
    assert not value.file_requests()


async def test_scoped_intent_guards_compose_existing_final_guard_at_each_wire_handoff(
    sdk,
):
    value = sdk()
    calls = []
    request = functions.messages.GetChatsRequest([42])
    request._enloq_final_guard = lambda native: calls.append("existing")
    with native_request_guard(lambda native: calls.append("outer")):
        with native_request_guard(lambda native: calls.append("inner")):
            await value.client(request)
    assert calls == ["outer", "inner", "existing", "outer", "inner", "existing"]
