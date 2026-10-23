"""Authenticated selection actions and exact owned selected/send previews."""

import hashlib
import os

from fastapi import APIRouter, HTTPException, Path, Request, Response

from ..domain.catalog import HEX
from ..domain.models import ChatKey, Refused
from ..domain.selected_media import SelectedMediaAction, SelectMedia, public_media
from .scope import MEMBER_ACCOUNT


def selected_media_routes(manager):
    router = APIRouter(
        prefix="/api/accounts/{account_id}/chats/{peer}/selected-media",
        dependencies=MEMBER_ACCOUNT,
    )

    def access(request, account_id, peer, app, *, cache_only=False):
        try:
            key = ChatKey(account_id=account_id, peer=peer).key
        except ValueError:
            raise Refused("A valid conversation is required") from None
        if not request.app.state.browser_auth.active(request.state.local_session):
            raise HTTPException(
                401,
                "Connect this browser from the Mac launcher",
                headers={"Clear-Site-Data": '"cache"'},
            )
        if manager.get(account_id) is not app:
            raise Refused("The active account workspace changed")
        if cache_only:
            app.selected_media.read_access(key)
        else:
            app.selected_media.allowed(key)
        return key

    @router.post("")
    async def select(account_id: int, peer: str, body: SelectMedia, request: Request):
        async with manager.lease(account_id) as app:
            key = access(request, account_id, peer, app)

            def guard():
                return access(request, account_id, peer, app)

            result = await app.selected_media.select(
                key,
                body.revision,
                request.state.local_session,
                body.token,
                body.catalog_revision,
                guard=guard,
            )
            guard()
            return result

    @router.post("/retry")
    async def retry(
        account_id: int, peer: str, body: SelectedMediaAction, request: Request
    ):
        async with manager.lease(account_id) as app:
            key = access(request, account_id, peer, app)

            def guard():
                return access(request, account_id, peer, app)

            result = await app.selected_media.retry(
                key, body.revision, body.id, guard=guard
            )
            guard()
            return result

    @router.post("/discard")
    async def discard(
        account_id: int, peer: str, body: SelectedMediaAction, request: Request
    ):
        app = manager.get(account_id)
        key = access(request, account_id, peer, app)
        return app.selected_media.discard(
            key,
            body.revision,
            body.id,
            guard=lambda: access(request, account_id, peer, app),
        )

    def response(value, path, *, animation=False):
        # A bounded private response buffer is independent of owner-file links;
        # an open preview never changes the retained file's link count or pins.
        facts = value["facts"]
        if animation:
            if (
                facts.get("format") != "tgs"
                or not facts.get("animation_json")
                or not public_media(value)["renderer_supported"]
            ):
                raise Refused("Safe sticker animation is unavailable")
            data, mime = facts["animation_json"].encode(), "application/json"
        else:
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as stream:
                    data = stream.read(facts["size"] + 1)
            except OSError:
                raise Refused("Exact selected media bytes are unavailable") from None
            if (
                len(data) != facts["size"]
                or hashlib.sha256(data).hexdigest() != facts["sha256"]
            ):
                raise Refused("Exact selected media bytes changed")
            mime = facts["mime"]
        return Response(
            data,
            media_type=mime,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/{identity}/content")
    @router.get("/{identity}/animation")
    async def selected_content(
        account_id: int, peer: str, request: Request, identity: str = Path(pattern=HEX)
    ):
        app = manager.get(account_id)
        key = access(request, account_id, peer, app, cache_only=True)
        value, path = app.selected_media.selected_file(key, identity)
        result = response(
            value, path, animation=request.url.path.endswith("/animation")
        )
        access(request, account_id, peer, app, cache_only=True)
        app.store.selected_media.current(key, identity)
        return result

    @router.get("/operations/{operation}/{identity}/content")
    @router.get("/operations/{operation}/{identity}/animation")
    async def operation_content(
        account_id: int,
        peer: str,
        request: Request,
        operation: str = Path(pattern=HEX),
        identity: str = Path(pattern=HEX),
    ):
        app = manager.get(account_id)
        key = access(request, account_id, peer, app, cache_only=True)
        value, path = app.selected_media.operation_file(key, operation, identity)
        result = response(
            value, path, animation=request.url.path.endswith("/animation")
        )
        access(request, account_id, peer, app, cache_only=True)
        app.selected_media.operation_file(key, operation, identity)
        return result

    return router
