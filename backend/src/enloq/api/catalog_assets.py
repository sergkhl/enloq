"""Authenticated, deliberate catalog asset preparation and cache-only reads."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from ..domain.catalog import HEX, REVISION
from ..domain.catalog_assets import CatalogAssetPreparation, CatalogAssetView
from ..domain.models import ChatKey, Refused, Unavailable
from .scope import MEMBER_ACCOUNT


def catalog_asset_routes(manager):
    router = APIRouter(
        prefix="/api/accounts/{account_id}/chats/{peer}/catalog/items/{token}/assets",
        dependencies=MEMBER_ACCOUNT,
    )

    def access(request, account_id, peer, app):
        try:
            key = ChatKey(account_id=account_id, peer=peer).key
        except ValueError:
            raise Refused("A valid conversation is required") from None
        session = request.state.local_session
        if not request.app.state.browser_auth.active(session):
            app.catalog.clear_transient(session, key)
            raise HTTPException(
                401,
                "Connect this browser from the Mac launcher",
                headers={"Clear-Site-Data": '"cache"'},
            )
        if manager.get(account_id) is not app:
            raise Refused("The active account workspace changed")
        app.catalog.allowed(key)
        return key, session

    def guard(request, account_id, peer, app):
        return lambda: access(request, account_id, peer, app)

    @router.get("", response_model=CatalogAssetView)
    async def describe(
        account_id: int,
        peer: str,
        request: Request,
        token: str = Path(pattern=HEX),
        revision: str = Query(pattern=REVISION),
        variant: Literal["thumbnail", "media"] = "thumbnail",
    ):
        async with manager.lease(account_id) as app:
            key, session = access(request, account_id, peer, app)
            try:
                return await app.catalog_assets.describe(
                    key,
                    session,
                    token,
                    revision,
                    variant=variant,
                    guard=guard(request, account_id, peer, app),
                )
            except Unavailable:
                raise HTTPException(
                    503, "Catalog verification unavailable; retry"
                ) from None

    @router.post("/prepare", response_model=CatalogAssetView)
    async def prepare(
        account_id: int,
        peer: str,
        body: CatalogAssetPreparation,
        request: Request,
        token: str = Path(pattern=HEX),
    ):
        async with manager.lease(account_id) as app:
            key, session = access(request, account_id, peer, app)
            try:
                return await app.catalog_assets.prepare(
                    key,
                    session,
                    token,
                    body.revision,
                    variant=body.variant,
                    guard=guard(request, account_id, peer, app),
                )
            except Unavailable:
                raise HTTPException(503, "Catalog preview unavailable; retry") from None

    @router.get("/file")
    async def file(
        account_id: int,
        peer: str,
        request: Request,
        token: str = Path(pattern=HEX),
        revision: str = Query(pattern=REVISION),
        variant: Literal["thumbnail", "media"] = "thumbnail",
    ):
        async with manager.lease(account_id) as app:
            key, session = access(request, account_id, peer, app)
            try:
                path, mime, etag = await app.catalog_assets.serve(
                    key,
                    session,
                    token,
                    revision,
                    variant=variant,
                    guard=guard(request, account_id, peer, app),
                )
            except Unavailable:
                raise HTTPException(
                    503, "Catalog verification unavailable; retry"
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
        account_id: int,
        peer: str,
        request: Request,
        token: str = Path(pattern=HEX),
        revision: str = Query(pattern=REVISION),
    ):
        async with manager.lease(account_id) as app:
            key, session = access(request, account_id, peer, app)
            try:
                value = await app.catalog_assets.animation(
                    key,
                    session,
                    token,
                    revision,
                    guard=guard(request, account_id, peer, app),
                )
            except Unavailable:
                raise HTTPException(
                    503, "Catalog verification unavailable; retry"
                ) from None
            return Response(
                value,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )

    return router
