"""Authenticated cached libraries and deliberate bounded owner-entered search."""

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response

from ..domain.catalog import (
    HEX,
    REVISION,
    CatalogOpen,
    CatalogPackView,
    CatalogPreview,
    CatalogRefresh,
    CatalogView,
    GifSearch,
    GifSearchScope,
    GifSearchView,
)
from ..domain.models import ChatKey, Refused, Unavailable
from .scope import MEMBER_ACCOUNT


def catalog_routes(manager):
    router = APIRouter(
        prefix="/api/accounts/{account_id}/chats/{peer}/catalog",
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
            app.catalog.clear_transient(session, key)
            raise Refused("The active account workspace changed")
        app.catalog.allowed(key)
        return key, session

    def guard(request, account_id, peer, app):
        return lambda: access(request, account_id, peer, app)

    @router.get("", response_model=CatalogView)
    async def cached(account_id: int, peer: str, request: Request):
        app = manager.get(account_id)
        key, session = access(request, account_id, peer, app)
        return app.catalog.cached(key, session)

    @router.post("/refresh", response_model=CatalogView)
    async def refresh(
        account_id: int, peer: str, body: CatalogRefresh, request: Request
    ):
        async with manager.lease(account_id) as app:
            key, session = access(request, account_id, peer, app)
            return await app.catalog.refresh(
                key,
                session,
                body.revision,
                guard=guard(request, account_id, peer, app),
            )

    @router.get("/packs/{identity}", response_model=CatalogPackView)
    async def pack(
        account_id: int,
        peer: str,
        request: Request,
        identity: str = Path(pattern=HEX),
        revision: str = Query(pattern=REVISION),
    ):
        app = manager.get(account_id)
        key, session = access(request, account_id, peer, app)
        return app.catalog.pack(key, session, identity, revision)

    @router.post("/packs/{identity}/refresh", response_model=CatalogPackView)
    async def refresh_pack(
        account_id: int,
        peer: str,
        body: CatalogRefresh,
        request: Request,
        identity: str = Path(pattern=HEX),
    ):
        async with manager.lease(account_id) as app:
            key, session = access(request, account_id, peer, app)
            return await app.catalog.refresh_pack(
                key,
                session,
                identity,
                body.revision,
                guard=guard(request, account_id, peer, app),
            )

    @router.post("/search/open", response_model=GifSearchScope)
    async def open_search(
        account_id: int, peer: str, body: CatalogOpen, request: Request
    ):
        app = manager.get(account_id)
        key, session = access(request, account_id, peer, app)
        return app.catalog.open_search(key, session)

    @router.post("/search", response_model=GifSearchView)
    async def search(account_id: int, peer: str, body: GifSearch, request: Request):
        async with manager.lease(account_id) as app:
            key, session = access(request, account_id, peer, app)
            try:
                return await app.catalog.search(
                    key,
                    session,
                    body,
                    guard=guard(request, account_id, peer, app),
                )
            except Unavailable:
                raise HTTPException(
                    503, "GIF search is unavailable; try again"
                ) from None

    @router.post("/search/close", status_code=204)
    async def close_search(
        account_id: int, peer: str, body: GifSearchScope, request: Request
    ):
        app = manager.get(account_id)
        key, session = access(request, account_id, peer, app)
        app.catalog.close_search(key, session, body.epoch)
        return Response(status_code=204)

    @router.get("/items/{token}/preview", response_model=CatalogPreview)
    async def preview(
        account_id: int,
        peer: str,
        request: Request,
        token: str = Path(pattern=HEX),
        revision: str = Query(pattern=REVISION),
    ):
        app = manager.get(account_id)
        key, session = access(request, account_id, peer, app)
        return app.catalog.preview(key, session, token, revision)

    return router
