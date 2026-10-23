"""GB1 uses authored SDK objects and controlled clocks, never native sessions."""

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application.conversations import Conversations
from enloq.domain.models import Chat, Message, Refused
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import RETENTION, SCHEMA_VERSION, WINDOW, Store
from enloq.transport import group_chats
from enloq.transport.telethon import PersonalTransport, native_peer, peer_name
from telethon import functions, types


def entity(peer, now, **flags):
    kind, identity = peer.split(":")
    common = dict(
        id=int(identity),
        title="Authored group",
        photo=types.ChatPhotoEmpty(),
        date=datetime.fromtimestamp(now, UTC),
    )
    if kind == "chat":
        return types.Chat(**common, participants_count=3, version=1, **flags)
    return types.Channel(**common, access_hash=100, **(dict(megagroup=True) | flags))


def native(
    peer,
    identity,
    now,
    *,
    text="Authored post",
    sender=7,
    outgoing=False,
    offset=0,
    **fields,
):
    return types.Message(
        id=identity,
        peer_id=native_peer(peer),
        date=datetime.fromtimestamp(now - offset, UTC),
        message=text,
        from_id=types.PeerUser(sender) if sender else None,
        out=outgoing,
        **fields,
    )


def dialog(value, now, *, top=100, pts=4):
    return SimpleNamespace(
        entity=value,
        name=value.title,
        date=datetime.fromtimestamp(now, UTC),
        archived=True,
        dialog=types.Dialog(
            peer=native_peer(group_chats.describe(value).peer),
            top_message=top,
            read_inbox_max_id=2,
            read_outbox_max_id=0,
            unread_count=0,
            unread_mentions_count=0,
            unread_reactions_count=0,
            unread_poll_votes_count=0,
            notify_settings=types.PeerNotifySettings(
                mute_until=datetime.fromtimestamp(now + 3600, UTC)
            ),
            pts=pts,
        ),
        message=SimpleNamespace(id=top, date=datetime.fromtimestamp(now, UTC)),
    )


class Client:
    """RPC stand-in also invokes the same final guard as the paced/wire seam."""

    def __init__(self):
        self.calls, self.lookups, self.attempted = [], [], []
        self.messages, self.entities, self.dialogs = [], {}, []
        self.response = None
        self.lookup_hook = self.before_wire = None
        self.online = True

    def is_connected(self):
        return self.online

    async def get_input_entity(self, peer):
        self.lookups.append(peer)
        if self.lookup_hook:
            await self.lookup_hook(peer)
        if isinstance(peer, types.PeerChannel):
            return types.InputPeerChannel(peer.channel_id, 100)
        if isinstance(peer, types.PeerChat):
            return types.InputPeerChat(peer.chat_id)
        return types.InputPeerUser(peer.user_id, 100)

    async def __call__(self, request):
        self.attempted.append(request)
        if self.before_wire:
            await self.before_wire(request)
        guard = getattr(request, "_enloq_final_guard", None)
        if guard:
            guard(request)
        self.calls.append(request)
        if self.response:
            return await self.response(request)
        if isinstance(request, functions.messages.GetHistoryRequest):
            peer = peer_name(request.peer)
            values = [
                value
                for value in self.messages
                if peer_name(value.peer_id) == peer
                and (not request.offset_id or value.id < request.offset_id)
            ]
            return types.messages.Messages(
                topics=[],
                messages=sorted(values, key=lambda value: -value.id)[: request.limit],
                chats=list(self.entities.values()),
                users=[types.User(id=7, first_name="Alice")],
            )
        if isinstance(
            request,
            (
                functions.messages.GetMessagesRequest,
                functions.channels.GetMessagesRequest,
            ),
        ):
            identities = {value.id for value in request.id}
            values = [
                value
                for value in self.messages
                if value.id in identities
                and (
                    not isinstance(request, functions.channels.GetMessagesRequest)
                    or value.peer_id == types.PeerChannel(request.channel.channel_id)
                )
            ]
            return types.messages.Messages(
                topics=[],
                messages=values,
                chats=list(self.entities.values()),
                users=[types.User(id=7, first_name="Alice")],
            )
        if isinstance(request, functions.updates.GetChannelDifferenceRequest):
            return types.updates.ChannelDifferenceEmpty(pts=request.pts, final=True)
        if isinstance(request, functions.updates.GetStateRequest):
            return types.updates.State(
                pts=4, qts=0, date=datetime.now(UTC), seq=0, unread_count=0
            )
        raise AssertionError(type(request).__name__)

    async def iter_dialogs(self, **kwargs):
        for value in self.dialogs:
            yield value


