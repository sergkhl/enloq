"""Authored own-user group deletion and bounded, truthful recovery boundaries."""

import asyncio
import json
import uuid

import pytest
from enloq.domain.deletion import (
    DeleteDescription,
    DeleteReceipt,
    DeletionRead,
    deletion_fingerprint,
)
from enloq.domain.models import (
    Chat,
    Conflict,
    MediaInfo,
    Message,
    Reaction,
    Refused,
)
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import RETENTION

from backend.tests.test_rebuild import fixture, message


def group(tmp_path, peer="chat:42", *, count=1, author_peer="user:1", outgoing=False):
    app = fixture(tmp_path)
    app.store.put_chat(
        Chat(account_id=1, peer=peer, title="Authored group", kind="group")
    )
    key = f"1/{peer}"
    app.store.groups.observe(key, access="available", reason="authored_native")
    sources = [
        Message(
            account_id=1,
            peer=peer,
            message_id=i,
            revision=1,
            date=app.store.clock(),
            text=f"Authored original {i}",
            author_peer=author_peer,
            outgoing=outgoing,
        )
        for i in range(1, count + 1)
    ]
    for source in sources:
        app.sync.ingest(source)
    app.transport.messages = sources
    return app, key


def targets(app, key, identities=None):
    if identities is None:
        identities = [m.message_id for m in app.store.messages(key, limit=1000)]
    return [
        {"message_id": i, "revision": app.store.message(key, i).revision}
        | (
            {"source_fingerprint": deletion_fingerprint(app.store.message(key, i))}
            if app.store.chat(key).kind == "group"
            else {}
        )
        for i in identities
    ]


async def submit(app, key, reviewed=None, **kwargs):
    return await app.deletion.submit(
        key,
        uuid.uuid4().hex,
        reviewed or targets(app, key),
        browser="authored-browser",
        **kwargs,
    )


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_verified_own_author_is_eligible_even_without_outgoing_or_ai(
    tmp_path, peer
):
    app, key = group(tmp_path, peer)
    app.account_processing_enabled = lambda: False
    AccountRegistry(tmp_path).set_processing_threshold(10000)
    original = app.store.message(key, 1)
    assert not original.outgoing and app.deletion.eligible(original)
    assert not app.store.groups.get(key).processing_enabled
    operation = await submit(app, key)
    reviewed = app.store.deletion_operations.get(operation["id"])
    assert reviewed["kind"] == "group"
    assert reviewed["targets"][0]["author_peer"] == "user:1"
    assert app.store.deletion_operations.reply_hold(key, 1) == "pending"
    completed = await app.deletion.dispatch(operation["id"])
    description = app.transport.deletion_calls[0]
    assert description.peer == peer and description.sources == (original,)
    assert description.access_revision == app.store.groups.get(key).access_revision
    assert completed["counts"]["acknowledged"] == 1
    assert completed["counts"]["absent"] == 0
    assert completed["targets"][0]["observation"] is None
    assert app.store.message(key, 1).deleted
    assert not app.engine.calls and not app.transport.reads


@pytest.mark.parametrize("author_peer", [None, "", "channel:42", "user:7", "chat:42"])
async def test_outgoing_does_not_prove_own_user_authorship(tmp_path, author_peer):
    app, key = group(tmp_path, author_peer=author_peer, outgoing=True)
    source = app.store.message(key, 1)
    assert not app.deletion.eligible(source)
    with pytest.raises(Refused, match="verified own-user"):
        await submit(app, key)
    assert not app.store.rows("SELECT 1 FROM deletion_operations")
    assert not app.transport.deletion_calls


@pytest.mark.parametrize("change", ["lost", "unsupported", "migration", "unproved"])
async def test_group_access_or_native_source_proof_is_required(tmp_path, change):
    app, key = group(tmp_path)
    if change == "unproved":
        with app.store.transaction() as db:
            db.execute("DELETE FROM group_sources")
    else:
        app.store.groups.observe(
            key,
            access="lost" if change == "lost" else "available",
            supported=change != "unsupported",
            reason="authored_change",
            migrated_to="channel:99" if change == "migration" else None,
        )
    assert not app.deletion.eligible(app.store.message(key, 1))
    with pytest.raises(Refused):
        await submit(app, key)
    assert not app.transport.deletion_calls


