# Behavior tests adapted from hercules-support-relay, the author's earlier private project; see docs/extraction.md.
import asyncio
import logging
import sqlite3
import struct
from types import SimpleNamespace

import pytest
from enloq.transport.mtproto import (
    MTProtoAttemptLimit,
    MTProtoRecoveryPaused,
    PacedTelegramClient,
    RequestPolicy,
    _Attempts,
    _BoundedPacker,
    observation_cycle,
)
from telethon import TelegramClient, errors
from telethon.errors import AuthKeyNotFound, InvalidBufferError, InvalidChecksumError
from telethon.tl import functions, types


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)

    def policy(self, path):
        return RequestPolicy(
            path, clock=lambda: self.now, sleep=self.sleep, jitter=lambda: 0.5
        )


def history(peer=7):
    return functions.messages.GetHistoryRequest(
        types.InputPeerChannel(peer, 1), 0, None, 0, 10, 0, 0, 0
    )


def send():
    return functions.messages.SendMessageRequest(
        types.InputPeerChannel(7, 1), "disposable", random_id=12345
    )


def test_reads_are_spaced_across_peers_and_restart_without_spending_wait_attempts(
    tmp_path,
):
    clock = Clock()
    path = tmp_path / "policy.sqlite3"
    starts = []

    async def run():
        async def read():
            starts.append(clock.now)
            return "one"

        await clock.policy(path).execute(history(), read)
        await clock.policy(path).execute(history(8), read)

    asyncio.run(run())
    assert starts == [1000, 1001]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 2
        assert db.execute("SELECT kind FROM waits").fetchone()[0] == "pacing"
    assert path.stat().st_mode & 0o777 == 0o600


def test_only_one_background_read_is_in_flight(tmp_path):
    async def run():
        clock = Clock()
        policy = clock.policy(tmp_path / "policy.sqlite3")
        entered, release = asyncio.Event(), asyncio.Event()
        running = peak = 0

        async def read():
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            entered.set()
            await release.wait()
            running -= 1
            return True

        first = asyncio.create_task(policy.execute(history(), read))
        await entered.wait()
        second = asyncio.create_task(policy.execute(history(8), read))
        await asyncio.sleep(0)
        assert running == 1
        release.set()
        await asyncio.gather(first, second)
        assert peak == 1

    asyncio.run(run())


def test_six_safe_attempts_preserve_first_failure_and_same_request(tmp_path):
    clock = Clock()
    path = tmp_path / "policy.sqlite3"
    policy = clock.policy(path)
    request, seen = history(), []

    async def work():
        seen.append(request)
        raise errors.ServerError(request, "INTERNAL")

    with pytest.raises(errors.ServerError):
        asyncio.run(policy.execute(request, work))
    assert len(seen) == 6 and all(item is request for item in seen)
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT attempt,error,wire_attempts FROM attempts ORDER BY id"
        ).fetchall()
    assert rows == [(n, "ServerError", 1) for n in range(1, 7)]


def test_flood_refusal_retries_same_random_id_but_unknown_send_never_retries(tmp_path):
    clock = Clock()
    policy = clock.policy(tmp_path / "policy.sqlite3")
    request, seen = send(), []

    async def write():
        seen.append(request.random_id)
        if len(seen) < 3:
            raise errors.FloodWaitError(request=request, capture=2)
        return 19

    assert asyncio.run(policy.execute(request, write)) == 19
    assert seen == [12345] * 3
    assert clock.waits == [2.5, 2.5]

    async def unknown():
        seen.append(request.random_id)
        raise ConnectionError("receipt missing")

    with pytest.raises(ConnectionError):
        asyncio.run(policy.execute(request, unknown))
    assert seen == [12345] * 4


def test_long_flood_cooldown_survives_restart_and_applies_to_other_methods(tmp_path):
    clock = Clock()
    path = tmp_path / "policy.sqlite3"
    policy = clock.policy(path)
    calls = 0

    async def flood():
        nonlocal calls
        calls += 1
        raise errors.FloodWaitError(request=history(), capture=7200)

    with pytest.raises(MTProtoRecoveryPaused):
        asyncio.run(policy.execute(history(), flood))
    restarted = clock.policy(path)
    with pytest.raises(MTProtoRecoveryPaused):
        asyncio.run(restarted.execute(send(), flood))
    assert calls == 1 and restarted.due("account") == 8200.5
    clock.now = 8200.5

    async def fresh():
        return True

    assert asyncio.run(restarted.execute(history(8), fresh)) is True


def test_slow_mode_only_holds_its_known_peer(tmp_path):
    clock = Clock()
    policy = clock.policy(tmp_path / "policy.sqlite3")

    async def flood():
        raise errors.SlowModeWaitError(request=history(), capture=7200)

    with pytest.raises(MTProtoRecoveryPaused):
        asyncio.run(policy.execute(history(), flood))

    async def okay():
        return True

    assert asyncio.run(policy.execute(history(8), okay)) is True
    assert policy.due("account") == 0


def test_read_cache_is_bounded_to_one_fresh_cycle_and_writes_invalidate_it(tmp_path):
    clock = Clock()
    policy = clock.policy(tmp_path / "policy.sqlite3")
    calls = 0

    async def read():
        nonlocal calls
        calls += 1
        return calls

    async def run():
        with observation_cycle():
            assert await policy.execute(history(), read) == 1
            assert await policy.execute(history(), read) == 1
            await policy.execute(send(), read)
            assert await policy.execute(history(), read) == 3
        with observation_cycle():
            assert await policy.execute(history(), read) == 4

    asyncio.run(run())
    assert calls == 4


def test_protocol_requeues_share_the_six_attempt_limit():
    async def run():
        accepted = []
        packer = _BoundedPacker(SimpleNamespace(append=accepted.append))
        request = send()
        request._enloq_attempts = _Attempts()
        state = SimpleNamespace(
            request=request, future=asyncio.get_running_loop().create_future()
        )
        for _ in range(7):
            packer.append(state)
        assert len(accepted) == 6 and request.random_id == 12345
        with pytest.raises(MTProtoAttemptLimit):
            await state.future

    asyncio.run(run())


def test_installed_sdk_adds_no_retry_sleep_or_fresh_send(tmp_path):
    async def run():
        clock = Clock()
        client = PacedTelegramClient(str(tmp_path / "fixture.session"), 1, "fixture")
        client.request_policy = clock.policy(tmp_path / "policy.sqlite3")
        assert client._request_retries == client._connection_retries == 0
        assert client.flood_sleep_threshold == 0 and client._auto_reconnect is False
        assert client._catch_up is True
        request, seen = send(), []

        class Sender:
            def __init__(self):
                self._send_queue = SimpleNamespace(append=lambda state: None)

            def send(self, item, ordered=False):
                seen.append(item)
                future = asyncio.get_running_loop().create_future()
                self._send_queue.append(SimpleNamespace(request=item, future=future))
                future.set_exception(errors.FloodWaitError(request=item, capture=1))
                return future

        try:
            with pytest.raises(errors.FloodWaitError):
                await client._call(Sender(), request)
            assert len(seen) == 6 and all(item is request for item in seen)
            assert clock.waits == [1.5] * 5
        finally:
            await client.disconnect()

    asyncio.run(run())


