"""Independent GB8 regressions use authored SDK objects and an isolated store."""

from datetime import UTC, datetime

import pytest
from enloq.domain.deletion import DeleteReceipt
from enloq.domain.models import (
    Conflict,
    MediaInfo,
    Reaction,
    ReactionActor,
    ReactionChip,
    ReactionSnapshot,
    Refused,
)
from enloq.transport.telethon import reaction_snapshot
from telethon import types

from backend.tests.test_group_intake import group, rig, source


def test_same_channel_reaction_is_aggregate_display_without_correspondent_work(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    message = source(app, now)
    app.sync.ingest(message)
    changes = []
    app.store.reactions.on_change.append(changes.append)
    before = app.store.message(key, 1).model_dump()
    calls = list(app.transport.client.calls)
    now[0] += 1
    reaction = types.ReactionEmoji("👍")
    native = types.MessageReactions(
        results=[types.ReactionCount(reaction, 1)],
        recent_reactions=[
            types.MessagePeerReaction(
                peer_id=types.PeerChannel(42),
                date=datetime.fromtimestamp(now[0], UTC),
                reaction=reaction,
            )
        ],
    )
    snapshot = reaction_snapshot(native, 1, "channel:42", now[0])
    assert app.store.reactions.apply(message.key, snapshot)
    view = app.store.reactions.view(message.key)
    assert len(view["chips"]) == 1 and view["chips"][0]["count"] == 1
    assert app.store.message(key, 1).model_dump() == before
    assert app.transport.client.calls == calls
    assert changes == []
    for table in (
        "reaction_events",
        "reaction_state",
        "emotion_state",
        "suggestion_state",
        "incoming_notices",
        "jobs",
        "reservations",
        "receipts",
    ):
        assert app.store.rows(f"SELECT * FROM {table}") == []


def test_group_reaction_storage_does_not_adopt_private_actor_semantics(tmp_path):
    app, now = rig(tmp_path)
    group(app, now)
    message = source(app, now)
    app.sync.ingest(message)
    changes = []
    app.store.reactions.on_change.append(changes.append)
    now[0] += 1
    reaction = Reaction(emoji="🤝")
    snapshot = ReactionSnapshot(
        chips=[ReactionChip(reaction=reaction, count=1)],
        actors=[ReactionActor(actor="peer", reaction=reaction, date=now[0])],
        complete=True,
        observed=now[0],
    )
    assert app.store.reactions.apply(message.key, snapshot)
    assert app.store.reactions.view(message.key)["chips"][0]["count"] == 1
    assert changes == []
    assert not app.store.rows("SELECT * FROM reaction_events")
    assert not app.store.rows("SELECT * FROM reaction_state")


async def test_known_group_ack_applies_cached_consequences_when_access_is_lost(
    tmp_path,
):
    from backend.tests.test_group_deletion import group as deletion_group
    from backend.tests.test_group_deletion import submit

    app, key = deletion_group(tmp_path, "channel:42")
    operation = await submit(app, key)
    identity = operation["id"]
    ledger = app.store.deletion_operations
    assert ledger.claim(identity)
    _, evidence = ledger.start_chunk(identity, 0, app.deletion._guard)
    evidence.calling()
    evidence.watching()
    evidence.handing_off()
    ledger.acknowledge(identity, 0, DeleteReceipt(7, 1))
    # The durable receipt survived, but a crash interrupted local consequences.
    assert not app.store.message(key, 1).deleted
    assert ledger.get(identity)["needs_reconcile"]
    app.store.groups.observe(key, access="lost", reason="authored_native_loss")
    try:
        await app.deletion.check(key, identity)
    except Refused:
        pass  # Access loss can refuse a fresh native read, never discard the ACK.
    current = ledger.get(identity)
    assert app.store.message(key, 1).deleted
    assert app.store.deletion_operations.reply_hold(key, 1) == "deleted"
    assert current["chunks"][0]["state"] == "acknowledged"
    assert current["chunks"][0]["receipt"] == {"pts": 7, "pts_count": 1}
    assert current["chunks"][0]["local_applied"] is True
    assert current["targets"][0]["observation"] is None
    assert app.deletion.get(key, identity)["counts"]["absent"] == 0
    assert not app.transport.deletion_calls
    assert not app.transport.deletion_reads


async def test_group_confirmation_rejects_changed_media_under_same_revision(
    tmp_path,
):
    from backend.tests.test_group_deletion import group as deletion_group
    from backend.tests.test_group_deletion import submit, targets

    app, key = deletion_group(tmp_path)
    original = app.store.message(key, 1).model_copy(
        update={
            "media": "document",
            "attachment_id": "authored-document",
            "media_info": MediaInfo(file_name="reviewed.txt", size=10),
        }
    )
    app.sync.ingest(original)
    reviewed = app.store.message(key, 1)
    reviewed_targets = targets(app, key)
    assert app.deletion.eligible(reviewed)
    refreshed = reviewed.model_copy(
        update={"media_info": MediaInfo(file_name="different.txt", size=20)}
    )
    app.sync.ingest(refreshed)
    current = app.store.message(key, 1)
    # Safe metadata refreshes retain the native content/AI revision, but a
    # destructive confirmation must still bind its exact reviewed media.
    assert current.revision == reviewed.revision
    assert app.store.groups.current(current, admission=False)
    assert not app.deletion.eligible(reviewed)
    with pytest.raises((Conflict, Refused)):
        await submit(app, key, reviewed_targets)
    assert not app.store.rows("SELECT * FROM deletion_operations")
    assert not app.transport.deletion_calls
