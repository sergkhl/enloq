"""One service, independently owned Telegram accounts, shared model ceilings."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..domain import agreement
from ..domain.briefing_contract import GROUP_ROLES
from ..domain.citation_check import CHECK_ROLES
from ..domain.group_filtering import FILTER_ROLES, RULES_HASH
from ..domain.models import ProcessingPaused, Refused, Syncing
from ..inference.audio import LocalWhisper, ProxyWhisper
from ..inference.decider import DeciderEngine
from ..inference.openrouter import MuseEngine, OpenRouterEngine
from ..inference.work import ROUTES
from ..storage.accounts import AccountRegistry
from ..storage.lock import OwnerLock
from ..storage.members import OWNER, UNAVAILABLE
from ..storage.store import Store
from ..transport.fake import FakeTransport
from ..transport.telethon import PersonalTransport
from .avatars import Avatars
from .blocks import (
    reconcile_admission,
    reconcile_unread,
    regroup_cached,
    requeue_unsupported,
)
from .capacity import CLASSES, LOW, RANK, Capacity
from .conversations import Conversations
from .demo import seed
from .fair_lock import FairLock
from .group_rollout import GroupBriefingRollout
from .media_cache import MediaCache, evict
from .reminders import ReminderClock
from .runtime import connection_status, housekeeping, personal_sync

GROUP_ROLES = GROUP_ROLES | FILTER_ROLES | CHECK_ROLES

_LOG = logging.getLogger("enloq.scheduler")

# ADR 48 Q19: a Join Request waits at most a week, and five wait at most.
REQUEST_AGE = 7 * 86400
WAITING_LIMIT = 5


@dataclass
class AccountRuntime:
    assistant: Conversations
    workers: list = field(default_factory=list)
    requests: set = field(default_factory=set)
    jobs: set = field(default_factory=set)
    accepting: bool = True
    served: int = 0
    # The member whose account this is (ADR 48), for their turns and budgets.
    member: str = OWNER


class AccountManager:
    def __init__(self, settings, *, store=None):
        self.settings = settings
        self.registry = AccountRegistry(settings.data)
        self.runtimes: dict[int, AccountRuntime] = {}
        self.reminders = ReminderClock(self)
        self.lifecycle = asyncio.Lock()
        # One global model-call limit across accounts (ADR 15); Whisper is one.
        self.capacity = Capacity(limit=self.registry.concurrency)
        self.active: dict[asyncio.Task, str] = {}
        self.turns = 0
        # Members take turns within a class (ADR 48 Q14): the turn each last had.
        self.member_turns: dict[str, int] = {}
        # ADR 39: the personal instance transcribes on the VPS.
        self.whisper = (
            ProxyWhisper(settings) if settings.mode == "personal" else LocalWhisper()
        )
        # One transcription at a time, handed to waiting members in turn.
        self.transcription = FairLock()
        self.running = False
        self.scheduler = None
        self.retention = None
        self.notice_tick = 0
        # The service's hooks for a new Join Request and a member who left or
        # was removed (ADR 48): the owner's push, and revoking their clients.
        self.on_request = lambda member: None
        self.on_member_removed = lambda member: None
        # ADR 49 Q14: the owner's one push when the decision model fails.
        self.on_decision_outage = lambda: None
        # Each member's removal in progress, which a second Remove or the
        # retention pass waits for instead of starting another.
        self.removals: dict[str, asyncio.Future] = {}
        if (settings.data / "assistant.sqlite3").exists() or any(
            (self.registry.path_for(e["directory"]) / "assistant.sqlite3").exists()
            for e in self.registry.accounts()
        ):
            raise Refused(
                "Run the local content cutover before starting the rebuilt application"
            )
        if settings.mode == "offline":
            if not self.registry.accounts():
                self.registry.register(
                    1, "synthetic.session", directory=".", name="Demo account"
                )
            for entry in self.registry.accounts():
                if entry["state"] == "authorized" and self.started(entry):
                    self._create(
                        entry, store=store if entry["account_id"] == 1 else None
                    )
        else:
            self.registry.migrate_legacy(settings)
            # ADR 29/30: a role's format-failure count restarts once when its
            # route changes.
            self.registry.bind_routes(ROUTES)
            for entry in self.registry.accounts():
                if entry["state"] == "authorized" and self.started(entry):
                    try:
                        self._create(entry)
                    except Refused:
                        self.registry.set_state(entry["account_id"], "requires_login")

    def started(self, entry):
        """Only an approved member's accounts run; a Join Request's account
        stays unused until the owner approves it (ADR 48 Q19)."""
        member = self.registry.member(entry["member"])
        return bool(member and member["state"] == "active")

    def _create(self, entry, *, store=None):
        identity = entry["account_id"]
        data = self.registry.path_for(entry["directory"])
        store = store or Store(
            data / "rewriting.sqlite3",
            account_id=identity,
            mode=self.settings.mode,
            timezone=self.settings.timezone,
            shared_path=self.registry.path,
        )
        if self.settings.mode == "offline":
            seed(store)
            transport = FakeTransport()
            transport.account_id = identity
            assistant = Conversations(store, transport)
        else:
            scoped = replace(
                self.settings,
                data=data,
                refs={
                    **self.settings.refs,
                    "EXPECTED_ACCOUNT_ID": str(identity),
                    "PERSONAL_SESSION_REF": str(
                        self.registry.path_for(entry["session"])
                    ),
                },
            )
            transport = PersonalTransport(
                scoped, store, session_root=self.settings.data
            )
            # Each member's own provider pseudonym (ADR 48 Q16).
            user = self.registry.provider_user(entry["member"])
            engine = OpenRouterEngine(scoped, user=user)
            decider = DeciderEngine(scoped, user=user)
            decider.on_route = self.decision_route
            calmer = MuseEngine(scoped, user=user)
            assistant = Conversations(store, transport, engine, decider, calmer)
        member = entry.get("member") or OWNER
        assistant.media.transcriber = self.whisper
        assistant.media.transcription_lock = self.transcription.handle(member)
        assistant.media.cache.evict = self.evict_media
        assistant.avatars.own_photo = lambda: self.registry.get(identity)["photo_id"]
        assistant.avatars.admit = lambda: self.get(identity)
        assistant.admit_ai = lambda: self.admit_ai(identity)
        assistant.account_processing_enabled = lambda: self.registry.processing(
            identity
        )["ai_enabled"]
        assistant.media.admit = assistant.admit_ai
        assistant.rewriting.work.capacity = self.capacity
        assistant.rewriting.work.guidance = self.registry.guidance
        assistant.rewriting.work.on_format = self.registry.record_format
        assistant.rewriting.paused_roles = self.registry.paused_roles
        assistant.memory.enabled = self.registry.memory_enabled
        assistant.suggestions.enabled = self.registry.suggestions_enabled
        assistant.reminders.preferences = lambda: self.reminders.settings(member)
        assistant.reminders.global_epoch = self.registry.suggestions_epoch
        assistant.memory.idle = self.memory_idle
        assistant.rewriting.work.admit = assistant.admit_ai
        runtime = AccountRuntime(assistant, member=member)
        self.runtimes[identity] = runtime
        assistant.group_runtime_admit = lambda: self.admit_group_runtime(
            identity, runtime
        )
        assistant.authorized = lambda: (
            self.runtimes.get(identity) is runtime
            and runtime.accepting
            and self.registry.get(identity)["state"] == "authorized"
        )
        assistant.media.authorized = assistant.authorized
        assistant.notifications.unavailable = lambda: (
            not self.registry.processing(identity)["ai_enabled"]
            or self.registry.decision_route() is not None
            or self.registry.budget(
                clock=store.clock, timezone=store.timezone, member=member
            )["reached"]
        )
        return runtime

    def decision_route(self, available, reason=None):
        """ADR 49 Q14: whether the decision model's route answers, one state
        for every account; the owner hears once when an outage starts."""
        if available and self.registry.decision_route() is None:
            return
        if self.registry.set_decision_route(available, reason):
            self.on_decision_outage()

    def evict_media(self, extra=()):
        """One 2 GB least-recently-used limit shared by every account's cache."""
        caches = [r.assistant.media.cache for r in self.runtimes.values()]
        return evict([*caches, *extra])

    def admit_ai(self, identity):
        assistant = self.get(identity)
        if not self.registry.processing(identity)["ai_enabled"]:
            raise ProcessingPaused("AI processing is Off for this account")
        rows = self.registry.rows(
            "SELECT m.role,m.agreed_version FROM members m JOIN accounts a ON a.member=m.id WHERE a.account_id=?",
            (identity,),
        )
        if (
            rows
            and rows[0]["role"] != "owner"
            and (rows[0]["agreed_version"] != agreement.VERSION)
        ):
            # A changed Member Agreement holds new AI work until the member
            # accepts it (ADR 48 Q3).
            raise ProcessingPaused("Accept the updated agreement to keep AI on")
        if self.settings.mode == "personal" and not assistant.transport.connected:
            raise Syncing("Waiting for this account's Telegram connection")

    def admit_group_runtime(self, identity, runtime):
        """Pure runtime checks for a stage holding the attached account ledger.

        Group admission reads authorization and processing on that transaction;
        opening the registry here would try to lock the same attached database.
        """
        if self.runtimes.get(identity) is not runtime or not runtime.accepting:
            raise Refused("This group account is unavailable")
        if (
            self.settings.mode == "personal"
            and not runtime.assistant.transport.connected
        ):
            raise Syncing("Waiting for this account's Telegram connection")

    def set_processing(self, identity, enabled):
        assistant = self.get(identity)
        value = self.registry.set_processing(identity, enabled)
        if enabled:
            reconcile_unread(assistant.store)
        else:
            # Off ends older-message runs, leaving their count for a later click.
            assistant.rewriting.end_all_older("off")
        assistant.briefings.reconcile()
        return value

    def set_processing_threshold(self, value):
        """A global Save reconciles cached waiting work in every open account."""
        saved = self.registry.set_processing_threshold(value)
        for runtime in tuple(self.runtimes.values()):
            if runtime.accepting:
                reconcile_admission(runtime.assistant.store)
                runtime.assistant.briefings.reconcile()
        return saved

    def set_suggestions(self, enabled):
        """Off stops queued suggestion work in every account; calls already
        started may finish into cache, hidden until the switch is On again."""
        value = self.registry.set_suggestions_enabled(enabled)
        if not value:
            for runtime in tuple(self.runtimes.values()):
                runtime.assistant.suggestions.cancel_queued()
        return value

    def get(self, identity):
        runtime = self.runtimes.get(identity)
        if (
            not runtime
            or not runtime.accepting
            or self.registry.get(identity)["state"] != "authorized"
        ):
            raise Refused(UNAVAILABLE)
        return runtime.assistant

    @contextlib.asynccontextmanager
    async def lease(self, identity):
        assistant = self.get(identity)
        runtime = self.runtimes[identity]
        task = asyncio.current_task()
        runtime.requests.add(task)
        try:
            yield assistant
        finally:
            runtime.requests.discard(task)

    async def start(self):
        self.running = True
        self.retention = asyncio.create_task(self._retain())
        for identity in tuple(self.runtimes):
            await self._start(identity)
        self.scheduler = asyncio.create_task(self._schedule())

    async def _start(self, identity):
        runtime = self.runtimes[identity]
        assistant = runtime.assistant
        assistant.store.recover()
        assistant.store.expire()
        assistant.sync.recover_deletions()
        regroup_cached(assistant.store)
        reconcile_unread(assistant.store)
        requeue_unsupported(assistant.store, assistant.rewriting.eligible)
        reconcile_admission(assistant.store)
        assistant.briefings.reconcile()
        runtime.workers.append(asyncio.create_task(housekeeping(assistant)))
        # Accepted submissions leave in order without a browser (ADR 26).
        runtime.workers.append(asyncio.create_task(assistant.delivery.run()))
        runtime.workers.append(asyncio.create_task(assistant.deletion.run()))
        if self.settings.mode == "personal":
            runtime.workers.append(
                asyncio.create_task(
                    personal_sync(
                        assistant,
                        on_connected=lambda user: self.registry.identity(
                            identity, user
                        ),
                        on_unauthorized=lambda: self._unauthorized(identity),
                    )
                )
            )

    def _unauthorized(self, identity):
        self.registry.set_state(identity, "requires_login")
        runtime = self.runtimes.get(identity)
        if runtime:
            runtime.assistant.notifications.cancel_all()
            runtime.assistant.catalog.clear_transient()

    async def _schedule(self):
        while True:
            try:
                if time.monotonic() >= self.notice_tick:
                    self.notice_tick = time.monotonic() + 1
                    for runtime in tuple(self.runtimes.values()):
                        runtime.assistant.unseen.advance()
                        runtime.assistant.notifications.advance()
                    self.reminders.tick()
                self.dispatch()
            except Exception:
                # Jobs keep their durable state; the next tick tries again.
                _LOG.warning("dispatch_unavailable", exc_info=True)
            await asyncio.sleep(0.25)

    def dispatch(self):
        """Claim due jobs while fewer than limit+1 are preparing or calling:
        highest class first, rotating between members and then their accounts
        within a class (ADR 48 Q14)."""
        self.capacity.wake()  # A changed limit applies without a restart.
        if not hasattr(self, "group_rollout"):
            self.group_rollout = GroupBriefingRollout(
                self, "readability-v2-" + RULES_HASH[:16]
            )
        self.group_rollout.tick()
        for runtime in tuple(self.runtimes.values()):
            if runtime.accepting:
                reconcile_admission(runtime.assistant.store)
        limit = max(1, self.registry.concurrency())
        for _ in range(limit + 1):
            if len(self.active) >= limit + 1:
                return
            low = sum(klass in LOW for klass in self.active.values())
            candidates = []
            for runtime in tuple(self.runtimes.values()):
                if not runtime.accepting:
                    continue
                try:
                    runtime.assistant.admit_ai()
                except Refused:
                    continue
                low_allowed = (low < limit - 1) if limit >= 2 else not self.active
                private_job = runtime.assistant.rewriting.next_job(
                    low_allowed=low_allowed,
                    memory_allowed=not self.active and self.memory_idle(),
                )
                group_job = runtime.assistant.briefings.next_job(
                    low_allowed=low_allowed
                )
                for job in (private_job, group_job):
                    if not job:
                        continue
                    klass = json.loads(job["payload"] or "{}").get("class", "new")
                    candidates.append(
                        (
                            RANK.get(klass, 1),
                            self.member_turns.get(runtime.member, 0),
                            runtime.served,
                            job,
                            runtime,
                        )
                    )
            if not candidates:
                return
            rank, _, _, job, runtime = min(candidates, key=lambda c: c[:3])
            work = (
                runtime.assistant.briefings
                if job["role"] in GROUP_ROLES
                else runtime.assistant.rewriting
            )
            if not work.claim(job):
                continue
            self.turns += 1
            runtime.served = self.member_turns[runtime.member] = self.turns
            task = asyncio.create_task(self._run(runtime, job))
            self.active[task] = CLASSES[rank]
            runtime.jobs.add(task)
            task.add_done_callback(runtime.jobs.discard)

    def memory_idle(self, job_id=None):
        """All accounts, including preparation, quiet/budget waits and Whisper.
        Native sync and browser readers do not count as AI work."""
        for runtime in tuple(self.runtimes.values()):
            if not runtime.accepting:
                continue
            app = runtime.assistant
            try:
                app.admit_ai()
                waiting = not app.memory.local_idle()
            except Refused:
                # Its waiting work cannot start (AI processing Off, an
                # outdated agreement or no connection): a job queued there
                # must not hold memory back everywhere, as Maya's did from
                # 2026-10-19 (ADR 55).
                waiting = False
            if waiting:
                return False
            # One memory call at a time across the server, traits included.
            if app.store.rows(
                "SELECT id FROM jobs WHERE role IN ('chat_memory','trait_scales','trait_notes') AND state IN ('claimed','running') AND id!=? LIMIT 1",
                (job_id or "",),
            ):
                return False
        return True

    async def _run(self, runtime, job):
        work = (
            runtime.assistant.briefings
            if job["role"] in GROUP_ROLES
            else runtime.assistant.rewriting
        )
        try:
            await work.run(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One job's unexpected error never touches sibling attempts.
            work.abandon(job)
        finally:
            self.active.pop(asyncio.current_task(), None)

    async def _retain(self):
        while True:
            try:
                await self.expire_requests()
            except Exception:
                _LOG.warning("request_expiry_unavailable", exc_info=True)
            idle = []
            for entry in self.registry.accounts():
                if entry["account_id"] not in self.runtimes:
                    store = self._store(entry)
                    store.expire()
                    store.selected_media.collect()
                    avatars = Avatars(store, None)
                    avatars.own_photo = lambda e=entry: e["photo_id"]
                    avatars.collect()
                    cache = MediaCache(store, None)
                    cache.collect()
                    idle.append(cache)
            self.evict_media(idle)
            await asyncio.sleep(60)

    def summaries(self, member=None):
        """The accounts of one member (ADR 48), or every account."""
        summaries = []
        for entry in self.registry.accounts(member):
            runtime = self.runtimes.get(entry["account_id"])
            assistant = runtime.assistant if runtime else None
            visible = entry["state"] == "authorized" and runtime and runtime.accepting
            summaries.append(
                {
                    "account_id": entry["account_id"],
                    "name": entry["name"],
                    "username": entry["username"],
                    "phone": entry["phone"],
                    "state": entry["state"],
                    "ai_enabled": bool(entry["ai_enabled"]),
                    "photo_id": entry["photo_id"] if visible else None,
                    "connected": bool(visible and assistant.transport.connected),
                    "status": (
                        connection_status(assistant)
                        if self.settings.mode == "personal"
                        else assistant.status
                    )
                    if visible
                    else {
                        "logged_out": "Signed out",
                        "requires_login": "Sign in again",
                        "logout_pending": "Logout pending; retry to confirm",
                    }.get(entry["state"], "Unavailable"),
                }
            )
        return summaries

    def takes_accounts(self, member):
        """A member leaving or gone keeps no new account (ADR 48 Q9)."""
        joined = self.registry.member(member)
        return bool(joined) and joined["state"] in {"active", "waiting"}

    async def activate(self, user, session, *, admit=None, member=OWNER):
        async with self.lifecycle:
            if admit:
                admit()
            if not self.takes_accounts(member):
                raise Refused("This person is leaving Enloq")
            existing = self.registry.rows(
                "SELECT * FROM accounts WHERE account_id=?", (user.id,)
            )
            if existing and existing[0]["state"] == "authorized":
                return False
            if existing and existing[0]["state"] == "logout_pending":
                raise Refused("Finish the pending logout before signing in again")
            if user.id in self.runtimes:
                await self._stop(user.id)
            if admit:
                admit()
            entry = self.registry.register(user.id, session, member=member)
            self.registry.identity(user.id, user)
            if not self.started(entry):
                return True  # A waiting member's account stays unused.
            self._create(entry)
            if self.running:
                await self._start(user.id)
            return True

    def sign_in_decision(self, account_id):
        """What a sign-in pairing's verified account pairs (ADR 48 Q11, Q12):
        ``(outcome, member, message)``. The Owner Account and a member's home
        account prove who they are, signing back in when signed out; another
        account a member holds is refused; an unknown one reads the Member
        Agreement."""
        rows = self.registry.rows(
            "SELECT * FROM accounts WHERE account_id=?", (account_id,)
        )
        entry = rows[0] if rows else None
        if entry and entry["state"] == "logout_pending":
            return (
                "refuse",
                None,
                "This account has an unfinished logout. Try again later.",
            )
        signed_in = bool(entry and entry["state"] == "authorized")
        if self.settings.owner_account and account_id == self.settings.owner_account:
            return ("prove" if signed_in else "sign_in"), OWNER, ""
        homes = self.registry.rows(
            "SELECT id,state FROM members WHERE home_account=?", (account_id,)
        )
        if homes and homes[0]["state"] == "leaving":
            return "refuse", None, "This person is leaving Enloq."
        if homes:
            return ("prove" if signed_in else "sign_in"), homes[0]["id"], ""
        if entry:
            return (
                "refuse",
                None,
                "Sign in with the first Telegram account you used here.",
            )
        return "agreement", None, ""

    def admit_request(self, version, account_id=None):
        """A Join Request needs the current agreement, a free place, and an
        account no other request or member holds yet."""
        if version != agreement.VERSION:
            raise Refused("The agreement changed. Read it again.")
        if account_id is not None and (
            self.registry.rows(
                "SELECT 1 FROM members WHERE home_account=?", (account_id,)
            )
            or self.registry.rows(
                "SELECT 1 FROM accounts WHERE account_id=?", (account_id,)
            )
        ):
            raise Refused(
                "This account already asked to join. Sign in again to wait here too."
            )
        waiting = self.registry.rows(
            "SELECT count(*) AS waiting FROM members WHERE state='waiting'"
        )[0]["waiting"]
        if waiting >= WAITING_LIMIT:
            raise Refused("Enloq isn't taking new requests right now. Try again later.")

    async def request(self, joining, session, version):
        """A Join Request (ADR 48 Q19): a waiting member holding the account,
        which stays unused until the owner approves it."""
        async with self.lifecycle:
            self.admit_request(version, joining["id"])
            member = secrets.token_hex(8)
            now = time.time()
            phone = joining["phone"]
            with self.registry.transaction() as db:
                db.execute(
                    "INSERT INTO members (id,role,state,home_account,name,username,phone,agreed_version,agreed_at,requested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        member,
                        "member",
                        "waiting",
                        joining["id"],
                        joining["name"],
                        joining["username"],
                        "••• " + phone if phone else "",
                        version,
                        now,
                        now,
                    ),
                )
            self.registry.register(
                joining["id"], session, member=member, name=joining["name"] or None
            )
            with self.registry.transaction() as db:
                db.execute(
                    "UPDATE accounts SET username=?,phone=? WHERE account_id=?",
                    (
                        joining["username"],
                        "••• " + phone if phone else "",
                        joining["id"],
                    ),
                )
        self.on_request(member)
        return member

    async def approve(self, member):
        """The owner approves a Join Request: the member's accounts start."""
        async with self.lifecycle:
            row = self.registry.member(member)
            if not row or row["state"] != "waiting":
                raise Refused("This request isn't waiting any more")
            with self.registry.transaction() as db:
                db.execute(
                    "UPDATE members SET state='active',approved_at=? WHERE id=?",
                    (time.time(), member),
                )
            for entry in self.registry.accounts(member):
                identity = entry["account_id"]
                if entry["state"] != "authorized" or identity in self.runtimes:
                    continue
                try:
                    self._create(entry)
                except Refused:
                    self.registry.set_state(identity, "requires_login")
                    continue
                if self.running:
                    await self._start(identity)

    def accept_agreement(self, member, version):
        if version != agreement.VERSION:
            raise Refused("The agreement changed. Read it again.")
        with self.registry.transaction() as db:
            db.execute(
                "UPDATE members SET agreed_version=?,agreed_at=? WHERE id=?",
                (version, time.time(), member),
            )

    async def remove_member(self, member, *, expiring=False):
        """Leave, Remove, Decline or expiry (ADR 48 Q9, Q19): sign the server
        out of every account the member holds, delete everything it keeps for
        them and forget their clients; spending stays with their ID. Returns
        whether Telegram confirmed every sign-out.

        The member is marked leaving and their clients forgotten first, so no
        login of theirs can finish and attach an account meanwhile; a removal
        interrupted by a restart resumes from the retention pass. One removal
        runs per member, and it finishes even when its request is cancelled."""
        if member not in self.removals:
            removal = asyncio.ensure_future(self._remove_member(member, expiring))
            self.removals[member] = removal
            removal.add_done_callback(lambda _: self.removals.pop(member, None))
        return await asyncio.shield(self.removals[member])

    async def _remove_member(self, member, expiring):
        async with self.lifecycle:
            row = self.registry.member(member)
            if not row or row["role"] == "owner":
                raise Refused("This person isn't on this server")
            if expiring and (
                row["state"] != "waiting"
                or time.time() - (row["requested_at"] or 0) < REQUEST_AGE
            ):
                return True  # Approved or renewed since the expiry pass read it.
            with self.registry.transaction() as db:
                db.execute("UPDATE members SET state='leaving' WHERE id=?", (member,))
        self.on_member_removed(member)
        confirmed = True
        while entries := self.registry.accounts(member):
            for entry in entries:
                identity = entry["account_id"]
                if entry["state"] != "logged_out":
                    try:
                        await self.logout(identity)
                    except Refused:
                        confirmed = False
                async with self.lifecycle:
                    if identity in self.runtimes:
                        await self._stop(identity)
                    # The authorization goes even when Telegram couldn't
                    # confirm the sign-out, so the server keeps no access.
                    folder = self.registry.path_for(entry["session"]).parent
                    if folder.is_relative_to(self.settings.data / "authorizations"):
                        shutil.rmtree(folder, ignore_errors=True)
                    self.registry.set_state(identity, "logged_out")
                await self.remove(identity, force=True)
        with self.registry.transaction() as db:
            db.execute("DELETE FROM member_settings WHERE member=?", (member,))
            db.execute("DELETE FROM members WHERE id=?", (member,))
        self.member_turns.pop(member, None)
        self.on_member_removed(member)
        return confirmed

    async def expire_requests(self):
        """Week-old requests go, and a removal a restart interrupted resumes."""
        for row in self.registry.members():
            if row["state"] == "leaving":
                await self.remove_member(row["id"])
            elif row["state"] == "waiting" and (
                time.time() - (row["requested_at"] or 0) >= REQUEST_AGE
            ):
                await self.remove_member(row["id"], expiring=True)

    async def _drain(self, runtime):
        runtime.accepting = False
        runtime.assistant.catalog.clear_transient()
        tasks = [
            *runtime.workers,
            *runtime.requests,
            *runtime.assistant.tasks,
            *runtime.jobs,
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.assistant.selected_media.close()
        runtime.workers.clear()
        runtime.assistant.store.recover()

    async def _stop(self, identity):
        runtime = self.runtimes.pop(identity)
        await self._drain(runtime)
        await runtime.assistant.transport.close()
        for engine in (runtime.assistant.engine, runtime.assistant.decider):
            if hasattr(engine, "client"):
                await engine.client.aclose()

    async def logout(self, identity):
        async with self.lifecycle:
            entry = self.registry.get(identity)
            if entry["state"] == "logged_out":
                return
            self.registry.set_state(identity, "logout_pending")
            if identity in self.runtimes:
                assistant = self.runtimes[identity].assistant
                assistant.notifications.cancel_all()
                await self._stop(identity)
                assistant.avatars.clear()
                assistant.media.cache.clear()
                assistant.catalog.clear()
            else:
                # Stale index rows without files are dropped by collection.
                shutil.rmtree(
                    self.registry.path_for(entry["directory"]) / "media-cache",
                    ignore_errors=True,
                )
            if self.settings.mode != "offline":
                from telethon import functions

                from ..setup_login import PersonalLoginClient

                path = self.registry.path_for(entry["session"])
                if path.exists():
                    with OwnerLock(Path(str(path) + ".owner.lock")):
                        client = PersonalLoginClient(
                            str(path),
                            int(self.settings.secret("TELEGRAM_API_ID_REF")),
                            self.settings.secret("TELEGRAM_API_HASH_REF"),
                            receive_updates=False,
                            catch_up=False,
                        )
                        try:
                            async with asyncio.timeout(60):
                                await client.connect()
                                if await client.is_user_authorized():
                                    me = await client.get_me()
                                    if not me or me.id != identity:
                                        raise Refused("Logout identity mismatch")
                                    await client(functions.auth.LogOutRequest())
                        except Exception:
                            raise Refused(
                                "Logout is unconfirmed. This account is paused; retry logout to check its authorization."
                            ) from None
                        finally:
                            await client.disconnect()
                            client.session.close()
                    path.unlink(missing_ok=True)
            self.registry.set_state(identity, "logged_out")

    async def reconnect(self, identity):
        async with self.lifecycle:
            entry = self.registry.get(identity)
            if entry["state"] != "authorized":
                raise Refused("Sign in again to reconnect this account")
            if identity in self.runtimes:
                await self._stop(identity)
            self._create(entry)
            if self.running:
                await self._start(identity)

    def _store(self, entry):
        return Store(
            self.registry.path_for(entry["directory"]) / "rewriting.sqlite3",
            account_id=entry["account_id"],
            mode=self.settings.mode,
            timezone=self.settings.timezone,
            shared_path=self.registry.path,
        )

    async def remove(self, identity, *, force=False):
        """Remove a logged-out account's local data. A member leaving
        (``force``) takes unresolved sends and deletions with them."""
        async with self.lifecycle:
            entry = self.registry.get(identity)
            if entry["state"] != "logged_out":
                raise Refused("Log out before removing local data")
            directory = self.registry.path_for(entry["directory"])
            store = self._store(entry)
            try:
                pending_sends = store.send_operations.blocks_removal()
                pending_deletions = store.deletion_operations.blocks_removal()
            finally:
                store.close()
            if force:
                pending_sends = pending_deletions = False
            if pending_sends:
                raise Refused(
                    "Sign in and resolve pending deliveries before removing local data"
                )
            if pending_deletions:
                raise Refused(
                    "Sign in and resolve pending deletions before removing local data"
                )
            # Keep registry/accounting and other accounts even for the legacy root.
            self.reminders.remove_account(identity)
            if directory == self.settings.data:
                for name in (
                    "rewriting.sqlite3",
                    "rewriting.sqlite3-journal",
                    "rewriting.sqlite3-wal",
                    "rewriting.sqlite3-shm",
                ):
                    (directory / name).unlink(missing_ok=True)
                for name in (
                    "media",
                    "media-cache",
                    "draft-images",
                    "selected-media",
                    "avatars",
                ):
                    if (directory / name).exists():
                        shutil.rmtree(directory / name)
            elif directory.exists():
                shutil.rmtree(directory)
            with self.registry.transaction() as db:
                db.execute("DELETE FROM accounts WHERE account_id=?", (identity,))

    async def close(self):
        self.running = False
        if self.retention:
            self.retention.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.retention
        if self.scheduler:
            self.scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.scheduler
        for identity in tuple(self.runtimes):
            await self._stop(identity)