def rig(tmp_path, *, account_id=1):
    now = [2_000_000_000.0]
    store = Store(
        tmp_path / str(account_id) / "store.sqlite3",
        account_id=account_id,
        clock=lambda: now[0],
    )
    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id, transport.client = (
        store,
        account_id,
        Client(),
    )
    transport.verified, transport.requires_login, transport.identity = True, False, None
    app = Conversations(store, transport)
    transport.sync = app.sync
    transport.bind_receipts(store.send_operations.record_receipts)
    return app, now


def group(app, now, peer="channel:42", **flags):
    value = entity(peer, now[0], **flags)
    app.transport.client.entities[peer] = value
    app.transport._discovered(dialog(value, now[0]))
    return f"{app.store.account_id}/{peer}"


def source(app, now, peer="channel:42", identity=1, **fields):
    return Message(
        account_id=app.store.account_id,
        peer=peer,
        message_id=identity,
        revision=1,
        date=now[0],
        text="Authored source",
        author="Alice",
        author_peer="user:7",
        **fields,
    )


def no_private_work(app):
    for table in (
        "blocks",
        "jobs",
        "derivatives",
        "memory_entries",
        "analyzed",
        "emotion_state",
        "suggestion_state",
        "reaction_events",
        "incoming_deferred",
        "incoming_notices",
        "reservations",
        "receipts",
    ):
        assert app.store.rows(f"SELECT * FROM {table}") == []
    assert not app.tasks and not app.engine.calls and not app.decider.calls


def test_typed_group_collisions_keep_private_default_and_account_isolation(tmp_path):
    app, now = rig(tmp_path)
    second, other_now = rig(tmp_path, account_id=2)
    for current, clock in ((app, now), (second, other_now)):
        for peer in ("chat:1", "channel:1", "chat:777000", "channel:777000"):
            key = group(current, clock, peer)
            assert not current.store.groups.get(key).processing_enabled
            current.sync.ingest(source(current, clock, peer))
        current.store.put_chat(
            Chat(
                account_id=current.store.account_id,
                peer="user:42",
                title="Private",
                kind="private",
            ),
            activity=clock[0],
        )
        assert [chat["peer"] for chat in current.store.recent_chats()] == ["user:42"]
        assert {chat["peer"] for chat in current.store.recent_chats("group")} == {
            "chat:1",
            "channel:1",
            "chat:777000",
            "channel:777000",
        }
        assert len(current.store.sync_targets()) == 5
    app.store.groups.set_processing_enabled("1/chat:1", True)
    assert second.store.groups.get("2/chat:1").processing_enabled is False
    with pytest.raises(Refused):
        second.store.groups.get("1/chat:1")
    with pytest.raises(Refused):
        app.store.groups.set_processing_enabled("1/chat:1", 1)
    no_private_work(app)


@pytest.mark.parametrize(
    "peer,flags",
    [
        ("chat:42", {"noforwards": True}),
        ("chat:42", {"left": True}),
        ("channel:42", {"broadcast": True}),
        ("channel:42", {"forum": True}),
        ("channel:42", {"monoforum": True}),
        ("channel:42", {"gigagroup": True}),
        ("channel:42", {"noforwards": True}),
        ("channel:42", {"min": True}),
    ],
)
async def test_excluded_groups_admit_no_content_or_work(tmp_path, peer, flags):
    app, now = rig(tmp_path)
    value = entity(peer, now[0], **flags)
    app.transport._discovered(dialog(value, now[0]))
    await app.transport._incoming(
        SimpleNamespace(message=native(peer, 1, now[0]), chat=value)
    )
    assert app.store.rows("SELECT * FROM chats") == []
    assert app.store.rows("SELECT * FROM messages") == []
    assert app.transport.client.calls == []
    no_private_work(app)