@pytest.mark.parametrize("change", ["author", "wording", "native_revision", "media"])
async def test_final_handoff_checks_full_native_review_not_preview(tmp_path, change):
    app, key = group(tmp_path)
    app.sync.ingest(app.store.message(key, 1).model_copy(update={"text": "a" * 500}))
    operation = await submit(app, key)
    actual = app.transport.delete

    async def changing(description, evidence):
        source = app.store.message(key, 1)
        values = {
            "author": {"author_peer": "channel:42"},
            "wording": {"text": "a" * 499 + "b"},
            "native_revision": {"native_revision": 100},
            "media": {
                "media": "image",
                "attachment_id": "new-native-image",
                "media_info": MediaInfo(size=5),
            },
        }[change]
        # A same-local-revision metadata change must not weaken the full review.
        changed = source.model_copy(update=values)
        with app.store.transaction() as db:
            db.execute(
                "UPDATE messages SET body=? WHERE key=?",
                (changed.model_dump_json(), changed.key),
            )
        return await actual(description, evidence)

    app.transport.delete = changing
    stopped = await app.deletion.dispatch(operation["id"])
    assert stopped["state"] == "failed" and not app.transport.deletion_calls
    assert not app.store.message(key, 1).deleted
    assert stopped["chunks"][0]["receipt"] is None


@pytest.mark.parametrize("change", ["loss", "loss_restore", "epoch", "author"])
async def test_later_chunks_stop_after_access_or_author_change_preserving_ack(
    tmp_path, change
):
    app, key = group(tmp_path, count=205)
    operation = await submit(app, key)
    actual = app.transport.delete

    async def first_only(description, evidence):
        receipt = await actual(description, evidence)
        if change in ("loss", "loss_restore"):
            app.store.groups.observe(key, access="lost", reason="authored_loss")
            if change == "loss_restore":
                app.store.groups.observe(
                    key, access="available", reason="authored_restoration"
                )
                for i in range(101, 206):
                    app.sync.ingest(app.store.message(key, i))
        elif change == "epoch":
            app.store.groups.mark_gap(key)
            for i in range(101, 206):
                app.sync.ingest(app.store.message(key, i))
        else:
            app.sync.ingest(
                app.store.message(key, 205).model_copy(update={"author_peer": "user:7"})
            )
        return receipt

    app.transport.delete = first_only
    stopped = await app.deletion.dispatch(operation["id"])
    assert stopped["state"] == "partial"
    assert [c["state"] for c in stopped["chunks"]] == [
        "acknowledged",
        "unstarted",
        "unstarted",
    ]
    assert stopped["counts"]["acknowledged"] == 100
    assert len(app.transport.deletion_calls) == 1
    assert not app.store.message(key, 205).deleted
    assert app.store.deletion_operations.reply_hold(key, 1) == "deleted"
    assert app.store.deletion_operations.reply_hold(key, 205) == "unresolved"


async def test_native_rights_refusal_stops_after_acknowledged_first_chunk(tmp_path):
    app, key = group(tmp_path, "channel:42", count=205)
    operation = await submit(app, key)
    app.transport.deletion_answers = [
        DeleteReceipt(100, 100),
        Refused("Native own-user deletion refused"),
    ]
    stopped = await app.deletion.dispatch(operation["id"])
    assert [c["state"] for c in stopped["chunks"]] == [
        "acknowledged",
        "failed",
        "unstarted",
    ]
    assert (
        stopped["counts"]["acknowledged"] == 100 and stopped["counts"]["failed"] == 100
    )
    assert len(app.transport.deletion_calls) == 2
    assert not app.store.message(key, 201).deleted


