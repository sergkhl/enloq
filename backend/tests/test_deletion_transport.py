"""Native deletion against authored wire objects, without authorizations or sockets.

The real paced _call/policy/packer run against an in-memory sender. Neither a
TelegramClient constructor nor a session/authorization/socket is created.
"""

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.domain.deletion import (
    DeleteDescription,
    DeleteReceipt,
    DeletionRead,
)
from enloq.domain.models import Message, Refused
from enloq.transport.fake import FakeTransport
from enloq.transport.mtproto import MTProtoAttemptLimit, PacedTelegramClient
from enloq.transport.telethon import PersonalTransport
from telethon import errors, functions, types
from telethon.network.mtprotosender import MTProtoSender

from backend.tests.test_transport import Clock

PEER = "user:42"
SILENT = object()
CORRECTION = object()


class Evidence:
    """Observe durable order with independently reopened evidence records."""

    def __init__(self, path, *, guard=None, fail=None):
        self.path, self.check_guard, self.fail = path, guard, fail
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS evidence (event TEXT)")

    def record(self, event):
        if event == self.fail:
            raise sqlite3.OperationalError("Authored evidence write failure")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO evidence VALUES (?)", (event,))

    def events(self):
        with sqlite3.connect(self.path) as db:
            return [row[0] for row in db.execute("SELECT event FROM evidence")]

    def guard(self):
        if self.check_guard:
            self.check_guard()
        self.record("guard")

    def calling(self):
        self.record("calling")

    def watching(self):
        self.record("watching")

    def holding(self, until):
        self.record("holding")

    def handing_off(self):
        self.record("exposed")

    def not_handed(self):
        self.record("not_handed")

    def flood_refused(self, error, until):
        self.record("flood_refused")

    def refused(self, reason):
        self.record("refused")


def native_message(identity, peer=42):
    return types.Message(
        id=identity,
        peer_id=types.PeerUser(peer),
        date=datetime.fromtimestamp(1000, UTC),
        message=f"Authored message {identity}",
        out=identity % 2 == 0,
    )


def native_page(messages):
    return types.messages.Messages(messages=messages, topics=[], users=[], chats=[])


def native_empty(identity):
    return types.MessageEmpty(identity, peer_id=None)


def authored_message(identity=1, *, account_id=1, peer=PEER):
    return Message(
        account_id=account_id,
        peer=peer,
        message_id=identity,
        revision=1,
        date=1000,
        text=f"Authored source {identity}",
    )


def rig(tmp_path, answers, *, on_wire=None):
    clock = Clock()
    policy = clock.policy(tmp_path / "policy.sqlite3")
    # Exercise the inherited SDK _call without constructing an SDK client or
    # opening even a synthetic authorization file.
    paced = object.__new__(PacedTelegramClient)
    paced._loop = None
    paced._flood_waited_requests = {}
    paced._no_updates = False
    paced._request_retries = 0
    paced._raise_last_call_error = True
    paced._flood_sleep_threshold = 0
    paced.request_policy = policy
    paced.session = SimpleNamespace(process_entities=lambda result: None)
    connected = [True]
    paced.is_connected = lambda: connected[0]
    calls, witnesses = [], []
    answers = list(answers)

    def append(state):
        calls.append(state.request)
        witness = getattr(state.request, "_enloq_wire", None)
        if witness:
            witnesses.append(witness.events())
        if on_wire:
            on_wire(state.request)

    class Sender:
        def __init__(self):
            self._send_queue = SimpleNamespace(append=append)

        def send(self, request, ordered=False):
            future = asyncio.get_running_loop().create_future()
            state = SimpleNamespace(request=request, future=future)
            self._send_queue.append(state)
            if future.done():
                return future
            answer = answers.pop(0)
            if answer is CORRECTION:
                # Execute Telethon's installed bad-salt handler, which tries
                # to re-enqueue the same pending request state.
                async def correct():
                    handler = object.__new__(MTProtoSender)
                    handler._log = logging.getLogger("authored-deletion-sender")
                    handler._state = SimpleNamespace(salt=0)
                    handler._pending_state = {123: state}
                    handler._send_queue = self._send_queue
                    await handler._handle_bad_server_salt(
                        SimpleNamespace(
                            obj=types.BadServerSalt(
                                bad_msg_id=123,
                                bad_msg_seqno=0,
                                error_code=48,
                                new_server_salt=9,
                            )
                        )
                    )

                asyncio.create_task(correct())
            elif answer is SILENT:
                pass
            elif isinstance(answer, BaseException):
                future.set_exception(answer)
            else:
                future.set_result(answer)
            return future

    sender = Sender()

    class Owner:
        _guarded_deletion = True
        request_policy = policy

        def is_connected(self):
            return connected[0]

        async def __call__(self, request):
            return await paced._call(sender, request)

    transport = object.__new__(PersonalTransport)
    transport.account_id, transport.verified = 1, True
    transport.store = SimpleNamespace(clock=lambda: clock.now, rows=lambda *args: [])
    transport.client = Owner()
    return SimpleNamespace(
        transport=transport,
        clock=clock,
        policy=policy,
        calls=calls,
        witnesses=witnesses,
        answers=answers,
        connected=connected,
    )


