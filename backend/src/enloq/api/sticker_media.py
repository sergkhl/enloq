"""Exact current/observed GIF and sticker previews without history fills."""

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from ..domain.catalog_assets import ReceivedAssetPreparation, ReceivedAssetView
from ..domain.models import ChatKey, Refused, Unavailable
from .scope import MEMBER_ACCOUNT


def sticker_media_routes(manager):
    router = APIRouter(
        prefix="/api/accounts/{account_id}/chats/{peer}/originals/{identity}/media",
        dependencies=MEMBER_ACCOUNT,
    )

    def source(request, account_id, peer, identity, revision, version, app):
        if not request.app.state.browser_auth.active(request.state.local_session):
            raise HTTPException(
                401,
                "Connect this browser from the Mac launcher",
                headers={"Clear-Site-Data": '"cache"'},
            )
        if manager.get(account_id) is not app or not app.media.authorized():
            raise Refused("Account authorization changed")
        try:
            key = ChatKey(account_id=account_id, peer=peer).key
        except ValueError:
            raise Refused("A valid conversation is required") from None
        if app.store.chat(key).kind == "group":
            app.retained.cache_read(key)
        message = (
            app.store.history.version(key, identity, version)
            if version
            else app.store.message(key, identity)
        )
        if not message or message.deleted or message.revision != revision:
            raise Refused("Media source changed or expired; refresh its original")
        app.media.preview_access(message, history=bool(version))
        return message

    def shown(app, account_id, peer, identity, version):
        """A deleted message's preview is its capture's, from the cache only
        (ADR 52 Q7)."""
        try:
            chat = ChatKey(account_id=account_id, peer=peer).key
        except ValueError:
            raise Refused("A valid conversation is required") from None
        return app.store.history.shown_version(chat, identity, version)

    def guard(request, account_id, peer, identity, revision, version, app):
        return lambda: source(
            request, account_id, peer, identity, revision, version, app
        )

    def view(message, version, value):
        return ReceivedAssetView(
            revision=message.revision, version=version, **value.model_dump()
        )

    @router.get("/representation", response_model=ReceivedAssetView)
    async def describe(
        request: Request,
        account_id: int,
        peer: str,
        identity: int = Path(gt=0, lt=2**63),
        revision: int = Query(ge=0),
        version: int | None = Query(default=None, gt=0),
    ):
        async with manager.lease(account_id) as app:
            version = shown(app, account_id, peer, identity, version)
            message = source(
                request, account_id, peer, identity, revision, version, app
            )
            try:
                value = await app.media.preview(
                    message,
                    history=bool(version),
                    guard=guard(
                        request, account_id, peer, identity, revision, version, app
                    ),
                )
            except Unavailable:
                raise HTTPException(
                    503, "Media verification unavailable; retry"
                ) from None
            return view(message, version, value)

    @router.post("/prepare-preview", response_model=ReceivedAssetView)
    async def prepare(
        request: Request,
        account_id: int,
        peer: str,
        body: ReceivedAssetPreparation,
        identity: int = Path(gt=0, lt=2**63),
    ):
        async with manager.lease(account_id) as app:
            version = shown(app, account_id, peer, identity, body.version)
            message = source(
                request, account_id, peer, identity, body.revision, version, app
            )
            try:
                value = await app.media.prepare_preview(
                    message,
                    history=bool(version),
                    guard=guard(
                        request,
                        account_id,
                        peer,
                        identity,
                        body.revision,
                        version,
                        app,
                    ),
                )
            except Unavailable:
                raise HTTPException(503, "Media preview unavailable; retry") from None
            return view(message, version, value)

    @router.get("/preview")
    async def file(
        request: Request,
        account_id: int,
        peer: str,
        identity: int = Path(gt=0, lt=2**63),
        revision: int = Query(ge=0),
        version: int | None = Query(default=None, gt=0),
    ):
        async with manager.lease(account_id) as app:
            version = shown(app, account_id, peer, identity, version)
            message = source(
                request, account_id, peer, identity, revision, version, app
            )
            try:
                path, mime, etag = await app.media.serve_preview(
                    message,
                    history=bool(version),
                    guard=guard(
                        request, account_id, peer, identity, revision, version, app
                    ),
                )
            except Unavailable:
                raise HTTPException(
                    503, "Media verification unavailable; retry"
                ) from None
            private = {"Cache-Control": "private, no-cache", "ETag": etag}
            if etag in request.headers.get("if-none-match", "").split(", "):
                path.unlink(missing_ok=True)
                return Response(status_code=304, headers=private)
            return FileResponse(
                path,
                media_type=mime,
                headers=private,
                background=BackgroundTask(path.unlink, missing_ok=True),
            )

    @router.get("/animation")
    async def animation(
        request: Request,
        account_id: int,
        peer: str,
        identity: int = Path(gt=0, lt=2**63),
        revision: int = Query(ge=0),
        version: int | None = Query(default=None, gt=0),
    ):
        async with manager.lease(account_id) as app:
            version = shown(app, account_id, peer, identity, version)
            message = source(
                request, account_id, peer, identity, revision, version, app
            )
            try:
                value = await app.media.animation(
                    message,
                    history=bool(version),
                    guard=guard(
                        request, account_id, peer, identity, revision, version, app
                    ),
                )
            except Unavailable:
                raise HTTPException(
                    503, "Media verification unavailable; retry"
                ) from None
            return Response(
                value,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )

    return router
