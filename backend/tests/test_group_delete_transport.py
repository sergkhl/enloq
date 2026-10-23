"""Authored group deletion uses the real paced SDK and physical wire packer."""

import pytest
from enloq.domain.deletion import DeleteDescription, DeleteReceipt
from enloq.domain.models import Reaction, Refused
from enloq.transport import group_sending
from enloq.transport.mtproto import MTProtoAttemptLimit
from telethon import errors, functions, types

from backend.tests.test_deletion_transport import (
    CORRECTION,
    Evidence,
    native_empty,
    native_page,
)
from backend.tests.test_deletion_transport import (
    rig as wire_rig,
)
from backend.tests.test_group_intake import (
    entity,
    group,
    native,
)
from backend.tests.test_group_intake import (
    rig as group_rig,
)

DELETIONS = (
    functions.messages.DeleteMessagesRequest,
    functions.channels.DeleteMessagesRequest,
)
EXACT_READS = (
    functions.messages.GetMessagesRequest,
    functions.channels.GetMessagesRequest,
)


def config(limit=172800):
    value = object.__new__(types.Config)
    value.revoke_time_limit = limit
    return value


async def setup(
    tmp_path,
    peer="channel:42",
    *,
    identities=(2,),
    offset=0,
    outgoing=True,
    limit=172800,
    answer=None,
    **metadata_flags,
):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    app, now = group_rig(fixture)
    key = group(app, now, peer)
    native_sources = [
        native(
            peer,
            identity,
            now[0],
            sender=1,
            outgoing=outgoing,
            offset=offset,
            text=f"Exact authored original {identity}",
        )
        for identity in identities
    ]
    for source in await app.transport.normalize_page(native_sources):
        app.sync.ingest(source)
    metadata = entity(peer, now[0], **metadata_flags)
    answers = [types.messages.Chats([metadata])]
    if peer.startswith("chat:"):
        answers.append(config(limit))
    answers.extend(
        [
            native_page(native_sources),
            types.messages.AffectedMessages(100, len(identities))
            if answer is None
            else answer,
        ]
    )
    wire_path = tmp_path / "wire"
    wire_path.mkdir()
    wire = wire_rig(wire_path, answers)
    wire.clock.now = now[0]
    app.store.clock = lambda: wire.clock.now
    wire.transport.store, wire.transport.sync = app.store, app.sync
    wire.transport.identity = None
    wire.user = types.User(id=1, access_hash=20, is_self=True)
    wire.identity_reads = []
    wire.lookups = []

    async def get_me():
        wire.identity_reads.append(wire.user)
        return wire.user

    async def get_input_entity(value):
        wire.lookups.append(value)
        if isinstance(value, types.PeerChannel):
            return types.InputPeerChannel(value.channel_id, 100)
        return types.InputPeerChat(value.chat_id)

    wire.transport.client.get_me = get_me
    wire.transport.client.get_input_entity = get_input_entity
    sources = tuple(app.store.message(key, identity) for identity in identities)
    description = DeleteDescription(
        "authored-group-operation",
        1,
        peer,
        identities,
        access_revision=app.store.groups.get(key).access_revision,
        sources=sources,
    )
    wire.app, wire.key, wire.native, wire.metadata = app, key, native_sources, metadata
    wire.evidence = Evidence(tmp_path / "evidence.sqlite3")
    return wire, description


def mutation_hook(wire, hook):
    original = wire.transport.client.__class__.__call__

    async def call(owner, request):
        if isinstance(request, DELETIONS):
            hook(request)
        return await original(owner, request)

    wire.transport.client.__class__.__call__ = call


