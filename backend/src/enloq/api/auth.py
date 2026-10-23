"""HTTP boundary for browser and device pairing.

Launcher control never grants API access, and a server with a public origin
takes none (ADR 47 Q1). A Trusted Device (ADR 38) claims a pairing without
cookies and receives its credential in the response body; a trusted browser or
phone starts and approves one itself (ADR 38 Q24, ADR 47 Q1), and only the
Enloq app claims those (ADR 47 Q10). Every pairing is noted in the Owner
Account's Saved Messages (ADR 47 Q2).
"""

import contextlib
import re
from typing import Literal
from urllib.parse import urlsplit

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Request, Response
from pydantic import Field

from ..browser_auth import COOKIE_AGE, PAIRING_AGE
from ..domain import agreement
from ..domain.models import Model, Refused
from ..storage.members import OWNER
from .accounts import LoginSecret, LoginState

# A device keeps the proof from its claim response and sends it back here.
PROOF_HEADER = "x-pairing-proof"


class LauncherCommand(Model):
    secret: str = Field(min_length=1, max_length=200, repr=False)
    action: Literal["status", "approve", "cancel"] = "status"
    code: str = Field(default="", max_length=6)


class BrowserStatus(Model):
    authenticated: bool
    # The client belongs to a Join Request waiting for the owner (ADR 48).
    waiting: bool = False


class PairingStatus(Model):
    id: str
    expires_at: float
    state: Literal[
        "waiting",
        "pending",
        "approved",
        "cancelled",
        "complete",
        "expired",
        "already_trusted",
    ]
    code: str
    label: str
    kind: Literal["", "browser", "device"] = ""
    approver: Literal["server", "browser"] = "server"


class DeviceClaim(Model):
    name: str = Field(default="", max_length=200)


class DeviceClaimStatus(PairingStatus):
    proof: str = Field(repr=False)


class DeviceCredential(Model):
    token: str = Field(repr=False)


class CodeStart(Model):
    name: str = Field(default="", max_length=200)
    # A browser pairs with the same code (ADR 44 Q10); its user agent names it.
    browser: bool = False


class CodeAttempt(Model):
    id: str
    # Returned once, at the start; the phone sends it back with each step.
    proof: str = Field(default="", repr=False)
    expires_at: float


class CodeEntry(Model):
    code: str = Field(min_length=1, max_length=12, repr=False)


class SignInStart(Model):
    phone: str = Field(min_length=1, max_length=64, repr=False)
    name: str = Field(default="", max_length=200)
    browser: bool = False


class Agreement(Model):
    """The Member Agreement an unknown account reads (ADR 48 Q3)."""

    version: str
    title: str
    paragraphs: list[str]


class AgreementAcceptance(Model):
    version: str = Field(min_length=1, max_length=32)


class SignInPairingState(Model):
    id: str
    # Returned once, at the start; the client sends it back with each step.
    proof: str = Field(default="", repr=False)
    login: LoginState
    # A phone's device credential, once the sign-in pairs it.
    token: str = Field(default="", repr=False)
    # A browser's trust cookie was set.
    authenticated: bool = False
    # ADR 48: what an unknown account reads before asking to join, and
    # whether the paired client waits for the owner's approval.
    agreement: Agreement | None = None
    waiting: bool = False


class ClientDecision(Model):
    action: Literal["approve", "cancel"]
    code: str = Field(default="", max_length=6)


class TrustedBrowser(Model):
    id: str
    label: str
    approved_at: float
    last_access: float
    current: bool
    kind: Literal["browser", "device"] = "browser"


def set_browser_cookie(response, auth, token):
    response.set_cookie(
        auth.cookie,
        token,
        httponly=True,
        samesite="strict",
        secure=auth.secure,
        path="/api",
        max_age=COOKIE_AGE,
    )


