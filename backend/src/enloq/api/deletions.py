"""Explicit current-original deletion through the existing account owner (ADR 31)."""

from fastapi import APIRouter, Path, Request
from pydantic import Field

from ..domain.models import ChatKey, Model, Refused
from .scope import MEMBER_ACCOUNT


class DeletionTarget(Model):
    message_id: int = Field(gt=0, lt=2**63)
    revision: int = Field(ge=0)
    source_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class DeletionSubmit(Model):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    targets: list[DeletionTarget] = Field(min_length=1)
    retry_of: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def deletion_routes(manager):
    router = APIRouter(
        prefix="/api/accounts/{account_id}/chats/{peer}/deletions",
        dependencies=MEMBER_ACCOUNT,
    )

    def key(account_id, peer):
        try:
            return ChatKey(account_id=account_id, peer=peer).key
        except ValueError:
            raise Refused("A valid conversation is required") from None

    @router.post("")
    async def submit(
        account_id: int, peer: str, body: DeletionSubmit, request: Request
    ):
        # Acceptance saves intent before returning; the service owns further work.
        async with manager.lease(account_id) as app:
            return await app.deletion.submit(
                key(account_id, peer),
                body.id,
                [target.model_dump(exclude_none=True) for target in body.targets],
                browser=request.state.local_session,
                retry_of=body.retry_of,
            )

    @router.get("/{identity}")
    def operation(
        account_id: int,
        peer: str,
        identity: str = Path(pattern=r"^[a-f0-9]{32}$"),
    ):
        return manager.get(account_id).deletion.get(key(account_id, peer), identity)

    @router.post("/{identity}/check")
    async def check(
        account_id: int,
        peer: str,
        identity: str = Path(pattern=r"^[a-f0-9]{32}$"),
    ):
        # This action performs exact reads only and cannot repeat a deletion.
        async with manager.lease(account_id) as app:
            return await app.deletion.check(key(account_id, peer), identity)

    return router