def reconnect_rig(tmp_path, monkeypatch, failures=()):
    """A PacedTelegramClient whose SDK connect/disconnect are recorded; the
    n-th reconnect raises failures[n] when given."""
    clock = Clock()
    client = PacedTelegramClient(str(tmp_path / "fixture.session"), 1, "fixture")
    client.request_policy = clock.policy(tmp_path / "policy.sqlite3")
    state = SimpleNamespace(future=None, connects=0, saved=0, catchups=0)
    failures = list(failures)

    async def connect(_):
        state.connects += 1
        state.future = asyncio.get_running_loop().create_future()
        if state.connects > 1 and failures:
            failure = failures.pop(0)
            if failure:
                raise failure

    async def disconnect(_):
        state.saved += 1

    async def catchup():
        state.catchups += 1

    monkeypatch.setattr(TelegramClient, "connect", connect)
    monkeypatch.setattr(TelegramClient, "disconnect", disconnect)
    monkeypatch.setattr(
        PacedTelegramClient, "disconnected", property(lambda _: state.future)
    )
    client.catch_up = catchup
    return client, clock, state


async def settle(client):
    for _ in range(50):
        if client._owner_reconnect.done():
            return
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "ending,backoff",
    [
        (lambda: None, 2),  # Telethon resolves a failed keepalive with no error
        (lambda: ConnectionError("authored: dropped"), 2),
        # Telegram closed the connection cleanly.
        (lambda: asyncio.IncompleteReadError(b"", 8), 2),
        (lambda: InvalidBufferError(b"\x00"), 2),
        # Telegram's transport-level flood waits the longest backoff.
        (lambda: InvalidBufferError(struct.pack("<i", -429)), 300),
    ],
)
def test_reconnect_owner_saves_cursors_and_catches_up_however_the_connection_ended(
    tmp_path, monkeypatch, ending, backoff
):
    async def run():
        client, clock, state = reconnect_rig(tmp_path, monkeypatch)
        await client.connect()
        await asyncio.sleep(0)
        error = ending()
        if error is None:
            state.future.set_result(None)
        else:
            state.future.set_exception(error)
        for _ in range(20):
            await asyncio.sleep(0)
            if state.catchups:
                break
        assert state.connects == 2 and state.saved == 1 and state.catchups == 1
        assert clock.waits == [backoff] and not client._owner_reconnect.done()
        await client.disconnect()
        client.session.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "closed",
    [
        lambda: asyncio.IncompleteReadError(b"", 8),
        lambda: InvalidBufferError(b"\x00"),
    ],
)
def test_a_connection_closed_during_a_reconnect_attempt_is_retried(
    tmp_path, monkeypatch, closed
):
    async def run():
        client, clock, state = reconnect_rig(tmp_path, monkeypatch, [closed()])
        await client.connect()
        await asyncio.sleep(0)
        state.future.set_exception(ConnectionError("authored: dropped"))
        for _ in range(40):
            await asyncio.sleep(0)
            if state.catchups:
                break
        assert state.connects == 3 and state.catchups == 1 and clock.waits == [2, 4]
        await client.disconnect()
        client.session.close()

    asyncio.run(run())


@pytest.mark.parametrize("during_reconnect", [False, True])
def test_reconnect_owner_stops_when_telegram_does_not_know_the_key(
    tmp_path, monkeypatch, caplog, during_reconnect
):
    async def run():
        failures = [AuthKeyNotFound()] if during_reconnect else []
        client, clock, state = reconnect_rig(tmp_path, monkeypatch, failures)
        await client.connect()
        await asyncio.sleep(0)
        state.future.set_exception(
            ConnectionError("authored: dropped")
            if during_reconnect
            else AuthKeyNotFound()
        )
        await settle(client)
        # No retry loop: the account waits for Reconnect.
        assert client._owner_reconnect.done() and state.catchups == 0
        assert state.connects == (2 if during_reconnect else 1)
        await client.disconnect()
        client.session.close()

    with caplog.at_level(logging.ERROR, logger="enloq.mtproto"):
        asyncio.run(run())
    assert [r.getMessage() for r in caplog.records] == ["mtproto_auth_key_unknown"]


def flood():
    return InvalidBufferError(struct.pack("<i", -429))


@pytest.mark.parametrize(
    "ending,failures,waits",
    [
        # Every try after a transport flood waits five minutes.
        (flood, [ConnectionError("authored: still down")], [300, 300]),
        # So does every try after one refused with a transport flood.
        (lambda: ConnectionError("authored: dropped"), [flood()], [2, 300]),
    ],
)
def test_transport_flood_waits_are_the_reconnect_owners_own(
    tmp_path, monkeypatch, ending, failures, waits
):
    async def run():
        client, clock, state = reconnect_rig(tmp_path, monkeypatch, failures)
        await client.connect()
        await asyncio.sleep(0)
        state.future.set_exception(ending())
        for _ in range(40):
            await asyncio.sleep(0)
            if state.catchups:
                break
        assert state.catchups == 1 and clock.waits == waits
        # A Reconnect or a restart does not wait them out: the account holds
        # at most the ordinary two-second first backoff.
        assert client.request_policy.due("account") <= 1000 + 2
        await client.disconnect()
        client.session.close()

    asyncio.run(run())


def test_long_network_outage_recovers_with_capped_backoff(tmp_path, monkeypatch):
    async def run():
        failures = [ConnectionError("authored: still down")] * 12
        client, clock, state = reconnect_rig(tmp_path, monkeypatch, failures)
        await client.connect()
        await asyncio.sleep(0)
        state.future.set_exception(asyncio.IncompleteReadError(b"", 8))
        for _ in range(200):
            await asyncio.sleep(0)
            if state.catchups:
                break
        assert client.reconnecting and state.connects == 14 and state.catchups == 1
        assert clock.waits == [2, 4, 8, 16, 32, 64, 128, 256] + [300] * 5
        # A later, separate outage starts with the short delay again.
        state.future.set_result(None)
        for _ in range(30):
            await asyncio.sleep(0)
            if state.catchups == 2:
                break
        assert state.catchups == 2 and clock.waits[-1] == 2
        await client.disconnect()
        assert not client.reconnecting
        client.session.close()

    asyncio.run(run())


@pytest.mark.parametrize("during_reconnect", [False, True])
@pytest.mark.parametrize(
    "error",
    [errors.SessionRevokedError(None), errors.AuthKeyDuplicatedError(None)],
)
def test_reconnect_stops_for_revoked_authorization(
    tmp_path, monkeypatch, during_reconnect, error
):
    async def run():
        client, _, state = reconnect_rig(
            tmp_path, monkeypatch, [error] if during_reconnect else []
        )
        await client.connect()
        await asyncio.sleep(0)
        state.future.set_exception(
            ConnectionError("authored") if during_reconnect else error
        )
        await settle(client)
        assert not client.reconnecting and state.catchups == 0
        assert state.connects == (2 if during_reconnect else 1)
        assert client._updates_error is error
        await client.disconnect()
        client.session.close()

    asyncio.run(run())


