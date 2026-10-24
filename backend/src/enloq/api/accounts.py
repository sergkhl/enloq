import asyncio
import json
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import Field

from ..domain.models import Model, Refused
from ..inference.guidance import DEFAULTS
from ..storage.events import commits
from ..storage.members import OWNER
from .scope import MEMBER_ACCOUNT, OWNER_ONLY, require_account

# Browser copies of media are outside the app's expiry; logout and removal
# ask the browser to drop them (ADR 16).
CLEAR_CACHE = '"cache"'


class AccountSummary(Model):
    account_id: int
    name: str
    username: str
    phone: str
    state: Literal["authorized", "logged_out", "requires_login", "logout_pending"]
    connected: bool
    status: str
    ai_enabled: bool
    photo_id: str | None


class Processing(Model):
    ai_enabled: bool


class AccountProcessing(Processing):
    account_id: int


class BudgetSummary(Model):
    engine: str
    calls: int
    reserved: int
    actual: int | None
    unknown: int


class Accounts(Model):
    mode: str
    timezone: str
    accounts: list[AccountSummary]
    budget: list[BudgetSummary]
    login_available: bool


class LoginStart(Model):
    method: Literal["qr", "phone"]
    phone: str = Field(default="", max_length=64)
    expected_id: int | None = Field(default=None, gt=0)


class LoginSecret(Model):
    value: str = Field(min_length=1, max_length=1024, repr=False)


class LoginState(Model):
    id: str
    method: Literal["qr", "phone"]
    stage: Literal[
        "connecting",
        "qr",
        "code",
        "password",
        # An unknown account's sign-in pairing reads the Member Agreement
        # (ADR 48 Q3).
        "agreement",
        "complete",
        "cancelled",
        "expired",
        "error",
    ]
    message: str
    expires_at: float
    delivery: str | None
    next_delivery: str | None
    retry_at: float | None
    can_resend: bool
    account_id: int | None
    qr_revision: int
    # The login code's length when Telegram gives one (ADR 47 Q3).
    code_length: int | None = None


class ReadingView(Model):
    reading_view: Literal["ai", "originals"]


class HideBots(Model):
    hide_bots: bool = Field(strict=True)


class ConfirmRemoval(Model):
    confirm: bool


class BudgetState(Model):
    day: str
    limit: int
    spent: int
    calls: int
    unknown: int
    flat_unknown: int
    reached: bool
    resets_at: float
    changes: list[dict]


class GuidanceState(Model):
    version: int
    defaults_revision: int
    fields: dict[str, str]
    defaults: dict[str, str]
    paused: list[str]


class DecisionModelState(Model):
    """Whether the decision model's route answers (ADR 49 Q14), and since when
    it has not."""

    available: bool
    since: float | None = None


class Settings(Model):
    budget: BudgetState
    group_budget: BudgetState
    concurrency: int
    processing_threshold: int = Field(ge=0, le=10_000)
    memory: bool
    suggestions: bool
    # Processing Guidance is the owner's (ADR 48 Q13); members get none.
    guidance: GuidanceState | None = None
    # So is the decision model's state (ADR 49 Q14).
    decision_model: DecisionModelState | None = None


class GuidanceEdit(Model):
    fields: dict[str, str | None] = Field(max_length=10)


class GuidanceKeep(Model):
    role: Literal["incoming_rewrite", "chat_memory"]


class MemorySwitch(Model):
    enabled: bool


class SuggestionsSwitch(Model):
    enabled: bool


class ConcurrencyEdit(Model):
    limit: int = Field(ge=1, le=8)


class ProcessingThresholdEdit(Model):
    limit: int = Field(strict=True, ge=0, le=10_000)


class BudgetEdit(Model):
    limit_usd: Decimal = Field(ge=0, le=10, decimal_places=2)


def changed(source, after):
    """Scopes changed after a sequence; a recreated store restarts from zero."""
    scopes, latest = source.changes(after)
    if latest < after:
        scopes, latest = source.changes(0)
    return scopes, latest


