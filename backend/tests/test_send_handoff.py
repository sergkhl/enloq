"""A send held back before reaching Telegram is Not sent and keeps its exact
content for a deliberate Retry or Cancel (ADR 26); anything that may have
reached Telegram stays unknown and is never resent or bypassed."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.domain.models import GapTooLong, Refused, Unavailable
from enloq.transport.mtproto import MTProtoRecoveryPaused, PacedTelegramClient
from enloq.transport.telethon import PersonalTransport
from telethon import errors, functions, types

from backend.tests.test_rebuild import attach, edit, fixture
from backend.tests.test_transport import Clock

KEY = "1/user:42"


LOST = object()  # Telegram executes the request, but its answer never arrives
SILENT = object()  # handed to the sender and never answered


def peer_transport(tmp_path, answers, *, on_send=None):
    """The real transport and request policy against an in-memory peer that
    answers each final send request from `answers` and records the random IDs
    it executed. Preparation requests (config, album uploads) always succeed."""
    clock = Clock()
    app = fixture(tmp_path, clock=lambda: clock.now)
    client = PacedTelegramClient(str(tmp_path / "fixture.session"), 1, "fixture")
    client.request_policy = clock.policy(tmp_path / "policy.sqlite3")
    executed = []

    class Sender:
        def __init__(self):
            self._send_queue = SimpleNamespace(append=lambda state: None)

        def send(self, item, ordered=False):
            future = asyncio.get_running_loop().create_future()
            self._send_queue.append(SimpleNamespace(request=item, future=future))
            if future.done():
                # The real MTProtoSender returns the failed future when the
                # bounded packer refuses append; it cannot execute that RPC.
                return future
            if isinstance(item, functions.help.GetConfigRequest):
                future.set_result(SimpleNamespace(caption_length_max=1024))
                return future
            if isinstance(item, functions.messages.UploadMediaRequest):
                photo = types.Photo(
                    id=1,
                    access_hash=1,
                    file_reference=b"authored",
                    date=datetime.now(UTC),
                    sizes=[],
                    dc_id=2,
                )
                future.set_result(SimpleNamespace(photo=photo))
                return future
            if on_send:
                on_send(item)
            identities = [
                m.random_id for m in getattr(item, "multi_media", None) or [item]
            ]
            answer = answers.pop(0)
            if answer is SILENT:
                return future
            if answer is LOST:
                executed.extend(identities)
                future.set_exception(ConnectionError("authored: answer lost"))
            elif isinstance(answer, BaseException):
                future.set_exception(answer)
            else:
                executed.extend(identities)
                future.set_result(
                    types.Updates(
                        updates=[
                            types.UpdateMessageID(id=answer + i, random_id=identity)
                            for i, identity in enumerate(identities)
                        ],
                        users=[],
                        chats=[],
                        date=datetime.now(UTC),
                        seq=0,
                    )
                )
            return future

    sender = Sender()

    class Client:
        request_policy = client.request_policy
        connected = True

        def is_connected(self):
            return self.connected

        async def get_input_entity(self, peer):
            return types.InputPeerUser(peer.user_id, 1)

        async def upload_file(self, path, **kwargs):
            return types.InputFile(
                id=1, parts=1, name="image.png", md5_checksum="authored"
            )

        async def __call__(self, request):
            return await client._call(sender, request)

    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id = app.store, 1
    transport.client, transport.verified = Client(), True
    transport.bind_receipts(app.store.send_operations.record_receipts)
    app.delivery.transport = transport
    return app, clock, client, executed


async def test_a_flood_held_send_is_refused_then_resent_once_with_its_random_id(
    tmp_path,
):
    flood = errors.FloodWaitError(request=None, capture=7200)
    app, clock, client, executed = peer_transport(tmp_path, [flood, 77])
    try:
        draft = edit(app, "Authored reply")
        held = await app.delivery.send(KEY, draft["revision"])
        assert held["state"] == "refused" and held["cause"] == "not_sent"
        assert held["reason"].startswith("Telegram asked this account to wait.")
        assert held["reason"].endswith("Nothing was sent.")
        assert held["retry_after"] == clock.now + 7200.5
        assert [e["outcome"] for e in held["wire"]] == ["watched", "flood_refused"]
        assert executed == []
        # The submission owns its content; the composer was cleared on
        # acceptance. Retry waits for Telegram's requested wait.
        assert app.store.draft(KEY)["text"] == "" and held["text"] == "Authored reply"
        with pytest.raises(Refused, match="wait"):
            app.delivery.retry(held["id"])
        clock.now += 7300
        app.delivery.retry(held["id"])
        sent = await app.delivery.drain(held["id"])
        assert sent["state"] == "confirmed" and sent["message_id"] == 77
        assert executed == [held["random_id"]]
        assert sent["random_id"] == held["random_id"]
        assert [a["cause"] for a in sent["attempts"]] == ["not_sent"]
    finally:
        await client.disconnect()


async def test_a_reused_random_id_that_telegram_already_executed_stays_unknown(
    tmp_path,
):
    flood = errors.FloodWaitError(request=None, capture=7200)
    duplicate = errors.RandomIdDuplicateError(request=None)
    app, clock, client, executed = peer_transport(tmp_path, [flood, duplicate])
    try:
        draft = edit(app, "Authored reply")
        held = await app.delivery.send(KEY, draft["revision"])
        clock.now += 7300
        app.delivery.retry(held["id"])
        again = await app.delivery.drain(held["id"])
        # Telegram says it has this random ID: never a second message.
        assert again["state"] == "unknown" and executed == []
        # An uncertain hand-off can be neither cancelled nor bypassed: a newer
        # submission is accepted and waits behind it.
        with pytest.raises(Refused):
            app.delivery.cancel(again["id"])
        with pytest.raises(Refused):
            app.delivery.retry(again["id"])
        newer = edit(app, "Authored follow-up")
        waiting = await app.delivery.send(KEY, newer["revision"])
        assert waiting["state"] == "queued" and executed == []
    finally:
        await client.disconnect()


async def test_a_held_send_keeps_its_content_until_cancelled_in_order(tmp_path):
    import time

    app = fixture(tmp_path, clock=time.time)  # the fake's hold uses wall time
    app.transport.held = 600
    attach(app)
    draft = edit(app, "Authored caption")
    held = await app.delivery.send(KEY, draft["revision"])
    assert held["state"] == "refused" and held["retry_after"]
    assert not app.transport.deliveries
    # The composer is free; the item keeps its exact text and images.
    cleared = app.store.draft(KEY)
    assert cleared["text"] == "" and not cleared["attachments"]
    image = held["attachments"][0]["id"]
    app.attachments.collect()
    assert app.attachments.get(KEY, image)
    # A later submission waits behind the failed one: one chat, one order.
    app.transport.held = 0
    newer = await app.delivery.send(KEY, edit(app, "Authored follow-up")["revision"])
    assert newer["state"] == "queued" and not app.transport.deliveries
    cancelled = app.delivery.cancel(held["id"])
    assert cancelled["state"] == "cancelled" and cancelled["text"] == "Authored caption"
    with pytest.raises(Refused):
        app.delivery.retry(held["id"])
    sent = await app.delivery.drain(newer["id"])
    assert sent["state"] == "confirmed" and len(app.transport.deliveries) == 1
    assert app.store.messages(KEY)[-1].text == "Authored follow-up"


@pytest.mark.parametrize(
    "ledger,image,expected",
    [
        ([], False, "refused"),
        ([{"outcome": "watched"}], False, "refused"),
        ([{"outcome": "watched"}, {"outcome": "flood_refused"}], False, "refused"),
        ([{"outcome": "watched"}, {"outcome": "not_handed"}], False, "refused"),
        ([{"outcome": "calling"}], False, "unknown"),
        (
            [
                {"outcome": "watched"},
                {"outcome": "flood_refused"},
                {"outcome": "exposed"},
            ],
            False,
            "unknown",
        ),
        # One exposed hand-off is enough, even before a later credited one.
        (
            [
                {"outcome": "watched"},
                {"outcome": "exposed"},
                {"outcome": "flood_refused"},
            ],
            False,
            "unknown",
        ),
        (
            [{"outcome": "watched"}, {"outcome": "exposed"}, {"outcome": "not_handed"}],
            False,
            "unknown",
        ),
        (None, False, "unknown"),  # a text send recorded before the ledger
        (None, True, "refused"),  # an old image send that never started
    ],
)
def test_restart_recovery_uses_the_handoff_ledger(tmp_path, ledger, image, expected):
    app = fixture(tmp_path)
    body = {"id": "authored", "chat_key": KEY, "state": "dispatching"}
    if ledger is not None:
        body["wire"] = ledger
    elif image:
        body["wire_started"] = False
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            ("authored", KEY, "dispatching", json.dumps(body)),
        )
    app.store.recover()
    assert app.store.send_operations.get("authored")["state"] == expected


@pytest.mark.parametrize(
    "failure",
    [
        Unavailable("authored"),
        GapTooLong("authored"),
        MTProtoRecoveryPaused("authored"),
        errors.FloodWaitError(request=None, capture=5),
        OSError("authored"),
        TimeoutError(),
    ],
)
async def test_check_delivery_reports_when_telegram_cannot_be_checked(
    tmp_path, failure
):
    app = fixture(tmp_path)
    app.transport.lose_receipt = True
    draft = edit(app, "Authored reply")
    unknown = await app.delivery.send(KEY, draft["revision"])
    assert unknown["state"] == "unknown"
    app.transport.check_failure = failure
    checked = await app.delivery.recheck(unknown["id"])
    assert checked["state"] == "unknown" and "could not be checked" in checked["check"]
    # A receipt found later still confirms it; nothing was resent.
    app.transport.check_failure = None
    confirmed = await app.delivery.recheck(unknown["id"])
    assert confirmed["state"] == "confirmed"
    assert len(app.transport.deliveries) == 1


async def test_check_delivery_lets_cancellation_through(tmp_path):
    app = fixture(tmp_path)
    app.transport.lose_receipt = True
    draft = edit(app, "Authored reply")
    unknown = await app.delivery.send(KEY, draft["revision"])
    app.transport.check_failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await app.delivery.recheck(unknown["id"])


async def test_a_failed_reply_target_read_sends_nothing(tmp_path):
    app = fixture(tmp_path)
    draft = edit(app, "Authored reply", reply_to=99)

    async def unreachable(peer, identity):
        raise OSError("authored")

    app.transport.exact = unreachable
    with pytest.raises(Refused, match="Nothing was sent"):
        await app.delivery.send(KEY, draft["revision"])
    assert not app.store.rows("SELECT 1 FROM operations")


async def test_a_refusal_counts_only_for_the_attempt_telegram_refused(tmp_path):
    flood = errors.FloodWaitError(request=None, capture=7200)
    forbidden = errors.ChatWriteForbiddenError(request=None)
    app, clock, client, executed = peer_transport(
        tmp_path, [flood, forbidden, LOST, 78]
    )
    try:
        draft = edit(app, "Authored reply")
        held = await app.delivery.send(KEY, draft["revision"])
        clock.now += 7300
        app.delivery.retry(held["id"])
        refused = await app.delivery.drain(held["id"])
        assert refused["state"] == "refused" and "cause" not in refused
        clock.now += 10
        # Retry reuses the random ID; Telegram executes it this time, but the
        # answer is lost.
        app.delivery.retry(held["id"])
        lost = await app.delivery.drain(held["id"])
        assert lost["random_id"] == refused["random_id"] == held["random_id"]
        assert lost["state"] == "unknown" and executed == [held["random_id"]]
        # The earlier attempt's refusal does not resolve this one.
        checked = await app.delivery.recheck(lost["id"])
        assert checked["state"] == "unknown" and checked["check"]
        newer = await app.delivery.send(KEY, edit(app, "Authored edit")["revision"])
        assert newer["state"] == "queued"
        # A receipt proves delivery; the paused chat then continues in order.
        await app.delivery.transport._receipt(
            types.UpdateMessageID(id=555, random_id=held["random_id"])
        )
        confirmed = await app.delivery.recheck(lost["id"])
        assert confirmed["state"] == "confirmed" and confirmed["message_id"] == 555
        assert executed == [held["random_id"]]
        assert (await app.delivery.drain(newer["id"]))["state"] == "confirmed"
        assert executed == [held["random_id"], newer["random_id"]]
    finally:
        await client.disconnect()


@pytest.mark.parametrize(
    "ledger,cursor,expected",
    [
        # The attempt Telegram refused, interrupted before recording it.
        (
            [{"outcome": "watched"}, {"outcome": "exposed"}],
            "refusal:op:authored",
            "refused",
        ),
        # A send recorded before the ledger, when random IDs were never shared.
        (None, "refusal:5", "refused"),
        # Another attempt's refusal of a shared random ID.
        ([{"outcome": "watched"}, {"outcome": "exposed"}], "refusal:5", "unknown"),
        # A receipt proves delivery, even of a random ID another attempt used.
        (
            [{"outcome": "watched"}, {"outcome": "exposed"}],
            "refusal:op:authored receipt:5",
            "confirmed",
        ),
    ],
)
async def test_check_delivery_honours_only_its_own_refusal(
    tmp_path, ledger, cursor, expected
):
    app, _, client, _ = peer_transport(tmp_path, [])
    body = dict(
        id="authored",
        account_id=1,
        peer="user:42",
        chat_key=KEY,
        text="Authored reply",
        attachments=[],
        reply_to=None,
        composer_revision=1,
        random_id=5,
        state="unknown",
        created=0,
        message_id=None,
    )
    if ledger is not None:
        body["wire"] = ledger
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            ("authored", KEY, "unknown", json.dumps(body)),
        )
        for key in cursor.split():
            db.execute(
                "INSERT INTO cursors VALUES (?,?)",
                (
                    key,
                    "555" if key.startswith("receipt:") else "ChatWriteForbiddenError",
                ),
            )
    # Offline, an unresolved check says so rather than finding nothing.
    app.delivery.transport.client.connected = False
    try:
        checked = await app.delivery.recheck("authored")
        assert checked["state"] == expected
        if expected == "unknown":
            assert "could not be checked" in checked["check"]
    finally:
        await client.disconnect()


async def test_cancelling_a_send_after_its_hand_off_leaves_it_unknown(tmp_path):
    handed = asyncio.Event()
    app, _, client, executed = peer_transport(
        tmp_path, [SILENT], on_send=lambda item: handed.set()
    )
    try:
        draft = edit(app, "Authored reply")
        task = asyncio.create_task(app.delivery.send(KEY, draft["revision"]))
        await handed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        [operation] = app.store.rows("SELECT state,body FROM operations")
        assert operation["state"] == "unknown"
        outcomes = [e["outcome"] for e in json.loads(operation["body"])["wire"]]
        assert outcomes == ["watched", "exposed"]
    finally:
        await client.disconnect()


async def test_cancelling_a_held_send_refuses_it_with_nothing_handed_off(tmp_path):
    handed = []
    app, clock, client, executed = peer_transport(tmp_path, [77], on_send=handed.append)
    waiting = asyncio.Event()

    async def hold(seconds):
        waiting.set()
        await asyncio.Event().wait()

    client.request_policy.sleep = hold
    client.request_policy.hold("account", clock.now + 600)
    try:
        draft = edit(app, "Authored reply")
        task = asyncio.create_task(app.delivery.send(KEY, draft["revision"]))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        [operation] = [
            json.loads(r["body"]) for r in app.store.rows("SELECT body FROM operations")
        ]
        assert operation["state"] == "refused" and operation["cause"] == "not_sent"
        assert operation["reason"] == (
            "Interrupted before reaching Telegram. Nothing was sent."
        )
        assert operation["retry_after"] == clock.now + 600
        assert handed == [] and executed == []
        assert operation["text"] == "Authored reply"
        assert app.store.draft(KEY)["text"] == ""
    finally:
        await client.disconnect()


async def test_each_hand_off_is_recorded_durably_before_the_sender_takes_it(tmp_path):
    app, _, client, _ = peer_transport(
        tmp_path, [77], on_send=lambda item: seen.append(stored())
    )
    seen = []

    def stored():
        [row] = app.store.rows("SELECT body FROM operations WHERE state='dispatching'")
        return [e["outcome"] for e in json.loads(row["body"])["wire"]]

    try:
        draft = edit(app, "Authored reply")
        sent = await app.delivery.send(KEY, draft["revision"])
        assert sent["state"] == "confirmed" and seen == [["watched", "exposed"]]
    finally:
        await client.disconnect()


@pytest.mark.parametrize("outcome,kept", [("calling", []), ("exposed", ["watched"])])
async def test_a_hand_off_whose_record_fails_is_never_handed_off(
    tmp_path, outcome, kept
):
    handed = []
    app, _, client, executed = peer_transport(tmp_path, [77], on_send=handed.append)
    with app.store.transaction() as db:
        db.execute(f"""CREATE TRIGGER fail_wire BEFORE UPDATE ON operations
            WHEN json_extract(NEW.body, '$.wire[#-1].outcome') = '{outcome}'
            BEGIN SELECT RAISE(ABORT, 'authored evidence failure'); END""")
    try:
        draft = edit(app, "Authored reply")
        refused = await app.delivery.send(KEY, draft["revision"])
        assert refused["state"] == "refused" and refused["cause"] == "not_sent"
        assert [e["outcome"] for e in refused["wire"]] == kept
        assert handed == [] and executed == []
    finally:
        await client.disconnect()


async def test_failed_flood_narrowing_leaves_broader_delivery_evidence(tmp_path):
    app, _, client, _ = peer_transport(
        tmp_path, [errors.FloodWaitError(request=None, capture=7200)]
    )
    with app.store.transaction() as db:
        db.execute("""CREATE TRIGGER fail_narrow BEFORE UPDATE ON operations
            WHEN json_extract(NEW.body, '$.wire[#-1].outcome') = 'flood_refused'
            BEGIN SELECT RAISE(ABORT, 'authored narrowing failure'); END""")
    try:
        draft = edit(app, "Authored reply")
        unknown = await app.delivery.send(KEY, draft["revision"])
        assert unknown["state"] == "unknown"
        assert unknown["wire"][-1]["outcome"] == "exposed"
        assert unknown["text"] == "Authored reply"
    finally:
        await client.disconnect()


@pytest.mark.parametrize("count", [1, 2])
async def test_a_held_image_send_is_resent_with_the_same_random_ids(tmp_path, count):
    flood = errors.FloodWaitError(request=None, capture=7200)
    app, clock, client, executed = peer_transport(tmp_path, [flood, 70])
    try:
        for _ in range(count):
            attach(app)
        draft = edit(app, "Authored caption")
        held = await app.delivery.send(KEY, draft["revision"])
        assert held["state"] == "refused" and held["cause"] == "not_sent"
        assert held["retry_after"] and executed == []
        clock.now += 7300
        app.delivery.retry(held["id"])
        sent = await app.delivery.drain(held["id"])
        assert sent["state"] == "confirmed" and sent["random_ids"] == held["random_ids"]
        assert executed == held["random_ids"]
    finally:
        await client.disconnect()


async def test_a_reused_album_that_telegram_already_executed_stays_unknown(tmp_path):
    flood = errors.FloodWaitError(request=None, capture=7200)
    duplicate = errors.RandomIdDuplicateError(request=None)
    app, clock, client, executed = peer_transport(tmp_path, [flood, duplicate])
    try:
        attach(app)
        attach(app)
        draft = edit(app, "Authored caption")
        held = await app.delivery.send(KEY, draft["revision"])
        clock.now += 7300
        app.delivery.retry(held["id"])
        again = await app.delivery.drain(held["id"])
        assert again["state"] == "unknown" and executed == []
    finally:
        await client.disconnect()


async def test_an_image_send_held_while_preparing_says_when_to_send_again(tmp_path):
    handed = []
    app, clock, client, executed = peer_transport(tmp_path, [70], on_send=handed.append)
    client.request_policy.hold("account", clock.now + 7200)
    try:
        attach(app)
        draft = edit(app, "Authored caption")
        held = await app.delivery.send(KEY, draft["revision"])
        assert held["state"] == "refused" and held["cause"] == "not_sent"
        assert held["reason"].startswith("Telegram asked this account to wait.")
        assert held["retry_after"] == clock.now + 7200 and held["wire"] == []
        assert handed == [] and executed == []
        assert held["text"] == "Authored caption" and held["attachments"]
    finally:
        await client.disconnect()


@pytest.mark.parametrize("images", [0, 1, 2])
async def test_a_refusal_recorded_before_a_crash_resolves_its_own_attempt(
    tmp_path, images
):
    forbidden = errors.ChatWriteForbiddenError(request=None)
    app, _, client, executed = peer_transport(tmp_path, [forbidden])
    try:
        for _ in range(images):
            attach(app)
        draft = edit(app, "Authored reply")
        refused = await app.delivery.send(KEY, draft["revision"])
        assert refused["state"] == "refused" and executed == []
        # The process stopped after Telegram refused but before the state was
        # saved: restart recovery leaves it unknown and Check delivery finds
        # the refusal Telegram gave this attempt.
        refused.update(state="dispatching")
        with app.store.transaction() as db:
            db.execute(
                "UPDATE operations SET state='dispatching',body=? WHERE id=?",
                (json.dumps(refused), refused["id"]),
            )
        app.store.recover()
        assert app.store.send_operations.get(refused["id"])["state"] == "unknown"
        checked = await app.delivery.recheck(refused["id"])
        assert checked["state"] == "refused"
        assert checked["reason"] == "ChatWriteForbiddenError"
    finally:
        await client.disconnect()


@pytest.mark.parametrize("broader", ["durable", "memory"])
def test_settle_needs_both_the_stored_and_the_in_memory_ledger(tmp_path, broader):
    app = fixture(tmp_path)
    draft = edit(app, "Authored reply")
    operation = app.store.send_operations.prepare(
        KEY, draft["revision"], snapshot=app.attachments.snapshot
    )
    assert app.store.send_operations.claim(operation["id"])
    evidence = app.store.send_operations.evidence(operation["id"])
    evidence.calling()
    evidence.watching()
    evidence.handing_off()
    if broader == "durable":
        # A failed in-memory update must not erase a durable observation.
        evidence._ledger = [{"outcome": "watched"}]
    else:
        # A stale durable record cannot overrule an in-memory hand-off.
        stored = app.store.send_operations.get(operation["id"])
        stored["wire"] = [{"outcome": "watched"}]
        with app.store.transaction() as db:
            db.execute(
                "UPDATE operations SET body=? WHERE id=?",
                (json.dumps(stored), operation["id"]),
            )
    app.store.send_operations.settle(evidence, error=TimeoutError())
    current = app.store.send_operations.get(operation["id"])
    assert current["state"] == "unknown"
    if broader == "durable":
        assert current["wire"][-1]["outcome"] == "exposed"


async def test_a_send_stopped_by_a_disconnect_does_not_blame_telegram(tmp_path):
    app, clock, client, executed = peer_transport(tmp_path, [77])

    async def dropped(peer):
        raise ConnectionError("authored: connection dropped")

    app.delivery.transport.client.get_input_entity = dropped
    # The reconnect owner holds the account while it backs off.
    client.request_policy.hold("account", clock.now + 2)
    try:
        draft = edit(app, "Authored reply")
        refused = await app.delivery.send(KEY, draft["revision"])
        assert refused["state"] == "refused" and refused["cause"] == "not_sent"
        assert refused["reason"].startswith("Telegram disconnected before sending.")
        assert "retry_after" not in refused and executed == []
    finally:
        await client.disconnect()


def hold_sends(app, seconds):
    """Delay this chat's send pacing by `seconds` from the store clock."""
    with app.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO cursors VALUES (?,?)",
            ("send_due:" + KEY, str(app.store.clock() + seconds)),
        )