def test_reconnect_preserves_a_long_flood_hold(tmp_path, monkeypatch):
    async def run():
        client, clock, state = reconnect_rig(tmp_path, monkeypatch)
        await client.connect()
        await asyncio.sleep(0)
        client.request_policy.hold("account", clock.now + 1800)
        state.future.set_result(None)
        for _ in range(30):
            await asyncio.sleep(0)
            if state.catchups:
                break
        assert state.catchups == 1 and clock.waits == [1800]
        await client.disconnect()
        client.session.close()

    asyncio.run(run())


def test_reconnect_observes_a_flood_hold_extended_while_asleep(tmp_path, monkeypatch):
    async def run():
        client, clock, state = reconnect_rig(tmp_path, monkeypatch)

        async def sleep(seconds):
            await clock.sleep(seconds)
            if len(clock.waits) == 1:
                client.request_policy.hold("account", clock.now + 1800)

        client.request_policy.sleep = sleep
        await client.connect()
        state.future.set_result(None)
        for _ in range(30):
            await asyncio.sleep(0)
            if state.catchups:
                break
        assert state.catchups == 1 and clock.waits == [2, 1800]
        await client.disconnect()
        client.session.close()

    asyncio.run(run())


def test_stop_cancels_a_reconnect_wait_without_another_connection(
    tmp_path, monkeypatch
):
    async def run():
        client, _, state = reconnect_rig(tmp_path, monkeypatch)
        entered = asyncio.Event()

        async def wait(_):
            entered.set()
            await asyncio.Event().wait()

        client.request_policy.sleep = wait
        await client.connect()
        state.future.set_result(None)
        async with asyncio.timeout(1):
            await entered.wait()
            await client.disconnect()
        assert state.connects == 1 and not client.reconnecting
        client.session.close()

    asyncio.run(run())


def test_unexpected_reconnect_failure_requires_intervention(tmp_path, monkeypatch):
    async def run():
        client, _, state = reconnect_rig(
            tmp_path, monkeypatch, [ValueError("authored")]
        )
        await client.connect()
        state.future.set_result(None)
        await settle(client)
        assert not client.reconnecting and state.connects == 2
        await client.disconnect()
        client.session.close()

    asyncio.run(run())


def test_reconnect_owner_pauses_while_incoming_handlers_are_unfinished(
    tmp_path, monkeypatch, caplog
):
    from enloq.transport import mtproto

    monkeypatch.setattr(mtproto, "REQUEST_TIMEOUT", 0.01)

    async def run():
        client, clock, state = reconnect_rig(tmp_path, monkeypatch)
        await client.connect()
        stuck = asyncio.create_task(asyncio.Event().wait())
        client._event_handler_tasks.add(stuck)
        state.future.set_exception(asyncio.IncompleteReadError(b"", 8))
        for _ in range(200):
            await asyncio.sleep(0.001)
            if client._owner_reconnect.done():
                break
        assert client._owner_reconnect.done() and state.connects == 1
        stuck.cancel()
        client._event_handler_tasks.discard(stuck)
        await client.disconnect()
        client.session.close()

    with caplog.at_level(logging.ERROR, logger="enloq.mtproto"):
        asyncio.run(run())
    assert [r.getMessage() for r in caplog.records] == ["mtproto_recovery_paused"]


def answered(tmp_path, error):
    """A client and a disconnected sender that fails its request with `error`,
    as Telethon fails pending requests when a connection ends."""
    clock = Clock()
    client = PacedTelegramClient(str(tmp_path / "fixture.session"), 1, "fixture")
    client.request_policy = clock.policy(tmp_path / "policy.sqlite3")

    class Sender:
        def __init__(self):
            self._send_queue = SimpleNamespace(append=lambda state: None)

        def send(self, item, ordered=False):
            future = asyncio.get_running_loop().create_future()
            self._send_queue.append(SimpleNamespace(request=item, future=future))
            future.set_exception(error)
            return future

    return client, Sender()


@pytest.mark.parametrize(
    "ending",
    [
        lambda: asyncio.IncompleteReadError(b"", 8),
        lambda: InvalidBufferError(b"\x00"),
        lambda: InvalidChecksumError(1, 2),
    ],
)
def test_a_request_failed_by_a_closed_connection_reads_as_a_lost_connection(
    tmp_path, ending
):
    async def run():
        client, sender = answered(tmp_path, ending())
        try:
            with pytest.raises(ConnectionError) as lost:
                await client._call(sender, history())
            assert type(lost.value.__cause__) is type(ending())
        finally:
            await client.disconnect()

    asyncio.run(run())


def test_a_drop_error_while_connected_is_passed_on_unchanged(tmp_path, monkeypatch):
    # Only a request failed by the connection ending reads as a lost connection.
    async def run():
        client, sender = answered(tmp_path, asyncio.IncompleteReadError(b"", 8))
        monkeypatch.setattr(client, "is_connected", lambda: True)
        try:
            with pytest.raises(asyncio.IncompleteReadError):
                await client._call(sender, history())
        finally:
            monkeypatch.undo()
            await client.disconnect()

    asyncio.run(run())


def test_requests_never_wait_out_the_reconnect_owners_flood_wait(tmp_path):
    async def run():
        clock = Clock()
        policy = clock.policy(tmp_path / "policy.sqlite3")
        policy.hold("reconnect", clock.now + 300)
        # A restart reads the same durable waits.
        restarted = clock.policy(tmp_path / "policy.sqlite3")
        assert restarted.due("account", "reconnect") == clock.now + 300

        async def answer():
            return "authored"

        for current in (policy, restarted):
            assert await current.execute(history(), answer) == "authored"
            assert await current.execute(send(), answer) == "authored"
        assert all(wait < 300 for wait in clock.waits)

    asyncio.run(run())


def test_a_forgotten_key_is_not_read_as_a_lost_connection(tmp_path):
    async def run():
        client, sender = answered(tmp_path, AuthKeyNotFound())
        try:
            with pytest.raises(AuthKeyNotFound):
                await client._call(sender, history())
        finally:
            await client.disconnect()

    asyncio.run(run())


class ClosingConnection:
    """A fake socket that ends with `ending` once a request has been written."""

    def __init__(self, ending):
        self.ending, self.sent = ending, asyncio.Event()

    async def connect(self, timeout=None):
        pass

    async def disconnect(self):
        pass

    async def send(self, data):
        self.sent.set()

    async def recv(self):
        await self.sent.wait()
        if self.ending is None:
            await asyncio.Event().wait()
        raise self.ending


