from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..application.accounts import AccountManager
from ..application.notification_delivery import NotificationDelivery
from ..application.owner_account import OwnerAccount, OwnerSignedOut
from ..browser_auth import BrowserAuth
from ..config import Settings
from ..domain.models import Conflict, Refused
from ..login import LoginManager
from ..storage.store import Store
from .accounts import account_routes
from .auth import DEVICE_PAIRING, auth_routes, public_auth_path
from .catalog import catalog_routes
from .catalog_assets import catalog_asset_routes
from .deletions import deletion_routes
from .group_filters import group_filter_routes
from .limits import RefusalLimit
from .notifications import notification_routes
from .people import people_routes
from .reminders import reminder_routes
from .routes import routes
from .saved import saved_routes
from .selected_media import selected_media_routes
from .send_resume import send_resume_routes
from .sticker_media import sticker_media_routes

# What a Join Request's client may reach while it waits (ADR 48 Q19).
WAITING_PATHS = frozenset(
    {"/api/auth/session", "/api/auth/disconnect", "/api/members/me"}
)


def create_app(
    settings: Settings,
    store: Store | None = None,
    *,
    login_factory=None,
    web: Path | None = None,
) -> FastAPI:
    # The built browser UI. Authored browser tests pass their own build, so
    # testing never replaces the UI a running service serves.
    web = web or Path(__file__).resolve().parents[4] / "web/dist"
    manager = AccountManager(settings, store=store)
    auth = BrowserAuth(settings)
    notifications = NotificationDelivery(manager, auth)
    logins = LoginManager(
        manager, client_factory=login_factory, owner_active=auth.login_owner_active
    )
    owner = OwnerAccount(manager, settings)
    # ADR 48: a new Join Request alerts the owner; a member who leaves or is
    # removed loses every trusted client at once.
    manager.on_request = notifications.join_request
    manager.on_member_removed = auth.revoke_member
    # ADR 49 Q14: an unavailable decision model alerts the owner once.
    manager.on_decision_outage = notifications.decision_outage

    @asynccontextmanager
    async def lifespan(app):
        await manager.start()
        recovery = asyncio.create_task(logins.recover())
        notification_worker = asyncio.create_task(notifications.run())
        try:
            yield
        finally:
            recovery.cancel()
            notification_worker.cancel()
            await asyncio.gather(recovery, notification_worker, return_exceptions=True)
            await logins.close()
            await manager.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.browser_auth = auth
    app.state.accounts, app.state.logins = manager, logins
    app.state.owner = owner
    app.state.notifications = notifications
    # Fixture access is explicit; HTTP never resolves an implicit current account.
    first = next(iter(manager.runtimes.values()), None)
    app.state.assistant = first.assistant if first else None
    app.state.store = first.assistant.store if first else None
    app.include_router(routes(manager, settings))
    app.include_router(catalog_routes(manager))
    app.include_router(catalog_asset_routes(manager))
    app.include_router(selected_media_routes(manager))
    app.include_router(sticker_media_routes(manager))
    app.include_router(deletion_routes(manager))
    app.include_router(group_filter_routes(manager))
    app.include_router(send_resume_routes(manager))
    app.include_router(account_routes(manager, logins, auth))
    app.include_router(auth_routes(auth, logins, owner))
    app.include_router(saved_routes(owner, auth))
    app.include_router(notification_routes(notifications))
    app.include_router(people_routes(manager, auth))
    app.include_router(reminder_routes(manager))

    expected_origin = settings.origin
    expected_host = urlsplit(expected_origin).netloc
    limit = RefusalLimit(settings.trusted_proxy) if settings.remote else None

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        # The address checks apply to every request; on a VPS the refusal
        # limit also guards the API reachable before browser trust.
        address = None
        if limit and request.url.path.startswith("/api/"):
            address = await limit.client(
                request.client.host if request.client else "",
                request.headers.get("x-forwarded-for", ""),
            )
            if wait := limit.blocked(address):
                return JSONResponse(
                    {"detail": "Too many refused attempts. Try again later."},
                    status_code=429,
                    headers={"Retry-After": str(wait), "Cache-Control": "no-store"},
                )
        # Sign-ins are also limited per client address (ADR 48 Q11).
        request.state.client_address = address
        response = await checked(request, call_next)
        if address and response.status_code in {400, 401, 403, 404, 422}:
            if public_auth_path(request.url.path) or response.status_code == 401:
                limit.record(address)
        return response

    async def checked(request: Request, call_next):
        if request.headers.get("host") != expected_host:
            return JSONResponse({"detail": "Local host required"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != expected_origin:
            return JSONResponse({"detail": "Same origin required"}, status_code=403)
        cookie = request.cookies.get(auth.cookie, "")
        authorization = request.headers.get("authorization")
        bearer = ""
        if authorization is not None:
            scheme, _, bearer = authorization.partition(" ")
            if scheme.lower() != "bearer" or not bearer.strip() or cookie:
                # One trust credential per request: a browser's cookie or a
                # Trusted Device's bearer header, never both (ADR 38).
                return JSONResponse(
                    {"detail": "Use one trust credential"}, status_code=400
                )
            bearer = bearer.strip()
        if request.method not in {"GET", "HEAD"}:
            # A device sends its credential explicitly, so a request without
            # cookies needs no Origin there; browsers keep ADR 11's rule.
            explicit = bearer or (
                not cookie and DEVICE_PAIRING.fullmatch(request.url.path)
            )
            if (
                origin != expected_origin and not (explicit and origin is None)
            ) or not request.headers.get("content-type", "").startswith(
                "application/json"
            ):
                return JSONResponse(
                    {"detail": "Authenticated same-origin JSON required"},
                    status_code=403,
                )
        client = auth.client(bearer, kind="device") if bearer else auth.client(cookie)
        # ADR 48: every trusted client belongs to one member, whose accounts,
        # preferences and clients it may reach.
        member = manager.registry.member(client["member"]) if client else None
        if client and not member:
            client = None  # A removed member's client is not trusted.
        request.state.local_session = client["id"] if client else None
        request.state.member = member["id"] if member else None
        request.state.member_role = member["role"] if member else None
        request.state.member_state = member["state"] if member else None
        request.state.client_kind = (
            ("device" if bearer else "browser") if request.state.local_session else None
        )
        if (
            request.state.member_state == "waiting"
            and request.url.path.startswith("/api/")
            and request.url.path not in WAITING_PATHS
        ):
            # A Join Request's client only waits until the owner decides
            # (ADR 48 Q19).
            return JSONResponse(
                {"detail": "Waiting for the owner's approval"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if (
            request.url.path.startswith("/api/")
            and not public_auth_path(request.url.path)
            and not request.state.local_session
        ):
            headers = {"Cache-Control": "no-store"}
            if cookie:
                # A revoked or unknown browser drops media it may have stored.
                headers["Clear-Site-Data"] = '"cache"'
            return JSONResponse(
                {"detail": auth.device_hint if bearer else auth.connect_hint},
                status_code=401,
                headers=headers,
            )
        response = await call_next(request)
        path = request.url.path
        response.headers.update(
            {
                # Hashed bundle names change with content; media routes set
                # private revalidation; everything else, including JSON, is
                # never stored.
                "Cache-Control": "public, max-age=31536000, immutable"
                if path.startswith("/assets/") and response.status_code == 200
                else response.headers.get("cache-control", "no-store")
                if path.startswith("/api/accounts/")
                and path.endswith(("/media", "/assets/file", "/media/preview"))
                else "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        if settings.remote:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse(
            {"detail": "Check the requested fields and try again"}, status_code=422
        )

    @app.exception_handler(Refused)
    async def refused(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(OwnerSignedOut)
    async def owner_signed_out(request, error):
        # The pairing screens answer by offering Sign in with Telegram
        # (ADR 47 Q13).
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/health")
    def health():
        return {"mode": settings.mode, "status": "ready"}

    @app.get("/", response_class=HTMLResponse)
    def home():
        index = web / "index.html"
        if index.is_file():
            return FileResponse(index)
        return "<html lang='en'><title>Enloq</title><h1>Enloq</h1><p>Offline foundation ready. Authored fixtures are available through the local authenticated API.</p></html>"

    @app.get("/notifications-sw.js")
    def notification_worker():
        return FileResponse(
            web / "notifications-sw.js",
            media_type="application/javascript",
        )

    assets = web / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    return app