def description(identities=(1,)):
    return DeleteDescription("authored-operation", 1, PEER, identities)


@pytest.mark.parametrize(
    "changes",
    [
        {"account_id": True},
        {"account_id": 0},
        {"peer": "chat:42"},
        {"peer": "user:042"},
        {"peer": "user:٤٢"},
        {"operation_id": ""},
        {"identities": ()},
        {"identities": (1, 1)},
        {"identities": (True,)},
        {"identities": (2**31,)},
        {"identities": tuple(range(1, 102))},
    ],
)
def test_captured_chunk_requires_exact_valid_private_membership(changes):
    values = dict(operation_id="authored", account_id=1, peer=PEER, identities=(1,))
    with pytest.raises(Refused):
        DeleteDescription(**(values | changes))


@pytest.mark.parametrize("values", [(-1, 0), (0, -1), (True, 1), (1, False), (1.0, 2)])
def test_native_receipt_rejects_unusable_counters(values):
    with pytest.raises(Refused):
        DeleteReceipt(*values)


def test_reconciliation_requires_disjoint_exhaustive_exact_identities():
    source = authored_message()
    assert DeletionRead(1, PEER, (1, 2, 3), (source,), (2,), (3,)).present == (source,)
    for present, absent, unavailable in (
        ((source,), (1,), (2,)),
        ((source,), (), ()),
        ((authored_message(account_id=2),), (2,), (3,)),
        ((authored_message(peer="user:43"),), (2,), (3,)),
        ((source.model_copy(update={"deleted": True}),), (2,), (3,)),
    ):
        with pytest.raises(Refused):
            DeletionRead(1, PEER, (1, 2, 3), present, absent, unavailable)


async def test_native_for_both_chunk_has_durable_guarded_wire_evidence(tmp_path):
    wire = rig(tmp_path, [types.messages.AffectedMessages(pts=7, pts_count=2)])
    evidence = Evidence(tmp_path / "evidence.sqlite3")
    receipt = await wire.transport.delete(description((1, 2)), evidence)
    assert receipt == DeleteReceipt(7, 2)
    assert len(wire.calls) == 1
    request = wire.calls[0]
    assert isinstance(request, functions.messages.DeleteMessagesRequest)
    assert request.id == [1, 2] and request.revoke is True
    assert not hasattr(request, "peer")
    assert wire.witnesses == [["calling", "watching", "guard", "exposed"]]


