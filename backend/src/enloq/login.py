"""Bounded browser login. Challenges live only in the owning process's memory."""

from __future__ import annotations

import asyncio
import contextlib
import io
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import qrcode
from telethon import errors, functions, utils

from .config import private_directory
from .domain.models import Refused
from .setup_login import _CODE_HELP, PersonalLoginClient, delivery_metadata
from .storage.lock import OwnerLock
from .storage.members import OWNER
from .transport.onboarding import OnboardingQueue, OnboardingRefused

TERMINAL = {"complete", "cancelled", "expired", "error"}
# At most this many logins run at once: trusted clients' Add account and Sign
# in again, and, counted apart so strangers never hold those up, sign-in
# pairings (ADR 48 Q11).
FLOWS = 8
PAIRING_FLOWS = 6


class LoginAdmission:
    """Permit identity/login RPCs, never correspondence or source-owner approval."""

    def __init__(self):
        self.count = 0

    def __call__(self, request):
        while hasattr(request, "query"):
            request = request.query
        name = type(request).__name__
        if self.count >= 200:
            raise OnboardingRefused("Login request ceiling reached")
        if name == "GetUsersRequest":
            from telethon import types

            if not request.id or any(
                not isinstance(user, types.InputUserSelf) for user in request.id
            ):
                raise OnboardingRefused("Login can only inspect its own identity")
        elif name not in {
            "SendCodeRequest",
            "ResendCodeRequest",
            "SignInRequest",
            "CancelCodeRequest",
            "ExportLoginTokenRequest",
            "ImportLoginTokenRequest",
            "GetPasswordRequest",
            "CheckPasswordRequest",
            "LogOutRequest",
            "PingRequest",
            "PingDelayDisconnectRequest",
            "MsgsAck",
            "HttpWaitRequest",
            "DestroySessionRequest",
            "GetConfigRequest",
            "GetStateRequest",
        }:
            raise OnboardingRefused("Operation outside login scope")
        self.count += 1


@dataclass
class LoginFlow:
    id: str
    owner: str = field(repr=False)
    session: str
    method: str
    expected_id: int | None
    expires_at: float
    stage: str = "connecting"
    message: str = "Connecting to Telegram…"
    delivery: str | None = None
    next_delivery: str | None = None
    retry_at: float | None = None
    resent: bool = False
    account_id: int | None = None
    qr_revision: int = 0
    qr: bytes | None = field(default=None, repr=False)
    phone: str = field(default="", repr=False)
    code_hash: str = field(default="", repr=False)
    client: object = field(default=None, repr=False)
    owner_lock: object = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    expiry_task: asyncio.Task | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    attempts: dict = field(default_factory=dict, repr=False)
    # The code's length when Telegram gives one, so a field can submit itself
    # on its last digit (ADR 47 Q3).
    code_length: int | None = None
    # The last code or password was not accepted.
    rejected: bool = field(default=False, repr=False)
    # A sign-in pairing's login (ADR 47 Q11, ADR 48 Q11): the account
    # Telegram verifies decides what it pairs.
    pairing: bool = field(default=False, repr=False)
    # The member a new account joins, or the one a sign-in pairing proved or
    # created (ADR 48).
    member: str = field(default=OWNER, repr=False)
    # An unknown account's details, kept while it reads the Member Agreement.
    joining: dict | None = field(default=None, repr=False)

    def public(self):
        return {
            key: getattr(self, key)
            for key in (
                "id",
                "method",
                "stage",
                "message",
                "expires_at",
                "delivery",
                "next_delivery",
                "retry_at",
                "account_id",
                "qr_revision",
                "code_length",
            )
        } | {
            "can_resend": not self.resent
            and self.retry_at is not None
            and self.next_delivery in _CODE_HELP
            and self.stage == "code"
        }