def queued(app):
    return [
        json.loads(r["body"])
        for r in app.store.rows("SELECT body FROM operations WHERE state='queued'")
    ]


async def test_a_long_pacing_hold_is_not_sent_and_retry_reuses_its_identity(
    tmp_path,
):
    app = fixture(tmp_path)
    attach(app)
    draft = edit(app, "Authored caption")
    hold_sends(app, 50)
    held = await app.delivery.send(KEY, draft["revision"])
    assert held["state"] == "refused" and held["cause"] == "not_sent"
    assert held["reason"] == (
        "Send pacing requires a later deliberate attempt. Nothing was sent."
    )
    # Its image stays held for Retry although the composer was cleared.
    image = held["attachments"][0]["id"]
    app.store.expire()
    app.attachments.collect()
    assert app.store.rows("SELECT 1 FROM attachments WHERE id=?", (image,))
    assert app.store.send_operations.get(held["id"])["state"] == "refused"
    hold_sends(app, 0)
    app.delivery.retry(held["id"])
    sent = await app.delivery.drain(held["id"])
    assert sent["state"] == "confirmed" and sent["random_id"] == held["random_id"]
    assert len(app.transport.deliveries) == 1
    # Confirmed, its image is released.
    app.attachments.collect()
    assert not app.store.rows("SELECT 1 FROM attachments WHERE id=?", (image,))