def account_routes(manager, logins, auth):
    router = APIRouter(prefix="/api")

    @router.get(
        "/accounts/{account_id}/processing",
        response_model=AccountProcessing,
        dependencies=MEMBER_ACCOUNT,
    )
    def processing(account_id: int):
        manager.get(account_id)
        return manager.registry.processing(account_id)

    @router.post(
        "/accounts/{account_id}/processing",
        response_model=AccountProcessing,
        dependencies=MEMBER_ACCOUNT,
    )
    async def set_processing(account_id: int, body: Processing):
        return manager.set_processing(account_id, body.ai_enabled)

    # Each member's own reading view and Hide Bots (ADR 48 Q20).
    @router.get("/preferences/reading-view", response_model=ReadingView)
    def reading_view(request: Request):
        return manager.registry.reading_view(request.state.member)

    @router.post("/preferences/reading-view", response_model=ReadingView)
    def set_reading_view(body: ReadingView, request: Request):
        return manager.registry.set_reading_view(
            body.reading_view, request.state.member
        )

    @router.get("/preferences/hide-bots", response_model=HideBots)
    def hide_bots(request: Request):
        return manager.registry.hide_bots(request.state.member)

    @router.post("/preferences/hide-bots", response_model=HideBots)
    def set_hide_bots(body: HideBots, request: Request):
        return manager.registry.set_hide_bots(body.hide_bots, request.state.member)

    @router.get("/accounts", response_model=Accounts)
    def accounts(request: Request):
        return dict(
            mode=manager.settings.mode,
            timezone=manager.settings.timezone,
            accounts=manager.summaries(request.state.member),
            budget=manager.registry.usage(member=request.state.member),
            login_available=logins.enabled,
        )

    @router.post("/logins", response_model=LoginState)
    async def start(body: LoginStart, request: Request):
        # Sign in again names one of the member's own accounts; another
        # member's answers as an unknown one (ADR 48).
        require_account(request, body.expected_id)
        # A new account joins the signed-in client's member (ADR 48).
        return await logins.begin(
            request.state.local_session,
            **body.model_dump(),
            member=request.state.member,
        )

    @router.get("/logins/{flow_id}", response_model=LoginState)
    def login_state(flow_id: str, request: Request):
        return logins.get(flow_id, request.state.local_session).public()

    @router.get("/logins/{flow_id}/qr")
    def qr(flow_id: str, request: Request):
        flow = logins.get(flow_id, request.state.local_session)
        if flow.stage != "qr" or not flow.qr:
            raise Refused("QR code is no longer available")
        return Response(content=flow.qr, media_type="image/png")

    @router.post("/logins/{flow_id}/code", response_model=LoginState)
    async def code(flow_id: str, body: LoginSecret, request: Request):
        return await logins.submit(
            flow_id, request.state.local_session, "code", body.value
        )

    @router.post("/logins/{flow_id}/password", response_model=LoginState)
    async def password(flow_id: str, body: LoginSecret, request: Request):
        return await logins.submit(
            flow_id, request.state.local_session, "password", body.value
        )

    @router.post("/logins/{flow_id}/resend", response_model=LoginState)
    async def resend(flow_id: str, request: Request):
        return await logins.submit(flow_id, request.state.local_session, "resend")

    @router.post("/logins/{flow_id}/cancel", response_model=LoginState)
    async def cancel(flow_id: str, request: Request):
        return await logins.cancel(flow_id, request.state.local_session)

    @router.post("/accounts/{account_id}/logout", dependencies=MEMBER_ACCOUNT)
    async def logout(account_id: int, response: Response):
        await manager.logout(account_id)
        response.headers["Clear-Site-Data"] = CLEAR_CACHE
        return {"ok": True}

    @router.post("/accounts/{account_id}/reconnect", dependencies=MEMBER_ACCOUNT)
    async def reconnect(account_id: int):
        await manager.reconnect(account_id)
        return {"ok": True}

    @router.post(
        "/accounts/{account_id}/remove-local-data", dependencies=MEMBER_ACCOUNT
    )
    async def remove(account_id: int, body: ConfirmRemoval, response: Response):
        if not body.confirm:
            raise Refused(
                "Confirm removal of this account's local drafts and conversation cache"
            )
        await manager.remove(account_id)
        response.headers["Clear-Site-Data"] = CLEAR_CACHE
        return {"ok": True}

    @router.get("/settings", response_model=Settings)
    def settings(request: Request):
        return current_settings(request.state.member)

    @router.post("/settings/budget", response_model=Settings, dependencies=OWNER_ONLY)
    def set_budget(body: BudgetEdit):
        manager.registry.set_budget(int(body.limit_usd * 1_000_000_000))
        return current_settings()

    @router.post(
        "/settings/group-budget", response_model=Settings, dependencies=OWNER_ONLY
    )
    def set_group_budget(body: BudgetEdit):
        manager.registry.set_budget(
            int(body.limit_usd * 1_000_000_000), family="group_briefing"
        )
        return current_settings()

    @router.post(
        "/settings/concurrency", response_model=Settings, dependencies=OWNER_ONLY
    )
    def set_concurrency(body: ConcurrencyEdit):
        manager.registry.set_concurrency(body.limit)
        manager.capacity.wake()
        return current_settings()

    @router.post(
        "/settings/processing-threshold",
        response_model=Settings,
        dependencies=OWNER_ONLY,
    )
    def set_processing_threshold(body: ProcessingThresholdEdit):
        manager.set_processing_threshold(body.limit)
        return current_settings()

    @router.post("/settings/guidance", response_model=Settings, dependencies=OWNER_ONLY)
    def set_guidance(body: GuidanceEdit):
        # The only writer of Processing Guidance; rewrite endpoints take none.
        manager.registry.set_guidance(body.fields)
        return current_settings()

    @router.post(
        "/settings/guidance/keep", response_model=Settings, dependencies=OWNER_ONLY
    )
    def keep_guidance(body: GuidanceKeep):
        manager.registry.keep_guidance(body.role)
        return current_settings()

    @router.post("/settings/memory", response_model=Settings, dependencies=OWNER_ONLY)
    def set_memory(body: MemorySwitch):
        manager.registry.set_memory_enabled(body.enabled)
        return current_settings()

    @router.post(
        "/settings/suggestions", response_model=Settings, dependencies=OWNER_ONLY
    )
    def set_suggestions(body: SuggestionsSwitch):
        manager.set_suggestions(body.enabled)
        return current_settings()

    def current_settings(member=OWNER):
        """The service-wide AI settings with the member's own budgets (ADR 48);
        only the owner reads Processing Guidance, which only they edit."""
        owner = member == OWNER
        return {
            "budget": manager.registry.budget(
                timezone=manager.settings.timezone, member=member
            ),
            "group_budget": manager.registry.budget(
                timezone=manager.settings.timezone,
                family="group_briefing",
                member=member,
            ),
            "concurrency": manager.registry.concurrency(),
            "processing_threshold": manager.registry.processing_threshold(),
            "memory": manager.registry.memory_enabled(),
            "suggestions": manager.registry.suggestions_enabled(),
            "guidance": {
                **manager.registry.guidance(),
                "defaults": DEFAULTS,
                "paused": sorted(manager.registry.paused_roles()),
            }
            if owner
            else None,
            "decision_model": decision_model() if owner else None,
        }

    def decision_model():
        outage = manager.registry.decision_route()
        if outage is None:
            return {"available": True}
        return {"available": False, "since": outage["since"]}

    @router.get("/events")
    async def events(request: Request):
        member = getattr(request.state, "member", None) or auth.member_of(
            request.state.local_session
        )

        async def stream():
            # Change notifications replace full polling. No correspondence
            # content or login challenges: only scopes (chat keys, settings),
            # and only the member's own accounts' chats (ADR 48).
            with commits.subscribe() as wake:
                seen: dict = {}
                first = True
                last_sent = asyncio.get_running_loop().time()
                while True:
                    wake.clear()
                    if not auth.active(request.state.local_session):
                        yield "event: revoked\ndata: {}\n\n"
                        return
                    shared, seen["shared"] = changed(
                        manager.registry, seen.get("shared", 0)
                    )
                    own = [a["account_id"] for a in manager.registry.accounts(member)]
                    chats = {}
                    for identity, runtime in tuple(manager.runtimes.items()):
                        if not runtime.accepting or identity not in own:
                            continue
                        scopes, seen[identity] = changed(
                            runtime.assistant.store, seen.get(identity, 0)
                        )
                        if scopes:
                            chats[str(identity)] = scopes
                    if first:
                        value = {"accounts": own}
                        yield "event: refresh\ndata: " + json.dumps(value) + "\n\n"
                        first = False
                    elif shared or chats:
                        value = {"shared": shared, "chats": chats}
                        yield "event: change\ndata: " + json.dumps(value) + "\n\n"
                        last_sent = asyncio.get_running_loop().time()
                    elif asyncio.get_running_loop().time() - last_sent >= 15:
                        yield ": keepalive\n\n"
                        last_sent = asyncio.get_running_loop().time()
                    try:
                        await asyncio.wait_for(wake.wait(), 15)
                    except TimeoutError:
                        pass  # Keepalive, auth expiry and out-of-process recovery.

        return StreamingResponse(stream(), media_type="text/event-stream")

    return router