@pytest.mark.parametrize(
    "ending",
    [
        lambda: asyncio.IncompleteReadError(b"", 8),
        lambda: InvalidBufferError(b"\x00"),
        lambda: InvalidChecksumError(1, 2),
        flood,
        lambda: ConnectionResetError("authored"),
    ],
)
def test_a_close_during_telethons_difference_request_leaves_the_owner_reconnecting(
    tmp_path, monkeypatch, ending
):
    """Telethon's own update loop has getDifference in flight when the
    connection ends; it must not disconnect the client for good."""
    import os

    from telethon._updates import SessionState

    reconnects = []

    async def reconnect(self):
        reconnects.append(1)
        await self._sender.connect(ClosingConnection(None))

    async def run():
        clock = Clock()
        client = PacedTelegramClient(
            str(tmp_path / "fixture.session"),
            1,
            "fixture",
            catch_up=False,
            sequential_updates=True,
        )
        client.request_policy = clock.policy(tmp_path / "policy.sqlite3")
        client._loop = asyncio.get_running_loop()
        client._sender.auth_key.key = os.urandom(256)  # synthetic, never sent
        await client._sender.connect(ClosingConnection(ending()))
        client._message_box.load(
            SessionState(0, 0, False, 10, 0, 1_700_000_000, 0, None), []
        )
        monkeypatch.setattr(TelegramClient, "connect", reconnect)
        client._updates_handle = asyncio.create_task(client._update_loop())
        client._owner_reconnect = asyncio.create_task(client._reconnect_owner())
        await client.catch_up()  # the update loop sends getDifference
        for _ in range(3000):
            await asyncio.sleep(0)
            if reconnects and client.is_connected():
                break
        assert reconnects and client.is_connected()
        assert client._updates_error is None and not client._owner_reconnect.done()
        await client.disconnect()
        client.session.close()

    asyncio.run(asyncio.wait_for(run(), 30))


def test_session_ownership_is_exclusive_across_processes_and_releases_on_exit(tmp_path):
    import subprocess
    import sys

    from enloq.storage.lock import OwnerLock

    path = tmp_path / "personal.owner.lock"
    code = "from pathlib import Path; from enloq.storage.lock import OwnerLock; from enloq.domain.models import Refused; import sys\ntry:\n OwnerLock(Path(sys.argv[1])).acquire()\nexcept Refused:\n sys.exit(7)"
    with OwnerLock(path):
        assert (
            subprocess.run(
                [sys.executable, "-c", code, str(path)], capture_output=True
            ).returncode
            == 7
        )
    assert (
        subprocess.run(
            [sys.executable, "-c", code, str(path)], capture_output=True
        ).returncode
        == 0
    )


async def test_bounded_sync_continuation_and_source_replay(tmp_path):
    from enloq.application.sync import Sync
    from enloq.domain.models import Chat, Message
    from enloq.storage.store import Store
    from enloq.transport.fake import FakeTransport

    store = Store(tmp_path / "db.sqlite3", account_id=1, clock=lambda: 1000)
    store.put_chat(
        Chat(
            account_id=1,
            peer="user:2",
            title="Synthetic",
            kind="private",
            ai_eligible=True,
            archived=True,
            muted=True,
        )
    )
    transport = FakeTransport()
    transport.messages = [
        Message(
            account_id=1,
            peer="user:2",
            message_id=i,
            revision=1,
            date=1000,
            text=f"Authored {i}",
        )
        for i in range(1, 603)
    ]
    sync = Sync(store, transport)
    progress = await sync.scan("1/user:2")
    assert progress["count"] == 100 and not progress["complete"]
    restarted = Sync(Store(store.path, account_id=1, clock=lambda: 1000), transport)
    for _ in range(6):
        progress = await restarted.scan("1/user:2")
    assert progress["count"] == 602 and progress["complete"]
    assert len(store.rows("SELECT key FROM messages")) == 602
    assert not store.rows("SELECT id FROM jobs")
    sync.ingest(transport.messages[-1])
    assert len(store.rows("SELECT key FROM messages")) == 602
    sync.deleted("user:2", [602])
    assert len(store.rows("SELECT key FROM messages WHERE deleted=1")) == 1


def test_personal_session_never_loads_source_or_starts_background_login(tmp_path):
    from enloq.config import Settings
    from enloq.domain.models import Refused
    from enloq.storage.store import Store
    from enloq.transport.telethon import PersonalTransport

    store = Store(tmp_path / "db.sqlite3", account_id=1, mode="personal")
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "EXPECTED_ACCOUNT_ID": "1",
            "PERSONAL_SESSION_REF": str(tmp_path / "missing.session"),
        },
    )
    with pytest.raises(Refused, match="existing personal session"):
        PersonalTransport(
            settings,
            store,
            client_factory=lambda *args, **kwargs: pytest.fail(
                "constructed before guards"
            ),
        )
    with pytest.raises(Refused):
        PersonalTransport(Settings(data=tmp_path), store)


@pytest.mark.parametrize(
    "authorized,identity,bot", [(False, 1, False), (True, 9, False), (True, 1, True)]
)
async def test_revoked_or_wrong_connected_account_disconnects_and_releases_lock(
    tmp_path, authorized, identity, bot
):
    from enloq.config import Settings, private_write
    from enloq.domain.models import Refused
    from enloq.storage.lock import OwnerLock
    from enloq.storage.store import Store
    from enloq.transport.telethon import PersonalTransport

    session = tmp_path / "personal.session"
    private_write(session, "synthetic placeholder")
    private_write(tmp_path / "id", "1")
    private_write(tmp_path / "hash", "authored-not-a-secret")
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "EXPECTED_ACCOUNT_ID": "1",
            "PERSONAL_SESSION_REF": str(session),
            "TELEGRAM_API_ID_REF": str(tmp_path / "id"),
            "TELEGRAM_API_HASH_REF": str(tmp_path / "hash"),
        },
    )

    class Client:
        session = SimpleNamespace(close=lambda: None)
        closed = False

        def __init__(self, *args, **kwargs):
            pass

        def add_event_handler(self, *args):
            pass

        async def connect(self):
            pass

        async def is_user_authorized(self):
            return authorized

        async def get_me(self):
            return SimpleNamespace(id=identity, bot=bot)

        async def disconnect(self):
            self.closed = True

    store = Store(tmp_path / "db.sqlite3", account_id=1, mode="personal")
    transport = PersonalTransport(settings, store, client_factory=Client)
    with pytest.raises(Refused):
        await transport.connect(None)
    assert transport.client.closed
    assert transport.requires_login and not transport.verified
    with OwnerLock(transport.lock.path):
        pass


async def test_application_gap_cursor_does_not_advance_on_failed_ingest(tmp_path):
    import json
    from datetime import UTC, datetime

    from enloq.domain.models import Message
    from enloq.storage.store import Store
    from enloq.transport.telethon import PersonalTransport

    store = Store(tmp_path / "db.sqlite3", account_id=1)
    baseline = {"pts": 4, "qts": 0, "date": 1000}
    with store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('global_difference',?)",
            (json.dumps(baseline),),
        )

    class Client:
        async def __call__(self, request):
            return types.updates.Difference(
                new_messages=[
                    types.Message(
                        id=1,
                        peer_id=types.PeerUser(2),
                        date=datetime.now(UTC),
                        message="Synthetic",
                    )
                ],
                new_encrypted_messages=[],
                other_updates=[],
                chats=[],
                users=[],
                state=types.updates.State(
                    pts=8, qts=0, date=datetime.now(UTC), seq=0, unread_count=0
                ),
            )

    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id, transport.client = store, 1, Client()
    transport.bind_receipts(store.send_operations.record_receipts)

    async def normalized(_):
        return Message(account_id=1, peer="user:2", message_id=1, date=1000, revision=1)

    transport.normalize = normalized
    from enloq.domain.models import Unavailable

    with pytest.raises(Unavailable):
        await transport.catch_up()
    assert (
        json.loads(
            store.rows("SELECT value FROM cursors WHERE key='global_difference'")[0][
                "value"
            ]
        )
        == baseline
    )