def auth_routes(auth, logins, owner):
    router = APIRouter(prefix="/api/auth")
    server = urlsplit(auth.origin).netloc

    @router.get("/session", response_model=BrowserStatus)
    async def status(request: Request, response: Response):
        current = request.state.local_session
        waiting = getattr(request.state, "member_state", None) == "waiting"
        if request.state.client_kind == "device":
            return {"authenticated": True, "waiting": waiting}
        response.delete_cookie("reply_session", path="/")
        if current:
            set_browser_cookie(response, auth, request.cookies[auth.cookie])
        else:
            response.delete_cookie(auth.cookie, path="/api")
        return {"authenticated": bool(current), "waiting": waiting}

    @router.post("/pairings", response_model=PairingStatus)
    async def create(body: LauncherCommand):
        auth.control(body.secret)
        if auth.remote:
            # ADR 47 Q1: nothing pairs a public server from its terminal.
            raise Refused(
                "This server pairs with the code from Saved Messages or a trusted phone or browser"
            )
        return auth.create_pairing().public()

    @router.post("/pairings/{identity}/control", response_model=PairingStatus)
    async def control(identity: str, body: LauncherCommand):
        auth.control(body.secret)
        return auth.decide(identity, body.action, body.code).public()

    @router.post("/pairings/{identity}/claim", response_model=PairingStatus)
    async def claim(identity: str, request: Request, response: Response):
        pairing, token = auth.claim(
            identity,
            request.cookies.get(auth.pair_cookie + identity, ""),
            request.headers.get("user-agent", ""),
            request.state.local_session,
        )
        if token:
            response.set_cookie(
                auth.pair_cookie + identity,
                token,
                httponly=True,
                samesite="strict",
                secure=auth.secure,
                path=f"/api/auth/pairings/{identity}",
                max_age=PAIRING_AGE,
            )
        return pairing.public()

    @router.get("/pairings/{identity}", response_model=PairingStatus)
    async def pairing_status(identity: str, request: Request):
        pairing = auth.pairing(identity)
        auth.check_proof(
            pairing,
            request.cookies.get(auth.pair_cookie + identity, "")
            or request.headers.get(PROOF_HEADER, ""),
        )
        return pairing.public()

    @router.post("/pairings/{identity}/device", response_model=DeviceClaimStatus)
    async def claim_device(identity: str, body: DeviceClaim, request: Request):
        if request.state.local_session or request.cookies.get(auth.cookie):
            raise Refused("Pair the app without another trust credential")
        pairing, proof = auth.claim_device(identity, body.name)
        return {**pairing.public(), "proof": proof}

    async def paired_note(name, token=None, kind="browser"):
        # The record of every client of the owner's that pairs is the owner's
        # check (ADR 43 Q8, ADR 47 Q2); a member's own pairings post nothing
        # (ADR 48 Q12).
        client = auth.client(token, kind) if token else None
        if client and client["member"] != OWNER:
            return
        try:
            await owner.post_note(f"{name} was paired with {server}.")
        except Refused:
            pass  # The pairing stands; the failure is logged by its class.

    @router.post(
        "/pairings/{identity}/device/complete", response_model=DeviceCredential
    )
    async def complete_device(identity: str, request: Request):
        name = auth.pairing(identity).name
        token = auth.complete(
            identity, request.headers.get(PROOF_HEADER, ""), kind="device"
        )
        await paired_note(name, token, "device")
        return {"token": token}

    @router.post("/pairings/{identity}/complete", response_model=BrowserStatus)
    async def complete(identity: str, request: Request, response: Response):
        name = auth.pairing(identity).name
        token = auth.complete(
            identity, request.cookies.get(auth.pair_cookie + identity, "")
        )
        set_browser_cookie(response, auth, token)
        response.delete_cookie(
            auth.pair_cookie + identity, path=f"/api/auth/pairings/{identity}"
        )
        await paired_note(name, token)
        return {"authenticated": True}

    # ADR 43 Q8: a phone or browser pairs with a code the service posts to the
    # Owner Account's Saved Messages: the six digits alone, in monospace, so a
    # tap copies them (ADR 47 Q2). Codes never reach a log or an answer, and
    # every refused code counts toward the refusal limit.
    async def send_code(attempt):
        code = auth.issue_code(attempt)
        await owner.post_note(code, monospace=True)

    @router.post("/code-pairings", response_model=CodeAttempt)
    async def start_code(body: CodeStart, request: Request):
        # A browser's stale cookie doesn't block it; a valid one, or a phone
        # carrying a cookie, does.
        if request.state.local_session or (
            not body.browser and request.cookies.get(auth.cookie)
        ):
            raise Refused("Pair the app without another trust credential")
        # A browser's claim always carries its own Origin (ADR 11).
        if body.browser and request.headers.get("origin") != auth.origin:
            raise Refused("Pair this browser from its own page")
        # The Owner Account's reason comes before anything starts.
        owner.transport()
        attempt, proof = auth.start_code(
            body.name,
            kind="browser" if body.browser else "device",
            agent=request.headers.get("user-agent", ""),
        )
        try:
            await send_code(attempt)
        except Refused:
            auth.end_code(attempt)
            raise
        return {"id": attempt.id, "proof": proof, "expires_at": attempt.expires_at}

    @router.post("/code-pairings/{identity}/resend", response_model=CodeAttempt)
    async def resend_code(identity: str, request: Request):
        attempt = auth.code_attempt(identity, request.headers.get(PROOF_HEADER, ""))
        await send_code(attempt)
        return {"id": attempt.id, "expires_at": attempt.expires_at}

    @router.post("/code-pairings/{identity}/code", response_model=DeviceCredential)
    async def enter_code(identity: str, body: CodeEntry, request: Request):
        attempt, token = auth.submit_code(
            identity, request.headers.get(PROOF_HEADER, ""), body.code
        )
        await paired_note(attempt.name)
        return {"token": token}

    # A browser's code sets its trust cookie; its token never reaches the
    # page (ADR 44 Q10, MW4).
    @router.post("/code-pairings/{identity}/browser", response_model=BrowserStatus)
    async def enter_browser_code(
        identity: str, body: CodeEntry, request: Request, response: Response
    ):
        if request.headers.get("origin") != auth.origin:
            raise Refused("Pair this browser from its own page")
        attempt, token = auth.submit_code(
            identity, request.headers.get(PROOF_HEADER, ""), body.code, kind="browser"
        )
        set_browser_cookie(response, auth, token)
        await paired_note(attempt.name)
        return {"authenticated": True}

    # ADR 47 Q11 and ADR 48 Q11, Q12: anyone may sign in with Telegram through
    # the service's own login, and the account Telegram verifies decides what
    # pairs: the Owner Account or a member's home account proves who they are
    # (signing back in when signed out), and an unknown account reads the
    # Member Agreement and waits as a Join Request. A refused step counts
    # toward the refusal limit.
    @router.post("/sign-in-pairings", response_model=SignInPairingState)
    async def start_sign_in(body: SignInStart, request: Request):
        if request.state.local_session or (
            not body.browser and request.cookies.get(auth.cookie)
        ):
            raise Refused("Pair the app without another trust credential")
        if body.browser and request.headers.get("origin") != auth.origin:
            raise Refused("Pair this browser from its own page")
        if not owner.identity:
            # Without an Owner Account nobody could approve a request, so an
            # empty server takes no stranger's sign-in (ADR 47 Q15).
            raise Refused("No Owner Account is set on this server")
        if not 7 <= sum(c.isdigit() for c in body.phone) <= 15:
            raise Refused("Enter a Telegram phone number with its country code")
        attempt, proof = auth.start_sign_in(
            body.name,
            kind="browser" if body.browser else "device",
            agent=request.headers.get("user-agent", ""),
            phone=body.phone,
            address=getattr(request.state, "client_address", None),
        )
        for earlier in auth.earlier_sign_ins(attempt):
            with contextlib.suppress(Refused):
                await logins.cancel(earlier.flow, auth.login_owner(earlier))
            auth.end_sign_in(earlier)
        try:
            login = await logins.begin(
                auth.login_owner(attempt), "phone", body.phone, None, pairing=True
            )
        except Refused:
            auth.end_sign_in(attempt)
            raise
        attempt.flow = login["id"]
        if login["stage"] not in {"code", "password"}:
            auth.end_sign_in(attempt)
            raise Refused(login["message"])
        return {"id": attempt.id, "proof": proof, "login": login}

    def sign_in_attempt(identity, request):
        attempt = auth.sign_in_attempt(identity, request.headers.get(PROOF_HEADER, ""))
        # A browser's attempt carries its own Origin at every step (ADR 11).
        if attempt.kind == "browser" and request.headers.get("origin") != auth.origin:
            raise Refused("Pair this browser from its own page")
        return attempt

    async def finished(attempt, login, response):
        """The sign-in paired its client to the member it proved or created."""
        member = logins.get(attempt.flow, auth.login_owner(attempt)).member
        token = auth.finish_sign_in(attempt, member)
        row = logins.registry.member(member)
        waiting = bool(row and row["state"] == "waiting")
        if member == OWNER:
            await owner.post_note_soon(f"{attempt.name} was paired with {server}.")
        if attempt.kind == "browser":
            set_browser_cookie(response, auth, token)
            return {
                "id": attempt.id,
                "login": login,
                "authenticated": True,
                "waiting": waiting,
            }
        return {"id": attempt.id, "login": login, "token": token, "waiting": waiting}

    async def sign_in_step(identity, action, value, request, response):
        attempt = sign_in_attempt(identity, request)
        login = await logins.submit(
            attempt.flow, auth.login_owner(attempt), action, value
        )
        if login["stage"] == "complete":
            return await finished(attempt, login, response)
        if login["stage"] == "agreement":
            return {"id": attempt.id, "login": login, "agreement": agreement.public()}
        if login["stage"] in {"error", "expired", "cancelled"}:
            auth.end_sign_in(attempt)
            raise Refused(login["message"])
        if logins.get(attempt.flow, auth.login_owner(attempt)).rejected:
            raise Refused(login["message"])
        return {"id": attempt.id, "login": login}

    @router.get("/sign-in-pairings/{identity}", response_model=SignInPairingState)
    async def sign_in_state(identity: str, request: Request):
        attempt = sign_in_attempt(identity, request)
        login = logins.get(attempt.flow, auth.login_owner(attempt)).public()
        if login["stage"] == "agreement":
            return {"id": attempt.id, "login": login, "agreement": agreement.public()}
        return {"id": attempt.id, "login": login}

    @router.post("/sign-in-pairings/{identity}/code", response_model=SignInPairingState)
    async def sign_in_code(
        identity: str, body: LoginSecret, request: Request, response: Response
    ):
        return await sign_in_step(identity, "code", body.value, request, response)

    @router.post(
        "/sign-in-pairings/{identity}/password", response_model=SignInPairingState
    )
    async def sign_in_password(
        identity: str, body: LoginSecret, request: Request, response: Response
    ):
        return await sign_in_step(identity, "password", body.value, request, response)

    @router.post(
        "/sign-in-pairings/{identity}/resend", response_model=SignInPairingState
    )
    async def sign_in_resend(identity: str, request: Request, response: Response):
        return await sign_in_step(identity, "resend", "", request, response)

    @router.post(
        "/sign-in-pairings/{identity}/agree", response_model=SignInPairingState
    )
    async def sign_in_agree(
        identity: str, body: AgreementAcceptance, request: Request, response: Response
    ):
        """The person accepts the Member Agreement and asks to join (ADR 48)."""
        attempt = sign_in_attempt(identity, request)
        login = await logins.agree(
            attempt.flow, auth.login_owner(attempt), body.version
        )
        if login["stage"] != "complete":
            auth.end_sign_in(attempt)
            raise Refused(login["message"])
        return await finished(attempt, login, response)

    @router.post(
        "/sign-in-pairings/{identity}/cancel", response_model=SignInPairingState
    )
    async def sign_in_cancel(identity: str, request: Request):
        attempt = sign_in_attempt(identity, request)
        login = await logins.cancel(attempt.flow, auth.login_owner(attempt))
        auth.end_sign_in(attempt)
        return {"id": attempt.id, "login": login}

    def started_by(request):
        # A trusted browser or phone pairs a phone (ADR 38 Q24, ADR 47 Q1).
        return request.state.local_session

    @router.post("/client-pairings", response_model=PairingStatus)
    async def start(request: Request):
        return auth.create_pairing(owner=started_by(request)).public()

    @router.get("/client-pairings/{identity}", response_model=PairingStatus)
    async def started(identity: str, request: Request):
        return auth.owned(identity, started_by(request)).public()

    @router.post("/client-pairings/{identity}", response_model=PairingStatus)
    async def decide(identity: str, body: ClientDecision, request: Request):
        return auth.decide_owned(
            identity, started_by(request), body.action, body.code
        ).public()

    @router.get("/client-pairings/{identity}/qr.svg")
    async def qr(identity: str, request: Request):
        pairing = auth.owned(identity, started_by(request))
        image = qrcode.make(
            pairing_link(auth.origin, pairing.id),
            image_factory=qrcode.image.svg.SvgPathFillImage,
            border=2,
        )
        return Response(image.to_string(), media_type="image/svg+xml")

    @router.post("/disconnect")
    async def disconnect(request: Request, response: Response):
        """A trusted client leaves this server (ADR 43 Q2): its own credential
        is revoked with Forget's effects, so its stream ends, its push
        destination retires and the logins it started are cancelled. A Join
        Request's last client withdraws the request (ADR 48 Q19)."""
        current = request.state.local_session
        member = request.state.member
        auth.revoke(current)
        if request.state.client_kind == "browser":
            response.delete_cookie(auth.cookie, path="/api")
            response.headers["Clear-Site-Data"] = '"cache"'  # ADR 16
        await logins.cancel_owner(current)
        if request.state.member_state == "waiting" and not auth.member_clients(member):
            await logins.accounts.remove_member(member)
        return {"ok": True}

    @router.get("/browsers", response_model=list[TrustedBrowser])
    async def browsers(request: Request):
        return auth.browsers(request.state.local_session)

    @router.post("/browsers/{identity}/revoke")
    async def revoke(identity: str, request: Request, response: Response):
        # A member forgets only their own clients (ADR 48).
        auth.revoke(identity, member=request.state.member)
        if identity == request.state.local_session:
            response.delete_cookie(auth.cookie, path="/api")
            response.headers["Clear-Site-Data"] = '"cache"'  # ADR 16
        await logins.cancel_owner(identity)
        return {"ok": True}

    return router