async def test_ordinary_pacing_is_waited_out_whatever_the_new_draft_says(tmp_path):
    app = fixture(tmp_path)
    draft = edit(app, "Authored reply")
    hold_sends(app, 0.2)
    task = asyncio.create_task(app.delivery.send(KEY, draft["revision"]))
    while not queued(app):
        await asyncio.sleep(0.01)
    edit(app, "Authored reply, edited for later")
    app.store.expire()
    result = await task
    assert result["state"] == "confirmed" and result["text"] == "Authored reply"
    assert len(app.transport.deliveries) == 1
    assert app.store.draft(KEY)["text"] == "Authored reply, edited for later"


async def test_restart_recovery_leaves_queued_work_for_a_deliberate_retry(tmp_path):
    app = fixture(tmp_path)
    draft = edit(app, "Authored reply")
    hold_sends(app, 0.5)
    task = asyncio.create_task(app.delivery.send(KEY, draft["revision"]))
    while not queued(app):
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [stranded] = queued(app)
    app.store.recover()
    refused = app.store.send_operations.get(stranded["id"])
    assert refused["state"] == "refused" and refused["cause"] == "not_sent"
    assert (
        refused["reason"] == "Interrupted before reaching Telegram. Nothing was sent."
    )
    # Recovery never restarts a queue on its own.
    assert app.store.send_operations.runnable(KEY) is None
    hold_sends(app, 0)
    app.delivery.retry(stranded["id"])
    sent = await app.delivery.drain(stranded["id"])
    assert sent["state"] == "confirmed" and sent["random_id"] == stranded["random_id"]
    assert len(app.transport.deliveries) == 1