async def test_A12_sender_flag_is_omitted_for_dm_and_account_verified_for_supergroup():
    from enloq.domain.models import Refused
    from enloq.transport.sender import account_sender

    class Client:
        offered = 1

        async def __call__(self, request):
            assert isinstance(request, functions.channels.GetSendAsRequest)
            return SimpleNamespace(
                peers=[SimpleNamespace(peer=types.PeerUser(self.offered))]
            )

        async def get_me(self):
            return types.User(id=1, access_hash=42, is_self=True)

    client = Client()
    assert await account_sender(client, types.InputPeerUser(2, 0), 1) is None
    assert await account_sender(client, types.InputPeerChat(2), 1) is None
    sender = await account_sender(client, types.InputPeerChannel(2, 0), 1)
    assert isinstance(sender, types.InputPeerUser) and sender.user_id == 1
    client.offered = 3
    with pytest.raises(Refused):
        await account_sender(client, types.InputPeerChannel(2, 0), 1)


async def test_native_preview_download_uses_cached_photo_without_fetching_url(tmp_path):
    from enloq.transport.telethon import PersonalTransport

    photo = object()
    requested = []

    class Client:
        def is_connected(self):
            return True

        async def get_messages(self, peer, ids):
            return SimpleNamespace(media=object(), photo=photo, document=None)

        async def iter_parts(self, asset, msg_data=None):
            requested.append(asset)
            yield b"bounded-native-photo"

    transport = PersonalTransport.__new__(PersonalTransport)
    transport.client = Client()
    transport.verified = True
    path = tmp_path / "media"
    path.touch(mode=0o600)
    await transport.download("user:2", 1, path, max_bytes=50)
    assert requested == [photo] and path.read_bytes() == b"bounded-native-photo"


async def test_file_parts_have_a_priority_lane_without_read_pacing(tmp_path):
    from enloq.transport.mtproto import (
        FILE_PRIORITY,
        PLAYBACK,
        PREFETCH,
        PREPARATION,
    )

    clock = Clock()
    policy = clock.policy(tmp_path / "policy.sqlite3")
    order, gate = [], asyncio.Event()

    def part(offset):
        return functions.upload.GetFileRequest(
            types.InputDocumentFileLocation(1, 1, b"", ""), offset, 1024 * 1024
        )

    async def perform(label, wait=False):
        if wait:
            await gate.wait()
        order.append(label)
        return label

    async def request(label, priority, wait=False):
        FILE_PRIORITY.set(priority)
        return await policy.execute(part(0), lambda: perform(label, wait))

    holder = asyncio.create_task(request("held", PREPARATION, wait=True))
    await asyncio.sleep(0)
    prefetch = asyncio.create_task(request("prefetch", PREFETCH))
    await asyncio.sleep(0)
    playback = asyncio.create_task(request("playback", PLAYBACK))
    await asyncio.sleep(0)
    # Message reads keep their own paced lane while a file part is in flight.
    assert await policy.execute(history(), lambda: perform("history")) == "history"
    gate.set()
    await asyncio.gather(holder, prefetch, playback)
    assert order == ["history", "held", "playback", "prefetch"]
    assert not [w for w in clock.waits if w >= 1]  # No one-second file gap.


async def test_installed_sdk_iterator_requests_one_mebibyte_aligned_parts(tmp_path):
    from enloq.transport.mtproto import FILE_PART

    client = PacedTelegramClient(str(tmp_path / "parts.session"), 1, "authored")
    total = 2 * FILE_PART + 5
    offsets = []

    async def call(sender, request, ordered=False, flood_sleep_threshold=None):
        assert isinstance(request, functions.upload.GetFileRequest)
        assert request.limit == FILE_PART
        offsets.append(request.offset)
        size = min(FILE_PART, total - request.offset)
        return types.upload.File(
            type=types.storage.FileUnknown(), mtime=0, bytes=b"x" * size
        )

    client._call = call
    document = types.Document(
        id=1,
        access_hash=1,
        file_reference=b"",
        date=None,
        mime_type="audio/ogg",
        size=total,
        dc_id=0,
        attributes=[],
    )
    chunks = [chunk async for chunk in client.iter_parts(document)]
    assert offsets == [0, FILE_PART, 2 * FILE_PART]
    assert sum(map(len, chunks)) == total
    client.session.close()


def native_message(identity, peer, *, out=False, from_id=None, post=None):
    from datetime import UTC, datetime

    return types.Message(
        id=identity,
        peer_id=peer,
        from_id=from_id,
        out=out,
        post=post,
        date=datetime(2026, 9, 1, 12, tzinfo=UTC),
        message=f"Authored synthetic text {identity}",
    )


def entityless_transport():
    from enloq.transport.telethon import PersonalTransport

    # No client or store: reply-less pages need no request and no stored parents.
    transport = object.__new__(PersonalTransport)
    transport.account_id = 1
    return transport


async def test_private_dm_without_from_id_uses_dialog_or_account_and_explicit_from_id_wins():
    dialog, other = types.PeerUser(42), types.PeerUser(77)
    values = [
        native_message(1, dialog),
        native_message(2, dialog, out=True),
        native_message(3, dialog, from_id=other),
        native_message(4, dialog, out=True, from_id=other),
    ]
    # No sender entity is cached, so labels come from the identity alone.
    assert all(m.sender is None for m in values)
    actual = await entityless_transport().normalize_page(values)
    assert [(m.message_id, m.author_peer, m.outgoing, m.author) for m in actual] == [
        (1, "user:42", False, "user:42"),
        (2, "user:1", True, "You"),
        (3, "user:77", False, "user:77"),
        (4, "user:77", True, "You"),
    ]


async def test_history_without_from_id_matches_the_live_update_sender():
    # Live updates carry from_id; history pages may omit it. A stored message
    # keeps its first author, so both encodings must name the same sender.
    dialog = types.PeerUser(42)
    transport = entityless_transport()
    for out, sender in ((False, dialog), (True, types.PeerUser(1))):
        history, live = await transport.normalize_page(
            [
                native_message(1, dialog, out=out),
                native_message(1, dialog, out=out, from_id=sender),
            ]
        )
        assert history.author_peer == live.author_peer == f"user:{sender.user_id}"


async def test_group_and_channel_messages_without_from_id_keep_author_peer_unknown():
    group, channel = types.PeerChat(5), types.PeerChannel(6)
    values = [
        native_message(5, group),
        native_message(6, group, out=True),
        native_message(7, channel, post=True),
        native_message(8, channel, out=True, post=True),
    ]
    actual = await entityless_transport().normalize_page(values)
    assert [(m.message_id, m.author_peer, m.outgoing, m.author) for m in actual] == [
        (5, None, False, "Unknown participant"),
        (6, None, True, "Unknown participant"),
        (7, None, False, "Unknown participant"),
        (8, None, True, "Unknown participant"),
    ]