@pytest.mark.parametrize(
    "failure,last_event",
    [
        (errors.FloodWaitError(request=None, capture=2), "flood_refused"),
        (
            errors.ServerError(request=None, message="Authored server failure"),
            "exposed",
        ),
        (ConnectionError("Authored acknowledgement lost"), "exposed"),
        (TimeoutError("Authored deadline"), "exposed"),
        (errors.MessageDeleteForbiddenError(request=None), "refused"),
    ],
)
async def test_actual_paced_deletion_never_retries_error(tmp_path, failure, last_event):
    wire = rig(tmp_path, [failure, types.messages.AffectedMessages(8, 1)])
    evidence = Evidence(tmp_path / "evidence.sqlite3")
    with pytest.raises(type(failure)):
        await wire.transport.delete(description(), evidence)
    assert len(wire.calls) == 1 and len(wire.answers) == 1
    assert evidence.events()[-1] == last_event
    # A flood cooldown is retained, without waiting then mutating again.
    if isinstance(failure, errors.FloodWaitError):
        assert wire.policy.due("account") == 1002.5 and wire.clock.waits == []
    with sqlite3.connect(wire.policy.path) as db:
        assert db.execute("SELECT attempt, wire_attempts FROM attempts").fetchall() == [
            (1, 1)
        ]


async def test_actual_sdk_correction_cannot_reenqueue_deletion(tmp_path):
    wire = rig(tmp_path, [CORRECTION])
    evidence = Evidence(tmp_path / "evidence.sqlite3")
    with pytest.raises(MTProtoAttemptLimit, match="cannot be automatically repeated"):
        await wire.transport.delete(description(), evidence)
    assert len(wire.calls) == 1
    assert evidence.events() == ["calling", "watching", "guard", "exposed"]


async def test_failed_flood_narrowing_retains_exposure_evidence(tmp_path):
    wire = rig(tmp_path, [errors.FloodWaitError(None, capture=2)])
    evidence = Evidence(tmp_path / "evidence.sqlite3", fail="flood_refused")
    with pytest.raises(errors.FloodWaitError):
        await wire.transport.delete(description(), evidence)
    assert len(wire.calls) == 1 and evidence.events()[-1] == "exposed"


@pytest.mark.parametrize("phase", ["calling", "watching", "exposed"])
async def test_failed_mandatory_deletion_evidence_blocks_wire(tmp_path, phase):
    wire = rig(tmp_path, [types.messages.AffectedMessages(8, 1)])
    evidence = Evidence(tmp_path / "evidence.sqlite3", fail=phase)
    with pytest.raises(sqlite3.OperationalError):
        await wire.transport.delete(description(), evidence)
    assert wire.calls == [] and len(wire.answers) == 1


async def test_final_source_guard_runs_after_pacing_and_before_wire(tmp_path):
    wire = rig(tmp_path, [types.messages.AffectedMessages(8, 1)])
    eligible = [True]

    def guard():
        if not eligible[0]:
            raise Refused("Authored source revision changed")

    original_sleep = wire.clock.sleep

    async def sleep(seconds):
        await original_sleep(seconds)
        eligible[0] = False

    wire.policy.sleep = sleep
    wire.policy.hold("account", 1005)
    evidence = Evidence(tmp_path / "evidence.sqlite3", guard=guard)
    with pytest.raises(Refused, match="revision changed"):
        await wire.transport.delete(description(), evidence)
    assert wire.clock.waits == [5] and wire.calls == []
    assert evidence.events() == ["calling", "watching", "holding"]
    with sqlite3.connect(wire.policy.path) as db:
        assert db.execute("SELECT wire_attempts FROM attempts").fetchall() == [(0,)]