@pytest.mark.parametrize("flag", ["forum", "noforwards", "broadcast", "left"])
def test_sparse_negative_metadata_revokes_known_group_and_cannot_restore(
    tmp_path, flag
):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now))
    assert app.store.groups.current(app.store.message(key, 1))
    sparse = entity("channel:42", now[0], min=True, **{flag: True})
    assert app.transport.admit_peer("channel:42", sparse) is False
    assert not app.store.recent_chats("group") and not app.store.sync_targets()
    assert not app.store.groups.current(app.store.message(key, 1))
    assert (
        app.transport.admit_peer("channel:42", entity("channel:42", now[0], min=True))
        is False
    )
    assert app.store.message(key, 1).text == "Authored source"


async def test_group_native_originals_are_independent_of_ai_and_never_prefetch(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now, "chat:42")
    for index, fields in enumerate(
        (
            {},
            {"outgoing": True},
            {"service": True},
            {"media": "audio"},
            {"media": "image"},
        ),
        1,
    ):
        app.sync.ingest(source(app, now, "chat:42", index, **fields), live=True)
        app.arrived(app.store.message(key, index))
    await app.process_due()
    assert len(app.store.messages(key)) == 5
    assert app.store.groups.get(key).processing_enabled is False
    assert app.store.rows("SELECT * FROM message_reactions") == []
    no_private_work(app)


