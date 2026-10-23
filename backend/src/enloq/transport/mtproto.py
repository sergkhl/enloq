# Adapted from hercules-support-relay, the author's earlier private project; see docs/extraction.md.
"""Bound requests made by an existing Telethon session owner.

Only correlation metadata and future due times are durable. No credential,
message, entity cache or copied session belongs to this policy database.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import functools
import hashlib
import json
import logging
import os
import random
import socket
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, functions, utils
from telethon.errors import AuthKeyNotFound, InvalidBufferError, InvalidChecksumError
from telethon.network.mtprotosender import MTProtoSender
from telethon.tl.alltlobjects import LAYER

MAX_ATTEMPTS = 6
READ_INTERVAL = 1.0
FILE_PART = 1024 * 1024
MAX_RECOVERY_WAIT = 900.0
REQUEST_TIMEOUT = 60.0
RECONNECT_MAX_DELAY = 300.0
RECONNECT_TIMEOUT = 120.0
_METADATA_TTL = 72 * 3600
_READS = frozenset(
    """help.GetConfigRequest messages.GetHistoryRequest messages.GetRepliesRequest messages.GetMessagesRequest
    messages.GetDialogsRequest messages.GetPeerDialogsRequest messages.GetFullChatRequest
    messages.GetChatsRequest messages.GetPeerSettingsRequest messages.GetMessageReactionsListRequest
    messages.GetUnreadReactionsRequest messages.GetAvailableReactionsRequest help.GetAppConfigRequest
    messages.GetSavedGifsRequest messages.GetAllStickersRequest messages.GetRecentStickersRequest
    messages.GetFavedStickersRequest messages.GetStickerSetRequest messages.GetInlineBotResultsRequest
    messages.SearchRequest messages.SearchGlobalRequest
    messages.GetForumTopicsRequest messages.GetForumTopicsByIDRequest
    channels.GetChannelsRequest channels.GetFullChannelRequest channels.GetParticipantsRequest
    channels.GetParticipantRequest channels.GetMessagesRequest users.GetUsersRequest
    users.GetFullUserRequest contacts.ResolveUsernameRequest contacts.ResolvePhoneRequest
    contacts.GetContactsRequest contacts.SearchRequest
    photos.GetUserPhotosRequest""".split()
)
# File parts use their own lane: one part in flight per account, no pacing gap,
# served by priority. Floods and transient backoff still apply.
_FILE_READS = frozenset(
    "upload.GetFileRequest upload.GetFileHashesRequest upload.GetCdnFileRequest".split()
)
PLAYBACK, DOWNLOAD, PREPARATION, PREFETCH = range(4)
FILE_PRIORITY: contextvars.ContextVar[int] = contextvars.ContextVar(
    "enloq_file_priority", default=DOWNLOAD
)
# Update difference reads preserve cursors and must never use an observation cache.
_GAP_READS = frozenset(
    "updates.GetStateRequest updates.GetDifferenceRequest updates.GetChannelDifferenceRequest".split()
)
_FLOODS = (
    errors.FloodWaitError,
    errors.SlowModeWaitError,
    errors.FloodTestPhoneWaitError,
)
# What Telethon puts on pending requests when a connection ends other than with
# an OSError: a clean close by Telegram or a bad transport buffer.
_DROPS = (asyncio.IncompleteReadError, InvalidBufferError, InvalidChecksumError)
_TRANSIENT = (
    errors.ServerError,
    errors.RpcCallFailError,
    errors.TimedOutError,
    OSError,
    TimeoutError,
)
_OBSERVATION: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "enloq_mtproto_observation", default=None
)
_REQUEST_GUARDS: contextvars.ContextVar[tuple[Callable[[Any], None], ...]] = (
    contextvars.ContextVar("enloq_request_guards", default=())
)
_LOG = logging.getLogger("enloq.mtproto")
_NO_REPLAY = frozenset(
    {"messages.DeleteMessagesRequest", "channels.DeleteMessagesRequest"}
)


@contextlib.contextmanager
def native_request_guard(guard: Callable[[Any], None]):
    """Keep a task's native intent guards on SDK-generated paced/wire requests."""
    token = _REQUEST_GUARDS.set((*_REQUEST_GUARDS.get(), guard))
    try:
        yield
    finally:
        _REQUEST_GUARDS.reset(token)