def mutations(wire):
    return [request for request in wire.calls if isinstance(request, DELETIONS)]


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_own_group_chunk_uses_exact_native_method_and_fresh_personal_identity(
    tmp_path, peer
):
    wire, description = await setup(tmp_path, peer, identities=(2, 3))
    result = await wire.transport.delete(description, wire.evidence)
    assert result == DeleteReceipt(100, 2)
    assert wire.identity_reads == [wire.user]
    [request] = mutations(wire)
    assert request.id == [2, 3]
    if peer.startswith("chat:"):
        assert isinstance(request, functions.messages.DeleteMessagesRequest)
        assert request.revoke is True
        assert any(
            isinstance(value, functions.help.GetConfigRequest) for value in wire.calls
        )
    else:
        assert isinstance(request, functions.channels.DeleteMessagesRequest)
        assert request.channel == types.InputChannel(42, 100)
        assert not any(
            isinstance(value, functions.help.GetConfigRequest) for value in wire.calls
        )
    [exact] = [value for value in wire.calls if isinstance(value, EXACT_READS)]
    assert tuple(value.id for value in exact.id) == (2, 3)
    assert isinstance(
        exact,
        functions.channels.GetMessagesRequest
        if peer.startswith("channel:")
        else functions.messages.GetMessagesRequest,
    )
    assert wire.evidence.events().index("exposed") > wire.evidence.events().index(
        "watching"
    )
    assert wire.evidence.events().count("exposed") == 1
    assert not any(
        isinstance(
            value,
            (
                functions.channels.GetParticipantRequest,
                functions.channels.GetParticipantsRequest,
            ),
        )
        for value in wire.calls
    )


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_own_user_author_needs_no_outgoing_or_moderator_shortcut(tmp_path, peer):
    wire, description = await setup(
        tmp_path,
        peer,
        outgoing=False,
        admin_rights=types.ChatAdminRights(anonymous=True, delete_messages=True),
        **{
            "banned_rights"
            if peer.startswith("channel:")
            else "default_banned_rights": types.ChatBannedRights(
                until_date=None, send_messages=True
            )
        },
    )
    current = description.sources[0]
    assert current.author_peer == "user:1" and not current.outgoing
    assert await wire.transport.delete(description, wire.evidence) == DeleteReceipt(
        100, 1
    )
    assert len(mutations(wire)) == 1


@pytest.mark.parametrize("sender", [None, types.PeerUser(7), types.PeerChannel(42)])
async def test_native_out_and_admin_flags_never_replace_exact_own_user_author(
    tmp_path, sender
):
    wire, description = await setup(
        tmp_path, admin_rights=types.ChatAdminRights(delete_messages=True)
    )
    wire.native[0].from_id = sender
    with pytest.raises(Refused, match="own-user.*source changed"):
        await wire.transport.delete(description, wire.evidence)
    assert mutations(wire) == [] and "exposed" not in wire.evidence.events()


@pytest.mark.parametrize(
    "user", [None, types.User(7), types.User(1, bot=True), types.User(1, deleted=True)]
)
async def test_missing_wrong_bot_or_deleted_connected_user_never_mutates(
    tmp_path, user
):
    wire, description = await setup(tmp_path)
    wire.user = user
    with pytest.raises(Refused, match="identity"):
        await wire.transport.delete(description, wire.evidence)
    assert mutations(wire) == [] and wire.calls == []


@pytest.mark.parametrize(
    "change",
    ["text", "author", "date", "reply", "native_revision", "missing", "duplicate"],
)
async def test_exact_native_material_or_membership_change_rejects_whole_chunk(
    tmp_path, change
):
    wire, description = await setup(tmp_path, identities=(2, 3))
    original = wire.native[0]
    if change == "text":
        original.message = "Changed authored wording"
    elif change == "author":
        original.post_author = "Changed signature"
    elif change == "date":
        original.date = original.date.replace(microsecond=1)
    elif change == "reply":
        original.reply_to = types.MessageReplyHeader(reply_to_msg_id=99)
    elif change == "native_revision":
        original.edit_date = original.date
    elif change == "missing":
        wire.native.pop()
    else:
        wire.native.append(original)
    with pytest.raises(Refused, match="(originals unavailable|source changed)"):
        await wire.transport.delete(description, wire.evidence)
    assert mutations(wire) == [] and "exposed" not in wire.evidence.events()


@pytest.mark.parametrize("offset", [172800, 172801])
async def test_basic_group_uses_server_revoke_time_limit(tmp_path, offset):
    wire, description = await setup(tmp_path, "chat:42", offset=offset)
    with pytest.raises(Refused, match="revoke time limit"):
        await wire.transport.delete(description, wire.evidence)
    assert mutations(wire) == []


async def test_supergroup_own_deletion_ignores_basic_group_revoke_limit(tmp_path):
    wire, description = await setup(tmp_path, offset=7 * 86400)
    assert await wire.transport.delete(description, wire.evidence) == DeleteReceipt(
        100, 1
    )