def gap_transport(tmp_path, respond, *, clock=None):
    from enloq.storage.store import Store
    from enloq.transport.telethon import PersonalTransport

    store = Store(tmp_path / "db.sqlite3", account_id=1, clock=clock or time_now)
    requests = []

    class Client:
        async def __call__(self, request):
            requests.append(type(request).__name__)
            return respond(request)

    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id, transport.client = store, 1, Client()
    transport.bind_receipts(store.send_operations.record_receipts)
    return transport, store, requests


def time_now():
    return 2_000_000_000


def cursors(store):
    return {r["key"]: r["value"] for r in store.rows("SELECT key,value FROM cursors")}


def seed_cursors(store):
    import json

    with store.transaction() as db:
        for key, value in {
            "global_difference": json.dumps({"pts": 4, "qts": 1, "date": 1000}),
            "dialogs": json.dumps({"complete": True}),
            "recent:1/user:4": json.dumps({"before": 0, "complete": True}),
            "coverage:1/user:4": "10",
            "coverage:1/user:5": "20",
            "receipt:77": "7",
            "refusal:78": "authored",
            "older:1/user:4": "{}",
            "run:1/user:4": "{}",
        }.items():
            db.execute("INSERT INTO cursors VALUES (?,?)", (key, value))


async def test_difference_too_long_raises_gap_and_changes_nothing(tmp_path):
    from enloq.domain.models import GapTooLong, Unavailable

    transport, store, requests = gap_transport(
        tmp_path, lambda _: types.updates.DifferenceTooLong(pts=50)
    )
    seed_cursors(store)
    before = cursors(store)
    with pytest.raises(GapTooLong) as refused:
        await transport.catch_up()
    assert isinstance(refused.value, Unavailable)
    assert cursors(store) == before
    assert requests == ["GetDifferenceRequest"]


async def test_restart_after_gap_takes_latest_state_and_reopens_bounded_scans(
    tmp_path,
):
    import json
    from datetime import UTC, datetime

    latest = datetime(2026, 9, 30, 4, tzinfo=UTC)
    transport, store, requests = gap_transport(
        tmp_path,
        lambda _: types.updates.State(
            pts=90, qts=3, date=latest, seq=0, unread_count=0
        ),
    )
    seed_cursors(store)
    before = cursors(store)
    await transport.restart_after_gap()
    after = cursors(store)
    assert json.loads(after.pop("global_difference")) == {
        "pts": 90,
        "qts": 3,
        "date": latest.timestamp(),
    }
    # Discovery, seven-day progress and every coverage floor start over; send
    # receipts, refusals and processing runs are kept.
    assert after == {
        k: v
        for k, v in before.items()
        if k not in {"global_difference", "dialogs"}
        and not k.startswith(("recent:", "coverage:"))
    }
    assert requests == ["GetStateRequest"]


@pytest.mark.parametrize("failure", ["request", "write"])
async def test_restart_after_gap_failure_changes_nothing(tmp_path, failure):
    def respond(_):
        if failure == "request":
            raise OSError("authored")
        return types.updates.State(pts=90, qts=3, date=None, seq=0, unread_count=0)

    transport, store, _ = gap_transport(tmp_path, respond)
    seed_cursors(store)
    before = cursors(store)
    with pytest.raises((OSError, AttributeError)):
        await transport.restart_after_gap()
    assert cursors(store) == before


def identity_transport(tmp_path, me):
    from enloq.storage.store import Store
    from enloq.transport.telethon import PersonalTransport

    now = [2_000_000_000]
    store = Store(tmp_path / "db.sqlite3", account_id=1, clock=lambda: now[0])
    calls = []

    class Client:
        def is_connected(self):
            return True

        async def get_me(self):
            calls.append("get_me")
            if isinstance(me, BaseException):
                raise me
            return me

        async def __call__(self, request):
            calls.append(type(request).__name__)

    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id, transport.client = store, 1, Client()
    transport.bind_receipts(store.send_operations.record_receipts)
    transport.identity = SimpleNamespace(id=1, bot=False)
    transport.identity_checked_at = now[0]
    transport.requires_login, transport.verified = False, True
    now[0] += 300
    return transport, calls


@pytest.mark.parametrize(
    "me", [None, SimpleNamespace(id=9, bot=False), SimpleNamespace(id=1, bot=True)]
)
async def test_identity_recheck_withdrawal_requires_login_and_stops_sends(tmp_path, me):
    from enloq.domain.models import Refused, Unavailable

    transport, calls = identity_transport(tmp_path, me)
    assert transport.connected
    with pytest.raises(Refused):
        await transport.discover()
    assert transport.requires_login and not transport.verified
    assert not transport.connected
    with pytest.raises(Unavailable):
        await transport.send(
            None, None
        )  # identity is rejected before using a description
    assert calls == ["get_me"]


@pytest.mark.parametrize(
    "failure", [OSError("authored"), errors.FloodWaitError(request=None, capture=5)]
)
async def test_failed_identity_recheck_request_changes_nothing(tmp_path, failure):
    transport, _ = identity_transport(tmp_path, failure)
    with pytest.raises(type(failure)):
        await transport.discover()
    assert not transport.requires_login and transport.verified


async def test_live_updates_need_a_verified_identity(tmp_path):
    from enloq.domain.models import Chat

    transport, _ = identity_transport(tmp_path, None)
    transport.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    received = []
    transport.sync = SimpleNamespace(
        ingest=lambda message, **options: received.append(
            ("ingest", message.message_id)
        ),
        deleted=lambda peer, ids: received.append(("deleted", tuple(ids))),
    )
    incoming = SimpleNamespace(message=native_message(1, types.PeerUser(42)))
    deleted = SimpleNamespace(peer=types.PeerUser(42), deleted_ids=[1])
    await transport._incoming(incoming)
    await transport._deleted(deleted)
    assert received == [("ingest", 1), ("deleted", (1,))]
    # Before connect verifies the identity, or after a recheck withdraws it,
    # updates are left to the gap cursor.
    received.clear()
    transport.verified = False
    await transport._incoming(incoming)
    await transport._deleted(deleted)
    assert received == []