async def test_recent_scan_includes_read_own_and_service_sources_but_stops_at_window(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    values = [
        native("channel:42", index, now[0], outgoing=index == 2, offset=index)
        for index in range(1, 4)
    ]
    values += [native("channel:42", 4, now[0], offset=WINDOW + 1)]
    app.transport.client.messages = values
    result = await app.sync.scan(key)
    assert result["complete"] and result["count"] == 3
    assert result["cutoff"] == now[0] - WINDOW
    assert {message.message_id for message in app.store.messages(key)} == {1, 2, 3}
    assert all(app.store.groups.current(message) for message in app.store.messages(key))
    before = list(app.transport.client.calls)
    await app.sync.scan(key)
    assert app.transport.client.calls == before
    no_private_work(app)


async def test_withheld_post_preserves_full_raw_page_progress_and_missing_count(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.transport.client.messages = [
        native("channel:42", index, now[0], noforwards=index == 50)
        for index in range(1, 102)
    ]
    first = await app.sync.scan(key)
    assert not first["complete"] and first["before"] == 2
    assert first["count"] == 99 and first["omitted"] == 1
    assert app.store.message(key, 50) is None
    second = await app.sync.scan(key)
    assert second["complete"] and second["count"] == 100
    assert [request.offset_id for request in app.transport.client.calls] == [0, 2]
    assert all(request.limit == 100 for request in app.transport.client.calls)


async def test_gap_invalidates_old_proofs_and_does_not_claim_suffix_or_older_days(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    first = source(app, now)
    app.sync.ingest(first)
    epoch = app.store.groups.intake(key).epoch
    app.store.groups.mark_gap(key)
    assert app.store.message(key, 1) == first
    assert not app.store.groups.current(first)
    assert not app.sync.ingest(
        first.model_copy(update={"text": "Stale read"}), group_epoch=epoch
    )
    app.transport.client.messages = [native("channel:42", 2, now[0])]
    await app.sync.scan(key)
    intake = app.store.groups.intake(key)
    assert intake.complete and intake.cutoff == now[0] - WINDOW
    assert app.store.groups.current(app.store.message(key, 2))
    assert not app.store.groups.current(first)
    with pytest.raises(Refused):
        await app.transport.history("channel:42", 1, 100)


async def test_basic_peerless_delete_is_unique_and_channel_delete_is_qualified(
    tmp_path,
):
    app, now = rig(tmp_path)
    basic = group(app, now, "chat:42")
    channel = group(app, now)
    app.sync.ingest(source(app, now, "chat:42"))
    app.sync.deleted(None, [1])
    assert app.store.message(basic, 1).deleted
    app.sync.ingest(source(app, now, "chat:42", 2))
    app.sync.ingest(source(app, now, "channel:42", 2))
    app.sync.deleted(None, [2])
    assert (
        not app.store.message(basic, 2).deleted
        and not app.store.message(channel, 2).deleted
    )
    app.sync.deleted("channel:42", [2])
    assert (
        not app.store.message(basic, 2).deleted
        and app.store.message(channel, 2).deleted
    )
    no_private_work(app)


async def test_migration_is_sticky_separate_identity_and_retains_drafts_opt_in(
    tmp_path,
):
    app, now = rig(tmp_path)
    old = group(app, now, "chat:42")
    app.store.groups.set_processing_enabled(old, True)
    app.store.mutate_draft(old, 0, lambda draft: draft.update(text="Kept old draft"))
    app.sync.ingest(source(app, now, "chat:42"))
    migrated = types.MessageService(
        id=10,
        peer_id=types.PeerChat(42),
        date=datetime.fromtimestamp(now[0], UTC),
        action=types.MessageActionChatMigrateTo(channel_id=42),
    )
    await app.transport._group_service(
        types.UpdateNewMessage(message=migrated, pts=5, pts_count=1)
    )
    current = group(app, now, "channel:42")
    app.transport._discovered(dialog(entity("chat:42", now[0]), now[0]))
    state = app.store.groups.get(old)
    assert (
        state.access == "lost"
        and state.migrated_to == "channel:42"
        and state.processing_enabled
    )
    assert not app.store.groups.get(current).processing_enabled
    assert (
        app.store.draft(old)["text"] == "Kept old draft"
        and not app.store.draft(current)["text"]
    )
    assert (
        app.store.message(old, 1).text == "Authored source"
        and app.store.messages(current) == []
    )
    assert not app.store.groups.current(app.store.message(old, 1))
    assert [row["key"] for row in app.store.recent_chats("group")] == [current]


def test_expiry_keeps_group_access_policy_and_checkpoints_without_sources(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now))
    app.store.groups.set_processing_enabled(key, True)
    app.store.mutate_draft(key, 0, lambda draft: draft.update(text="Retained draft"))
    state, intake = app.store.groups.get(key), app.store.groups.intake(key)
    now[0] += RETENTION + 1
    app.store.expire()
    assert not app.store.messages(key) and not app.store.rows(
        "SELECT * FROM group_sources"
    )
    assert app.store.groups.get(key) == state and app.store.groups.intake(key) == intake
    assert (
        app.store.draft(key)["text"] == "Retained draft"
        and not app.store.sync_targets()
    )


def test_schema29_additive_upgrade_keeps_real_content_settings_and_spending(tmp_path):
    registry = AccountRegistry(tmp_path)
    registry.register(1, "owned.session", name="Authored account")
    registry.set_processing(1, False)
    registry.set_processing_threshold(345)
    registry.set_budget(2_500_000)
    with registry.transaction() as db:
        db.execute(
            "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES (1,'shared-kept','scope','incoming_rewrite','revision','day','engine',100,75,'receipted',?)",
            (2e9,),
        )
        db.execute(
            "INSERT INTO model_receipts (account_id,id,role,model,provider,usage,error) VALUES (1,'shared-kept','incoming_rewrite','model','provider','{}',NULL)"
        )
    store = Store(
        tmp_path / "content.sqlite3",
        account_id=1,
        clock=lambda: 2e9,
        shared_path=registry.path,
    )
    chat = Chat(account_id=1, peer="user:42", title="Authored private", kind="private")
    store.put_chat(chat, activity=2e9)
    store.apply_message(
        Message(
            account_id=1,
            peer=chat.peer,
            message_id=1,
            revision=1,
            date=2e9,
            text="Retained original",
        )
    )
    store.mutate_draft(chat.key, 0, lambda draft: draft.update(text="Retained draft"))
    legacy_group = Chat(
        account_id=1,
        peer="chat:42",
        title="Retained legacy group",
        kind="group",
        ai_eligible=False,
    )
    store.put_chat(legacy_group, activity=2e9)
    store.mutate_draft(
        legacy_group.key, 0, lambda draft: draft.update(text="Retained group draft")
    )
    legacy_source = Message(
        account_id=1,
        peer=legacy_group.peer,
        message_id=2,
        revision=1,
        date=2e9,
        text="Retained group original",
        author="Alice",
        author_peer="user:7",
    )
    with store.transaction() as db:
        db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (
                legacy_source.key,
                legacy_group.key,
                2,
                2e9,
                1,
                0,
                legacy_source.model_dump_json(),
            ),
        )
        db.execute(
            "INSERT INTO reservations (id,bucket,engine,reserved,actual,status,created) VALUES ('kept','day','engine',100,NULL,'unknown',?)",
            (2e9,),
        )
        db.execute(
            "INSERT INTO receipts (id,role,model,provider,usage,error) VALUES ('kept','incoming_rewrite','model','provider','{}',NULL)"
        )
        # Recreate the actual pre-family schema rather than merely relabeling
        # the current schema as 29. The later GB3 migration must stay additive.
        for table in ("reservations", "receipts", "budget_changes"):
            db.execute(f"ALTER TABLE {table} DROP COLUMN family")
    with registry.transaction() as db:
        for table in ("model_reservations", "model_receipts", "budget_changes"):
            db.execute(f"ALTER TABLE {table} DROP COLUMN family")
    tables = ("metadata", "chats", "messages", "drafts", "reservations", "receipts")
    before = {table: store.rows(f"SELECT * FROM {table}") for table in tables}
    shared_tables = (
        "metadata",
        "accounts",
        "model_reservations",
        "model_receipts",
        "budget_changes",
    )
    shared = {table: registry.rows(f"SELECT * FROM {table}") for table in shared_tables}
    store.close()
    registry.connections.close()
    with sqlite3.connect(store.path) as db:
        for table in ("group_sources", "group_intake", "group_state"):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=29")
    registry = AccountRegistry(tmp_path)
    upgraded = Store(
        store.path, account_id=1, clock=lambda: 2e9, shared_path=registry.path
    )
    assert (
        upgraded.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION == 38
    )

    def legacy_rows(owner, table):
        return [
            {key: value for key, value in row.items() if key != "family"}
            for row in owner.rows(f"SELECT * FROM {table}")
        ]

    assert {table: legacy_rows(upgraded, table) for table in tables} == before
    assert {table: legacy_rows(registry, table) for table in shared_tables} == shared
    for owner, ledger_tables in (
        (upgraded, ("reservations", "receipts", "budget_changes")),
        (registry, ("model_reservations", "model_receipts", "budget_changes")),
    ):
        for table in ledger_tables:
            assert all(
                row["family"] == "daily" for row in owner.rows(f"SELECT * FROM {table}")
            )
    assert (
        registry.processing_threshold() == 345
        and registry.budget()["limit"] == 2_500_000
    )
    assert registry.processing(1)["ai_enabled"] is False
    assert upgraded.groups.get(legacy_group.key).access == "unknown"
    assert not upgraded.groups.get(legacy_group.key).processing_enabled
    intake = upgraded.groups.intake(legacy_group.key)
    assert intake.epoch == 0 and intake.pts is None and not intake.complete
    assert not upgraded.groups.current(legacy_source)
    assert upgraded.rows("SELECT * FROM group_sources") == []
    assert len(upgraded.rows("SELECT * FROM group_state")) == 1
    assert len(upgraded.rows("SELECT * FROM group_intake")) == 1
    new = Chat(account_id=1, peer="channel:42", title="New group", kind="group")
    upgraded.put_chat(new)
    assert not upgraded.groups.get(new.key).processing_enabled
    assert upgraded.groups.intake(new.key).pts is None