def _scope_request(request):
    guards = _REQUEST_GUARDS.get()
    if not guards:
        return
    if getattr(request, "_enloq_scoped_guards", None) != guards:
        previous = getattr(request, "_enloq_final_guard", None)

        def final_guard(native):
            for guard in guards:
                guard(native)
            if previous:
                previous(native)

        request._enloq_final_guard = final_guard
        request._enloq_scoped_guards = guards
    request._enloq_final_guard(request)


def _witness(witness, event, *args):
    """Record a hand-off fact; a failed record leaves the send exposed, so it
    can only turn a not-sent verdict into unknown, never the reverse."""
    if witness is None:
        return
    try:
        getattr(witness, event)(*args)
    except Exception:
        _LOG.warning("send_evidence_unsaved", exc_info=True)


class MTProtoRecoveryPaused(RuntimeError):
    """The bounded recovery allowance ended; preserve cooldowns and progress."""


class MTProtoAttemptLimit(ConnectionError):
    """Even protocol-level re-enqueues cannot exceed one RPC's attempt allowance."""


class _SettledError(Exception):
    def __init__(self, error: Exception):
        self.error = error


@dataclass
class _Attempts:
    used: int = 0
    recovery_start: float | None = None


class _BoundedPacker:
    """Count SDK protocol corrections as well as application RPC attempts."""

    def __init__(self, packer):
        self._packer = packer

    def __getattr__(self, name):
        return getattr(self._packer, name)

    def append(self, state):
        if state.future.done():
            return
        budget = getattr(state.request, "_enloq_attempts", None)
        if budget is None:
            budget = _Attempts()
            state.request._enloq_attempts = budget
        deletion = method_name(state.request) in _NO_REPLAY
        if budget.used >= (1 if deletion else MAX_ATTEMPTS):
            if not state.future.done():
                state.future.set_exception(
                    MTProtoAttemptLimit(
                        "Native deletion cannot be automatically repeated"
                        if deletion
                        else "MTProto request attempts exhausted"
                    )
                )
            return
        guard = getattr(state.request, "_enloq_wire_guard", None)
        if guard:
            try:
                guard(state.request)
            except Exception as error:
                state.future.set_exception(error)
                return
        if deletion or getattr(state.request, "_enloq_final_guard", None):
            try:
                # Source/owner validation and mandatory durable exposure
                # precede every hand-off, including SDK protocol corrections.
                final_guard = getattr(state.request, "_enloq_final_guard", None)
                if final_guard:
                    final_guard(state.request)
                witness = getattr(state.request, "_enloq_wire", None)
                if witness:
                    witness.handing_off()
                    state.request._enloq_wire_marked = True
            except Exception as error:
                state.future.set_exception(error)
                return
        budget.used += 1
        self._packer.append(state)

    def extend(self, states):
        for state in states:
            self.append(state)


@contextlib.contextmanager
def observation_cycle():
    """Coalesce identical bounded reads only inside this fresh observation."""
    token = _OBSERVATION.set({})
    try:
        yield
    finally:
        _OBSERVATION.reset(token)


def observed(work):
    @functools.wraps(work)
    async def run(*args, **kwargs):
        with observation_cycle():
            return await work(*args, **kwargs)

    return run


def method_name(request) -> str:
    return (
        request.__class__.__module__.split(".functions.")[-1]
        + "."
        + request.__class__.__name__
    )


def _peer(request) -> str:
    peer = getattr(request, "peer", getattr(request, "channel", None))
    for kind in ("channel_id", "chat_id", "user_id"):
        value = getattr(peer, kind, None)
        if isinstance(value, int):
            return f"{kind}:{value}"
    return "unknown"


def _failure_stage(error: Exception) -> str:
    if isinstance(error, socket.gaierror):
        return "dns"
    if isinstance(error, TimeoutError):
        return "read_timeout"
    if isinstance(error, OSError):
        return "connection"
    return "rpc_response"