async def test_lost_receipt_then_native_absence_never_manufactures_acknowledgement(
    tmp_path,
):
    app, key = group(tmp_path)
    operation = await submit(app, key)
    app.transport.lose_delete_receipt = True
    uncertain = await app.deletion.dispatch(operation["id"])
    assert uncertain["state"] == "uncertain" and not app.store.message(key, 1).deleted
    checked = await app.deletion.check(key, operation["id"])
    assert checked["state"] == "uncertain" and checked["counts"]["absent"] == 1
    assert (
        checked["counts"]["acknowledged"] == 0
        and checked["chunks"][0]["receipt"] is None
    )
    assert checked["retry_targets"] == [] and app.store.message(key, 1).deleted
    assert len(app.transport.deletion_calls) == 1


async def test_checked_retry_requires_current_own_author_and_renewed_review(tmp_path):
    app, key = group(tmp_path, count=3)
    operation = await submit(app, key)
    app.transport.deletion_failure = TimeoutError()
    await app.deletion.dispatch(operation["id"])
    with pytest.raises(Conflict):
        await submit(app, key, retry_of=operation["id"])
    checked = await app.deletion.check(key, operation["id"])
    assert checked["retry_targets"] == targets(app, key)
    app.sync.ingest(
        app.store.message(key, 2).model_copy(update={"author_peer": "user:7"})
    )
    app.store.groups.withdraw_source(app.store.message(key, 3).key)
    assert app.deletion.get(key, operation["id"])["retry_targets"] == targets(
        app, key, [1]
    )
    with pytest.raises(Refused):
        await submit(app, key, targets(app, key, [2]), retry_of=operation["id"])
    app.transport.deletion_failure = None
    retried = await submit(app, key, targets(app, key, [1]), retry_of=operation["id"])
    completed = await app.deletion.dispatch(retried["id"])
    assert completed["counts"]["acknowledged"] == 1
    old = app.deletion.get(key, operation["id"])
    assert old["counts"]["acknowledged"] == 0 and old["counts"]["resolved"] == 1
    assert old["state"] == "uncertain" and len(app.transport.deletion_calls) == 2


async def test_lost_access_refuses_check_without_native_read_and_restoration_needs_new_review(
    tmp_path,
):
    app, key = group(tmp_path)
    operation = await submit(app, key)
    app.transport.deletion_failure = Refused("authored native refusal")
    await app.deletion.dispatch(operation["id"])
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    with pytest.raises(Refused, match="access"):
        await app.deletion.check(key, operation["id"])
    assert not app.transport.deletion_reads
    app.store.groups.observe(key, access="available", reason="authored_restoration")
    checked = await app.deletion.check(key, operation["id"])
    assert checked["retry_targets"] == targets(app, key)
    assert len(app.transport.deletion_calls) == 1
    new = await submit(app, key, checked["retry_targets"], retry_of=operation["id"])
    assert new["id"] != operation["id"]
    assert (
        app.store.deletion_operations.get(new["id"])["access_revision"]
        > app.store.deletion_operations.get(operation["id"])["access_revision"]
    )


async def test_check_filters_expired_or_missing_group_sources_without_backfill(
    tmp_path,
):
    app, key = group(tmp_path, count=3)
    operation = await submit(app, key)
    app.transport.deletion_failure = Refused("authored native refusal")
    await app.deletion.dispatch(operation["id"])
    with app.store.transaction() as db:
        db.execute("DELETE FROM messages WHERE chat_key=? AND message_id=2", (key,))
    source = app.store.message(key, 3).model_copy(
        update={"date": app.store.clock() - RETENTION - 1}
    )
    with app.store.transaction() as db:
        db.execute(
            "UPDATE messages SET body=? WHERE key=?",
            (source.model_dump_json(), source.key),
        )
    checked = await app.deletion.check(key, operation["id"])
    assert app.transport.deletion_reads == [("chat:42", (1,))]
    assert {t["message_id"]: t["observation"] for t in checked["targets"]} == {
        1: "present",
        2: "unavailable",
        3: "unavailable",
    }
    assert app.store.message(key, 2) is None and not app.store.message(key, 3).deleted