async def test_final_guard_checks_native_ids_account_and_revoke(tmp_path):
    for index, alter in enumerate(
        [
            lambda request: setattr(request, "id", [2]),
            lambda request: setattr(request, "revoke", False),
            lambda request: setattr(wire.transport, "account_id", 2),
        ]
    ):
        # Alter between SDK preparation and the paced final packer append.
        directory = tmp_path / str(index)
        directory.mkdir()
        wire = rig(directory, [types.messages.AffectedMessages(8, 1)])
        original = wire.transport.client.__class__.__call__

        async def changed(owner, request):
            alter(request)
            return await original(owner, request)

        wire.transport.client.__class__.__call__ = changed
        evidence = Evidence(directory / "evidence.sqlite3")
        with pytest.raises(Refused):
            await wire.transport.delete(description(), evidence)
        assert wire.calls == [] and "exposed" not in evidence.events()


async def test_wrong_account_or_unpaced_owner_is_rejected_before_calling(tmp_path):
    wire = rig(tmp_path, [])
    evidence = Evidence(tmp_path / "evidence.sqlite3")
    with pytest.raises(Refused, match="Wrong connected"):
        await wire.transport.delete(
            DeleteDescription("authored", 2, PEER, (1,)), evidence
        )
    wire.transport.client._guarded_deletion = False
    with pytest.raises(Refused, match="paced session owner"):
        await wire.transport.delete(description(), evidence)
    assert evidence.events() == [] and wire.calls == []


async def test_cancellation_after_wire_stops_without_repeating_mutation(tmp_path):
    handed = asyncio.Event()
    wire = rig(tmp_path, [SILENT], on_wire=lambda request: handed.set())
    evidence = Evidence(tmp_path / "evidence.sqlite3")
    task = asyncio.create_task(wire.transport.delete(description(), evidence))
    await handed.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(wire.calls) == 1 and evidence.events()[-1] == "exposed"


async def test_cancellation_while_held_never_reaches_wire(tmp_path):
    waiting = asyncio.Event()
    wire = rig(tmp_path, [types.messages.AffectedMessages(8, 1)])
    wire.policy.hold("account", 1100)

    async def held(seconds):
        waiting.set()
        await asyncio.Event().wait()

    wire.policy.sleep = held
    evidence = Evidence(tmp_path / "evidence.sqlite3")
    task = asyncio.create_task(wire.transport.delete(description(), evidence))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire.calls == [] and "exposed" not in evidence.events()


@pytest.mark.parametrize(
    "answer",
    [None, SimpleNamespace(pts=1, pts_count=1), types.messages.AffectedMessages(-1, 1)],
)
async def test_unexpected_native_success_is_uncertain_not_a_receipt(tmp_path, answer):
    wire = rig(tmp_path, [answer])
    evidence = Evidence(tmp_path / "evidence.sqlite3")
    with pytest.raises(TimeoutError, match="acknowledgement"):
        await wire.transport.delete(description(), evidence)
    assert len(wire.calls) == 1 and evidence.events()[-1] == "exposed"


@pytest.mark.parametrize(
    "failure",
    [errors.FloodWaitError(None, capture=2), ConnectionError("Authored lost reply")],
)
async def test_sequential_chunk_loop_stops_after_first_unresolved_result(
    tmp_path, failure
):
    wire = rig(
        tmp_path,
        [
            types.messages.AffectedMessages(100, 100),
            failure,
            types.messages.AffectedMessages(205, 5),
        ],
    )
    completed = []
    with pytest.raises(type(failure)):
        for offset in range(0, 205, 100):
            ids = tuple(range(offset + 1, min(offset + 100, 205) + 1))
            evidence = Evidence(tmp_path / f"chunk-{offset}.sqlite3")
            completed.append(await wire.transport.delete(description(ids), evidence))
    assert completed == [DeleteReceipt(100, 100)]
    assert [len(request.id) for request in wire.calls] == [100, 100]
    assert all(request.revoke is True for request in wire.calls)
    assert len(wire.answers) == 1