class FileLane:
    """One holder at a time; waiters are served by priority, then arrival."""

    def __init__(self):
        self._busy = False
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._order = 0

    @contextlib.asynccontextmanager
    async def slot(self, priority: int):
        if self._busy or self._waiters:
            self._order += 1
            waiter = asyncio.get_running_loop().create_future()
            entry = (priority, self._order, waiter)
            self._waiters.append(entry)
            self._waiters.sort(key=lambda w: w[:2])
            try:
                await waiter
            except BaseException:
                if entry in self._waiters:
                    self._waiters.remove(entry)
                elif waiter.done() and not waiter.cancelled():
                    self._release()
                raise
        else:
            self._busy = True
        try:
            yield
        finally:
            self._release()

    def _release(self):
        while self._waiters:
            _, _, waiter = self._waiters.pop(0)
            if not waiter.done():
                waiter.set_result(None)
                return
        self._busy = False


class RequestPolicy:
    def __init__(
        self,
        path: Path,
        *,
        clock=time.time,
        sleep=asyncio.sleep,
        jitter=lambda: random.uniform(0.05, 1.0),
    ):
        self.path = path
        self.clock, self.sleep, self.jitter = clock, sleep, jitter
        self._reads = asyncio.Lock()
        self.files = FileLane()
        self.recovery_wait = 0.0
        # This metadata file is ignored before creation and is private from its first byte.
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        path.chmod(0o600)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS due_times (scope TEXT PRIMARY KEY, due REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, method TEXT NOT NULL,
                    peer TEXT NOT NULL, attempt INTEGER NOT NULL, started REAL NOT NULL,
                    finished REAL, error TEXT, stage TEXT, requested_wait REAL,
                    applied_wait REAL, wire_attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS waits (
                    id INTEGER PRIMARY KEY, started REAL NOT NULL, finished REAL NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('pacing','recovery'))
                );
            """)
            now = clock()
            db.execute("DELETE FROM attempts WHERE started<?", (now - _METADATA_TTL,))
            db.execute("DELETE FROM waits WHERE finished<?", (now - _METADATA_TTL,))
            db.execute("DELETE FROM due_times WHERE due<?", (now,))

    @contextlib.contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def due(self, *scopes: str) -> float:
        with self._db() as db:
            return max(
                (
                    db.execute(
                        "SELECT due FROM due_times WHERE scope=?", (scope,)
                    ).fetchone()
                    or (0,)
                )[0]
                for scope in scopes
            )

    def hold(self, scope: str, due: float):
        with self._db() as db:
            db.execute(
                "INSERT INTO due_times VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET due=max(due,excluded.due)",
                (scope, due),
            )

    async def wait_until(
        self, due: float, *, kind: str, allowance: float = MAX_RECOVERY_WAIT
    ):
        started = self.clock()
        wait = max(0, due - started)
        if wait == 0:
            return
        if kind == "recovery" and wait > allowance:
            raise MTProtoRecoveryPaused(
                "MTProto recovery would exceed 900 seconds; cooldown retained"
            )
        with self._db() as db:
            wait_id = db.execute(
                "INSERT INTO waits (started,finished,kind) VALUES (?,?,?)",
                (started, due, kind),
            ).lastrowid
        try:
            await self.sleep(wait)
        finally:
            finished = self.clock()
            elapsed = max(0, finished - started)
            if kind == "recovery":
                self.recovery_wait += elapsed
            with self._db() as db:
                db.execute(
                    "UPDATE waits SET finished=? WHERE id=?", (finished, wait_id)
                )

    async def execute(
        self, request, perform: Callable[[], Awaitable[Any]], *, attempts=None
    ):
        _scope_request(request)
        method, peer = method_name(request), _peer(request)
        background = method in _READS | _GAP_READS
        cache = _OBSERVATION.get() if method in _READS else None
        cache_key = (
            str(self.path.resolve()) + ":" + hashlib.sha256(bytes(request)).hexdigest()
        )
        if cache is not None and cache_key in cache:
            return cache[cache_key]
        if method in _FILE_READS:
            async with self.files.slot(FILE_PRIORITY.get()):
                return await self._execute(
                    request,
                    method,
                    peer,
                    perform,
                    background=True,
                    paced=False,
                    attempts=attempts,
                )
        if background:
            async with self._reads:
                # Concurrent identical reads may have settled while this read waited.
                if cache is not None and cache_key in cache:
                    _scope_request(request)
                    return cache[cache_key]
                result = await self._execute(
                    request, method, peer, perform, background=True, attempts=attempts
                )
        else:
            if _OBSERVATION.get() is not None:
                _OBSERVATION.get().clear()
            result = await self._execute(
                request, method, peer, perform, background=False, attempts=attempts
            )
        if cache is not None and len(cache) < 256:
            cache[cache_key] = result
        return result

    async def _execute(
        self, request, method, peer, perform, *, background, paced=True, attempts=None
    ):
        request_id = uuid.uuid4().hex
        deletion = method in _NO_REPLAY
        guarded = deletion or bool(getattr(request, "_enloq_final_guard", None))
        limit = 1 if deletion else MAX_ATTEMPTS
        budget = attempts if attempts is not None else _Attempts()
        if budget.recovery_start is None:
            budget.recovery_start = self.recovery_wait
        recovery_start = budget.recovery_start
        request._enloq_attempts = budget
        # A send's final request carries a witness that records, durably and
        # before each hand-off, whether it may have reached Telegram.
        witness = getattr(request, "_enloq_wire", None)
        if guarded and witness:
            witness.watching()
        else:
            _witness(witness, "watching")
        for attempt in range(1, limit + 1):
            if budget.used >= limit:
                raise MTProtoAttemptLimit("MTProto request attempts exhausted")
            due = self.due("account", "method:" + method, "peer:" + peer)
            if due > self.clock():
                _witness(witness, "holding", due)
            await self.wait_until(
                due,
                kind="recovery",
                allowance=max(
                    0, MAX_RECOVERY_WAIT - (self.recovery_wait - recovery_start)
                ),
            )
            if background and paced:
                await self.wait_until(self.due("reads"), kind="pacing")
                self.hold("reads", self.clock() + READ_INTERVAL)
            started = self.clock()
            with self._db() as db:
                row_id = db.execute(
                    "INSERT INTO attempts (request_id,method,peer,attempt,started) VALUES (?,?,?,?,?)",
                    (request_id, method, peer, attempt, started),
                ).lastrowid
            error, result = None, None
            requested = applied = 0.0
            before = budget.used
            marked = handed = False
            request._enloq_wire_marked = False
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    if guarded and not getattr(request, "_enloq_packed", False):
                        final_guard = getattr(request, "_enloq_final_guard", None)
                        if final_guard:
                            final_guard(request)
                    if witness and not (
                        guarded and getattr(request, "_enloq_packed", False)
                    ):
                        # A failed record stops the hand-off: nothing is sent.
                        witness.handing_off()
                        marked = True
                    pending = perform()
                    handed = True
                    result = await pending
            except BaseException as exc:
                error = exc
                if isinstance(exc, _FLOODS):
                    requested = max(0, int(exc.seconds))
                    applied = requested + max(0.05, self.jitter())
                    scope = (
                        "peer:" + peer
                        if isinstance(exc, errors.SlowModeWaitError)
                        and peer != "unknown"
                        else "account"
                    )
                    self.hold(scope, self.clock() + applied)
                elif background and isinstance(exc, _TRANSIENT):
                    applied = min(2**attempt, 300) * max(0.05, self.jitter())
                    self.hold("method:" + method, self.clock() + applied)
                marked = marked or bool(
                    guarded and getattr(request, "_enloq_wire_marked", False)
                )
                if marked:
                    # Downgrade only on proof from the packer: nothing appended,
                    # or exactly one transmission answered by a flood wait.
                    wires = budget.used - before
                    if not handed and wires == 0:
                        _witness(witness, "not_handed")
                    elif handed and wires == 1 and isinstance(exc, _FLOODS):
                        _witness(
                            witness,
                            "flood_refused",
                            type(exc).__name__,
                            self.clock() + applied,
                        )
            finally:
                # Test senders do not use a Telethon packer; one perform is still one attempt.
                packed_guarded = guarded and getattr(request, "_enloq_packed", False)
                wires = max(0 if packed_guarded else 1, budget.used - before)
                if budget.used == before and not packed_guarded:
                    budget.used += 1
                record = dict(
                    requestId=request_id,
                    method=method,
                    peer=peer,
                    attempt=attempt,
                    wireAttempts=wires,
                    elapsedMs=round((self.clock() - started) * 1000),
                    error=None if error is None else type(error).__name__,
                    stage=None if error is None else _failure_stage(error),
                    requestedWait=requested,
                    appliedWait=applied,
                )
                with self._db() as db:
                    db.execute(
                        "UPDATE attempts SET finished=?,error=?,stage=?,requested_wait=?,applied_wait=?,wire_attempts=? WHERE id=?",
                        (
                            self.clock(),
                            record["error"],
                            record["stage"],
                            requested,
                            applied,
                            wires,
                            row_id,
                        ),
                    )
                _LOG.info("mtproto request %s", json.dumps(record, sort_keys=True))
            if error is None:
                return result
            safe = not deletion and (
                isinstance(error, _FLOODS)
                or (background and isinstance(error, _TRANSIENT))
            )
            if not safe or budget.used >= limit or attempt == limit:
                raise error
        raise AssertionError("bounded request loop exhausted without a result")


class _Sender:
    def __init__(self, sender, policy, attempts=None):
        self.sender, self.policy = sender, policy
        self.attempts = attempts
        if not isinstance(sender._send_queue, _BoundedPacker):
            sender._send_queue = _BoundedPacker(sender._send_queue)

    def send(self, request, ordered=False):
        if utils.is_list_like(request):
            return [self.send(item, ordered=ordered) for item in request]

        async def run():
            try:
                request._enloq_packed = True
                guard = getattr(self.policy, "wire_guard", None)
                if guard:
                    request._enloq_wire_guard = guard
                return await self.policy.execute(
                    request,
                    lambda: self.sender.send(request, ordered=ordered),
                    attempts=self.attempts,
                )
            except Exception as exc:
                # Escape the SDK's retry/flood/sleep branches; the outer _call restores
                # the original error for existing gateway refusal/ambiguity handling.
                raise _SettledError(exc) from exc

        return asyncio.create_task(run())


def _transport_flood(error):
    return isinstance(error, InvalidBufferError) and error.code == 429


def connection_retry_delay(error, delay):
    """Only transient connection failures retry; authorization needs the owner."""
    if isinstance(
        error, (AuthKeyNotFound, errors.UnauthorizedError, errors.AuthKeyError)
    ):
        return None
    if _transport_flood(error):
        return RECONNECT_MAX_DELAY
    if isinstance(error, _FLOODS):
        return max(delay, error.seconds)
    if error is None or isinstance(error, (*_TRANSIENT, *_DROPS)):
        return delay
    return None


async def wait_for_connection(policy):
    # Connection recovery can wait out long flood holds without extending an
    # individual RPC's allowance. A concurrent request can lengthen the hold.
    while (due := policy.due("account", "reconnect")) > policy.clock():
        await policy.wait_until(due, kind="recovery", allowance=float("inf"))


class PacedTelegramClient(TelegramClient):
    _guarded_deletion = True

    def __init__(self, session, api_id, api_hash, **kwargs):
        # No SDK RPC retry, flood sleep or reconnect replay. This owner restores
        # connections with capped backoff; individual requests remain bounded.
        # `catch_up` is the one of these a caller may lower: the daemon owes
        # every update it missed, and a read that owns no intake owes none.
        kwargs.update(
            request_retries=0,
            connection_retries=0,
            flood_sleep_threshold=0,
            auto_reconnect=False,
            raise_last_call_error=True,
            catch_up=kwargs.get("catch_up", True),
        )
        super().__init__(session, api_id, api_hash, **kwargs)
        self._sender._send_queue = _BoundedPacker(self._sender._send_queue)
        self.request_policy = RequestPolicy(Path(str(session) + ".pacing.sqlite3"))
        self._owner_reconnect = None
        self._owner_stopping = False

    async def _create_exported_sender(self, dc_id):
        if not _REQUEST_GUARDS.get():
            return await super()._create_exported_sender(dc_id)
        # The installed SDK sends its import directly, outside _call. A guarded
        # download must carry the same intent through this initialization too.
        dc = await self._get_dc(dc_id)
        _scope_request(functions.auth.ExportAuthorizationRequest(dc_id))
        sender = MTProtoSender(None, loggers=self._log)
        sender._send_queue = _BoundedPacker(sender._send_queue)
        try:
            await sender.connect(
                self._connection(
                    dc.ip_address,
                    dc.port,
                    dc.id,
                    loggers=self._log,
                    proxy=self._proxy,
                    local_addr=self._local_addr,
                )
            )
            auth = await self(functions.auth.ExportAuthorizationRequest(dc_id))
            initial = copy.copy(self._init_request)
            initial.query = functions.auth.ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes
            )
            await self._call(sender, functions.InvokeWithLayerRequest(LAYER, initial))
            return sender
        except BaseException:
            await sender.disconnect()
            raise

    async def _call(
        self,
        sender,
        request,
        ordered=False,
        flood_sleep_threshold=None,
        *,
        attempts=None,
    ):
        try:
            return await super()._call(
                _Sender(sender, self.request_policy, attempts),
                request,
                ordered=ordered,
                flood_sleep_threshold=0,
            )
        except _SettledError as exc:
            if isinstance(exc.error, _DROPS) and not self.is_connected():
                # To callers, and to Telethon's update loop, which would
                # otherwise disconnect for good, this is a lost connection that
                # the reconnect owner restores.
                raise ConnectionError("Telegram closed the connection") from exc.error
            raise exc.error from exc

    def iter_parts(self, asset, *, msg_data=None):
        """1 MiB ``upload.getFile`` parts (Telethon's public iterator stops at
        512 KiB). Offsets stay 1 MiB-aligned, as Telegram requires; DC export,
        CDN and file-reference handling remain the installed SDK's."""
        from telethon.client.downloads import _DirectDownloadIter

        info = utils._get_file_info(asset)
        size = info.size
        return _DirectDownloadIter(
            self,
            (size + FILE_PART - 1) // FILE_PART if size else None,
            file=info.location,
            dc_id=info.dc_id,
            offset=0,
            stride=FILE_PART,
            chunk_size=FILE_PART,
            request_size=FILE_PART,
            file_size=size,
            msg_data=msg_data,
        )

    async def connect(self):
        await super().connect()
        self._owner_stopping = False
        if self._owner_reconnect is None or self._owner_reconnect.done():
            self._owner_reconnect = asyncio.create_task(self._reconnect_owner())

    @property
    def reconnecting(self):
        return bool(
            not self._owner_stopping
            and self._owner_reconnect
            and not self._owner_reconnect.done()
        )

    async def _reconnect_owner(self):
        while not self._owner_stopping:
            dropped = None
            try:
                await asyncio.shield(self.disconnected)
            except Exception as error:
                # Telethon keeps what ended the connection: a network error, a
                # clean close by Telegram (IncompleteReadError), a bad transport
                # buffer or an RPC error. Only disconnect() cancels this owner.
                dropped = error
            if self._owner_stopping:
                return
            dropped = self._updates_error or dropped
            flood = _transport_flood(dropped)
            delay = 2.0
            while not self._owner_stopping:
                if isinstance(dropped, AuthKeyNotFound):
                    _LOG.error("mtproto_auth_key_unknown", exc_info=dropped)
                    return
                if isinstance(dropped, (errors.UnauthorizedError, errors.AuthKeyError)):
                    # The sync owner observes this and withdraws the account.
                    self._updates_error = dropped
                    _LOG.error("mtproto_sign_in_required", exc_info=dropped)
                    return
                wait = connection_retry_delay(dropped, delay)
                if wait is None:
                    _LOG.error("mtproto_reconnect_stopped", exc_info=dropped)
                    return
                now = self.request_policy.clock()
                if flood:
                    # After Telegram's transport-level flood, wait fully before
                    # each try. The wait is the owner's own: a Reconnect or a
                    # restart does not sit it out.
                    self.request_policy.hold(
                        "reconnect", now + max(wait, RECONNECT_MAX_DELAY)
                    )
                else:
                    self.request_policy.hold("account", now + wait)
                try:
                    await wait_for_connection(self.request_policy)
                    incoming = tuple(self._event_handler_tasks)
                    if incoming:
                        _, unfinished = await asyncio.wait(
                            incoming, timeout=REQUEST_TIMEOUT
                        )
                        if unfinished:
                            raise MTProtoRecoveryPaused(
                                "incoming handlers have not reached their durable boundary"
                            )
                    # Save update cursors and finish the disconnected owner's loops.
                    async with asyncio.timeout(RECONNECT_TIMEOUT):
                        await super().disconnect()
                        await super().connect()
                        await self.catch_up()
                    break
                except MTProtoRecoveryPaused:
                    # Cooldown and update cursors are retained.
                    _LOG.error("mtproto_recovery_paused", exc_info=True)
                    return
                except Exception as error:
                    dropped = error
                    flood = flood or _transport_flood(error)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def disconnect(self):
        self._owner_stopping = True
        if self._owner_reconnect is not None:
            self._owner_reconnect.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._owner_reconnect
            self._owner_reconnect = None
        await super().disconnect()