@pytest.mark.parametrize("limit", [None, 0, -1, True])
async def test_missing_or_malformed_basic_revoke_limit_is_no_mutation(tmp_path, limit):
    wire, description = await setup(tmp_path, "chat:42", limit=limit)
    with pytest.raises(Refused, match="revoke time limit unavailable"):
        await wire.transport.delete(description, wire.evidence)
    assert mutations(wire) == []


@pytest.mark.parametrize(
    "change",
    ["source", "identity", "access", "epoch", "rights", "destination", "identities"],
)
async def test_final_physical_wire_guard_rechecks_every_captured_proof(
    tmp_path, change
):
    wire, description = await setup(tmp_path)

    def alter(request):
        if change == "source":
            source = wire.app.store.message(wire.key, 2)
            wire.app.sync.ingest(
                source.model_copy(
                    update={
                        "revision": source.revision + 1,
                        "text": "Changed after review",
                    }
                )
            )
        elif change == "identity":
            wire.transport.identity = types.User(7)
        elif change == "access":
            wire.app.store.groups.observe(
                wire.key, access="lost", reason="authored", observed_at=wire.clock.now
            )
        elif change == "epoch":
            wire.app.store.groups.mark_gap(wire.key)
        elif change == "rights":
            group_sending.observe_metadata(
                wire.transport,
                entity(
                    description.peer,
                    wire.clock.now,
                    banned_rights=types.ChatBannedRights(
                        until_date=None, view_messages=True
                    ),
                ),
            )
        elif change == "destination":
            request.channel = types.InputChannel(43, 100)
        else:
            request.id = [3]

    mutation_hook(wire, alter)
    with pytest.raises(Refused):
        await wire.transport.delete(description, wire.evidence)
    assert mutations(wire) == [] and "exposed" not in wire.evidence.events()


async def test_pacing_expiry_stops_before_native_mutation(tmp_path):
    wire, description = await setup(tmp_path)
    mutation_hook(
        wire, lambda request: wire.policy.hold("account", wire.clock.now + 16)
    )
    with pytest.raises(Refused, match="rights expired"):
        await wire.transport.delete(description, wire.evidence)
    assert wire.clock.waits[-1] == 16
    assert mutations(wire) == [] and "exposed" not in wire.evidence.events()


async def test_stale_metadata_response_cannot_replace_newer_native_rights(tmp_path):
    wire, description = await setup(tmp_path)
    original = wire.transport.client.__class__.__call__

    async def call(owner, request):
        result = await original(owner, request)
        if isinstance(request, functions.channels.GetChannelsRequest):
            group_sending.observe_metadata(
                wire.transport,
                entity(
                    description.peer,
                    wire.clock.now,
                    banned_rights=types.ChatBannedRights(
                        until_date=None, view_messages=True
                    ),
                ),
            )
        return result

    wire.transport.client.__class__.__call__ = call
    with pytest.raises(Refused, match="rights changed during read"):
        await wire.transport.delete(description, wire.evidence)
    assert mutations(wire) == [] and len(wire.calls) == 1


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_sequential_chunks_refresh_identity_metadata_and_exact_source_membership(
    tmp_path, peer
):
    wire, first = await setup(tmp_path, peer, identities=tuple(range(2, 102)))
    extras = [
        native(
            peer,
            identity,
            wire.clock.now,
            sender=1,
            outgoing=True,
            text=f"Exact authored original {identity}",
        )
        for identity in range(102, 107)
    ]
    for source in await wire.transport.normalize_page(extras):
        wire.app.sync.ingest(source)
    answers = [types.messages.Chats([wire.metadata])]
    if peer.startswith("chat:"):
        answers.append(config())
    answers += [native_page(extras), types.messages.AffectedMessages(105, 5)]
    wire.answers.extend(answers)
    second = DeleteDescription(
        first.operation_id,
        1,
        peer,
        tuple(range(102, 107)),
        access_revision=first.access_revision,
        sources=tuple(
            wire.app.store.message(wire.key, value) for value in range(102, 107)
        ),
    )
    receipts = []
    for description in (first, second):
        receipts.append(await wire.transport.delete(description, wire.evidence))
    assert receipts == [DeleteReceipt(100, 100), DeleteReceipt(105, 5)]
    assert len(wire.identity_reads) == 2
    assert [len(request.id) for request in mutations(wire)] == [100, 5]
    assert [
        tuple(value.id for value in request.id)
        for request in wire.calls
        if isinstance(request, EXACT_READS)
    ] == [first.identities, second.identities]
    assert wire.evidence.events().count("exposed") == 2


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_protocol_correction_never_replays_group_deletion(tmp_path, peer):
    wire, description = await setup(tmp_path, peer, answer=CORRECTION)
    with pytest.raises(MTProtoAttemptLimit, match="cannot be automatically repeated"):
        await wire.transport.delete(description, wire.evidence)
    assert len(mutations(wire)) == 1
    assert wire.evidence.events().count("exposed") == 1