async def test_exact_recovery_covers_every_batch_and_keeps_read_retries(tmp_path):
    first = [native_empty(identity) for identity in range(1, 101)]
    first[0] = native_message(1)
    first[2] = native_message(3, peer=43)
    first = [native for native in first if native.id != 4]  # omission is unavailable
    first.append(native_empty(5))  # duplicate is also unavailable
    wire = rig(
        tmp_path,
        [
            native_page(first),
            errors.ServerError(None, "Authored bounded read retry"),
            native_page([native_empty(identity) for identity in range(101, 201)]),
            types.messages.MessagesNotModified(5),
        ],
    )
    result = await wire.transport.inspect_deletion(PEER, tuple(range(1, 206)))
    assert result.identities == tuple(range(1, 206))
    assert len(result.present) == 1 and result.present[0].text == "Authored message 1"
    assert result.unavailable == (3, 4, 5, 201, 202, 203, 204, 205)
    assert len(result.absent) == 196
    assert len(wire.calls) == 4
    requested = [tuple(item.id for item in request.id) for request in wire.calls]
    assert requested == [
        tuple(range(1, 101)),
        tuple(range(101, 201)),
        tuple(range(101, 201)),
        tuple(range(201, 206)),
    ]
    assert all(
        isinstance(request, functions.messages.GetMessagesRequest)
        for request in wire.calls
    )


@pytest.mark.parametrize(
    "response",
    [
        native_page([]),
        native_page([native_empty(9)]),
        native_page([types.MessageEmpty(1, peer_id=types.PeerUser(43))]),
        SimpleNamespace(messages=[native_empty(1)]),
        native_page([native_message(1, peer=43)]),
        native_page(
            [
                types.Message(
                    id=1, peer_id=types.PeerUser(42), date=None, message="Authored"
                )
            ]
        ),
    ],
)
async def test_unusable_or_wrong_peer_read_never_becomes_absence(tmp_path, response):
    wire = rig(tmp_path, [response])
    checked = await wire.transport.inspect_deletion(PEER, (1,))
    assert (
        checked.unavailable == (1,) and checked.absent == () and checked.present == ()
    )


async def test_exact_read_failure_continues_covering_later_batches(tmp_path):
    wire = rig(
        tmp_path,
        [
            errors.MessageIdInvalidError(None),
            native_page([native_empty(101)]),
        ],
    )
    result = await wire.transport.inspect_deletion(PEER, tuple(range(1, 102)))
    assert result.unavailable == tuple(range(1, 101)) and result.absent == (101,)
    assert len(wire.calls) == 2


async def test_offline_recovery_marks_all_ids_unavailable_without_wire(tmp_path):
    wire = rig(tmp_path, [])
    wire.connected[0] = False
    result = await wire.transport.inspect_deletion(PEER, tuple(range(1, 206)))
    assert result.unavailable == tuple(range(1, 206)) and result.absent == ()
    assert wire.calls == []


async def test_independent_checks_get_fresh_exact_native_results(tmp_path):
    wire = rig(
        tmp_path,
        [native_page([native_message(1)]), native_page([native_empty(1)])],
    )
    first = await wire.transport.inspect_deletion(PEER, (1,))
    second = await wire.transport.inspect_deletion(PEER, (1,))
    assert len(first.present) == 1 and second.absent == (1,)
    assert len(wire.calls) == 2


async def test_fake_lost_acknowledgement_and_readback_never_fabricate_receipt(tmp_path):
    fake = FakeTransport()
    fake.messages = [authored_message(1), authored_message(2, peer="user:43")]
    fake.lose_delete_receipt = True
    evidence = Evidence(tmp_path / "evidence.sqlite3")
    with pytest.raises(TimeoutError):
        await fake.delete(description(), evidence)
    read = await fake.inspect_deletion(PEER, tuple(range(1, 206)))
    assert read.absent == (1,) + tuple(range(3, 206)) and not read.present
    assert read.unavailable == (2,)
    assert len(fake.deletion_calls) == 1
    assert len(fake.deletion_reads) == 3
    assert fake.messages == [authored_message(2, peer="user:43")]
    assert not isinstance(read, DeleteReceipt)