def dialog_transport(tmp_path, count, *, missing=(), empty=()):
    """Telethon's real iter_dialogs against an authored DialogsSlice server."""
    from datetime import UTC, datetime

    from enloq.storage.store import Store
    from enloq.transport.telethon import PersonalTransport
    from telethon.client.dialogs import DialogMethods

    base = datetime(2026, 9, 29, tzinfo=UTC).timestamp()
    users = [
        types.User(id=1000 + i, first_name="Authored", access_hash=1)
        for i in range(count)
    ]

    def top(i):
        if i in missing:
            return None
        if i in empty:
            return types.MessageEmpty(id=5000 - i, peer_id=types.PeerUser(1000 + i))
        return types.Message(
            id=5000 - i,
            peer_id=types.PeerUser(1000 + i),
            date=datetime.fromtimestamp(base - i, UTC),
            message="Authored",
        )

    messages = [top(i) for i in range(count)]
    dialogs = [
        types.Dialog(
            peer=types.PeerUser(1000 + i),
            top_message=0 if i in missing else 5000 - i,
            read_inbox_max_id=0,
            read_outbox_max_id=0,
            unread_count=0,
            unread_mentions_count=0,
            unread_reactions_count=0,
            unread_poll_votes_count=0,
            notify_settings=types.PeerNotifySettings(),
        )
        for i in range(count)
    ]
    requests = []

    class Client:
        _mb_entity_cache = SimpleNamespace(extend=lambda *a: None)
        _message_box = SimpleNamespace(try_set_channel_state=lambda *a: None)
        _self_id = 1
        iter_dialogs = DialogMethods.iter_dialogs

        async def __call__(self, request):
            after = getattr(request.offset_peer, "user_id", None)
            first = 0 if not after else after - 1000 + 1
            chunk = range(first, min(count, first + request.limit))
            requests.append((request.limit, len(chunk)))
            return types.messages.DialogsSlice(
                count=count,
                dialogs=[dialogs[i] for i in chunk],
                messages=[messages[i] for i in chunk if messages[i]],
                chats=[],
                users=[users[i] for i in chunk],
            )

    store = Store(tmp_path / "db.sqlite3", account_id=1)
    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id, transport.client = store, 1, Client()
    transport.bind_receipts(store.send_operations.record_receipts)
    return transport, store, requests


async def test_discover_walks_every_dialog_despite_messageless_tails(tmp_path):
    # Telethon ends a resumed page early when its chunk ends in dialogs without
    # a top message; a capped page must still take a full 100 dialogs.
    transport, store, _ = dialog_transport(tmp_path, 400, missing={199})
    pages = [await transport.discover() for _ in range(4)]
    assert [p["complete"] for p in pages] == [False, False, False, True]
    # Resumed pages skip the message-less dialog, as Telethon does.
    assert len(store.rows("SELECT key FROM chats")) == 399


async def test_discover_resumes_from_the_last_dated_top_message(tmp_path):
    # The 100th dialog of page one lacks a message and the 99th has an empty
    # one; the walk resumes from the 98th instead of restarting forever.
    transport, store, _ = dialog_transport(tmp_path, 150, missing={99}, empty={98})
    first = await transport.discover()
    assert not first["complete"] and first["peer"] == "user:1097"
    second = await transport.discover()
    assert second["complete"]
    # Page one keeps both dialogs; only resumed pages skip them, as Telethon does.
    assert len(store.rows("SELECT key FROM chats")) == 150


def refresh_transport(tmp_path, peers, *, unresolvable=(), refused=(), gone=()):
    from enloq.domain.models import Chat
    from enloq.storage.store import Store
    from enloq.transport.telethon import PersonalTransport

    store = Store(tmp_path / "db.sqlite3", account_id=1)
    for index, peer in enumerate(peers):
        store.put_chat(
            Chat(
                account_id=1,
                peer=peer,
                title="Authored",
                kind="private",
                read_through=50 if index == 0 else 0,
            )
        )
    batches = []

    class Client:
        async def get_input_entity(self, peer):
            if f"user:{peer.user_id}" in unresolvable:
                raise ValueError("authored: not in the entity cache")
            return types.InputPeerUser(peer.user_id, 0)

        async def __call__(self, request):
            assert isinstance(request, functions.messages.GetPeerDialogsRequest)
            batches.append(len(request.peers))
            if any(f"user:{p.peer.user_id}" in refused for p in request.peers):
                raise errors.PeerIdInvalidError(request=None)
            return SimpleNamespace(
                dialogs=[
                    SimpleNamespace(
                        peer=types.PeerUser(p.peer.user_id), read_inbox_max_id=20
                    )
                    for p in request.peers
                    if f"user:{p.peer.user_id}" not in gone
                ]
            )

    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id, transport.client = store, 1, Client()
    transport.bind_receipts(store.send_operations.record_receipts)
    return transport, store, batches


def opened(store):
    import json

    return {
        r["key"][len("recent:1/") :]: json.loads(r["value"])
        for r in store.rows("SELECT key,value FROM cursors WHERE key LIKE 'recent:%'")
    }


async def test_refresh_read_positions_batches_and_opens_catch_up(tmp_path):
    peers = [f"user:{100 + i}" for i in range(150)]
    transport, store, batches = refresh_transport(
        tmp_path, peers, unresolvable={"user:249"}
    )
    await transport.refresh_read_positions(peers)
    # The unresolvable chat is skipped without holding back the others.
    assert batches == [100, 49]
    assert store.chat("1/user:101").read_through == 20
    # A position never moves backwards.
    assert store.chat("1/user:100").read_through == 50
    assert set(opened(store)) == set(peers) - {"user:249"}
    assert opened(store)["user:101"] == {"before": 0, "complete": False}


async def test_refresh_read_positions_isolates_a_refused_peer(tmp_path):
    peers = ["user:100", "user:101", "user:102"]
    transport, store, batches = refresh_transport(tmp_path, peers, refused={"user:101"})
    await transport.refresh_read_positions(peers)
    assert batches == [3, 1, 1, 1]
    assert set(opened(store)) == {"user:100", "user:102"}


@pytest.mark.parametrize(
    "error,lost",
    [
        (errors.AuthKeyDuplicatedError(request=None), True),
        (errors.AuthKeyUnregisteredError(request=None), True),
        (ValueError("authored"), False),
        (None, False),
    ],
)
def test_update_loop_authorization_error_requires_login(tmp_path, error, lost):
    from enloq.domain.models import Refused

    transport, _ = identity_transport(tmp_path, None)
    transport.client._updates_error = error
    if lost:
        with pytest.raises(Refused):
            transport.ensure_authorized()
    else:
        transport.ensure_authorized()
    assert transport.requires_login is lost and transport.verified is not lost


async def test_refresh_closes_chats_without_a_dialog_and_asks_once(tmp_path):
    peers = ["user:100", "user:101"]
    transport, store, batches = refresh_transport(tmp_path, peers, gone={"user:101"})
    await transport.refresh_read_positions(peers)
    # Telegram answered without a dialog: nothing is left to catch up.
    assert opened(store) == {
        "user:100": {"before": 0, "complete": False},
        "user:101": {"before": 0, "complete": True},
    }
    assert store.chat("1/user:101").read_through == 0
    # A lone refused peer is not asked for twice.
    transport, store, batches = refresh_transport(
        tmp_path / "b", ["user:100"], refused={"user:100"}
    )
    await transport.refresh_read_positions(["user:100"])
    assert batches == [1] and opened(store) == {}


class RecordingWitness:
    """The request policy's view of a send's hand-off ledger."""

    def __init__(self):
        self.log = []

    def watching(self):
        self.log.append("watched")

    def holding(self, until):
        self.log.append("holding")

    def handing_off(self):
        self.log.append("exposed")

    def not_handed(self):
        self.log.append("not_handed")

    def flood_refused(self, error, until):
        self.log.append(("flood_refused", error))