class LoginManager:
    def __init__(self, accounts, *, client_factory=None, owner_active=None):
        self.owner_active = owner_active or (lambda owner: True)
        self.accounts = accounts
        self.settings = accounts.settings
        self.registry = accounts.registry
        self.factory = client_factory or PersonalLoginClient
        self.enabled = self.settings.mode == "personal" or client_factory is not None
        # One login per trusted client or sign-in attempt (ADR 48), so a
        # stranger's sign-in never holds up another person's.
        self.flows: dict[str, LoginFlow] = {}
        self.gate = asyncio.Lock()

    @property
    def flow(self) -> LoginFlow | None:
        """The most recently started login."""
        return next(reversed(self.flows.values()), None)

    def _client(self, session, *, qr=False):
        path = self.registry.path_for(session)
        private_directory(path.parent)
        lock = OwnerLock(Path(str(path) + ".owner.lock")).acquire()
        try:
            client = self.factory(
                str(path),
                int(self.settings.secret("TELEGRAM_API_ID_REF"))
                if self.settings.mode == "personal"
                else 1,
                self.settings.secret("TELEGRAM_API_HASH_REF")
                if self.settings.mode == "personal"
                else "synthetic",
                receive_updates=qr,
                catch_up=False,
                device_model="Enloq",
            )
            if hasattr(client, "_sender"):
                from .transport.mtproto import RequestPolicy

                # A new authorization file must not reset login server cooldowns.
                client.request_policy = RequestPolicy(
                    self.settings.data / "login.pacing.sqlite3"
                )
                client._sender._send_queue = OnboardingQueue(
                    client._sender._send_queue, LoginAdmission()
                )
            if path.exists():
                path.chmod(0o600)
            return client, lock
        except BaseException:
            lock.close()
            raise

    def require_owner(self, owner):
        if not self.owner_active(owner):
            raise Refused("This browser is no longer trusted")

    async def cancel_owner(self, owner):
        async with self.gate:
            flows = [
                flow
                for flow in self.flows.values()
                if flow.owner == owner and flow.stage != "complete"
            ]
        # Each cancel waits on its flow's lock, which begin holds until its
        # client is installed, so nothing is closed half-made.
        for flow in flows:
            await self.cancel(flow.id, owner)

    def _prune(self, owner):
        """Forget the owner's earlier logins and anyone's long-ended ones."""
        for identity, flow in tuple(self.flows.items()):
            if flow.stage in TERMINAL and (
                flow.owner == owner or flow.expires_at < time.time()
            ):
                del self.flows[identity]

    async def begin(
        self, owner, method, phone="", expected_id=None, *, pairing=False, member=None
    ):
        self.require_owner(owner)
        if not self.enabled:
            raise Refused("Login is available in personal mode")
        if method not in {"qr", "phone"}:
            raise Refused("Choose QR or phone login")
        async with self.gate:
            for flow in self.flows.values():
                if flow.owner == owner and flow.stage not in TERMINAL:
                    return flow.public()
            self._prune(owner)
            if sum(
                f.stage not in TERMINAL and f.pairing == pairing
                for f in self.flows.values()
            ) >= (PAIRING_FLOWS if pairing else FLOWS):
                raise Refused("Too many sign-ins are running. Try again in a minute.")
            await self.recover()
            self.require_owner(owner)
            running = {f.id for f in self.flows.values() if f.stage not in TERMINAL}
            if any(
                row["id"] not in running
                for row in self.registry.rows("SELECT id FROM pending_logins")
            ):
                raise Refused(
                    "An interrupted authorization is still unconfirmed. Try again when Telegram is reachable."
                )
            if expected_id:
                # An Owner Account not on the server yet may be signed in by a
                # sign-in pairing (ADR 47 Q11).
                rows = self.registry.rows(
                    "SELECT state FROM accounts WHERE account_id=?", (expected_id,)
                )
                if rows and rows[0]["state"] in {"authorized", "logout_pending"}:
                    raise Refused(
                        "This account is connected or has an unfinished logout"
                    )
            normalized = utils.parse_phone(phone) if method == "phone" else ""
            if method == "phone" and not normalized:
                raise Refused("Enter a Telegram phone number with its country code")
            identity = uuid.uuid4().hex
            session = f"authorizations/{identity}/telegram.session"
            flow = LoginFlow(
                identity,
                owner,
                session,
                method,
                expected_id,
                time.time() + 600,
                phone=normalized,
                pairing=pairing,
                member=member or OWNER,
            )
            self.flows[identity] = flow
            with self.registry.transaction() as db:
                db.execute(
                    "INSERT INTO pending_logins VALUES (?,?,?,?)",
                    (identity, session, expected_id, time.time()),
                )
            # Telegram's answers come under the flow's own lock, so one slow
            # login holds up no one else's, nor Disconnect or Forget.
            await flow.lock.acquire()
        try:
            flow.client, flow.owner_lock = self._client(session, qr=method == "qr")
            async with asyncio.timeout(60):
                await flow.client.connect()
            self.require_owner(owner)
            flow.expiry_task = asyncio.create_task(self._expire(flow))
            if method == "qr":
                flow.task = asyncio.create_task(self._qr(flow))
            else:
                async with asyncio.timeout(60):
                    sent = await flow.client.send_code_request(flow.phone)
                self._sent(flow, sent)
        except Exception:
            await self._fail(
                flow,
                "Telegram login is unavailable. Check the connection or try QR login.",
            )
        finally:
            flow.lock.release()
        return flow.public()

    def _sent(self, flow, sent):
        metadata = delivery_metadata(sent)
        flow.delivery, flow.next_delivery = (
            metadata["delivery"],
            metadata["next_delivery"],
        )
        timeout = metadata["retry_after_seconds"]
        flow.retry_at = time.time() + timeout if timeout is not None else None
        length = getattr(sent.type, "length", None)
        flow.code_length = (
            length if isinstance(length, int) and 0 < length <= 12 else None
        )
        flow.code_hash = sent.phone_code_hash
        if flow.delivery not in _CODE_HELP:
            raise Refused(
                "This Telegram challenge requires the official app; use QR login instead"
            )
        flow.stage, flow.message = "code", _CODE_HELP[flow.delivery]

    async def _qr(self, flow):
        try:
            while time.time() < flow.expires_at:
                ignored = [
                    a["account_id"]
                    for a in self.registry.accounts()
                    if a["state"] == "authorized"
                ]
                async with asyncio.timeout(60):
                    qr = await flow.client.qr_login(ignored_ids=ignored)
                waiter = asyncio.create_task(
                    qr.wait(
                        timeout=max(
                            0,
                            min(qr.expires.timestamp(), flow.expires_at) - time.time(),
                        )
                    )
                )
                try:
                    # Telethon registers UpdateLoginToken when wait first runs.
                    await asyncio.sleep(0)
                    output = io.BytesIO()
                    qrcode.make(qr.url).save(output, format="PNG")
                    flow.qr, flow.qr_revision = output.getvalue(), flow.qr_revision + 1
                    flow.stage, flow.message = (
                        "qr",
                        "Open Telegram on your phone → Settings → Devices → Link Desktop Device.",
                    )
                    user = await waiter
                except TimeoutError:
                    flow.qr = None
                    continue
                finally:
                    if not waiter.done():
                        waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await waiter
                async with flow.lock:
                    await self._complete(flow, user)
                return
        except errors.SessionPasswordNeededError:
            flow.qr = None
            flow.stage, flow.message = (
                "password",
                "Enter this account’s Telegram two-step password.",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._fail(
                flow,
                "QR login could not finish. Start a new login when Telegram is reachable.",
            )

    def get(self, identity, owner):
        flow = self.flows.get(identity)
        if not flow or flow.owner != owner:
            raise Refused("This login belongs to another or expired browser session")
        return flow

    async def submit(self, identity, owner, action, value=""):
        self.require_owner(owner)
        flow = self.get(identity, owner)
        async with flow.lock:
            self.require_owner(owner)
            flow.rejected = False
            if flow.stage in TERMINAL or time.time() >= flow.expires_at:
                raise Refused("This login has ended; start a new login")
            client = flow.client
            try:
                async with asyncio.timeout(60):
                    if action == "resend":
                        if (
                            flow.stage != "code"
                            or not flow.public()["can_resend"]
                            or time.time() < flow.retry_at
                        ):
                            raise Refused("Wait for Telegram's allowed resend time")
                        flow.resent = True
                        self._sent(
                            flow,
                            await client(
                                functions.auth.ResendCodeRequest(
                                    flow.phone, flow.code_hash
                                )
                            ),
                        )
                        return flow.public()
                    if (
                        action not in {"code", "password"}
                        or action != flow.stage
                        or not value.strip()
                    ):
                        raise Refused("Enter the requested login information")
                    flow.attempts[action] = flow.attempts.get(action, 0) + 1
                    if flow.attempts[action] > 3:
                        await self._fail(
                            flow,
                            "Too many attempts in this login. Start again after Telegram’s cooldown.",
                        )
                        return flow.public()
                    if action == "code":
                        await client.sign_in(
                            phone=flow.phone,
                            code=value.strip(),
                            phone_code_hash=flow.code_hash,
                        )
                    else:
                        await client.sign_in(password=value)
                    user = await client.get_me()
                await self._complete(flow, user)
            except errors.SessionPasswordNeededError:
                flow.stage, flow.message = (
                    "password",
                    "Enter this account’s Telegram two-step password.",
                )
            except (errors.PhoneCodeInvalidError, errors.PasswordHashInvalidError):
                flow.rejected = True
                flow.message = (
                    "That code or password was not accepted. Check it and try again."
                )
            except errors.PhoneCodeExpiredError:
                await self._fail(flow, "The login code expired. Start a new login.")
            except Refused:
                raise
            except Exception:
                await self._fail(
                    flow,
                    "Login outcome is unconfirmed. Your existing accounts are unchanged; retry once Telegram is reachable.",
                )
            return flow.public()

    async def _close_client(self, flow):
        if flow.client:
            try:
                await flow.client.disconnect()
            finally:
                flow.client.session.close()
                flow.client = None
                if flow.owner_lock:
                    flow.owner_lock.close()
                    flow.owner_lock = None

    def _forget_pending(self, identity):
        with self.registry.transaction() as db:
            db.execute("DELETE FROM pending_logins WHERE id=?", (identity,))

    def _cleanup_unpromoted(self, session):
        if not self.registry.rows(
            "SELECT 1 FROM accounts WHERE session=?", (session,)
        ) and not self.registry.rows(
            "SELECT 1 FROM pending_logins WHERE session=?", (session,)
        ):
            path = self.registry.path_for(session).parent
            if (
                path.is_relative_to(self.settings.data / "authorizations")
                and path.exists()
            ):
                shutil.rmtree(path)

    async def _complete(self, flow, user):
        self.require_owner(flow.owner)
        if not user or user.bot or (flow.expected_id and flow.expected_id != user.id):
            # Another account's new authorization ends at once (ADR 47 Q11).
            try:
                async with asyncio.timeout(60):
                    await flow.client(functions.auth.LogOutRequest())
            except Exception:
                logged_out = False  # recover() revokes it before the next login
            else:
                logged_out = True
                self._forget_pending(flow.id)
            await self._fail(
                flow,
                "Sign in with a person's Telegram account."
                if flow.pairing
                else "The signed-in account does not match the account being restored.",
            )
            if logged_out:
                self._cleanup_unpromoted(flow.session)
            return
        # Confirm self through the same owner, then close it before promotion.
        actual = await flow.client.get_me()
        if not actual or actual.id != user.id:
            raise Refused("Login identity could not be verified")
        self.require_owner(flow.owner)
        if flow.pairing:
            # ADR 48 Q11, Q12: the verified account decides what a sign-in
            # pairing pairs.
            outcome, member, message = self.accounts.sign_in_decision(user.id)
            if outcome == "refuse":
                await self._end_authorization(flow, message)
                return
            if outcome == "agreement":
                # The new authorization waits, its client still open, while
                # the person reads the Member Agreement.
                flow.joining = {
                    "id": user.id,
                    "name": " ".join(
                        filter(None, (user.first_name, getattr(user, "last_name", "")))
                    )[:256],
                    "username": (getattr(user, "username", "") or "")[:64],
                    "phone": (getattr(user, "phone", "") or "")[-4:],
                }
                flow.account_id, flow.stage = user.id, "agreement"
                flow.message = "Read the agreement to ask to join."
                flow.phone, flow.code_hash = "", ""
                return
            flow.member = member
        existing = self.registry.rows(
            "SELECT * FROM accounts WHERE account_id=?", (user.id,)
        )
        if existing and existing[0]["member"] != flow.member:
            # Another member's account: its new authorization ends at once and
            # nothing about that member changes (ADR 48).
            await flow.client(functions.auth.LogOutRequest())
            await self._close_client(flow)
            self._forget_pending(flow.id)
            self._cleanup_unpromoted(flow.session)
            flow.stage, flow.message = (
                "error",
                "This account belongs to another person on this server.",
            )
            flow.qr, flow.phone, flow.code_hash = None, "", ""
            return
        if existing and existing[0]["state"] in {"authorized", "logout_pending"}:
            await flow.client(functions.auth.LogOutRequest())
            await self._close_client(flow)
            self._forget_pending(flow.id)
            self._cleanup_unpromoted(flow.session)
            if existing[0]["state"] == "logout_pending":
                flow.stage, flow.message = (
                    "error",
                    "Finish this account’s pending logout before signing in again.",
                )
                return
            flow.message = "This account is already added."
        else:
            if not self.accounts.takes_accounts(flow.member):
                # Their removal began during this login (ADR 48 Q9).
                await self._end_authorization(flow, "This person is leaving Enloq.")
                return
            await self._close_client(flow)
            await self.accounts.activate(
                user,
                flow.session,
                admit=lambda: self.require_owner(flow.owner),
                member=flow.member,
            )
            self._forget_pending(flow.id)
            flow.message = "Account added."
        flow.account_id, flow.stage = user.id, "complete"
        flow.qr, flow.phone, flow.code_hash = None, "", ""

    async def _end_authorization(self, flow, message):
        """Sign a verified new authorization out at once and end the login."""
        try:
            async with asyncio.timeout(60):
                await flow.client(functions.auth.LogOutRequest())
        except Exception:
            logged_out = False  # recover() revokes it before the next login
        else:
            logged_out = True
            self._forget_pending(flow.id)
        await self._fail(flow, message)
        if logged_out:
            self._cleanup_unpromoted(flow.session)

    async def agree(self, identity, owner, version):
        """The person accepted the Member Agreement: their account waits for
        the owner as a Join Request, unused (ADR 48 Q11, Q19)."""
        self.require_owner(owner)
        flow = self.get(identity, owner)
        async with flow.lock:
            self.require_owner(owner)
            if flow.stage != "agreement" or time.time() >= flow.expires_at:
                raise Refused("This sign-in has ended; start again")
            try:
                self.accounts.admit_request(version, flow.joining["id"])
            except Refused as error:
                await self._end_authorization(flow, str(error))
                return flow.public()
            await self._close_client(flow)
            flow.member = await self.accounts.request(
                flow.joining, flow.session, version
            )
            self._forget_pending(flow.id)
            flow.stage, flow.message = "complete", "Asked to join."
            flow.joining = None
            return flow.public()

    async def _fail(self, flow, message):
        flow.stage, flow.message = "error", message
        flow.qr, flow.phone, flow.code_hash = None, "", ""
        flow.joining = None
        # Retain only the app-owned authorization and non-secret recovery pointer.
        await self._close_client(flow)

    async def cancel(self, identity, owner, *, expired=False):
        flow = self.get(identity, owner)
        async with flow.lock:
            if flow.stage == "complete":
                return flow.public()
            if (
                flow.task
                and not flow.task.done()
                and flow.task is not asyncio.current_task()
            ):
                flow.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await flow.task
            flow.stage = "expired" if expired else "cancelled"
            flow.message = (
                "Login expired. Start again." if expired else "Login cancelled."
            )
            try:
                if flow.client:
                    async with asyncio.timeout(60):
                        if await flow.client.is_user_authorized():
                            await flow.client(functions.auth.LogOutRequest())
                    self._forget_pending(flow.id)
                elif self.registry.rows(
                    "SELECT 1 FROM pending_logins WHERE id=?", (flow.id,)
                ):
                    flow.message += (
                        " Authorization cleanup will be checked before another login."
                    )
            except Exception:
                flow.message += " Authorization cleanup is unconfirmed; it will be checked before another login."
            finally:
                await self._close_client(flow)
                flow.qr, flow.phone, flow.code_hash = None, "", ""
                flow.joining = None
                self._cleanup_unpromoted(flow.session)
            return flow.public()

    async def _expire(self, flow):
        await asyncio.sleep(max(0, flow.expires_at - time.time()))
        if flow.stage not in TERMINAL:
            await self.cancel(flow.id, flow.owner, expired=True)

    async def recover(self):
        """Inspect interrupted sessions without ever requesting another code/token."""
        if not self.enabled:
            return
        running = {f.id for f in self.flows.values() if f.stage not in TERMINAL}
        for pending in self.registry.rows(
            "SELECT * FROM pending_logins ORDER BY created"
        ):
            if pending["id"] in running:
                continue  # Another person's login is still going.
            if self.registry.rows(
                "SELECT 1 FROM accounts WHERE session=?", (pending["session"],)
            ):
                self._forget_pending(pending["id"])
                continue
            client = lock = None
            try:
                client, lock = self._client(pending["session"])
                async with asyncio.timeout(60):
                    await client.connect()
                    authorized = await client.is_user_authorized()
                    user = await client.get_me() if authorized else None
                    # Interrupted flows never silently add an account; revoke this
                    # unpromoted authorization, then the user starts a fresh flow.
                    if authorized:
                        if not user or user.bot:
                            raise Refused("Interrupted login identity unavailable")
                        await client(functions.auth.LogOutRequest())
                self._forget_pending(pending["id"])
            except Exception:
                continue
            finally:
                if client:
                    await client.disconnect()
                    client.session.close()
                if lock:
                    lock.close()
            directory = self.registry.path_for(pending["session"]).parent
            if directory.exists():
                shutil.rmtree(directory)

    async def close(self):
        for flow in tuple(self.flows.values()):
            if flow.expiry_task:
                flow.expiry_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await flow.expiry_task
            if flow.task:
                flow.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await flow.task
            await self._close_client(flow)