async def test_access_change_during_read_cannot_publish_absence_or_retry_proof(
    tmp_path,
):
    app, key = group(tmp_path)
    operation = await submit(app, key)
    app.transport.deletion_failure = Refused("authored native refusal")
    await app.deletion.dispatch(operation["id"])

    async def stale_absence(peer, ids):
        app.store.groups.observe(key, access="lost", reason="authored_loss")
        app.store.groups.observe(key, access="available", reason="authored_restoration")
        return DeletionRead(1, peer, tuple(ids), absent=tuple(ids))

    app.transport.inspect_deletion = stale_absence
    checked = await app.deletion.check(key, operation["id"])
    assert checked["targets"][0]["observation"] == "unavailable"
    assert checked["retry_targets"] == [] and not app.store.message(key, 1).deleted


async def test_group_private_peer_collision_and_restart_never_retarged_or_replay(
    tmp_path,
):
    app, key = group(tmp_path)
    private = message(app, 1)
    app.sync.ingest(private)
    app.transport.messages.append(private)
    operation = await submit(app, key)
    with pytest.raises(Refused, match="another account or conversation"):
        app.deletion.get("1/user:42", operation["id"])
    assert app.store.deletion_operations.reply_hold("1/user:42", 1) is None
    app.store.recover()
    recovered = app.deletion.get(key, operation["id"])
    assert (
        recovered["state"] == "failed" and not app.store.deletion_operations.waiting()
    )
    await app.deletion.check(key, operation["id"])
    assert (
        not app.transport.deletion_calls
        and not app.store.message("1/user:42", 1).deleted
    )


def test_group_descriptor_requires_exact_native_own_originals_and_copied_proof(
    tmp_path,
):
    app, key = group(tmp_path)
    source = app.store.message(key, 1)
    with pytest.raises(Refused):
        DeleteDescription("authored", 1, "chat:42", (1,))
    with pytest.raises(Refused):
        DeleteDescription(
            "authored",
            1,
            "chat:42",
            (1,),
            0,
            (source.model_copy(update={"author_peer": None}),),
        )
    proof = DeleteDescription("authored", 1, "chat:42", (1,), 0, (source,))
    source.text = "Changed caller value"
    assert proof.sources[0].text == "Authored original 1"
    assert (
        json.loads(
            app.store.rows("SELECT body FROM messages WHERE chat_key=?", (key,))[0][
                "body"
            ]
        )["text"]
        == proof.sources[0].text
    )


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
@pytest.mark.parametrize("reactions", [[], [Reaction(emoji="👍")]])
async def test_direct_group_reaction_writes_and_removals_are_refused_before_mutation(
    tmp_path, peer, reactions
):
    app, _ = group(tmp_path, peer)
    with pytest.raises(Refused, match="display-only"):
        await app.transport.send_reaction(peer, 1, reactions)
    assert app.transport.reaction_calls == [] and app.transport.reaction_state == {}


async def test_one_lost_group_reconciliation_does_not_hold_private_deletion_queue(
    tmp_path,
):
    app, key = group(tmp_path)
    operation = await submit(app, key)
    app.transport.deletion_failure = TimeoutError()
    await app.deletion.dispatch(operation["id"])
    # Restart can only reconcile this unknown outcome; it cannot repeat it.
    with app.store.transaction() as db:
        pending = app.store.deletion_operations._read(db, operation["id"])
        pending["needs_reconcile"] = True
        app.store.deletion_operations._save(db, pending)
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    private = message(app, 2)
    app.sync.ingest(private)
    app.transport.messages.append(private)
    other = await submit(
        app, "1/user:42", [{"message_id": 2, "revision": private.revision}]
    )
    app.transport.deletion_failure = None
    worker = asyncio.create_task(app.deletion.run())
    try:
        async with asyncio.timeout(5):
            while app.deletion.get("1/user:42", other["id"])["state"] != "completed":
                await asyncio.sleep(0.01)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    assert app.store.message("1/user:42", 2).deleted
    assert not app.store.message(key, 1).deleted
    assert [call.peer for call in app.transport.deletion_calls] == [
        "chat:42",
        "user:42",
    ]
    assert app.transport.deletion_reads == []


