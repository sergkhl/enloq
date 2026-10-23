"""Exercise the installed SDK and pacing wrapper with synthetic wire responses."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from enloq import setup
from enloq.transport.mtproto import (
    MTProtoAttemptLimit,
    MTProtoRecoveryPaused,
    RequestPolicy,
)
from telethon import errors
from telethon.tl import functions, types

PHONE = "12025550123"


class Sender:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []
        self._send_queue = SimpleNamespace(append=lambda state: None)

    def send(self, request, ordered=False):
        self.requests.append(request)
        future = asyncio.get_running_loop().create_future()
        self._send_queue.append(SimpleNamespace(request=request, future=future))
        if not future.done():
            response = next(self.responses)
            if isinstance(response, Exception):
                future.set_exception(response)
            else:
                future.set_result(response)
        return future


@pytest.fixture
async def transport(tmp_path):
    client = setup.PersonalLoginClient(
        str(tmp_path / "fixture.session"), 1, "fixture", receive_updates=False
    )
    original_sender = client._sender
    state = SimpleNamespace(now=1000.0, waits=[], switches=[], destination=None)

    async def sleep(seconds):
        state.waits.append(seconds)
        state.now += seconds

    client.request_policy = RequestPolicy(
        tmp_path / "policy.sqlite3",
        clock=lambda: state.now,
        sleep=sleep,
        jitter=lambda: 0.5,
    )
    client._authorized = False
    client.session.set_dc(2, "127.0.0.1", 443)

    async def switch_dc(dc):
        state.switches.append(dc)
        client._sender = state.destination

    client._switch_dc = switch_dc
    try:
        yield client, state
    finally:
        client._sender = original_sender
        await client.disconnect()
        client.session.close()


@pytest.mark.parametrize(
    "error_type", [errors.PhoneMigrateError, errors.NetworkMigrateError]
)
async def test_code_login_follows_one_redirect_with_same_request_and_new_sender(
    tmp_path, error_type
):
    client = setup.PersonalLoginClient(
        str(tmp_path / "fixture.session"), 1, "fixture", receive_updates=False
    )
    original_sender = client._sender
    target_dc = 3
    response = types.auth.SentCode(
        type=types.auth.SentCodeTypeApp(length=5), phone_code_hash="synthetic"
    )
    first = Sender(error_type(request=None, capture=target_dc))
    second = Sender(response)
    client._sender = first
    client._authorized = False
    switches = []

    async def switch_dc(dc):
        switches.append(dc)
        client._sender = second

    client._switch_dc = switch_dc
    try:
        assert await client.send_code_request(PHONE) is response
        assert switches == [target_dc]
        assert len(first.requests) == len(second.requests) == 1
        before, after = first.requests[0], second.requests[0]
        assert isinstance(before, functions.InvokeWithoutUpdatesRequest)
        assert before.query is after.query
        assert isinstance(before.query, functions.auth.SendCodeRequest)
        with sqlite3.connect(client.request_policy.path) as db:
            assert db.execute(
                "SELECT error,wire_attempts FROM attempts ORDER BY id"
            ).fetchall() == [(error_type.__name__, 1), (None, 1)]
    finally:
        client._sender = original_sender
        await client.disconnect()
        client.session.close()


async def test_repeated_login_redirect_stops_after_one_handoff(transport):
    client, state = transport
    first = Sender(errors.PhoneMigrateError(None, capture=3))
    second = Sender(errors.PhoneMigrateError(None, capture=4))
    client._sender, state.destination = first, second
    with pytest.raises(errors.PhoneMigrateError):
        await client.send_code_request(PHONE)
    assert state.switches == [3]
    assert len(first.requests) == len(second.requests) == 1


@pytest.mark.parametrize("authorized,new_dc", [(True, 3), (False, 0), (False, 2)])
async def test_authorized_account_or_invalid_destination_does_not_move(
    transport, authorized, new_dc
):
    client, state = transport
    client._authorized = authorized
    sender = Sender(errors.PhoneMigrateError(None, capture=new_dc))
    client._sender = sender
    with pytest.raises(errors.PhoneMigrateError):
        await client.send_code_request(PHONE)
    assert state.switches == [] and len(sender.requests) == 1


@pytest.mark.parametrize(
    "error", [ConnectionError(), TimeoutError(), errors.ServerError(None, "INTERNAL")]
)
async def test_uncertain_code_request_is_never_redirected_or_retried(transport, error):
    client, state = transport
    sender = Sender(error)
    client._sender = sender
    with pytest.raises(type(error)):
        await client.send_code_request(PHONE)
    assert state.switches == [] and len(sender.requests) == 1
    assert state.waits == []


async def test_handoff_failure_does_not_send_the_code_request_again(transport):
    client, state = transport
    sender = Sender(errors.PhoneMigrateError(None, capture=3))
    client._sender = sender

    async def failed_switch(dc):
        state.switches.append(dc)
        raise TimeoutError()

    client._switch_dc = failed_switch
    with pytest.raises(TimeoutError):
        await client.send_code_request(PHONE)
    assert state.switches == [3] and len(sender.requests) == 1


async def test_login_redirect_cannot_replay_chat_writes(transport):
    client, state = transport
    sender = Sender(errors.PhoneMigrateError(None, capture=3))
    client._sender = sender
    request = functions.messages.SendMessageRequest(
        types.InputPeerSelf(), "synthetic", random_id=1
    )
    with pytest.raises(errors.PhoneMigrateError):
        await client(request)
    assert state.switches == [] and len(sender.requests) == 1


async def test_explicit_resend_can_follow_redirect_without_restarting_code_login(
    transport,
):
    client, state = transport
    first = Sender(errors.PhoneMigrateError(None, capture=3))
    response = types.auth.SentCode(types.auth.SentCodeTypeSms(5), "synthetic-next")
    second = Sender(response)
    client._sender, state.destination = first, second
    request = functions.auth.ResendCodeRequest(PHONE, "synthetic-first")
    assert await client(request) is response
    assert state.switches == [3]
    assert first.requests[0].query is second.requests[0].query is request
    assert request.phone_code_hash == "synthetic-first"


async def test_redirect_and_flood_retries_share_six_wire_attempts(transport):
    client, state = transport
    first = Sender(
        errors.FloodWaitError(None, capture=1),
        errors.FloodWaitError(None, capture=1),
        errors.PhoneMigrateError(None, capture=3),
    )
    second = Sender(*(errors.FloodWaitError(None, capture=1) for _ in range(6)))
    client._sender, state.destination = first, second
    with pytest.raises(errors.FloodWaitError):
        await client.send_code_request(PHONE)
    assert state.switches == [3]
    assert len(first.requests) == len(second.requests) == 3
    assert all(item.query is first.requests[0].query for item in second.requests)
    with sqlite3.connect(client.request_policy.path) as db:
        assert db.execute("SELECT sum(wire_attempts) FROM attempts").fetchone()[0] == 6


async def test_exhausted_wire_budget_stops_before_handoff(transport):
    client, state = transport
    first = Sender(
        *(errors.FloodWaitError(None, capture=1) for _ in range(5)),
        errors.PhoneMigrateError(None, capture=3),
    )
    client._sender = first
    with pytest.raises(MTProtoAttemptLimit):
        await client.send_code_request(PHONE)
    assert state.switches == [] and len(first.requests) == 6


async def test_redirect_does_not_reset_recovery_wait_allowance(transport):
    client, state = transport
    first = Sender(
        errors.FloodWaitError(None, capture=600),
        errors.PhoneMigrateError(None, capture=3),
    )
    second = Sender(errors.FloodWaitError(None, capture=600))
    client._sender, state.destination = first, second
    with pytest.raises(MTProtoRecoveryPaused):
        await client.send_code_request(PHONE)
    assert state.switches == [3] and state.waits == [600.5]
    assert len(first.requests) == 2 and len(second.requests) == 1
    assert client.request_policy.due("account") == 2201.0


async def test_login_connection_does_not_start_reconnect_or_history_read(
    transport, monkeypatch
):
    client, _ = transport
    disconnected = asyncio.get_running_loop().create_future()
    calls = []

    async def connect(_):
        calls.append("connect")

    async def catch_up():
        calls.append("catch_up")

    from telethon import TelegramClient

    monkeypatch.setattr(TelegramClient, "connect", connect)
    monkeypatch.setattr(type(client), "disconnected", property(lambda _: disconnected))
    client.catch_up = catch_up
    await client.connect()
    await asyncio.sleep(0)
    disconnected.set_result(None)
    await client._owner_reconnect
    assert calls == ["connect"]