def pairing_link(origin: str, identity: str) -> str:
    # The public locator: it cannot approve or complete a pairing.
    return f"{origin}/?connect={identity}"


# A phone's pairing requests, which carry no Origin or cookie (ADR 38, ADR 43),
# and the code and sign-in routes a browser shares with it (ADR 44 Q10,
# ADR 47 Q11).
DEVICE_PAIRING = re.compile(
    r"/api/auth/(?:pairings/[a-f0-9]{32}/device(?:/complete)?"
    r"|code-pairings(?:/[a-f0-9]{32}/(?:resend|code|browser))?"
    r"|sign-in-pairings(?:/[a-f0-9]{32}(?:/(?:code|password|resend|agree|cancel))?)?)"
)


def public_auth_path(path: str) -> bool:
    # Explicit operations only; never exempt arbitrary /auth content APIs.
    return (
        path
        in {
            "/api/auth/session",
            "/api/auth/pairings",
            "/api/auth/saved-messages",
            # The host's reads (ADR 43): loopback and
            # the control credential, like the post.
            "/api/auth/saved-messages/latest-image",
            "/api/auth/saved-messages/pairing-code",
            # The owner's last few notes (ADR 51 Q1).
            "/api/auth/saved-messages/notes",
        }
        or bool(
            re.fullmatch(
                r"/api/auth/pairings/[a-f0-9]{32}(?:/(?:control|claim|complete))?",
                path,
            )
        )
        or bool(DEVICE_PAIRING.fullmatch(path))
    )
