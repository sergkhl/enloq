import asyncio
import json
import logging

from telethon import errors

from ..domain.models import GapTooLong
from ..transport.mtproto import (
    RECONNECT_MAX_DELAY,
    RECONNECT_TIMEOUT,
    MTProtoRecoveryPaused,
    connection_retry_delay,
)

_LOG = logging.getLogger("enloq.sync")

AUTH_ERRORS = (
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.UserDeactivatedError,
    # The session was used from two places and can never be used again.
    errors.AuthKeyDuplicatedError,
)
# Conditions that affect every request of the account, so the rest of the cycle
# would fail the same way: lost connection or timeouts (OSError), an exhausted
# recovery allowance, flood holds, server errors and other authorization errors.
# Telethon reports a 401 to the identity recheck as a missing identity, which
# then stops the account.
ACCOUNT_WIDE = (
    OSError,
    MTProtoRecoveryPaused,
    errors.FloodError,
    errors.ServerError,
    errors.TimedOutError,
    errors.UnauthorizedError,
    errors.AuthKeyError,
)
INCOMPLETE = "Sync incomplete; retrying bounded recovery"


def _cancelled():
    """Whether this task itself is being cancelled. Without auto-reconnect,
    Telethon cancels pending request futures when it drops a connection; that
    CancelledError is a lost connection, not a request to stop syncing."""
    task = asyncio.current_task()
    return task is None or task.cancelling() > 0


def _progress(store):
    return {
        r["key"][len("recent:") :]: json.loads(r["value"])
        for r in store.rows("SELECT key,value FROM cursors WHERE key LIKE 'recent:%'")
    }


def _unopened(store, chats):
    """Recent chats whose seven-day catch-up has stored no page yet (new chats,
    every chat after a gap restart, or a first page that failed): each needs a
    current read position before its first page."""
    progress = _progress(store)
    return [
        chat
        for chat in chats
        if chat["key"] not in progress
        or (
            not progress[chat["key"]]["complete"]
            and not progress[chat["key"]]["before"]
        )
    ]


async def _attempt(transport, work):
    """Run one sync step. A failure limited to that step lets the cycle go on."""
    try:
        await work()
        return "done"
    except AUTH_ERRORS:
        raise
    except asyncio.CancelledError:
        if _cancelled():
            raise
        return "halted"
    except Exception as error:
        if getattr(transport, "requires_login", False):
            raise
        if isinstance(error, ACCOUNT_WIDE) or not transport.connected:
            return "halted"
        return "failed"


async def sync_cycle(assistant, on_connected=None):
    """Discovery, gap catch-up and one page per recent chat, each failing on its
    own. Returns whether every step completed."""
    transport = assistant.transport
    try:
        check = getattr(transport, "ensure_authorized", None)
        if check:
            check()
        restarted = False

        async def discovery():
            async with asyncio.timeout(120):
                await transport.discover()
            if on_connected:
                on_connected(transport.identity)

        async def gap():
            nonlocal restarted
            async with asyncio.timeout(120):
                try:
                    await transport.catch_up()
                except GapTooLong:
                    await transport.restart_after_gap()
                    restarted = True
                    _LOG.warning("sync_gap_restarted")

        discovered = await _attempt(transport, discovery)
        if discovered == "halted":
            return False
        caught_up = await _attempt(transport, gap)
        if caught_up == "halted" or restarted or discovered != "done":
            # History pages choose targets by read position, so they run only
            # after this cycle's discovery refreshed it.
            return False
        outcomes = [discovered, caught_up]
        chats = assistant.store.sync_targets()
        refresh = getattr(transport, "refresh_read_positions", None)
        unopened = (
            _unopened(
                assistant.store, [chat for chat in chats if chat["kind"] == "private"]
            )
            if refresh
            else []
        )
        if unopened:
            # A chat's first page waits for its current read position; a chat
            # Telegram cannot resolve waits alone. A cursor from an earlier
            # fetch whose page never stored is dropped first, so afterwards only
            # chats answered by this fetch have one.
            with assistant.store.transaction() as db:
                db.executemany(
                    "DELETE FROM cursors WHERE key=?",
                    [("recent:" + c["key"],) for c in unopened],
                )
            outcome = await _attempt(
                transport, lambda: refresh([c["peer"] for c in unopened])
            )
            if outcome == "halted":
                return False
            outcomes.append(outcome)
            progress = _progress(assistant.store)
            waiting = {c["key"] for c in unopened if c["key"] not in progress}
            if waiting:
                outcomes.append("failed")
            chats = [c for c in chats if c["key"] not in waiting]
        for chat in chats:
            if chat["kind"] == "group" and chat["peer"].startswith("channel:"):
                recover = getattr(transport, "channel_catch_up", None)
                if recover:
                    outcome = await _attempt(
                        transport, lambda peer=chat["peer"]: recover(peer)
                    )
                    if outcome == "halted":
                        return False
                    outcomes.append(outcome)
            outcome = await _attempt(
                transport, lambda key=chat["key"]: assistant.sync.scan(key)
            )
            if outcome == "halted":
                return False
            outcomes.append(outcome)
        for chat in chats:
            # Reaction updates have no replayable sequence; Telegram's unread
            # count asks for one bounded read of the affected messages.
            outcome = await _attempt(
                transport,
                lambda key=chat["key"]: assistant.sync.reaction_catch_up(key),
            )
            if outcome == "halted":
                return False
            outcomes.append(outcome)
        return all(outcome == "done" for outcome in outcomes)
    except AUTH_ERRORS:
        raise
    except asyncio.CancelledError:
        if _cancelled():
            raise
        return False
    except Exception:
        if getattr(transport, "requires_login", False):
            raise
        return False


async def personal_sync(assistant, *, on_connected=None, on_unauthorized=None):
    transport = assistant.transport
    try:
        delay = 2.0
        while True:
            try:
                async with asyncio.timeout(RECONNECT_TIMEOUT):
                    await transport.connect(assistant.sync)
                break
            except Exception as error:
                wait = connection_retry_delay(error, delay)
                if transport.requires_login or wait is None:
                    raise
                assistant.status = "Reconnecting"
                await asyncio.sleep(wait)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)
        if on_connected:
            on_connected(transport.identity)
        assistant.status = "Connected"
        while True:
            complete = await sync_cycle(assistant, on_connected)
            assistant.status = "Connected" if complete else INCOMPLETE
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if transport.requires_login or isinstance(
            error, (errors.UnauthorizedError, errors.AuthKeyError)
        ):
            _LOG.warning("sync_sign_in_required", exc_info=True)
            if on_unauthorized:
                on_unauthorized()
            assistant.status = "Sign in again"
        else:
            assistant.status = "Reconnect required"
    finally:
        await transport.close()


def connection_status(assistant):
    """Snapshot the connection owner, not a stale sync-cycle message."""
    transport = assistant.transport
    if transport.requires_login:
        return "Sign in again"
    if transport.connected or assistant.status in {"Connecting", "Reconnecting"}:
        return assistant.status
    return (
        "Reconnecting"
        if getattr(transport, "reconnecting", False)
        else "Reconnect required"
    )


async def housekeeping(assistant):
    while True:
        assistant.store.expire()
        assistant.sync.recover_deletions()
        assistant.attachments.collect()
        assistant.store.selected_media.collect()
        assistant.avatars.collect()
        assistant.media.cache.collect()
        assistant.memory.schedule_all()
        await asyncio.sleep(60)