@pytest.mark.parametrize(
    "error",
    [
        errors.MessageDeleteForbiddenError(None),
        errors.ChannelPrivateError(None),
        errors.ServerError(None, "Authored uncertain reply"),
    ],
)
async def test_native_refusal_and_uncertain_exposure_stay_distinct(tmp_path, error):
    wire, description = await setup(tmp_path, answer=error)
    with pytest.raises(type(error)):
        await wire.transport.delete(description, wire.evidence)
    assert len(mutations(wire)) == 1
    if isinstance(error, errors.ServerError):
        assert "refused" not in wire.evidence.events()
    else:
        assert wire.evidence.events()[-1] == "refused"
    if isinstance(error, errors.ChannelPrivateError):
        assert wire.app.store.groups.get(wire.key).access == "lost"


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_group_exact_recovery_distinguishes_present_empty_omitted_and_duplicate(
    tmp_path, peer
):
    wire, _ = await setup(tmp_path, peer, identities=(2, 3, 4, 5, 6))
    wrong = native("chat:43", 6, wire.clock.now, sender=1)
    wire.answers[:] = [
        native_page(
            [wire.native[0], native_empty(3), native_empty(5), native_empty(5), wrong]
        )
    ]
    checked = await wire.transport.inspect_deletion(peer, (2, 3, 4, 5, 6))
    assert [source.message_id for source in checked.present] == [2]
    assert checked.absent == (3,) and checked.unavailable == (4, 5, 6)
    assert not mutations(wire)
    [request] = wire.calls
    assert isinstance(
        request,
        functions.channels.GetMessagesRequest
        if peer.startswith("channel:")
        else functions.messages.GetMessagesRequest,
    )


async def test_read_only_recovery_accepts_known_tombstones_but_never_uncached_expired_ids(
    tmp_path,
):
    wire, _ = await setup(tmp_path)
    original = wire.app.store.message(wire.key, 2)
    wire.app.sync.deleted("channel:42", [2])
    assert wire.app.store.message(wire.key, 2).deleted
    wire.answers[:] = [native_page([native_empty(2)])]
    checked = await wire.transport.inspect_deletion("channel:42", (2,))
    assert checked.absent == (2,)
    wire.calls.clear()
    checked = await wire.transport.inspect_deletion("channel:42", (99,))
    assert checked.unavailable == (99,) and wire.calls == []
    wire.clock.now = original.date + 30 * 86400 + 1
    checked = await wire.transport.inspect_deletion("channel:42", (2,))
    assert checked.unavailable == (2,) and wire.calls == []


async def test_group_recovery_held_beyond_identity_proof_marks_unavailable_without_wire(
    tmp_path,
):
    wire, _ = await setup(tmp_path)
    wire.answers[:] = [native_page([native_empty(2)])]
    wire.policy.hold("account", wire.clock.now + 16)
    checked = await wire.transport.inspect_deletion("channel:42", (2,))
    assert checked.unavailable == (2,) and not checked.absent
    assert wire.calls == [] and wire.clock.waits == [16]


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
@pytest.mark.parametrize("choices", [[], [Reaction(emoji="👍")]])
async def test_group_reaction_write_or_removal_is_refused_before_resolution(
    tmp_path, peer, choices
):
    wire, _ = await setup(tmp_path, peer)
    with pytest.raises(Refused, match="display-only"):
        await wire.transport.send_reaction(peer, 2, choices)
    assert wire.calls == [] and wire.lookups == []
