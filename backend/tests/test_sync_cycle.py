"""The per-account sync cycle: steps fail on their own, a too-long gap restarts
from the latest state, and a withdrawn identity stops the account."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from enloq.application.runtime import (
    connection_status,
    personal_sync,
    sync_cycle,
)
from enloq.domain.models import (
    Chat,
    GapTooLong,
    Message,
    Refused,
    Unavailable,
)
from enloq.storage.store import WINDOW
from enloq.transport.mtproto import MTProtoRecoveryPaused
from telethon import errors

from backend.tests.test_rebuild import fixture


def rig(tmp_path, failing=None):
    """Two recent chats (user:42 most active, then user:43) and one inactive."""
    app = fixture(tmp_path)
    now = app.store.clock()
    for peer, activity in (
        ("user:42", now),
        ("user:43", now - 60),
        ("user:44", now - WINDOW - 60),
    ):
        app.store.put_chat(
            Chat(
                account_id=1,
                peer=peer,
                title="Authored conversation",
                kind="private",
                ai_eligible=True,
            ),
            activity=activity,
        )
    transport = app.transport
    transport.calls, transport.requires_login = [], False
    transport.identity = SimpleNamespace(id=1)
    transport.messages = [
        Message(
            account_id=1,
            peer=peer,
            message_id=identity,
            revision=1,
            date=now - 600 + identity,
            text="Authored message.",
        )
        for peer, identity in (("user:42", 1), ("user:43", 2), ("user:44", 3))
    ]
    failing = failing or {}
    history = transport.history

    async def discover():
        transport.calls.append("discover")
        if "discover" in failing:
            raise failing["discover"]

    async def catch_up():
        transport.calls.append("catch_up")
        if "catch_up" in failing:
            raise failing.pop("catch_up")

    async def restart_after_gap():
        transport.calls.append("restart")

    async def paged(peer, before, limit):
        transport.calls.append(peer)
        if peer in failing:
            raise failing[peer]
        return await history(peer, before, limit)

    transport.discover, transport.catch_up = discover, catch_up
    transport.restart_after_gap, transport.history = restart_after_gap, paged
    return app


def progress(app, peer):
    rows = app.store.rows(
        "SELECT value FROM cursors WHERE key=?", (f"recent:1/{peer}",)
    )
    return json.loads(rows[0]["value"]) if rows else None


async def test_failing_chat_does_not_starve_later_chats(tmp_path):
    app = rig(tmp_path, {"user:42": ValueError("authored failure")})
    assert await sync_cycle(app) is False
    assert app.transport.calls == ["discover", "catch_up", "user:42", "user:43"]
    assert progress(app, "user:42") is None
    assert progress(app, "user:43")["complete"]
    assert app.store.message("1/user:43", 2)


@pytest.mark.parametrize(
    "error",
    [
        MTProtoRecoveryPaused("authored"),
        ConnectionError("authored"),
        TimeoutError(),
        errors.FloodWaitError(request=None, capture=5),
        errors.SessionExpiredError(request=None),
        errors.ServerError(None, "authored"),
        errors.TimedOutError(None, "authored"),
        errors.AuthKeyError(None, "authored"),
    ],
)
async def test_account_wide_failure_ends_the_cycle(tmp_path, error):
    app = rig(tmp_path, {"user:42": error})
    assert await sync_cycle(app) is False
    assert "user:43" not in app.transport.calls


async def test_failed_discovery_or_lost_connection_stops_the_cycle(tmp_path):
    app = rig(tmp_path, {"discover": OSError("authored")})
    assert await sync_cycle(app) is False
    assert app.transport.calls == ["discover"]
    app = rig(tmp_path / "b", {"user:42": ValueError("authored")})
    app.transport.connected = False
    assert await sync_cycle(app) is False
    assert "user:43" not in app.transport.calls


@pytest.mark.parametrize(
    "error",
    [
        Unavailable("Catch-up checkpointed; further bounded continuation required"),
        Unavailable("Private dialog metadata unavailable; discover before retry"),
    ],
)
async def test_scans_run_when_catch_up_is_checkpointed_or_stalled(tmp_path, error):
    app = rig(tmp_path, {"catch_up": error})
    assert await sync_cycle(app) is False
    assert app.transport.calls == ["discover", "catch_up", "user:42", "user:43"]
    # The next cycle, with catch-up finished, completes.
    assert await sync_cycle(app) is True


async def test_gap_restart_defers_scans_to_the_next_cycle(tmp_path):
    app = rig(tmp_path, {"catch_up": GapTooLong("authored")})
    assert await sync_cycle(app) is False
    assert app.transport.calls == ["discover", "catch_up", "restart"]
    app.transport.calls.clear()
    assert await sync_cycle(app) is True
    assert app.transport.calls == ["discover", "catch_up", "user:42", "user:43"]


async def test_unexpected_error_outside_steps_keeps_the_loop_alive(tmp_path):
    app = rig(tmp_path)

    def broken():
        raise sqlite3.OperationalError("authored")

    app.store.recent_chats = broken
    assert await sync_cycle(app) is False


async def test_identity_withdrawal_propagates_but_catch_up_failure_does_not(
    tmp_path,
):
    app = rig(tmp_path)

    async def withdrawn():
        app.transport.requires_login = True
        raise Refused("Connected Telegram identity mismatch")

    app.transport.discover = withdrawn
    with pytest.raises(Refused):
        await sync_cycle(app)
    app = rig(tmp_path / "b", {"catch_up": Unavailable("authored")})
    assert await sync_cycle(app) is False


async def test_personal_sync_identity_mismatch_stops_like_connect(tmp_path):
    app = rig(tmp_path)
    transport = app.transport
    closed, unauthorized = [], []

    async def connect(sync):
        transport.calls.append("connect")

    async def withdrawn():
        transport.requires_login = True
        raise Refused("Connected Telegram identity mismatch")

    async def close():
        closed.append(True)

    transport.connect, transport.discover, transport.close = connect, withdrawn, close
    async with asyncio.timeout(5):
        await personal_sync(app, on_unauthorized=lambda: unauthorized.append(True))
    assert unauthorized == [True] and closed == [True]
    assert app.status == "Sign in again"


async def test_service_started_without_network_keeps_retrying_and_then_syncs(
    tmp_path, monkeypatch
):
    from enloq.application import runtime

    app = rig(tmp_path)
    transport = app.transport
    transport.connected = False
    attempts, waits, connected, closed = [], [], [], []

    async def connect(sync):
        attempts.append(True)
        if len(attempts) <= 10:
            raise ConnectionError("authored startup outage")
        transport.connected = True

    async def sleep(delay):
        if delay == 30:
            # One complete sync cycle has run after the network returned.
            raise asyncio.CancelledError
        waits.append(delay)
        assert connection_status(app) == "Reconnecting"

    async def close():
        closed.append(True)

    transport.connect, transport.close = connect, close
    monkeypatch.setattr(runtime.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await personal_sync(app, on_connected=lambda user: connected.append(user.id))
    assert len(attempts) == 11
    assert waits == [2, 4, 8, 16, 32, 64, 128, 256, 300, 300]
    assert connected and set(connected) == {1} and closed == [True]
    assert app.store.message("1/user:43", 2)
    assert app.status == "Connected"


def test_status_distinguishes_a_reconnecting_owner_from_stopped_recovery(tmp_path):
    app = rig(tmp_path)
    app.status = "Sync incomplete; retrying bounded recovery"
    app.transport.connected = False
    app.transport.reconnecting = True
    assert connection_status(app) == "Reconnecting"
    app.transport.reconnecting = False
    assert connection_status(app) == "Reconnect required"
    app.transport.requires_login = True
    assert connection_status(app) == "Sign in again"


async def test_non_network_startup_error_does_not_retry(tmp_path):
    app = rig(tmp_path)
    calls = []

    async def connect(sync):
        calls.append("connect")
        raise ValueError("authored configuration error")

    async def close():
        calls.append("close")

    app.transport.connect, app.transport.close = connect, close
    await personal_sync(app)
    assert calls == ["connect", "close"]
    assert app.status == "Reconnect required"


async def test_scans_never_read_chats_outside_the_seven_day_window(tmp_path):
    app = rig(tmp_path)
    assert await sync_cycle(app) is True
    assert "user:44" not in app.transport.calls
    assert app.store.message("1/user:44", 3) is None


async def test_gap_rescan_stays_in_window_and_targets_only_unread(tmp_path):
    """After a restart clears every cursor, the seven-day catch-up reads the
    chat again: nothing older is stored. An unread source admits the complete
    same-day group and withdraws a rendering for its earlier membership."""
    app = rig(tmp_path)
    now, key = app.store.clock(), "1/user:42"
    app.store.put_chat(
        Chat(
            account_id=1,
            peer="user:42",
            title="Authored conversation",
            kind="private",
            ai_eligible=True,
            read_through=5,
        ),
        activity=now,
    )

    def authored(identity, date):
        return Message(
            account_id=1,
            peer="user:42",
            message_id=identity,
            revision=1,
            date=date,
            text=f"Authored message {identity}.",
        )

    stored = [authored(i, now - 3000 + i) for i in (2, 3, 4)]
    for message in stored:
        app.sync.ingest(message, target=False, arrival=False)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',rendered='Authored rendering' WHERE chat_key=?",
            (key,),
        )
        db.execute("DELETE FROM jobs")
    app.transport.messages = [
        authored(1, now - WINDOW - 3600),
        *stored,
        authored(5, now - 2000),
        authored(6, now - 1000),
    ]
    assert progress(app, "user:42") is None  # as left by restart_after_gap
    await app.sync.scan(key)
    assert app.store.message(key, 1) is None
    assert app.store.message(key, 5) and app.store.message(key, 6)
    blocks = {
        json.loads(r["sources"])[0]: r
        for r in app.store.rows(
            "SELECT id,sources,state,rendered FROM blocks WHERE chat_key=?", (key,)
        )
    }
    jobs = app.store.rows("SELECT scope,payload FROM jobs WHERE state='queued'")
    assert [j["scope"] for j in jobs] == [blocks[2]["id"]]
    assert json.loads(jobs[0]["payload"])["class"] == "catchup"
    assert json.loads(blocks[2]["sources"]) == [2, 3, 4, 5, 6]
    assert blocks[2]["state"] == "queued" and blocks[2]["rendered"] is None
    assert progress(app, "user:42")["complete"]


async def test_failed_discovery_defers_history_pages(tmp_path):
    # Pages choose targets by the read position discovery refreshes.
    app = rig(tmp_path, {"discover": ValueError("authored")})
    assert await sync_cycle(app) is False
    assert app.transport.calls == ["discover", "catch_up"]
    assert progress(app, "user:42") is None
    assert not app.store.rows("SELECT 1 FROM jobs")


async def test_first_pages_wait_for_fetched_read_positions(tmp_path):
    """New chats, and every chat after a gap restart, get their current read
    position before their first page; one unresolved chat waits alone."""
    app = rig(tmp_path)
    fetched = []

    async def refresh(peers):
        app.transport.calls.append("refresh")
        fetched.append(sorted(peers))
        with app.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO cursors VALUES ('recent:1/user:42',?)",
                (json.dumps({"before": 0, "complete": False}),),
            )

    app.transport.refresh_read_positions = refresh
    assert await sync_cycle(app) is False
    assert app.transport.calls == ["discover", "catch_up", "refresh", "user:42"]
    assert fetched == [["user:42", "user:43"]]
    # Opened chats are not fetched again; the waiting one is tried again.
    app.transport.calls.clear()
    assert await sync_cycle(app) is False
    assert app.transport.calls == ["discover", "catch_up", "refresh"]
    assert fetched[-1] == ["user:43"]


def open_catch_up(app, peer, before):
    with app.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO cursors VALUES (?,?)",
            (f"recent:1/{peer}", json.dumps({"before": before, "complete": False})),
        )


async def test_a_failed_read_position_fetch_pages_only_opened_chats(tmp_path):
    app = rig(tmp_path)
    open_catch_up(app, "user:42", 5)  # mid catch-up: its position is known

    async def failing(peers):
        app.transport.calls.append(("refresh", tuple(peers)))
        raise ValueError("authored")

    app.transport.refresh_read_positions = failing
    assert await sync_cycle(app) is False
    assert app.transport.calls == [
        "discover",
        "catch_up",
        ("refresh", ("user:43",)),
        "user:42",
    ]
    # An account-wide failure during the fetch pages nothing at all.
    for error in (ConnectionError("authored"), MTProtoRecoveryPaused("authored")):
        app = rig(tmp_path / type(error).__name__)
        open_catch_up(app, "user:42", 5)

        async def halted(peers, error=error):
            raise error

        app.transport.refresh_read_positions = halted
        assert await sync_cycle(app) is False
        assert app.transport.calls == ["discover", "catch_up"]


async def test_an_unanswered_fetch_never_pages_a_chat_with_an_old_position(
    tmp_path,
):
    # user:42's earlier fetch opened it but its first page never stored.
    app = rig(tmp_path)
    open_catch_up(app, "user:42", 0)

    async def failing(peers):
        raise ValueError("authored")

    app.transport.refresh_read_positions = failing
    assert await sync_cycle(app) is False
    assert "user:42" not in app.transport.calls
    # Telegram answers only user:43 this time; user:42 still waits.
    app = rig(tmp_path / "b")
    open_catch_up(app, "user:42", 0)

    async def partial(peers):
        open_catch_up(app, "user:43", 0)

    app.transport.refresh_read_positions = partial
    assert await sync_cycle(app) is False
    assert "user:43" in app.transport.calls
    assert "user:42" not in app.transport.calls


async def test_a_failed_first_page_fetches_the_read_position_again(tmp_path):
    app = rig(tmp_path, {"user:42": ValueError("authored")})
    fetched = []

    async def refresh(peers):
        fetched.append(sorted(peers))
        for peer in peers:
            open_catch_up(app, peer, 0)

    app.transport.refresh_read_positions = refresh
    assert await sync_cycle(app) is False
    assert fetched == [["user:42", "user:43"]]
    # user:43 stored its first page; user:42 did not and is fetched again.
    assert await sync_cycle(app) is False
    assert fetched[-1] == ["user:42"]


async def test_lost_authorization_stops_the_account(tmp_path):
    app = rig(tmp_path)

    def lost():
        app.transport.requires_login = True
        raise Refused("Telegram authorization revoked; explicit setup required")

    app.transport.ensure_authorized = lost
    with pytest.raises(Refused):
        await sync_cycle(app)
    assert app.transport.calls == []


async def test_dropped_connection_cancellation_halts_but_a_real_cancel_stops(
    tmp_path,
):
    # Telethon cancels pending requests when it drops a connection.
    app = rig(tmp_path, {"user:42": asyncio.CancelledError()})
    assert await sync_cycle(app) is False
    assert "user:43" not in app.transport.calls
    app = rig(tmp_path / "b")
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(60)

    app.transport.discover = slow
    task = asyncio.create_task(sync_cycle(app))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "error",
    [
        errors.AuthKeyUnregisteredError(request=None),
        errors.AuthKeyDuplicatedError(request=None),
    ],
)
async def test_permanent_authorization_errors_propagate(tmp_path, error):
    app = rig(tmp_path, {"discover": error})
    with pytest.raises(type(error)):
        await sync_cycle(app)