async def test_same_revision_media_refresh_requires_full_source_review_again(tmp_path):
    app, key = group(tmp_path)
    app.sync.ingest(
        app.store.message(key, 1).model_copy(
            update={
                "revision": 2,
                "native_revision": 2,
                "media": "document",
                "attachment_id": "native-document",
                "media_info": MediaInfo(
                    file_name="first.txt", size=20, mime="text/plain"
                ),
            }
        )
    )
    seen = app.store.message(key, 1)
    reviewed = targets(app, key)
    app.sync.ingest(
        seen.model_copy(
            update={
                "media_info": MediaInfo(
                    file_name="current.txt", size=30, mime="text/plain"
                ),
            }
        )
    )
    current = app.store.message(key, 1)
    assert (
        current.revision == seen.revision
        and current.native_revision == seen.native_revision
    )
    assert not app.deletion.eligible(seen) and app.deletion.eligible(current)
    assert (
        targets(app, key)[0]["source_fingerprint"] != reviewed[0]["source_fingerprint"]
    )
    with pytest.raises(Conflict, match="wording and media"):
        await submit(app, key, reviewed)
    assert (
        not app.store.rows("SELECT 1 FROM deletion_operations")
        and not app.transport.deletion_calls
    )
    accepted = await submit(app, key, targets(app, key))
    captured = app.store.deletion_operations.get(accepted["id"])
    assert captured["reviewed_targets"] == targets(app, key)
    assert captured["targets"][0]["source_fingerprint"] == deletion_fingerprint(current)


@pytest.mark.parametrize("fingerprint", [None, "", "A" * 64, "a" * 63, 7])
async def test_group_submission_requires_canonical_full_source_review_proof(
    tmp_path, fingerprint
):
    app, key = group(tmp_path)
    reviewed = [{"message_id": 1, "revision": 1}]
    if fingerprint is not None:
        reviewed[0]["source_fingerprint"] = fingerprint
    with pytest.raises(Refused):
        await submit(app, key, reviewed)
    assert (
        not app.store.rows("SELECT 1 FROM deletion_operations")
        and not app.transport.deletion_calls
    )


async def test_repeated_group_submission_preserves_exact_review_proof_after_dispatch(
    tmp_path,
):
    app, key = group(tmp_path)
    reviewed = targets(app, key)
    identity = uuid.uuid4().hex
    first = await app.deletion.submit(
        key, identity, reviewed, browser="authored-browser"
    )
    completed = await app.deletion.dispatch(first["id"])
    app.transport.connected = False
    repeated = await app.deletion.submit(
        key, identity, reviewed, browser="authored-browser"
    )
    assert repeated == completed and len(app.transport.deletion_calls) == 1
    different = [dict(reviewed[0], source_fingerprint="0" * 64)]
    with pytest.raises(Conflict, match="different reviewed intent"):
        await app.deletion.submit(key, identity, different, browser="authored-browser")


async def test_private_hashless_intent_remains_compatible_and_optional_proof_is_immutable(
    tmp_path,
):
    app = fixture(tmp_path)
    source = message(app)
    app.sync.ingest(source)
    app.transport.messages = [source]
    identity = uuid.uuid4().hex
    reviewed = [
        {
            "message_id": 1,
            "revision": source.revision,
            "source_fingerprint": deletion_fingerprint(source),
        }
    ]
    accepted = await app.deletion.submit(
        "1/user:42", identity, reviewed, browser="authored-browser"
    )
    repeated = await app.deletion.submit(
        "1/user:42", identity, reviewed, browser="authored-browser"
    )
    assert repeated == accepted
    with pytest.raises(Conflict):
        await app.deletion.submit(
            "1/user:42",
            identity,
            [{"message_id": 1, "revision": source.revision}],
            browser="authored-browser",
        )
