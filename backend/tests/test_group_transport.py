"""GB1 native contract and race checks use only SDK objects/RPC stand-ins."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application.runtime import sync_cycle
from enloq.domain.models import Refused, Unavailable
from enloq.storage.store import RETENTION, Store
from enloq.transport import group_chats
from telethon import errors, functions, types

from backend.tests.group_test_support import known_non_ads
from backend.tests.test_group_intake import (
    dialog,
    entity,
    group,
    native,
    no_private_work,
    rig,
    source,
)
from backend.tests.test_media_kinds import document, sticker_attribute, video


def delta(app, now, *, pts=6, messages=(), updates=(), final=True):
    return types.updates.ChannelDifference(
        pts=pts,
        new_messages=list(messages),
        other_updates=list(updates),
        chats=[entity("channel:42", now[0])],
        users=[types.User(id=7, first_name="Alice")],
        final=final,
    )


async def test_authors_use_supplied_typed_entities_and_never_infer_own_from_outgoing(
    tmp_path,
):
    app, now = rig(tmp_path)
    values = [
        native("chat:42", 1, now[0]),
        native("chat:42", 2, now[0], sender=None, outgoing=True),
        native(
            "channel:42", 3, now[0], sender=None, outgoing=True, post_author="Moderator"
        ),
    ]
    values[2].from_id = types.PeerChannel(42)
    supplied = group_chats.entity_map(
        [types.User(id=7, first_name="Alice"), entity("channel:42", now[0])]
    )
    normalized = await app.transport.normalize_page(values, entities=supplied)
    assert [
        (message.author_peer, message.author, message.author_signature)
        for message in normalized
    ] == [
        ("user:7", "Alice", None),
        (None, "Unknown participant", None),
        ("channel:42", "Authored group", "Moderator"),
    ]
    assert app.transport.client.calls == app.transport.client.lookups == []


async def test_raw_group_media_and_forward_attribution_use_supplied_evidence_without_fetch(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    media = [
        types.MessageMediaPhoto(
            photo=types.Photo(
                id=80,
                access_hash=100,
                file_reference=b"authored",
                date=datetime.fromtimestamp(now[0], UTC),
                sizes=[],
                dc_id=1,
            )
        ),
        document("audio/ogg", types.DocumentAttributeAudio(duration=3, voice=True)),
        document("video/mp4", video(round_message=True)),
        document("video/mp4", types.DocumentAttributeAnimated(), video()),
        document("image/webp", sticker_attribute()),
    ]
    values = [
        native("channel:42", index, now[0], media=value)
        for index, value in enumerate(media, 1)
    ]
    values[0].fwd_from = types.MessageFwdHeader(
        date=datetime.fromtimestamp(now[0] - 60, UTC), from_id=types.PeerUser(8)
    )
    supplied = group_chats.entity_map(
        [types.User(id=7, first_name="Alice"), types.User(id=8, first_name="Bob")]
    )
    normalized = await app.transport.normalize_page(values, entities=supplied)
    assert [message.media for message in normalized] == [
        "image",
        "audio",
        "video_note",
        "gif",
        "sticker",
    ]
    assert normalized[0].forwarded_from == "Bob"
    assert normalized[0].forward_date == now[0] - 60
    assert normalized[0].attachment_id == "photo:80"
    assert normalized[1].media_info.voice
    for message in normalized:
        app.sync.ingest(message, live=True)
        app.arrived(message)
    now[0] += 1800
    app.memory.schedule_all()
    await app.process_due()
    app.notifications.advance()
    assert app.transport.client.calls == app.transport.client.lookups == []
    no_private_work(app)
    # Opt-in now admits GB5's bounded media preparation. It still fetches only
    # current known AI sources, without group prefetch or private AI roles.
    known_non_ads(app)
    app.store.groups.set_processing_enabled(key, True)
    now[0] += 31
    await app.process_due()
    # The new bounded native-author stage precedes the existing media stage.
    prepared = app.briefings.next_job()
    assert prepared and json.loads(prepared["payload"])["stage"] == "prepare"
    await app.process_due()
    app.notifications.advance()
    calls = app.transport.client.calls
    assert calls and len(calls) <= 3
    assert all(
        isinstance(call, functions.channels.GetMessagesRequest) for call in calls
    )
    assert all(0 < len(call.id) <= 1 and call.id[0].id in {1, 2, 3} for call in calls)
    assert all(
        isinstance(peer, types.PeerChannel) and peer.channel_id == 42
        for peer in app.transport.client.lookups
    )
    from enloq.domain.briefing_contract import GROUP_ROLES

    assert all(
        row["role"] in GROUP_ROLES for row in app.store.rows("SELECT role FROM jobs")
    )
    assert not app.decider.calls
    for table in (
        "blocks",
        "memory_entries",
        "emotion_state",
        "suggestion_state",
        "reaction_events",
        "incoming_deferred",
        "incoming_notices",
    ):
        assert app.store.rows(f"SELECT * FROM {table}") == []


async def test_author_identity_signature_service_changes_are_material_but_name_refresh_is_metadata(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    original = source(app, now)
    changes = []
    app.group_changed = lambda message, **kwargs: changes.append(message.revision)
    assert app.sync.ingest(original)
    assert not app.sync.ingest(original.model_copy(update={"author": "Alice Updated"}))
    assert app.store.message(key, 1).revision == 1 and changes == [1]
    for field, value in [
        ("author_peer", "channel:42"),
        ("author_signature", "Moderator"),
        ("service", True),
    ]:
        current = app.store.message(key, 1)
        assert app.sync.ingest(current.model_copy(update={field: value}))
        assert app.store.message(key, 1).revision == current.revision + 1
    assert changes == [1, 2, 3, 4]
    assert len(app.store.rows("SELECT * FROM message_versions")) == 3
    no_private_work(app)


async def test_account_disconnection_never_changes_group_access(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now))
    before = app.store.groups.get(key)
    app.transport.client.online = False
    with pytest.raises(Unavailable, match="connected and verified"):
        await app.transport.channel_catch_up("channel:42")
    assert app.store.groups.get(key) == before
    assert app.transport.client.calls == []


async def test_global_replay_hydrates_basic_groups_and_channel_edits_deletions(
    tmp_path,
):
    app, now = rig(tmp_path)
    basic, channel = group(app, now, "chat:42"), group(app, now)
    app.sync.ingest(source(app, now, "channel:42", 2))
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('global_difference',?)",
            (json.dumps(dict(pts=4, qts=0, date=now[0])),),
        )

    async def respond(request):
        assert isinstance(request, functions.updates.GetDifferenceRequest)
        return types.updates.Difference(
            new_messages=[native("chat:42", 1, now[0])],
            new_encrypted_messages=[],
            other_updates=[
                types.UpdateEditChannelMessage(
                    native(
                        "channel:42",
                        2,
                        now[0],
                        text="Changed post",
                        edit_date=datetime.fromtimestamp(now[0] + 1, UTC),
                    ),
                    pts=5,
                    pts_count=1,
                ),
                types.UpdateDeleteChannelMessages(
                    channel_id=42, messages=[2], pts=6, pts_count=1
                ),
            ],
            chats=[entity("chat:42", now[0]), entity("channel:42", now[0])],
            users=[types.User(id=7, first_name="Alice")],
            state=types.updates.State(
                pts=8,
                qts=0,
                date=datetime.fromtimestamp(now[0], UTC),
                seq=0,
                unread_count=0,
            ),
        )

    app.transport.client.response = respond
    await app.transport.catch_up()
    assert app.store.message(basic, 1).author == "Alice"
    assert app.store.message(channel, 2).deleted
    assert (
        json.loads(
            app.store.rows("SELECT value FROM cursors WHERE key='global_difference'")[
                0
            ]["value"]
        )["pts"]
        == 8
    )
    assert app.store.groups.intake(channel).pts == 4
    no_private_work(app)


async def test_channel_checkpoint_waits_for_durable_source_effects_and_retries_marker(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    messages = [native("channel:42", index, now[0]) for index in (1, 2)]
    fail = [True]
    seen = []

    def changed(message, *, arrival):
        seen.append(message.message_id)
        if message.message_id == 2 and fail[0]:
            raise RuntimeError("Authored local interruption")

    app.group_changed = changed

    async def respond(request):
        return delta(app, now, messages=messages)

    app.transport.client.response = respond
    with pytest.raises(RuntimeError, match="Authored local interruption"):
        await app.transport.channel_catch_up("channel:42")
    assert app.store.groups.intake(key).pts == 4
    assert app.store.groups.intake(key).replay_pending
    assert app.store.rows(
        "SELECT value FROM cursors WHERE key='group-effects:1/channel:42/2'"
    )
    fail[0] = False
    await app.transport.channel_catch_up("channel:42")
    assert (
        app.store.groups.intake(key).pts == 6
        and not app.store.groups.intake(key).replay_pending
    )
    assert seen == [1, 2, 2]
    assert (
        app.store.rows("SELECT value FROM cursors WHERE key LIKE 'group-effects:%'")
        == []
    )
    assert app.store.groups.current(app.store.message(key, 2))


@pytest.mark.parametrize("empty", [True, False])
async def test_nonfinal_channel_slices_continue_at_most_five_and_preserve_pending(
    tmp_path, empty
):
    app, now = rig(tmp_path)
    key = group(app, now)
    original = source(app, now)
    app.sync.ingest(original)
    epoch = app.store.groups.intake(key).epoch

    async def respond(request):
        if empty:
            return types.updates.ChannelDifferenceEmpty(
                pts=request.pts + 1, final=False
            )
        return delta(app, now, pts=request.pts + 1, final=False)

    app.transport.client.response = respond
    with pytest.raises(Unavailable, match="bounded continuation"):
        await app.transport.channel_catch_up("channel:42")
    assert len(app.transport.client.calls) == 5
    assert [request.pts for request in app.transport.client.calls] == [4, 5, 6, 7, 8]
    intake = app.store.groups.intake(key)
    assert intake.pts == 9 and intake.epoch == epoch and intake.replay_pending
    assert not intake.complete and not app.store.groups.current(original)
    assert app.store.groups.current(original, admission=False)

    async def finished(request):
        return types.updates.ChannelDifferenceEmpty(pts=9, final=True)

    app.transport.client.response = finished
    await app.transport.channel_catch_up("channel:42")
    assert not app.store.groups.intake(key).replay_pending
    assert app.store.groups.current(original)


async def test_channel_too_long_uses_dialog_pts_and_preserves_explicit_incomplete_window(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    original = source(app, now)
    app.sync.ingest(original)

    async def respond(request):
        return types.updates.ChannelDifferenceTooLong(
            dialog=dialog(entity("channel:42", now[0]), now[0], top=10, pts=17).dialog,
            messages=[native("channel:42", 10, now[0])],
            chats=[entity("channel:42", now[0])],
            users=[],
            final=True,
        )

    app.transport.client.response = respond
    await app.transport.channel_catch_up("channel:42")
    intake = app.store.groups.intake(key)
    assert intake.pts == 17 and intake.gap and not intake.complete
    assert intake.before == 0 and intake.count == 0
    assert not app.store.groups.current(original)
    assert app.store.message(key, 10).text == "Authored post"
    assert len(app.transport.client.calls) == 1


@pytest.mark.parametrize("bad", ["message", "deletion", "pts"])
async def test_channel_response_identity_and_checkpoint_refusals_do_not_advance(
    tmp_path, bad
):
    app, now = rig(tmp_path)
    key = group(app, now)

    async def respond(request):
        if bad == "message":
            return delta(app, now, messages=[native("channel:43", 1, now[0])])
        if bad == "deletion":
            return delta(
                app,
                now,
                updates=[
                    types.UpdateDeleteChannelMessages(
                        channel_id=43, messages=[1], pts=6, pts_count=1
                    )
                ],
            )
        return types.updates.ChannelDifferenceEmpty(pts=3, final=True)

    app.transport.client.response = respond
    with pytest.raises(Refused):
        await app.transport.channel_catch_up("channel:42")
    assert (
        app.store.groups.intake(key).pts == 4
        and app.store.groups.intake(key).replay_pending
    )
    assert app.store.rows("SELECT * FROM messages") == []


@pytest.mark.parametrize("read", ["history", "exact", "difference"])
@pytest.mark.parametrize("race", ["lookup", "wire"])
async def test_access_loss_between_lookup_pacing_and_wire_starts_no_content_rpc(
    tmp_path, read, race
):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now))

    async def revoke(_):
        await app.transport._group_metadata(
            types.UpdateChannelParticipant(
                channel_id=42,
                date=datetime.fromtimestamp(now[0], UTC),
                actor_id=9,
                user_id=1,
                qts=1,
                new_participant=types.ChannelParticipantLeft(types.PeerUser(1)),
            )
        )

    if race == "lookup":
        app.transport.client.lookup_hook = revoke
    else:
        app.transport.client.before_wire = revoke
    with pytest.raises(Refused):
        if read == "history":
            await app.transport.history("channel:42", 0, 100)
        elif read == "exact":
            await app.transport.exact("channel:42", 1)
        else:
            await app.transport.channel_catch_up("channel:42")
    assert app.transport.client.calls == []
    assert app.store.groups.get(key).access == "lost"
    assert app.store.message(key, 1).text == "Authored source"


@pytest.mark.parametrize("known", [True, False])
async def test_discovery_started_before_membership_loss_cannot_restore_access(
    tmp_path, known
):
    app, now = rig(tmp_path)
    key = group(app, now, "chat:42") if known else "1/chat:42"
    older = dialog(entity("chat:42", now[0]), now[0])

    async def stale(**kwargs):
        await app.transport._group_metadata(
            types.UpdateChatParticipantDelete(chat_id=42, user_id=1, version=2)
        )
        yield older

    app.transport.client.iter_dialogs = stale
    await app.transport.discover()
    assert app.store.groups.get(key).access == "lost"
    assert not app.store.recent_chats("group") and not app.store.sync_targets()
    assert app.store.messages(key) == []


async def test_global_difference_started_before_first_membership_loss_cannot_restore_access(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = "1/chat:42"
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('global_difference',?)",
            (json.dumps(dict(pts=4, qts=0, date=now[0])),),
        )

    async def stale(request):
        await app.transport._group_metadata(
            types.UpdateChatParticipantDelete(chat_id=42, user_id=1, version=2)
        )
        return types.updates.Difference(
            new_messages=[native("chat:42", 1, now[0])],
            new_encrypted_messages=[],
            other_updates=[],
            chats=[entity("chat:42", now[0])],
            users=[],
            state=types.updates.State(
                pts=8,
                qts=0,
                date=datetime.fromtimestamp(now[0], UTC),
                seq=0,
                unread_count=0,
            ),
        )

    app.transport.client.response = stale
    await app.transport.catch_up()
    assert app.store.groups.get(key).access == "lost"
    assert not app.store.messages(key) and not app.store.sync_targets()
    no_private_work(app)


@pytest.mark.parametrize("wrapper", ["new", "edit", "wrapped-new"])
@pytest.mark.parametrize(
    "peer,flag", [("chat:42", "left"), ("channel:42", "noforwards")]
)
async def test_stale_negative_global_metadata_and_sources_cannot_revoke_newer_restoration(
    tmp_path, wrapper, peer, flag
):
    app, now = rig(tmp_path)
    key = group(app, now, peer)
    app.sync.ingest(source(app, now, peer))
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('global_difference',?)",
            (json.dumps(dict(pts=4, qts=0, date=now[0])),),
        )

    async def stale(request):
        app.store.groups.observe(
            key, access="lost", supported=True, reason="membership_removed"
        )
        app.transport._discovered(dialog(entity(peer, now[0]), now[0]))
        post = native(peer, 1 if wrapper == "edit" else 2, now[0], text="Stale content")
        values = [post] if wrapper == "new" else []
        updates = []
        if wrapper != "new":
            update_type = (
                (
                    types.UpdateEditChannelMessage
                    if peer.startswith("channel:")
                    else types.UpdateEditMessage
                )
                if wrapper == "edit"
                else (
                    types.UpdateNewChannelMessage
                    if peer.startswith("channel:")
                    else types.UpdateNewMessage
                )
            )
            updates = [update_type(post, pts=7, pts_count=1)]
        return types.updates.Difference(
            new_messages=values,
            new_encrypted_messages=[],
            other_updates=updates,
            chats=[entity(peer, now[0], **{flag: True})],
            users=[],
            state=types.updates.State(
                pts=8,
                qts=0,
                date=datetime.fromtimestamp(now[0], UTC),
                seq=0,
                unread_count=0,
            ),
        )

    app.transport.client.response = stale
    await app.transport.catch_up()
    state = app.store.groups.get(key)
    assert (
        state.access == "available" and state.supported and state.access_revision == 3
    )
    assert (
        app.store.message(key, 1).text == "Authored source"
        and app.store.message(key, 2) is None
    )
    assert not app.store.groups.current(app.store.message(key, 1))
    assert (
        json.loads(
            app.store.rows("SELECT value FROM cursors WHERE key='global_difference'")[
                0
            ]["value"]
        )["pts"]
        == 8
    )
    no_private_work(app)


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_first_fresh_global_group_metadata_permits_same_response_source_intake(
    tmp_path, peer
):
    app, now = rig(tmp_path)
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('global_difference',?)",
            (json.dumps(dict(pts=4, qts=0, date=now[0])),),
        )

    async def fresh(request):
        return types.updates.Difference(
            new_messages=[native(peer, 1, now[0])],
            new_encrypted_messages=[],
            other_updates=[],
            chats=[entity(peer, now[0])],
            users=[types.User(id=7, first_name="Alice")],
            state=types.updates.State(
                pts=8,
                qts=0,
                date=datetime.fromtimestamp(now[0], UTC),
                seq=0,
                unread_count=0,
            ),
        )

    app.transport.client.response = fresh
    await app.transport.catch_up()
    key = f"1/{peer}"
    assert app.store.groups.current(app.store.message(key, 1))
    assert app.store.message(key, 1).author == "Alice"
    assert not app.store.groups.get(key).processing_enabled
    no_private_work(app)


@pytest.mark.parametrize("edit", [False, True])
async def test_exact_refresh_during_global_replay_cannot_bless_earlier_entity_snapshot(
    tmp_path, edit
):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now))
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('global_difference',?)",
            (json.dumps(dict(pts=4, qts=0, date=now[0])),),
        )

    async def respond(request):
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return SimpleNamespace(
                chats=[entity("channel:42", now[0])], full_chat=SimpleNamespace(pts=4)
            )
        post = native(
            "channel:42", 1 if edit else 2, now[0], text="Earlier response snapshot"
        )
        update_type = (
            types.UpdateEditChannelMessage if edit else types.UpdateNewChannelMessage
        )
        return types.updates.Difference(
            new_messages=[],
            new_encrypted_messages=[],
            other_updates=[
                types.UpdateChannel(42),
                update_type(post, pts=7, pts_count=1),
            ],
            chats=[entity("channel:42", now[0], left=True)],
            users=[],
            state=types.updates.State(
                pts=8,
                qts=0,
                date=datetime.fromtimestamp(now[0], UTC),
                seq=0,
                unread_count=0,
            ),
        )

    app.transport.client.response = respond
    await app.transport.catch_up()
    state = app.store.groups.get(key)
    assert state.access == "available" and state.access_revision == 3
    assert (
        app.store.message(key, 1).text == "Authored source"
        and app.store.message(key, 2) is None
    )
    assert not app.store.groups.current(app.store.message(key, 1))
    assert [type(request) for request in app.transport.client.calls] == [
        functions.updates.GetDifferenceRequest,
        functions.channels.GetFullChannelRequest,
    ]
    assert (
        json.loads(
            app.store.rows("SELECT value FROM cursors WHERE key='global_difference'")[
                0
            ]["value"]
        )["pts"]
        == 8
    )
    no_private_work(app)


@pytest.mark.parametrize("evidence", ["membership", "protected", "forum"])
async def test_fresh_negative_arrivals_survive_wall_clock_rollback(tmp_path, evidence):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now))
    app.store.groups.set_processing_enabled(key, True)
    app.store.mutate_draft(key, 0, lambda draft: draft.update(text="Kept draft"))
    before = app.store.groups.get(key)
    now[0] -= 1
    if evidence == "membership":
        await app.transport._group_metadata(
            types.UpdateChannelParticipant(
                channel_id=42,
                date=datetime.fromtimestamp(now[0], UTC),
                actor_id=9,
                user_id=1,
                qts=1,
                new_participant=types.ChannelParticipantLeft(types.PeerUser(1)),
            )
        )
    else:
        app.transport.admit_peer(
            "channel:42",
            entity(
                "channel:42",
                now[0],
                min=True,
                **{"noforwards" if evidence == "protected" else "forum": True},
            ),
        )
    state = app.store.groups.get(key)
    assert state.access == "lost" if evidence == "membership" else not state.supported
    assert state.access_revision == before.access_revision + 1
    assert state.observed_at == before.observed_at and state.processing_enabled
    assert not app.store.groups.current(app.store.message(key, 1))
    assert app.store.message(key, 1).text == "Authored source"
    assert app.store.draft(key)["text"] == "Kept draft"


@pytest.mark.parametrize("read", ["history", "exact", "difference", "metadata"])
@pytest.mark.parametrize("obsolete", [False, True])
async def test_confirmed_read_loss_survives_rollback_but_obsolete_response_cannot_revoke_restoration(
    tmp_path, read, obsolete
):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now))
    app.store.groups.set_processing_enabled(key, True)
    intake = app.store.groups.intake(key)
    app.store.groups.checkpoint(key, 10, epoch=intake.epoch)
    before = app.store.groups.get(key)
    now[0] -= 1

    async def refused(request):
        if obsolete:
            app.store.groups.observe(
                key, access="lost", supported=True, reason="membership_removed"
            )
            app.transport._discovered(dialog(entity("channel:42", now[0]), now[0]))
        raise errors.ChannelPrivateError(request)

    app.transport.client.response = refused
    with pytest.raises(Refused, match="read access was revoked"):
        if read == "history":
            await app.transport.history("channel:42", 0, 100)
        elif read == "exact":
            await app.transport.exact("channel:42", 1)
        elif read == "difference":
            await app.transport.channel_catch_up("channel:42")
        else:
            await app.transport.refresh_group("channel:42")
    state = app.store.groups.get(key)
    assert state.access == ("available" if obsolete else "lost")
    assert state.access_revision == before.access_revision + (2 if obsolete else 1)
    assert state.observed_at == before.observed_at and state.processing_enabled
    assert app.store.groups.intake(key).pts == 10
    assert app.store.message(key, 1).text == "Authored source"


async def test_too_long_checkpoint_waits_for_saved_effects_and_can_resume(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    fail = [True]

    def changed(message, *, arrival):
        if fail[0]:
            raise RuntimeError("Authored local interruption")

    app.group_changed = changed

    async def respond(request):
        return types.updates.ChannelDifferenceTooLong(
            dialog=dialog(entity("channel:42", now[0]), now[0], pts=17).dialog,
            messages=[native("channel:42", 10, now[0])],
            chats=[entity("channel:42", now[0])],
            users=[],
            final=True,
        )

    app.transport.client.response = respond
    with pytest.raises(RuntimeError, match="local interruption"):
        await app.transport.channel_catch_up("channel:42")
    intake = app.store.groups.intake(key)
    assert intake.pts == 4 and intake.gap and intake.replay_pending
    assert not app.store.groups.current(app.store.message(key, 10))
    assert app.store.rows(
        "SELECT 1 FROM cursors WHERE key='group-effects:1/channel:42/10'"
    )
    fail[0] = False
    await app.transport.channel_catch_up("channel:42")
    intake = app.store.groups.intake(key)
    assert intake.pts == 17 and intake.gap and not intake.replay_pending
    assert not app.store.rows("SELECT 1 FROM cursors WHERE key LIKE 'group-effects:%'")


async def test_stale_recent_read_cannot_publish_after_gap_or_access_change(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)

    async def raced(request):
        app.store.groups.mark_gap(key)
        return types.messages.Messages(
            topics=[], messages=[native("channel:42", 1, now[0])], chats=[], users=[]
        )

    app.transport.client.response = raced
    with pytest.raises(Refused, match="changed during read"):
        await app.sync.scan(key)
    assert app.store.messages(key) == [] and not app.store.groups.intake(key).complete


@pytest.mark.parametrize(
    "failure,lost",
    [
        (errors.ChannelPrivateError, True),
        (errors.UserBannedInChannelError, False),
        (errors.ChannelInvalidError, False),
        (TimeoutError, False),
    ],
)
async def test_only_confirmed_native_read_failure_loses_access(tmp_path, failure, lost):
    app, now = rig(tmp_path)
    key = group(app, now)

    async def refused(request):
        raise failure(request) if failure is not TimeoutError else TimeoutError()

    app.transport.client.response = refused
    with pytest.raises((Refused, errors.RPCError, TimeoutError)):
        await app.transport.history("channel:42", 0, 100)
    assert (app.store.groups.get(key).access == "lost") is lost
    assert app.store.groups.get(key).processing_enabled is False


async def test_restored_same_peer_requires_fresh_sources_and_preserves_policy_draft(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now))
    app.store.groups.set_processing_enabled(key, True)
    app.store.mutate_draft(key, 0, lambda draft: draft.update(text="Retained draft"))
    app.transport._observe_group(
        types.ChannelForbidden(
            id=42, access_hash=100, title="Authored group", megagroup=True
        )
    )
    assert not app.store.groups.current(app.store.message(key, 1))
    app.transport._discovered(dialog(entity("channel:42", now[0]), now[0]))
    assert (
        app.store.groups.get(key).access == "available"
        and app.store.groups.get(key).processing_enabled
    )
    assert app.store.draft(key)["text"] == "Retained draft"
    assert not app.store.groups.current(app.store.message(key, 1))
    app.transport.client.messages = [native("channel:42", 1, now[0])]
    refreshed = await app.transport.exact("channel:42", 1)
    app.sync.ingest(refreshed, target=False)
    assert app.store.groups.current(app.store.message(key, 1))
    now[0] += RETENTION + 1
    with pytest.raises(Refused, match="retained cached"):
        await app.transport.exact("channel:42", 1)


@pytest.mark.parametrize("forum", [False, True])
async def test_explicit_forum_reply_evidence_refuses_but_ordinary_top_reply_is_supported(
    tmp_path, forum
):
    app, now = rig(tmp_path)
    key = group(app, now)
    post = native(
        "channel:42",
        1,
        now[0],
        reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, reply_to_top_id=2, forum_topic=forum
        ),
    )
    await app.transport._incoming(
        SimpleNamespace(message=post, chat=entity("channel:42", now[0]))
    )
    assert (app.store.message(key, 1) is None) is forum
    assert app.store.groups.get(key).supported is (not forum)
    no_private_work(app)


async def test_topic_creation_service_is_exclusion_evidence_not_group_content(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    service = types.MessageService(
        id=1,
        peer_id=types.PeerChannel(42),
        date=datetime.fromtimestamp(now[0], UTC),
        action=types.MessageActionTopicCreate(title="Authored topic", icon_color=0),
    )
    await app.transport._group_service(
        types.UpdateNewChannelMessage(service, pts=5, pts_count=1)
    )
    assert not app.store.groups.get(key).supported and not app.store.messages(key)


async def test_runtime_syncs_native_groups_without_reactions_or_private_work(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.transport.client.messages = [
        native("channel:42", index, now[0]) for index in (1, 2)
    ]

    async def discover():
        pass

    async def catch_up():
        pass

    app.transport.discover, app.transport.catch_up = discover, catch_up
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES (?,?)",
            ("recent:" + key, json.dumps(dict(before=0, complete=False))),
        )
        db.execute(
            "INSERT INTO cursors VALUES (?,?)",
            ("unread_reactions:" + key, json.dumps(dict(count=1, read=0))),
        )
    assert await sync_cycle(app)
    assert [type(request) for request in app.transport.client.calls] == [
        functions.updates.GetChannelDifferenceRequest,
        functions.messages.GetHistoryRequest,
    ]
    assert len(app.store.messages(key)) == 2
    no_private_work(app)


def test_group_change_recovery_after_restart_runs_only_saved_local_hook(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.group_changed = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("Authored interruption")
    )
    with pytest.raises(RuntimeError):
        app.sync.ingest(source(app, now))
    reopened = Store(app.store.path, account_id=1, clock=lambda: now[0])
    seen = []
    app.store = reopened
    app.sync.store = reopened
    app.group_changed = lambda message, **kwargs: seen.append(message.key)
    app.sync.recover_deletions()
    assert seen == [key + "/1"] and app.transport.client.calls == []
    assert not reopened.rows("SELECT * FROM cursors WHERE key LIKE 'group-effects:%'")