def handoff_client(tmp_path, outcomes, *, sleep=None, disconnected=False):
    """A PacedTelegramClient whose sender appends to the real _BoundedPacker and
    then answers each transmission from `outcomes` (an exception, a value, or
    None to never answer)."""
    clock = Clock()
    client = PacedTelegramClient(str(tmp_path / "fixture.session"), 1, "fixture")
    policy = clock.policy(tmp_path / "policy.sqlite3")
    if sleep:
        policy.sleep = sleep
    client.request_policy = policy
    appended = []

    class Sender:
        def __init__(self):
            self._send_queue = SimpleNamespace(append=appended.append)

        def send(self, item, ordered=False):
            if disconnected:
                raise ConnectionError("Cannot send requests while disconnected")
            future = asyncio.get_running_loop().create_future()
            outcome = outcomes.pop(0) if outcomes else None
            for _ in range(outcome[1] if isinstance(outcome, tuple) else 1):
                self._send_queue.append(SimpleNamespace(request=item, future=future))
            outcome = outcome[0] if isinstance(outcome, tuple) else outcome
            if isinstance(outcome, BaseException):
                future.set_exception(outcome)
            elif outcome is not None:
                future.set_result(outcome)
            return future

    return client, clock, policy, Sender(), appended


def witnessed():
    request = send()
    request._enloq_wire = RecordingWitness()
    return request, request._enloq_wire


def test_a_hold_cancelled_by_the_send_deadline_hands_nothing_off(tmp_path):
    async def run():
        async def forever(seconds):
            await asyncio.Event().wait()

        client, clock, policy, sender, appended = handoff_client(
            tmp_path, [19], sleep=forever
        )
        policy.hold("account", clock.now + 120)
        request, witness = witnessed()
        try:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await client._call(sender, request)
            await asyncio.sleep(0.05)
            assert appended == [] and witness.log == ["watched", "holding"]
        finally:
            await client.disconnect()

    asyncio.run(run())


def test_a_hold_beyond_the_allowance_pauses_before_any_handoff(tmp_path):
    async def run():
        client, clock, policy, sender, appended = handoff_client(tmp_path, [19])
        policy.hold("account", clock.now + 7200)
        request, witness = witnessed()
        try:
            with pytest.raises(MTProtoRecoveryPaused):
                await client._call(sender, request)
            assert appended == [] and witness.log == ["watched", "holding"]
        finally:
            await client.disconnect()

    asyncio.run(run())


def test_single_transmission_floods_are_credited(tmp_path):
    async def run():
        flood = errors.FloodWaitError(request=None, capture=2)
        long = errors.FloodWaitError(request=None, capture=7200)
        client, clock, policy, sender, appended = handoff_client(
            tmp_path, [flood, long]
        )
        request, witness = witnessed()
        try:
            with pytest.raises(MTProtoRecoveryPaused):
                await client._call(sender, request)
            assert len(appended) == 2
            assert witness.log == [
                "watched",
                "exposed",
                ("flood_refused", "FloodWaitError"),
                "holding",
                "exposed",
                ("flood_refused", "FloodWaitError"),
                "holding",
            ]
        finally:
            await client.disconnect()

    asyncio.run(run())


@pytest.mark.parametrize(
    "outcome",
    [
        # A protocol re-queue appended the request twice before the flood.
        (errors.FloodWaitError(request=None, capture=7200), 2),
        errors.RandomIdDuplicateError(request=None),
        errors.PeerFloodError(request=None),
        ConnectionError("authored: connection lost after writing"),
        None,  # never answered: the request timeout expires after the hand-off
    ],
)
def test_outcomes_after_a_handoff_stay_exposed(tmp_path, monkeypatch, outcome):
    from enloq.transport import mtproto

    monkeypatch.setattr(mtproto, "REQUEST_TIMEOUT", 0.05)

    async def run():
        client, _, _, sender, appended = handoff_client(tmp_path, [outcome])
        request, witness = witnessed()
        try:
            with pytest.raises(BaseException):
                await client._call(sender, request)
            # "holding" only annotates the last entry of the real ledger.
            entries = [e for e in witness.log if e != "holding"]
            assert appended and entries[-1] == "exposed"
        finally:
            await client.disconnect()

    asyncio.run(run())


def test_a_synchronous_disconnect_is_not_handed_off(tmp_path):
    async def run():
        client, _, _, sender, appended = handoff_client(tmp_path, [], disconnected=True)
        request, witness = witnessed()
        try:
            with pytest.raises(ConnectionError):
                await client._call(sender, request)
            assert appended == []
            assert witness.log == ["watched", "exposed", "not_handed"]
        finally:
            await client.disconnect()

    asyncio.run(run())


def test_a_failed_handoff_record_stops_the_handoff(tmp_path):
    class Unsaved(RecordingWitness):
        def handing_off(self):
            raise OSError("authored: record not saved")

    async def run():
        client, _, _, sender, appended = handoff_client(tmp_path, [19])
        request = send()
        request._enloq_wire = witness = Unsaved()
        try:
            with pytest.raises(OSError):
                await client._call(sender, request)
            assert appended == [] and witness.log == ["watched"]
        finally:
            await client.disconnect()

    asyncio.run(run())


def test_installed_sender_queues_nothing_while_disconnected(tmp_path):
    # ADR 17 relies on Telethon 1.44.0 raising before it queues anything.
    client = PacedTelegramClient(str(tmp_path / "fixture.session"), 1, "fixture")
    sender, request = client._sender, send()
    try:
        assert isinstance(sender._send_queue, _BoundedPacker)
        with pytest.raises(ConnectionError):
            sender.send(request)
        assert not sender._send_queue._packer._deque
        assert not hasattr(request, "_enloq_attempts")
    finally:
        client.session.close()


def test_installed_sender_counts_requeues_and_requeues_nothing_after_a_drop(tmp_path):
    # ADR 17 relies on every Telethon 1.44.0 re-queue passing the counting
    # wrapper, and on a dropped connection re-queuing nothing without
    # automatic reconnection.
    from telethon.network.requeststate import RequestState

    async def run():
        client = PacedTelegramClient(str(tmp_path / "fixture.session"), 1, "fixture")
        sender, request = client._sender, send()
        request._enloq_attempts = _Attempts(used=1)
        state = RequestState(request)
        state.msg_id = 5
        sender._pending_state[5] = state
        await sender._handle_bad_server_salt(
            SimpleNamespace(obj=SimpleNamespace(bad_msg_id=5, new_server_salt=1))
        )
        assert list(sender._send_queue._packer._deque) == [state]
        assert request._enloq_attempts.used == 2
        sender._send_queue._packer._deque.clear()
        # So does a bad message notification that asks for a resend.
        sender._pending_state[5] = state
        await sender._handle_bad_notification(
            SimpleNamespace(msg_id=7, obj=SimpleNamespace(bad_msg_id=5, error_code=32))
        )
        assert list(sender._send_queue._packer._deque) == [state]
        assert request._enloq_attempts.used == 3
        sender._send_queue._packer._deque.clear()
        state.msg_id = 6
        sender._pending_state[6] = state

        class Connection:
            async def disconnect(self):
                pass

        sender._connection, sender._user_connected = Connection(), True
        try:
            await sender._reconnect(ConnectionError("authored: dropped"))
            assert not sender._send_queue._packer._deque
            assert request._enloq_attempts.used == 3
            with pytest.raises(ConnectionError):
                await state.future
        finally:
            client.session.close()

    asyncio.run(run())
