"""Deletion holds retain exact authored replies before native hand-off.

Authored stores and an in-memory sender exercise actual pacing/packer methods;
no SDK constructor, session, sockets, Telegram or provider work is involved.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application.delivery import operation_view
from enloq.domain.deletion import DeleteReceipt, DeletionRead
from enloq.domain.delivery import ReceiptBatch, ReplySendBlocked
from enloq.domain.models import Refused
from enloq.storage.store import RETENTION
from enloq.transport.mtproto import PacedTelegramClient
from enloq.transport.telethon import PersonalTransport
from telethon import errors, functions, types
from telethon.network.mtprotosender import MTProtoSender

from backend.tests.test_rebuild import attach, edit, fixture, message
from backend.tests.test_transport import Clock

KEY = "1/user:42"
LOST, CORRECTION = object(), object()


def source(app):
    app.sync.ingest(message(app, 1))
    return app.store.message(KEY, 1)


def pending_delete(app):
    original = app.store.message(KEY, 1)
    return app.store.deletion_operations.prepare(
        KEY,
        uuid.uuid4().hex,
        [{"message_id": original.message_id, "revision": original.revision}],
        browser="authored-browser",
    )


def settle_delete(app, deletion, *, uncertain=False, acknowledged=False):
    owner = app.store.deletion_operations
    assert owner.claim(deletion["id"])
    _, evidence = owner.start_chunk(deletion["id"], 0, lambda: None)
    evidence.calling()
    evidence.watching()
    if uncertain or acknowledged:
        evidence.handing_off()
    if acknowledged:
        owner.acknowledge(deletion["id"], 0, DeleteReceipt(1, 1))
        app.sync.deleted("user:42", [1])
        owner.applied(deletion["id"], 0)
    else:
        owner.settle(deletion["id"], 0, evidence)


def saved(app, identity):
    return app.store.send_operations.get(identity)


def send_rig(tmp_path, answers, *, on_wire=None, upload=None):
    clock = Clock()
    app = fixture(tmp_path, clock=lambda: clock.now)
    source(app)
    policy = clock.policy(tmp_path / "policy.sqlite3")
    paced = object.__new__(PacedTelegramClient)
    paced._loop, paced._no_updates = None, False
    paced._flood_waited_requests = {}
    paced._request_retries, paced._raise_last_call_error = 0, True
    paced._flood_sleep_threshold = 0
    paced.request_policy = policy
    paced.session = SimpleNamespace(process_entities=lambda result: None)
    paced.is_connected = lambda: True
    calls, executed, ledgers = [], [], []
    answers = list(answers)

    def append(state):
        request = state.request
        if isinstance(request, functions.help.GetConfigRequest):
            return
        calls.append(request)
        [row] = app.store.rows("SELECT body FROM operations WHERE state='dispatching'")
        ledgers.append([entry["outcome"] for entry in json.loads(row["body"])["wire"]])
        if on_wire:
            on_wire(app, request)

    class Sender:
        def __init__(self):
            self._send_queue = SimpleNamespace(append=append)

        def send(self, request, ordered=False):
            future = asyncio.get_running_loop().create_future()
            state = SimpleNamespace(request=request, future=future)
            self._send_queue.append(state)
            if future.done():
                return future
            if isinstance(request, functions.help.GetConfigRequest):
                future.set_result(SimpleNamespace(caption_length_max=1024))
                return future
            identities = [
                part.random_id
                for part in getattr(request, "multi_media", None) or [request]
            ]
            answer = answers.pop(0)
            if answer is CORRECTION:

                async def corrected():
                    handler = object.__new__(MTProtoSender)
                    handler._log = logging.getLogger("authored-reply-sender")
                    handler._state = SimpleNamespace(salt=0)
                    handler._pending_state = {123: state}
                    handler._send_queue = self._send_queue
                    await handler._handle_bad_server_salt(
                        SimpleNamespace(obj=types.BadServerSalt(123, 0, 48, 9))
                    )

                asyncio.create_task(corrected())
            elif answer is LOST:
                executed.extend(identities)
                future.set_exception(ConnectionError("Authored lost acknowledgement"))
            elif isinstance(answer, BaseException):
                future.set_exception(answer)
            else:
                executed.extend(identities)
                future.set_result(
                    types.Updates(
                        updates=[
                            types.UpdateMessageID(answer + index, identity)
                            for index, identity in enumerate(identities)
                        ],
                        users=[],
                        chats=[],
                        date=datetime.now(UTC),
                        seq=0,
                    )
                )
            return future

    sender = Sender()

    class Owner:
        _guarded_deletion = True
        request_policy = policy

        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            return types.InputPeerUser(peer.user_id, 1)

        async def upload_file(self, path, **kwargs):
            if upload:
                await upload(app)
            return types.InputFile(1, 1, "authored.png", "authored")

        async def __call__(self, request):
            return await paced._call(sender, request)

    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id = app.store, 1
    transport.client, transport.verified = Owner(), True
    transport.bind_receipts(app.store.send_operations.record_receipts)
    app.delivery.transport = transport
    return SimpleNamespace(
        app=app,
        clock=clock,
        policy=policy,
        transport=transport,
        calls=calls,
        executed=executed,
        ledgers=ledgers,
        answers=answers,
    )


@pytest.mark.parametrize("status", ["pending", "unresolved", "deleted"])
async def test_new_send_to_deletion_target_keeps_current_draft(tmp_path, status):
    app = fixture(tmp_path)
    source(app)
    deletion = pending_delete(app)
    if status != "pending":
        settle_delete(
            app,
            deletion,
            uncertain=status == "unresolved",
            acknowledged=status == "deleted",
        )
    attach(app)
    draft = edit(app, "Keep this authored reply", reply_to=1)
    with pytest.raises(ReplySendBlocked) as blocked:
        await app.delivery.submit(KEY, draft["revision"])
    assert blocked.value.status == status
    assert app.store.draft(KEY) == draft
    assert (
        not app.store.rows("SELECT 1 FROM operations") and not app.transport.deliveries
    )


async def test_pending_deletion_holds_queued_head_and_preserves_submission_order(
    tmp_path,
):
    app = fixture(tmp_path)
    source(app)
    attach(app)
    original = edit(app, "Exact submitted caption", reply_to=1)
    head = await app.delivery.submit(KEY, original["revision"])
    later = await app.delivery.submit(KEY, edit(app, "Queued second")["revision"])
    deletion = pending_delete(app)
    assert app.store.send_operations.runnable(KEY) is None
    assert app.store.send_operations.claim(head["id"]) is False
    assert (await app.delivery.drain(later["id"]))["state"] == "queued"
    held = saved(app, head["id"])
    assert held["state"] == "queued" and held["cause"] == "reply_deletion"
    assert held["text"] == "Exact submitted caption" and held["reply_to"] == 1
    assert held["attachments"] == head["attachments"] and app.delivery.paused(KEY)
    settle_delete(app, deletion, acknowledged=True)
    assert app.store.send_operations.runnable(KEY) is None
    refused = saved(app, head["id"])
    assert refused["state"] == "refused" and refused["cause"] == "reply_deleted"
    assert refused["reply_to"] == 1 and not app.transport.deliveries
    view = operation_view(refused, app.store.clock())
    assert not view["retryable"] and view["cancellable"]
    app.delivery.cancel(head["id"])
    assert (await app.delivery.drain(later["id"]))["state"] == "confirmed"


async def test_checked_present_definite_failure_releases_hold_without_changing_reply(
    tmp_path,
):
    app = fixture(tmp_path)
    original = source(app)
    queued = await app.delivery.submit(
        KEY, edit(app, "Reviewed reply", reply_to=1)["revision"]
    )
    deletion = pending_delete(app)
    settle_delete(app, deletion)
    assert app.store.send_operations.runnable(KEY) is None
    app.store.deletion_operations.record_read(
        deletion["id"], DeletionRead(1, "user:42", (1,), (original,))
    )
    sent = await app.delivery.drain(queued["id"])
    assert sent["state"] == "confirmed" and sent["reply_to"] == 1
    [delivery] = app.transport.deliveries.values()
    assert delivery["description"].reply_to == 1


async def test_deletion_after_claim_blocks_atomic_exposure_write(tmp_path):
    app = fixture(tmp_path)
    source(app)
    queued = await app.delivery.submit(
        KEY, edit(app, "Claimed reply", reply_to=1)["revision"]
    )
    assert app.store.send_operations.claim(queued["id"])
    evidence = app.store.send_operations.evidence(queued["id"])
    evidence.calling()
    evidence.watching()
    pending_delete(app)
    with pytest.raises(ReplySendBlocked) as stopped:
        # The write itself rechecks holds in its own transaction, even if a
        # transport forgot to call the independent guard immediately before.
        evidence.handing_off()
    app.store.send_operations.settle(evidence, error=stopped.value)
    held = saved(app, queued["id"])
    assert held["state"] == "queued" and held["wire"][-1]["outcome"] == "watched"
    assert held["text"] == "Claimed reply" and held["reply_to"] == 1


async def test_deletion_during_queue_pacing_prevents_claim_without_losing_images(
    tmp_path,
):
    app = fixture(tmp_path)
    source(app)
    attach(app)
    queued = await app.delivery.submit(
        KEY, edit(app, "Paced caption", reply_to=1)["revision"]
    )
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES (?,?)",
            ("send_due:" + KEY, str(app.store.clock() + 0.2)),
        )
    task = asyncio.create_task(app.delivery.drain(queued["id"]))
    async with asyncio.timeout(2):
        while not app.store.rows("SELECT 1 FROM cursors WHERE key='send_due:global'"):
            await asyncio.sleep(0)
    pending_delete(app)
    result = await task
    assert result["state"] == "queued" and result["cause"] == "reply_deletion"
    assert result["wire"] == [] and result["attachments"] == queued["attachments"]
    assert result["reply_to"] == 1 and not app.transport.deliveries


@pytest.mark.parametrize("deleted", [False, True])
async def test_upload_time_deletion_blocks_final_send_and_keeps_newer_draft(
    tmp_path, deleted
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def upload(app):
        entered.set()
        await release.wait()

    wire = send_rig(tmp_path, [77], upload=upload)
    app = wire.app
    attach(app)
    before = edit(app, "Submitted image caption", reply_to=1)
    operation = await app.delivery.submit(KEY, before["revision"])
    task = asyncio.create_task(app.delivery.drain(operation["id"]))
    await entered.wait()
    deletion = pending_delete(app)
    if deleted:
        settle_delete(app, deletion, acknowledged=True)
    attach(app)
    newer = edit(app, "Newer draft stays editable")
    release.set()
    result = await task
    assert result["state"] == ("refused" if deleted else "queued")
    assert result["reply_to"] == 1 and result["text"] == "Submitted image caption"
    assert result["attachments"] == operation["attachments"]
    assert app.store.draft(KEY) == newer and wire.calls == [] and wire.executed == []
    app.attachments.collect()
    assert app.store.rows(
        "SELECT 1 FROM attachments WHERE id=?", (before["attachments"][0],)
    )


@pytest.mark.parametrize("deleted", [False, True])
async def test_paced_wire_guard_observes_deletion_accepted_while_waiting(
    tmp_path, deleted
):
    wire = send_rig(tmp_path, [77])
    queued = await wire.app.delivery.submit(
        KEY, edit(wire.app, "Waited reply", reply_to=1)["revision"]
    )
    wire.policy.hold("account", wire.clock.now + 2)
    sleep = wire.clock.sleep

    async def change(seconds):
        await sleep(seconds)
        deletion = pending_delete(wire.app)
        if deleted:
            settle_delete(wire.app, deletion, acknowledged=True)

    wire.policy.sleep = change
    result = await wire.app.delivery.drain(queued["id"])
    assert result["state"] == ("refused" if deleted else "queued")
    assert wire.calls == [] and wire.executed == [] and len(wire.answers) == 1
    assert [entry["outcome"] for entry in result["wire"]] == ["watched"]


async def test_flood_retry_rechecks_reply_hold_before_second_append(tmp_path):
    wire = send_rig(tmp_path, [errors.FloodWaitError(None, capture=2), 77])
    queued = await wire.app.delivery.submit(
        KEY, edit(wire.app, "Flood-held reply", reply_to=1)["revision"]
    )
    sleep = wire.clock.sleep

    async def change(seconds):
        await sleep(seconds)
        pending_delete(wire.app)

    wire.policy.sleep = change
    result = await wire.app.delivery.drain(queued["id"])
    assert result["state"] == "queued" and result["cause"] == "reply_deletion"
    assert len(wire.calls) == 1 and not wire.executed and len(wire.answers) == 1
    assert [entry["outcome"] for entry in result["wire"]] == [
        "watched",
        "flood_refused",
    ]
    assert result["random_id"] == queued["random_id"]


async def test_sdk_reappend_hold_preserves_first_possible_exposure(tmp_path):
    wire = send_rig(
        tmp_path, [CORRECTION], on_wire=lambda app, request: pending_delete(app)
    )
    result = await wire.app.delivery.send(
        KEY, edit(wire.app, "Possibly handed reply", reply_to=1)["revision"]
    )
    assert result["state"] == "unknown" and result["reply_to"] == 1
    assert len(wire.calls) == 1 and not wire.executed
    assert wire.ledgers == [["watched", "exposed"]]
    assert result["wire"][-1]["outcome"] == "exposed"
    with pytest.raises(Refused):
        wire.app.delivery.cancel(result["id"])


async def test_lost_acknowledgement_remains_unknown_after_target_delete(tmp_path):
    def delete(app, request):
        settle_delete(app, pending_delete(app), acknowledged=True)

    wire = send_rig(tmp_path, [LOST], on_wire=delete)
    result = await wire.app.delivery.send(
        KEY, edit(wire.app, "Already handed reply", reply_to=1)["revision"]
    )
    assert result["state"] == "unknown" and wire.executed == [result["random_id"]]
    wire.app.store.recover()
    assert saved(wire.app, result["id"])["state"] == "unknown"
    with pytest.raises(Refused):
        wire.app.delivery.retry(result["id"])
    with pytest.raises(Refused):
        wire.app.delivery.cancel(result["id"])
    wire.app.store.send_operations.record_receipts(
        ReceiptBatch(((result["random_id"], 77),))
    )
    checked = await wire.app.delivery.recheck(result["id"])
    assert checked["state"] == "confirmed" and checked["reply_to"] == 1
    assert len(wire.calls) == 1


async def test_cache_expiry_and_restart_keep_unresolved_hold_and_authored_submission(
    tmp_path,
):
    clock = Clock()
    app = fixture(tmp_path, clock=lambda: clock.now)
    original = source(app)
    attach(app)
    queued = await app.delivery.submit(
        KEY, edit(app, "Durable caption", reply_to=1)["revision"]
    )
    deletion = pending_delete(app)
    settle_delete(app, deletion, uncertain=True)
    app.store.deletion_operations.record_read(
        deletion["id"], DeletionRead(1, "user:42", (1,), (original,))
    )
    assert app.store.deletion_operations.reply_hold(KEY, 1) == "unresolved"
    clock.now += RETENTION + 1
    app.store.expire()
    assert app.store.message(KEY, 1) is None
    assert app.store.send_operations.runnable(KEY) is None
    held = saved(app, queued["id"])
    assert held["state"] == "queued" and held["cause"] == "reply_deletion"
    app.store.recover()
    recovered = saved(app, queued["id"])
    assert recovered["state"] == "refused" and recovered["reply_to"] == 1
    assert (
        recovered["text"] == "Durable caption"
        and recovered["attachments"] == queued["attachments"]
    )
    with pytest.raises(ReplySendBlocked) as blocked:
        app.delivery.retry(queued["id"])
    assert blocked.value.status == "unresolved"
    assert not app.transport.deliveries


async def test_expired_source_without_native_evidence_is_unavailable_not_deleted(
    tmp_path,
):
    app = fixture(tmp_path)
    source(app)
    queued = await app.delivery.submit(
        KEY, edit(app, "Reply before cache loss", reply_to=1)["revision"]
    )
    with app.store.transaction() as db:
        db.execute("DELETE FROM messages WHERE chat_key=?", (KEY,))
    assert app.store.send_operations.claim(queued["id"]) is False
    result = saved(app, queued["id"])
    assert (
        result["cause"] == "reply_unavailable"
        and "deleted" not in result["reason"].lower()
    )
    assert result["reply_to"] == 1 and not app.transport.deliveries


async def test_pending_deletion_prevents_exact_read_of_expired_composer_target(
    tmp_path,
):
    app = fixture(tmp_path)
    source(app)
    pending_delete(app)
    draft = edit(app, "Authored reply after local eviction", reply_to=1)
    with app.store.transaction() as db:
        db.execute("DELETE FROM messages WHERE chat_key=?", (KEY,))
    reads = []

    async def exact(peer, identity):
        reads.append((peer, identity))
        return None

    app.transport.exact = exact
    with pytest.raises(ReplySendBlocked):
        await app.delivery.submit(KEY, draft["revision"])
    assert reads == [] and app.store.draft(KEY) == draft
